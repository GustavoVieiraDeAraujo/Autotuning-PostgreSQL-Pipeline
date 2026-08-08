"""
tpc_ds
======
Benchmark TPC-DS: 98 queries ativas OLAP sobre dataset de 24 tabelas (Q95 pulada).

Gerenciamento de containers e imagens Docker está em ``benchmarks.container``
e ``benchmarks.image_builder``.

    from benchmarks.tpc_ds import run_benchmark

    results = run_benchmark(container)
"""

from .benchmark import run_benchmark

__all__ = ["run_benchmark"]
