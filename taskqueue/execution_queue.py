"""
Fila de execução persistente para testes sequenciais de configurações PostgreSQL.

Backend: Postgres (ver db/schema.sql), não mais um arquivo JSON local. Isso
permite que múltiplos workers `cli/run.py` — potencialmente em máquinas
diferentes — reivindiquem tarefas da mesma fila com segurança, via
`SELECT ... FOR UPDATE SKIP LOCKED` em ``next()``.

Estrutura de uma tarefa (dict retornado por next()/pending()/done()/__iter__)
------------------------------------------------------------------------------
    {
        "id":               int,         # identificador único (BIGSERIAL)
        "combination":      str,         # ex: "s1", "s1_s2_s3"
        "tier":             str,         # "low" | "medium" | "high"
        "config":           dict,        # parâmetros PostgreSQL gerados
        "repetition":       int,
        "status":           str,         # ver ciclo de vida abaixo
        "retry_count":      int,         # tentativas já realizadas (0 = primeira vez)
        "abandoned_reason": str | None,  # "invalid_config" | "timeout" | "max_retries"
        "result":           dict | None, # resumo preenchido após execução bem-sucedida
        "error":            str  | None  # preenchido em caso de falha
    }

Ciclo de vida de uma tarefa
---------------------------
    pending → running → done
                     ↘ pending (retry automático, até MAX_RETRIES tentativas)
                     ↘ abandoned (invalid_config | timeout | max_retries esgotado)

A transição pending→running ocorre atomicamente em ``next()``, via
``UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED)`` — dois workers
nunca recebem a mesma tarefa, mesmo concorrendo pela mesma linha.

Recuperação de crash: como não há mais um único processo "dono" da fila que
recarrega o arquivo ao reiniciar, ``next()`` também reivindica tarefas presas
em "running" cujo lease (baseado no timeout do próprio tier — ver
runner/task_executor.py::_TASK_TIMEOUT_S) já expirou. Um worker que morreu
sem marcar a tarefa como concluída/abandonada libera a tarefa automaticamente
depois desse tempo, sem precisar de heartbeat nem de reiniciar nada.

Nota: o estado "failed" ainda existe para compatibilidade com ``--retry-failed``,
mas não é mais usado durante o fluxo normal de retry (``requeue_with_retry`` vai
direto para "pending").
"""

from collections.abc import Iterator

from psycopg.types.json import Jsonb

from utils.db import connect, get_dsn

# Status válidos
_PENDING   = "pending"
_RUNNING   = "running"
_DONE      = "done"
_FAILED    = "failed"
_ABANDONED = "abandoned"

_ALL_STATUSES = (_PENDING, _RUNNING, _DONE, _FAILED, _ABANDONED)

# Colunas retornadas por tarefa — "result_summary" é exposto como "result"
# para manter o mesmo formato de dict usado no resto do projeto.
_TASK_COLUMNS = """
    id, combination, tier, config, repetition, status, retry_count,
    abandoned_reason, error, result_summary AS result,
    claimed_at, claimed_by, created_at, updated_at
"""

# Lease de reivindicação por tier: quanto tempo uma tarefa "running" pode
# ficar sem lock antes de ser considerada abandonada por um worker morto e
# devolvida à fila. Espelha runner/task_executor.py::_TASK_TIMEOUT_S mais
# uma margem de segurança — uma tarefa legítima sempre termina (ou levanta
# TaskTimeoutError) bem antes desse prazo.
_LEASE_SQL_CASE = """
    CASE tier
        WHEN 'low'    THEN INTERVAL '3.5 hours'
        WHEN 'medium' THEN INTERVAL '4.5 hours'
        WHEN 'high'   THEN INTERVAL '8.5 hours'
        ELSE INTERVAL '9 hours'
    END
"""


