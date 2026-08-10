"""
Benchmark TPC-H: wrapper de execução.

Thin wrapper sobre ``benchmarks.query_executor`` que pré-configura o
banco de dados ("tpch") e as 22 queries do TPC-H.

Uso típico
----------
    from benchmarks.tpc_h.benchmark import run_benchmark

    results = run_benchmark(container)
    # results["queries"]  → lista com métricas das 22 queries
    # results["summary"]  → métricas agregadas (geo-média, cache hit, spill)
    # results["pg_stats"] → estatísticas globais do PostgreSQL
"""

from collections.abc import Callable
from typing import Any

from docker.models.containers import Container

from benchmarks import query_executor as qx
from .queries import QUERIES

DB_NAME = "tpch"

# Queries estruturalmente lentas em qualquer configuração testada:
# Q17 e Q20 têm correlated subqueries que o planner não consegue otimizar
# com os parâmetros do espaço amostral: timeout de 15 min em 100% das runs.
# Pulá-las economiza 30 min por tarefa sem perda de sinal ML.
_SKIP_QUERY_IDS: frozenset[int] = frozenset({17, 20})
_ACTIVE_QUERIES: list[dict] = [q for q in QUERIES if q["id"] not in _SKIP_QUERY_IDS]


def run_benchmark(
    container: Container,
    query_callback: Callable[[dict], None] | None = None,
    hw_snapshot_fn: Callable[[], dict | None] | None = None,
    task_deadline: float | None = None,
) -> dict[str, Any]:
    """Pipeline completo do benchmark TPC-H.

    Sequência:
        1. Zera contadores (``reset_stats``)
        2. Executa as 20 queries ativas (Q17 e Q20 puladas: sempre timeout)
        3. Coleta estatísticas globais (``collect_pg_stats``)

    Args:
        container:       Container PostgreSQL com dados TPC-H pré-carregados.
        query_callback:  Callable opcional ``(query_result: dict) -> None``
                         invocado após cada query (ex: para salvar incrementalmente).
        hw_snapshot_fn:  Callable opcional ``() -> dict | None`` para exibir
                         métricas de hardware no spinner em tempo real.
        task_deadline:   ``time.monotonic()`` deadline da tarefa inteira.

    Returns:
        Dict com:
            queries: lista de 20 dicts (Q17 e Q20 excluídas permanentemente)
            summary: métricas agregadas (geo-média, cache hit ratio, spill)
            pg_stats: estatísticas do PostgreSQL (bgwriter, wal, settings, ...)
            total_ms: tempo total do benchmark (ms)
            n_success: número de queries bem-sucedidas
            n_failed: número de queries com erro
    """
    return qx.run_benchmark(container, _ACTIVE_QUERIES, DB_NAME, query_callback,
                            hw_snapshot_fn, task_deadline=task_deadline)
