"""
benchmarks
==========
Execução dos benchmarks TPC-H e TPC-DS em containers PostgreSQL isolados.

Submódulos
----------
    tpc_h/          — 20 queries TPC-H ativas (SF1/SF2/SF4)
    tpc_ds/         — 98 queries TPC-DS ativas (SF1/SF2/SF4)
    container       — ciclo de vida de containers PostgreSQL
    image_builder   — construção de imagens Docker com dados pré-carregados
    query_executor  — execução de queries e coleta de métricas (compartilhado)

Uso rápido
----------
    from benchmarks.container import start_postgres_container, remove_postgres_container
    from benchmarks.tpc_h import run_benchmark

    container = start_postgres_container(tier_config, pg_config, db_name="tpch",
                                         image="tpch-postgres:sf2")
    results = run_benchmark(container)
    remove_postgres_container(container)
"""
