"""
Verificações pré-execução (preflight) dos benchmarks.

Executa 8 testes antes de iniciar o loop principal de benchmarks para
garantir que o ambiente está completamente configurado. Termina o processo
com sys.exit(1) se qualquer verificação crítica falhar.

Testes
------
    1. Docker daemon acessível
    2. Espaço em disco suficiente (>10 GB livres; prune automático se <20 GB)
    3. Containers stale removidos
    4. Integridade da fila (campos obrigatórios presentes)
    5. Tabelas TPC-H presentes em todos os tiers
    6. Tabelas TPC-DS presentes em todos os tiers
    7. Conectividade end-to-end TPC-H (SELECT 1 por tier)
    8. Conectividade end-to-end TPC-DS (SELECT 1 por tier)

Funções
-------
    run_preflight_checks(tier_configs, queue_tasks, results_dir)
        Executa todos os 8 testes e encerra o processo em caso de falha.
"""

import sys
from pathlib import Path

import docker as _docker

from benchmarks.container import (
    remove_postgres_container,
    start_postgres_container,
)
from benchmarks.image_builder import TIER_IMAGE_TAGS
from utils.docker_cleanup import auto_prune_if_needed, disk_free_gb, docker_used_gb
from utils.logging import log, sep

_MIN_FREE_GB  = 10   # falha se livre < 10 GB mesmo após prune
_WARN_FREE_GB = 20   # tenta prune se livre < 20 GB

_TPCH_TABLES = [
    "lineitem", "orders", "customer", "supplier",
    "part", "partsupp", "nation", "region",
]

_TPCDS_TABLES = [
    "store_sales", "store_returns", "catalog_sales", "web_sales",
    "item", "date_dim", "store", "customer",
    "warehouse", "household_demographics",
]

# Query genérica: só verifica que o PostgreSQL aceita conexões e responde.
# O schema já foi validado nos checks 5 e 6.
_CONN_TEST_SQL = "SELECT 1;"


