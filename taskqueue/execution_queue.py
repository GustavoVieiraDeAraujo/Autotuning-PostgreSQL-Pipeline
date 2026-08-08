"""
Fila de execução persistente para testes sequenciais de configurações PostgreSQL.

Estrutura de uma tarefa
-----------------------
    {
        "id":               int,         # identificador único sequencial
        "combination":      str,         # ex: "s1", "s1_s2_s3"
        "tier":             str,         # "low" | "medium" | "high"
        "config":           dict,        # parâmetros PostgreSQL gerados
        "status":           str,         # ver ciclo de vida abaixo
        "retry_count":      int,         # tentativas já realizadas (0 = primeira vez)
        "abandoned_reason": str | None,  # "invalid_config" | "timeout" | "max_retries"
        "result":           dict | None, # preenchido após execução bem-sucedida
        "error":            str  | None  # preenchido em caso de falha
    }

Ciclo de vida de uma tarefa
---------------------------
    pending → running → done
                     ↘ pending (retry automático, até MAX_RETRIES tentativas)
                     ↘ abandoned (invalid_config | timeout | max_retries esgotado)

A transição pending→running ocorre atomicamente em ``next()``.
Tarefas interrompidas em estado "running" voltam para "pending" no
próximo carregamento da fila (auto-recuperação).

Nota: o estado "failed" ainda existe para compatibilidade com ``--retry-failed``,
mas não é mais usado durante o fluxo normal de retry (``requeue_with_retry`` vai
direto para "pending", evitando um write duplo e eliminando o estado transitório
visível no queue.json).
"""

import json
import os
from collections import deque
from collections.abc import Iterator
from pathlib import Path

# Status válidos
_PENDING   = "pending"
_RUNNING   = "running"
_DONE      = "done"
_FAILED    = "failed"
_ABANDONED = "abandoned"


