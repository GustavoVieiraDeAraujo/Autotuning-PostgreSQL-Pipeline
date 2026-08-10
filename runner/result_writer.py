"""
Gerenciamento dos resultados de benchmark no Postgres de controle.

Cada tarefa da fila produz uma linha em ``task_results`` (ver db/schema.sql).
A linha é criada vazia quando a tarefa inicia e preenchida incrementalmente
à medida que queries terminam, permitindo que a interface web exiba
progresso em tempo real mesmo que o processo seja interrompido.

Substitui o antigo esquema de um arquivo JSON por tarefa
(``data/raw/{tier}/{combination}/task_{id}.json``): a escrita atômica
incremental agora é responsabilidade nativa do Postgres (cada UPDATE é uma
transação), não precisa mais de tmp+fsync+rename nem de chmod pra "travar"
o arquivo final. Status/motivo de abandono ficam só em ``tasks`` (via
``ExecutionQueue``): ``task_results`` guarda exclusivamente o conteúdo do
benchmark, não o estado do ciclo de vida da tarefa.

Dois campos deliberadamente NÃO são persistidos aqui, porque o pipeline de
ML nunca os lê (ver docs/decisoes-de-engenharia.md e a limpeza dos datasets
de coleta) e eram os maiores responsáveis por inchar os antigos arquivos:
    - ``pg_stats`` (dump de configurações/estatísticas do Postgres por tarefa)
    - ``hw_metrics.samples`` (série temporal bruta: só o resumo agregado é salvo)

Funções
-------
    init_task_result(task, started_at)
        Cria a linha da tarefa com seções vazias para TPC-H e TPC-DS.

    append_query_result(task_id, query_result, benchmark_key)
        Adiciona o resultado de uma query ao benchmark correto.

    finalize_benchmark_section(task_id, benchmark_key, result)
        Preenche summary e total_ms da seção do benchmark.

    save_hw_metrics(task_id, hw_metrics)
        Salva o resumo agregado de métricas de hardware.

    finalize_task_result(task_id, finished_at, duration_s)
        Registra o tempo total da tarefa (sucesso ou abandono).
"""

from psycopg.types.json import Jsonb

from utils.db import connect

_EMPTY_BENCH = {"queries": [], "summary": None, "total_ms": None, "n_success": 0, "n_failed": 0}


def init_task_result(task: dict, started_at: str, dsn: str | None = None) -> int:
    """Cria a linha de resultado da tarefa com seções vazias.

    Args:
        task:       Dict da tarefa (usa apenas ``id``).
        started_at: Timestamp ISO 8601 de início.
        dsn:        Connection string do banco de controle.

    Returns:
        O próprio ``task["id"]``: identifica a tarefa nas demais chamadas
        (substitui o antigo ``out_path``).
    """
    with connect(dsn) as conn:
        conn.execute(
            """
            INSERT INTO task_results (task_id, started_at, tpc_h, tpc_ds)
            VALUES (%(id)s, %(started_at)s, %(bench)s, %(bench)s)
            ON CONFLICT (task_id) DO UPDATE
                SET started_at = EXCLUDED.started_at,
                    tpc_h = EXCLUDED.tpc_h, tpc_ds = EXCLUDED.tpc_ds
            """,
            {"id": task["id"], "started_at": started_at, "bench": Jsonb(dict(_EMPTY_BENCH))},
        )
    return task["id"]


def _load_bench(conn, task_id: int, benchmark_key: str) -> dict:
    row = conn.execute(
        f"SELECT {benchmark_key} AS bench FROM task_results WHERE task_id = %s", (task_id,)
    ).fetchone()
    return row["bench"]


def append_query_result(
    task_id: int,
    query_result: dict,
    benchmark_key: str,
    dsn: str | None = None,
) -> None:
    """Adiciona o resultado de uma query ao benchmark correto.

    Atualiza ``n_success`` / ``n_failed`` a cada chamada. Chamado via
    query_callback durante ``run_all_queries()``.

    Args:
        task_id:       ID da tarefa (retornado por ``init_task_result``).
        query_result:  Dict retornado por ``run_query()``.
        benchmark_key: ``"tpc_h"`` ou ``"tpc_ds"``.
        dsn:           Connection string do banco de controle.
    """
    with connect(dsn) as conn:
        bench = _load_bench(conn, task_id, benchmark_key)
        bench["queries"].append(query_result)
        if query_result.get("success"):
            bench["n_success"] += 1
        else:
            bench["n_failed"] += 1
        conn.execute(
            f"UPDATE task_results SET {benchmark_key} = %s WHERE task_id = %s",
            (Jsonb(bench), task_id),
        )


def finalize_benchmark_section(
    task_id: int,
    benchmark_key: str,
    result: dict,
    dsn: str | None = None,
) -> None:
    """Preenche summary e total_ms da seção do benchmark.

    ``pg_stats`` (se presente em ``result``) é ignorado de propósito: nunca
    é lido pelo pipeline de ML e só infla o tamanho do registro.

    Args:
        task_id:       ID da tarefa.
        benchmark_key: ``"tpc_h"`` ou ``"tpc_ds"``.
        result:        Dict retornado por ``run_benchmark()``.
        dsn:           Connection string do banco de controle.
    """
    with connect(dsn) as conn:
        bench = _load_bench(conn, task_id, benchmark_key)
        bench["summary"]  = result.get("summary", {})
        bench["total_ms"] = round(result["total_ms"], 3)
        conn.execute(
            f"UPDATE task_results SET {benchmark_key} = %s WHERE task_id = %s",
            (Jsonb(bench), task_id),
        )


def save_hw_metrics(task_id: int, hw_metrics: dict, dsn: str | None = None) -> None:
    """Salva o resumo agregado de métricas de hardware.

    Só ``hw_metrics["summary"]`` é persistido: as amostras brutas
    (``hw_metrics["samples"]``) nunca são lidas por nada no projeto.

    Args:
        task_id:    ID da tarefa.
        hw_metrics: Dict retornado por ``MetricsCollector.stop()``.
        dsn:        Connection string do banco de controle.
    """
    with connect(dsn) as conn:
        conn.execute(
            "UPDATE task_results SET hw_metrics = %s WHERE task_id = %s",
            (Jsonb({"summary": hw_metrics.get("summary")}), task_id),
        )


def finalize_task_result(
    task_id: int,
    finished_at: str,
    duration_s: float,
    dsn: str | None = None,
) -> None:
    """Registra o momento e a duração final da tarefa (sucesso ou abandono).

    O status/motivo do ciclo de vida (done/abandoned/reason/error) fica só
    em ``tasks``: ver ``ExecutionQueue.mark_done`` / ``mark_abandoned``.

    Args:
        task_id:     ID da tarefa.
        finished_at: Timestamp ISO 8601 de conclusão/abandono.
        duration_s:  Duração total da tarefa em segundos.
        dsn:         Connection string do banco de controle.
    """
    with connect(dsn) as conn:
        conn.execute(
            "UPDATE task_results SET finished_at = %s, duration_s = %s WHERE task_id = %s",
            (finished_at, round(duration_s, 3), task_id),
        )