def run_preflight_checks(
    tier_configs: dict,
    queue_tasks: list,
    results_dir: Path,
) -> None:
    """Executa as 8 verificações pré-execução.

    Em caso de qualquer falha crítica, exibe os erros e termina o
    processo com ``sys.exit(1)``. Avisos (WARN) não encerram.

    Args:
        tier_configs: Dict ``{tier: {cpu, memory_mb, ...}}`` de docker.json.
        queue_tasks:  Lista de todos os dicts de tarefa da fila.
        results_dir:  Diretório raiz de resultados para verificação de espaço.
    """
    failures: list[str] = []

    # ── 1. Docker daemon ──────────────────────────────────────────────────
    log("1/8  Docker daemon...", indent=1)
    try:
        client = _docker.from_env(timeout=10)
        try:
            client.ping()
        finally:
            client.close()
        log("     OK", level="OK", indent=1)
    except Exception as exc:  # noqa: BLE001
        log(f"     FALHA: {exc}", level="ERROR", indent=1)
        failures.append("Docker daemon inacessível")

    # ── 2. Espaço em disco ────────────────────────────────────────────────
    log("2/8  Espaço em disco...", indent=1)
    try:
        free_gb = disk_free_gb(results_dir.parent)
        docker_usage = docker_used_gb()
        log(f"     Livre: {free_gb:.1f} GB | Docker: {docker_usage['total']:.1f} GB "
            f"(imagens={docker_usage['images']:.1f} GB, "
            f"cache={docker_usage['build_cache']:.1f} GB)", indent=1)

        if free_gb < _WARN_FREE_GB:
            log(f"     Espaço abaixo de {_WARN_FREE_GB} GB: executando limpeza Docker...",
                level="WARN", indent=1)
            auto_prune_if_needed(
                free_threshold_gb=_WARN_FREE_GB,
                critical_threshold_gb=_MIN_FREE_GB,
                path=results_dir.parent,
                verbose=True,
            )
            free_gb = disk_free_gb(results_dir.parent)
            log(f"     Após limpeza: {free_gb:.1f} GB livres", indent=1)

        if free_gb < _MIN_FREE_GB:
            log(f"     FALHA: {free_gb:.1f} GB livres (mínimo: {_MIN_FREE_GB} GB)",
                level="ERROR", indent=1)
            failures.append(f"Espaço insuficiente: {free_gb:.1f} GB livres")
        else:
            log(f"     OK: {free_gb:.1f} GB livres", level="OK", indent=1)
    except Exception as exc:  # noqa: BLE001
        log(f"     AVISO: não foi possível verificar disco: {exc}", level="WARN", indent=1)

    # ── 3. Containers stale ───────────────────────────────────────────────
    log("3/8  Containers stale...", indent=1)
    try:
        client = _docker.from_env()
        try:
            stale = [
                c for c in client.containers.list(all=True)
                if any(c.name.startswith(p) for p in (
                    "tpch_bench_", "tpch_conntest_",
                    "tpcds_bench_", "tpcds_conntest_",
                ))
            ]
            for c in stale:
                log(f"     Removendo: {c.name}", level="WARN", indent=1)
                c.remove(force=True)
        finally:
            client.close()
        log(f"     OK: {len(stale)} container(s) stale removido(s)", level="OK", indent=1)
    except Exception as exc:  # noqa: BLE001
        log(f"     AVISO: {exc}", level="WARN", indent=1)

    # ── 4. Integridade da fila ────────────────────────────────────────────
    log("4/8  Integridade da fila...", indent=1)
    required = {"id", "combination", "tier", "config", "status"}
    corrupt  = [t.get("id", "?") for t in queue_tasks if not required.issubset(t.keys())]
    if corrupt:
        log(f"     FALHA: {len(corrupt)} tarefa(s) corrompida(s): {corrupt[:5]}",
            level="ERROR", indent=1)
        failures.append(f"Tarefas corrompidas: {corrupt[:5]}")
    else:
        log(f"     OK: {len(queue_tasks)} tarefas com campos válidos", level="OK", indent=1)

    # ── 5. Tabelas TPC-H por tier ─────────────────────────────────────────
    log("5/8  Tabelas TPC-H por tier...", indent=1)
    for tier, tag in TIER_IMAGE_TAGS["tpch"].items():
        _check_tables(tier, tag, "tpch", _TPCH_TABLES, tier_configs, failures)

    # ── 6. Tabelas TPC-DS por tier ────────────────────────────────────────
    log("6/8  Tabelas TPC-DS por tier...", indent=1)
    for tier, tag in TIER_IMAGE_TAGS["tpcds"].items():
        _check_tables(tier, tag, "tpcds", _TPCDS_TABLES, tier_configs, failures)

    # ── 7. Conectividade end-to-end TPC-H ────────────────────────────────
    log("7/8  Conectividade end-to-end TPC-H (SELECT 1)...", indent=1)
    for tier, tag in TIER_IMAGE_TAGS["tpch"].items():
        sample = next(
            (t for t in queue_tasks if t["tier"] == tier and t["status"] == "pending"), None
        )
        if sample is None:
            log(f"     [{tier}] Sem tarefas pendentes: pulando", level="WARN", indent=1)
            continue
        _check_end_to_end(tier, tag, "tpch", _CONN_TEST_SQL, sample["config"],
                          tier_configs, failures, label="SELECT 1")

    # ── 8. Conectividade end-to-end TPC-DS ───────────────────────────────
    log("8/8  Conectividade end-to-end TPC-DS (SELECT 1)...", indent=1)
    for tier, tag in TIER_IMAGE_TAGS["tpcds"].items():
        sample = next(
            (t for t in queue_tasks if t["tier"] == tier and t["status"] == "pending"), None
        )
        if sample is None:
            log(f"     [{tier}] Sem tarefas pendentes: pulando", level="WARN", indent=1)
            continue
        _check_end_to_end(tier, tag, "tpcds", _CONN_TEST_SQL, sample["config"],
                          tier_configs, failures, label="SELECT 1")

    # ── Resultado final ───────────────────────────────────────────────────
    sep()
    if failures:
        log(f"Preflight FALHOU: {len(failures)} problema(s):", level="ERROR")
        for msg in failures:
            log(f"  • {msg}", level="ERROR", indent=1)
        sys.exit(1)

    log("Preflight OK: todos os testes passaram.", level="OK")


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _check_tables(
    tier: str,
    image_tag: str,
    db_name: str,
    expected_tables: list[str],
    tier_configs: dict,
    failures: list[str],
) -> None:
    """Verifica se as tabelas esperadas existem no banco do tier."""
    container = None
    try:
        container = start_postgres_container(
            tier_config    = tier_configs[tier],
            pg_config      = {},
            db_name        = db_name,
            image          = image_tag,
            container_name = f"{db_name}_conntest_{tier}",
        )
        sql = (
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = 'public' "
            f"AND table_name = ANY(ARRAY{expected_tables!r}::text[]);"
        )
        res   = container.exec_run(
            ["psql", "-U", "postgres", "-d", db_name, "-t", "-A", "-c", sql],
            demux=False,
        )
        out   = res.output.decode(errors="replace").strip() if res.output else ""
        found = int(out) if out.isdigit() else 0
        n     = len(expected_tables)
        if res.exit_code != 0 or found != n:
            log(f"     [{tier}] FALHA: {found}/{n} tabelas", level="ERROR", indent=1)
            failures.append(f"Tabelas {db_name.upper()} ausentes no tier {tier}")
        else:
            log(f"     [{tier}] OK: {found}/{n} tabelas", level="OK", indent=1)
    except Exception as exc:  # noqa: BLE001
        log(f"     [{tier}] ERRO: {exc}", level="ERROR", indent=1)
        failures.append(f"{db_name.upper()}: conexão falhou no tier {tier}")
    finally:
        if container:
            try:
                remove_postgres_container(container)
            except Exception:  # noqa: BLE001
                pass


def _check_end_to_end(
    tier: str,
    image_tag: str,
    db_name: str,
    test_sql: str,
    pg_config: dict,
    tier_configs: dict,
    failures: list[str],
    label: str = "query de teste",
) -> None:
    """Executa uma query de teste para verificar conectividade end-to-end."""
    container = None
    try:
        container = start_postgres_container(
            tier_config    = tier_configs[tier],
            pg_config      = pg_config,
            db_name        = db_name,
            image          = image_tag,
            container_name = f"{db_name}_conntest_{tier}",
        )
        res = container.exec_run(
            ["psql", "-U", "postgres", "-d", db_name, "-t", "-A", "-c", test_sql],
            demux=False,
        )
        out = res.output.decode(errors="replace").strip() if res.output else ""
        if res.exit_code != 0:
            log(f"     [{tier}] FALHA: {out[:120]}", level="ERROR", indent=1)
            failures.append(f"{db_name.upper()} {label} falhou no tier {tier}")
        else:
            log(f"     [{tier}] OK: {label} executou", level="OK", indent=1)
    except Exception as exc:  # noqa: BLE001
        log(f"     [{tier}] ERRO: {exc}", level="ERROR", indent=1)
        failures.append(f"{db_name.upper()}: end-to-end falhou no tier {tier}")
    finally:
        if container:
            try:
                remove_postgres_container(container)
            except Exception:  # noqa: BLE001
                pass