class ExecutionQueue:
    """Fila de execução persistente para configurações PostgreSQL, em Postgres.

    Args:
        dsn: Connection string do banco de controle. Se omitida, usa
             ``utils.db.get_dsn()`` (variável de ambiente ``DATABASE_URL``).
    """

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or get_dsn()

    # ------------------------------------------------------------------
    # Fábrica — constrói a fila a partir dos resultados gerados
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(
        cls,
        all_results: dict[str, dict[str, list]],
        dsn: str | None = None,
        repetitions: int = 1,
    ) -> "ExecutionQueue":
        """Popula a fila a partir de um dict em memória.

        Não-destrutivo: se a fila já tiver tarefas, retorna sem inserir nada
        (mesma proteção contra sobrescrever uma fila em andamento que existia
        no backend em arquivo). Use ``reset()`` explicitamente antes, se a
        intenção for começar uma fila nova.

        Args:
            all_results: Dict ``{label: {tier: [Config]}}`` — saída direta
                         de ``generate_configs()``.
            dsn:         Connection string do banco de controle.
            repetitions: Número de vezes que cada config é enfileirada.

        Returns:
            Instância de ExecutionQueue pronta para uso.
        """
        queue = cls(dsn)
        if not queue.is_empty():
            return queue

        rows: list[tuple] = []
        for tier in ["low", "medium", "high"]:
            for label, tier_configs in all_results.items():
                for config in tier_configs.get(tier, []):
                    for rep in range(repetitions):
                        rows.append((label, tier, Jsonb(config), rep))

        with connect(queue._dsn) as conn:
            conn.cursor().executemany(
                "INSERT INTO tasks (combination, tier, config, repetition) "
                "VALUES (%s, %s, %s, %s)",
                rows,
            )
        return queue

    # ------------------------------------------------------------------
    # API principal de execução
    # ------------------------------------------------------------------

    def next(self, worker_id: str | None = None) -> dict | None:
        """Reivindica a próxima tarefa disponível e a marca como 'running'.

        Atômico mesmo com múltiplos workers concorrentes (``FOR UPDATE
        SKIP LOCKED``): prioriza tarefas 'pending' genuínas; se não houver
        nenhuma, reivindica uma tarefa 'running' cujo lease expirou (worker
        anterior presumivelmente morto).

        Args:
            worker_id: Identificador opcional do worker (host:pid, por
                       exemplo) — gravado em ``claimed_by`` para diagnóstico.

        Returns:
            Dict da tarefa, ou None se não houver nenhuma disponível.
        """
        with connect(self._dsn) as conn:
            row = conn.execute(
                f"""
                UPDATE tasks
                SET status = 'running', claimed_at = now(), claimed_by = %(worker_id)s
                WHERE id = (
                    SELECT id FROM tasks
                    WHERE status = 'pending'
                       OR (status = 'running' AND claimed_at < now() - ({_LEASE_SQL_CASE}))
                    ORDER BY (status = 'running'), id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING {_TASK_COLUMNS}
                """,
                {"worker_id": worker_id},
            ).fetchone()
        return row

    def mark_done(self, task_id: int, result: dict) -> None:
        """Marca uma tarefa como concluída com sucesso.

        Args:
            task_id: ID da tarefa retornada por ``next()``.
            result:  Dict com os resultados do benchmark (resumo).
        """
        with connect(self._dsn) as conn:
            conn.execute(
                "UPDATE tasks SET status = %s, result_summary = %s WHERE id = %s",
                (_DONE, Jsonb(result), task_id),
            )

    def mark_abandoned(self, task_id: int, error: str, reason: str = "") -> None:
        """Marca uma tarefa como abandonada permanentemente.

        Args:
            task_id: ID da tarefa.
            error:   Traceback ou descrição do último erro.
            reason:  Causa do abandono: "invalid_config" | "timeout" | "max_retries".
        """
        with connect(self._dsn) as conn:
            conn.execute(
                "UPDATE tasks SET status = %s, error = %s, abandoned_reason = %s WHERE id = %s",
                (_ABANDONED, error, reason or None, task_id),
            )

    def requeue_with_retry(self, task_id: int, error: str) -> None:
        """Recoloca uma tarefa na fila incrementando seu contador de tentativas.

        Vai direto para "pending" — mesmo fluxo que no backend em arquivo.

        Args:
            task_id: ID da tarefa que falhou.
            error:   Descrição do erro desta tentativa (salva para diagnóstico).
        """
        with connect(self._dsn) as conn:
            conn.execute(
                """
                UPDATE tasks
                SET status = %s, retry_count = retry_count + 1, error = %s,
                    claimed_at = NULL, claimed_by = NULL
                WHERE id = %s
                """,
                (_PENDING, error, task_id),
            )

    def retry_failed(self) -> int:
        """Recoloca todas as tarefas com status 'failed' de volta na fila.

        Tarefas com status 'abandoned' não são incluídas.

        Returns:
            Número de tarefas reenfileiradas.
        """
        with connect(self._dsn) as conn:
            rows = conn.execute(
                "UPDATE tasks SET status = %s, error = NULL WHERE status = %s RETURNING id",
                (_PENDING, _FAILED),
            ).fetchall()
        return len(rows)

    # ------------------------------------------------------------------
    # Consultas de estado
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        """Retorna contagem de tarefas por status."""
        counts: dict[str, int] = dict.fromkeys(_ALL_STATUSES, 0)
        with connect(self._dsn) as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status").fetchall()
        for row in rows:
            counts[row["status"]] = row["n"]
        return counts

    def pending(self) -> list[dict]:
        """Retorna todas as tarefas com status 'pending'."""
        with connect(self._dsn) as conn:
            return conn.execute(
                f"SELECT {_TASK_COLUMNS} FROM tasks WHERE status = %s ORDER BY id", (_PENDING,)
            ).fetchall()

    def done(self) -> list[dict]:
        """Retorna todas as tarefas concluídas."""
        with connect(self._dsn) as conn:
            return conn.execute(
                f"SELECT {_TASK_COLUMNS} FROM tasks WHERE status = %s ORDER BY id", (_DONE,)
            ).fetchall()

    def is_empty(self) -> bool:
        """True se a fila não tiver nenhuma tarefa (nunca gerada, ou resetada)."""
        with connect(self._dsn) as conn:
            row = conn.execute("SELECT NOT EXISTS (SELECT 1 FROM tasks) AS empty").fetchone()
        return row["empty"]

    def reset(self) -> None:
        """Apaga todas as tarefas e resultados — começa a fila do zero.

        Substitui o antigo ``queue_path.unlink()``. Reinicia a contagem de
        IDs (``RESTART IDENTITY``); ``CASCADE`` também limpa ``task_results``.
        """
        with connect(self._dsn) as conn:
            conn.execute("TRUNCATE tasks RESTART IDENTITY CASCADE")

    def __len__(self) -> int:
        """Número total de tarefas na fila (qualquer status)."""
        with connect(self._dsn) as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()
        return row["n"]

    def __iter__(self) -> Iterator[dict]:
        """Itera sobre todas as tarefas (qualquer status)."""
        with connect(self._dsn) as conn:
            rows = conn.execute(f"SELECT {_TASK_COLUMNS} FROM tasks ORDER BY id").fetchall()
        return iter(rows)

    def __repr__(self) -> str:
        s = self.stats()
        return (
            f"ExecutionQueue(total={sum(s.values())}, "
            f"pending={s[_PENDING]}, running={s[_RUNNING]}, "
            f"done={s[_DONE]}, failed={s[_FAILED]}, abandoned={s[_ABANDONED]})"
        )
