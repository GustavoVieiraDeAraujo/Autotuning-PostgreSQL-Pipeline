"""
tpc_h
=====
Benchmark TPC-H: 20 queries ativas OLAP sobre dataset de 8 tabelas (Q17 e Q20 puladas).

Gerenciamento de containers e imagens Docker está em ``benchmarks.container``
e ``benchmarks.image_builder``.

    from benchmarks.tpc_h import run_benchmark

    results = run_benchmark(container)
"""

from .benchmark import run_benchmark

__all__ = ["run_benchmark"]