class ExecutionQueue:
    """Fila de execução persistente para configurações PostgreSQL.

    Usa internamente um ``deque`` para operações O(1) de enqueue/dequeue
    e persiste o estado em JSON após cada mudança, garantindo que o
    progresso seja preservado entre execuções.

    Args:
        queue_path: Caminho do arquivo JSON que persiste o estado da fila.
                    Criado automaticamente se não existir.
    """

    def __init__(self, queue_path: str | Path) -> None:
        self._path      = Path(queue_path)
        self._tasks: list[dict] = []
        self._queue: deque[int] = deque()  # IDs das tasks "pending"
        self._id_to_idx: dict[int, int] = {}  # task_id → índice em _tasks

        if self._path.exists():
            self._load()
        else:
            self._save()

    # ------------------------------------------------------------------
    # Fábrica — constrói a fila a partir dos resultados gerados
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(
        cls,
        all_results: dict[str, dict[str, list]],
        queue_path: str | Path,
        repetitions: int = 1,
    ) -> "ExecutionQueue":
        """Cria a fila diretamente de um dict em memória.

        Args:
            all_results:  Dict ``{label: {tier: [Config]}}`` — saída direta
                          de ``generate_configs()``.
            queue_path:   Onde salvar o estado da fila (``queue.json``).
            repetitions:  Número de vezes que cada config é enfileirada.
                          Padrão 1. Use >1 para robustez estatística (mediana
                          de N runs por config).

        Returns:
            Instância de ExecutionQueue pronta para uso.
        """
        queue = cls(queue_path)

        if queue._tasks:
            return queue

        task_id = 0
        for tier in ["low", "medium", "high"]:
            for label, tier_configs in all_results.items():
                for config in tier_configs.get(tier, []):
                    for rep in range(repetitions):
                        queue._tasks.append({
                            "id":               task_id,
                            "combination":      label,
                            "tier":             tier,
                            "config":           config,
                            "repetition":       rep,
                            "status":           _PENDING,
                            "retry_count":      0,
                            "abandoned_reason": None,
                            "result":           None,
                            "error":            None,
                        })
                        queue._queue.append(task_id)
                        task_id += 1

        queue._id_to_idx = {t["id"]: i for i, t in enumerate(queue._tasks)}
        queue._save()
        return queue

    # ------------------------------------------------------------------
    # API principal de execução
    # ------------------------------------------------------------------

    def next(self) -> dict | None:
        """Retorna a próxima tarefa pendente e a marca como 'running'.

        A mudança de status é persistida imediatamente para garantir
        que uma tarefa não seja entregue duas vezes em caso de reinício.

        Returns:
            Dict da tarefa, ou None se não houver pendentes.
        """
        while self._queue:
            task_id = self._queue.popleft()
            task    = self._tasks[self._id_to_idx[task_id]]
            if task["status"] == _PENDING:
                task["status"] = _RUNNING
                self._save()
                return task
        return None

    def mark_done(self, task_id: int, result: dict) -> None:
        """Marca uma tarefa como concluída com sucesso.

        Args:
            task_id: ID da tarefa retornada por ``next()``.
            result:  Dict com os resultados do benchmark (resumo).
        """
        self._set_status(task_id, _DONE, result=result)

    def mark_abandoned(self, task_id: int, error: str, reason: str = "") -> None:
        """Marca uma tarefa como abandonada permanentemente.

        Tarefas abandonadas não são reenfileiradas automaticamente e
        ficam registradas para análise posterior.

        Args:
            task_id: ID da tarefa.
            error:   Traceback ou descrição do último erro.
            reason:  Causa do abandono: "invalid_config" | "timeout" | "max_retries".
        """
        task = self._tasks[self._id_to_idx[task_id]]
        task["status"]           = _ABANDONED
        task["error"]            = error
        task["abandoned_reason"] = reason or None
        self._save()

    def requeue_with_retry(self, task_id: int, error: str) -> None:
        """Recoloca uma tarefa na fila incrementando seu contador de tentativas.

        Vai direto para "pending" em um único write, sem passar pelo estado
        transitório "failed". Use este método no fluxo normal de retry.

        Args:
            task_id: ID da tarefa que falhou.
            error:   Descrição do erro desta tentativa (salva para diagnóstico).
        """
        task = self._tasks[self._id_to_idx[task_id]]
        task["retry_count"] = task.get("retry_count", 0) + 1
        task["status"]      = _PENDING
        task["error"]       = error
        self._queue.append(task_id)
        self._save()

    def retry_failed(self) -> int:
        """Recoloca todas as tarefas com status 'failed' de volta na fila.

        Tarefas com status 'abandoned' não são incluídas.

        Returns:
            Número de tarefas reenfileiradas.
        """
        count = 0
        for task in self._tasks:
            if task["status"] == _FAILED:
                task["status"] = _PENDING
                task["error"]  = None
                self._queue.append(task["id"])
                count += 1
        if count:
            self._save()
        return count

    # ------------------------------------------------------------------
    # Consultas de estado
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        """Retorna contagem de tarefas por status."""
        counts: dict[str, int] = {
            _PENDING: 0, _RUNNING: 0, _DONE: 0, _FAILED: 0, _ABANDONED: 0,
        }
        for task in self._tasks:
            counts[task["status"]] += 1
        return counts

    def pending(self) -> list[dict]:
        """Retorna todas as tarefas com status 'pending'."""
        return [t for t in self._tasks if t["status"] == _PENDING]

    def done(self) -> list[dict]:
        """Retorna todas as tarefas concluídas."""
        return [t for t in self._tasks if t["status"] == _DONE]

    def __len__(self) -> int:
        """Número total de tarefas na fila (qualquer status)."""
        return len(self._tasks)

    def __iter__(self) -> Iterator[dict]:
        """Itera sobre todas as tarefas (qualquer status)."""
        return iter(self._tasks)

    def __repr__(self) -> str:
        s = self.stats()
        return (
            f"ExecutionQueue(total={len(self)}, "
            f"pending={s[_PENDING]}, running={s[_RUNNING]}, "
            f"done={s[_DONE]}, failed={s[_FAILED]}, abandoned={s[_ABANDONED]})"
        )

    # ------------------------------------------------------------------
    # Persistência
    # ------------------------------------------------------------------

    def _set_status(
        self,
        task_id: int,
        status: str,
        result: dict | None = None,
        error: str | None = None,
    ) -> None:
        task           = self._tasks[self._id_to_idx[task_id]]
        task["status"] = status
        if result is not None:
            task["result"] = result
        if error is not None:
            task["error"] = error
        self._save()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._tasks, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(self._path)

    def _load(self) -> None:
        with open(self._path, encoding="utf-8") as f:
            self._tasks = json.load(f)

        # Compatibilidade retroativa: garante campos novos em tasks antigas
        for task in self._tasks:
            task.setdefault("retry_count",      0)
            task.setdefault("abandoned_reason", None)

        # Constrói o mapa task_id → índice (suporta start_id > 0)
        self._id_to_idx = {t["id"]: i for i, t in enumerate(self._tasks)}

        # Reconstrói o deque com os IDs pendentes (na ordem original)
        self._queue = deque(
            t["id"] for t in self._tasks if t["status"] == _PENDING
        )

        # Tarefas interrompidas em "running" voltam para pending (auto-recuperação)
        for task in self._tasks:
            if task["status"] == _RUNNING:
                task["status"] = _PENDING
                self._queue.appendleft(task["id"])
