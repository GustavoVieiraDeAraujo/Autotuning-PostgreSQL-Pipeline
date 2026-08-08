"""
Execução sequencial de uma tarefa (TPC-H seguido de TPC-DS).

Sobe um container por benchmark, executa o benchmark completo e
remove o container ao final — independentemente de sucesso ou falha.

Funções
-------
    run_task(task, tier_configs, tpch_callback, tpcds_callback)
        Executa TPC-H e TPC-DS para uma tarefa da fila.
"""

import time

import docker
import docker.errors

from benchmarks.container import (
    InvalidConfigError,
    remove_postgres_container,
    start_postgres_container,
)
from benchmarks.image_builder import TIER_IMAGE_TAGS
from benchmarks.query_executor import TaskTimeoutError
from benchmarks.tpc_h import run_benchmark as run_tpch_benchmark
from benchmarks.tpc_ds import run_benchmark as run_tpcds_benchmark
from monitoring import MetricsCollector
from utils.formatting import fmt_duration
from utils.logging import CYAN, RESET, VIOLET, log

# Limite total de tempo por tarefa (TPC-H + TPC-DS combinados).
# Evita que configurações patológicas bloqueiem o runner por horas.
_TASK_TIMEOUT_S: dict[str, int] = {
    "low":    3 * 3600,   # 3h — SF=1, 2 vCPU, 2 GB RAM (configs com planners desligados podem ser lentas)
    "medium": 4 * 3600,   # 4h — SF=2, 4 vCPU, 4 GB RAM
    "high":   8 * 3600,   # 8h — SF=4, 6 vCPU, 5 GB RAM
}

# Re-exporta para uso em cli/run.py sem importar os módulos internos
__all__ = ["run_task", "InvalidConfigError", "TaskTimeoutError"]


def _force_remove_container(name: str) -> None:
    """Remove um container pelo nome, se existir.

    Chamado antes de criar um container para limpar resíduos de tentativas
    anteriores interrompidas (SIGKILL, crash do processo, etc.).
    Silencioso — ignora erros e containers inexistentes.
    """
    try:
        client = docker.from_env()
        try:
            c = client.containers.get(name)
            c.remove(force=True)
        except docker.errors.NotFound:
            pass
        finally:
            client.close()
    except Exception:  # noqa: BLE001
        pass


def run_task(
    task: dict,
    tier_configs: dict,
    tpch_callback=None,
    tpcds_callback=None,
) -> tuple[dict, dict, dict]:
    """Executa TPC-H e TPC-DS sequencialmente para uma tarefa da fila.

    Cada benchmark roda em seu próprio container Docker isolado.
    O container é removido ao final, independentemente de sucesso ou falha.

    Args:
        task:           Dict da tarefa (campos: id, tier, config).
        tier_configs:   Dict ``{tier: {cpu, memory_mb, ...}}`` carregado
                        de ``config/docker.json``.
        tpch_callback:  Callable opcional ``(query_result: dict) -> None``
                        chamado após cada query TPC-H.
        tpcds_callback: Callable opcional ``(query_result: dict) -> None``
                        chamado após cada query TPC-DS.

    Returns:
        Tupla ``(tpch_result, tpcds_result, hw_metrics)`` onde ``hw_metrics``
        é o dict com métricas de hardware coletadas durante toda a tarefa
        (amostras brutas + resumo estatístico).

    Raises:
        RuntimeError: Se o container sair antes do PostgreSQL estar pronto.
        TimeoutError: Se o PostgreSQL não ficar pronto dentro do timeout.
        Exception:    Qualquer outro erro durante o benchmark.
    """
    tier        = task["tier"]
    tier_config = tier_configs[tier]
    pg_config   = task["config"]

    # Deadline absoluto para toda a tarefa (TPC-H + TPC-DS)
    task_deadline = time.monotonic() + _TASK_TIMEOUT_S.get(tier, 4 * 3600)
    timeout_h     = _TASK_TIMEOUT_S.get(tier, 4 * 3600) // 3600
    log(f"Limite de tempo: {timeout_h}h (tier={tier})", level="DIM", indent=2)

    # Inicia coleta de métricas de hardware (amostragem a cada 1s)
    collector = MetricsCollector(interval_s=1.0)
    collector.start()

    try:
        # ── TPC-H ──────────────────────────────────────────────────────────────
        print(f"\n  {CYAN}┌─ TPC-H ─── 20 queries ──────────────────────────────────┐{RESET}",
              flush=True)
        tpch_image = TIER_IMAGE_TAGS["tpch"][tier]
        log(f"Imagem : {tpch_image}", indent=2)
        log(f"Recursos: {tier_config['cpu']} vCPU | {tier_config['memory_mb']} MB RAM | "
            f"shm={tier_config['shm_size_mb']} MB", indent=2)

        t0 = time.perf_counter()

        def _tpch_log_fn(msg: str) -> None:
            log(f"[TPC-H] {msg}", level="DIM", indent=2)

        tpch_name = f"tpch_bench_{task['id']}"
        _force_remove_container(tpch_name)   # limpa resíduo de tentativa anterior
        log("Iniciando container...", indent=2)
        tpch_container = start_postgres_container(
            tier_config    = tier_config,
            pg_config      = pg_config,
            db_name        = "tpch",
            image          = tpch_image,
            container_name = tpch_name,
            max_wait_s     = 120,
            log_fn         = _tpch_log_fn,
        )
        log(f"Container pronto em {fmt_duration(time.perf_counter() - t0)}", level="OK", indent=2)

        try:
            tpch_result = run_tpch_benchmark(
                tpch_container,
                query_callback=tpch_callback,
                hw_snapshot_fn=lambda: collector.latest,
                task_deadline=task_deadline,
            )
        finally:
            remove_postgres_container(tpch_container)
            log("Container removido.", level="DIM", indent=2)

        # ── TPC-DS ─────────────────────────────────────────────────────────────
        print(f"\n  {VIOLET}┌─ TPC-DS ── 98 queries ──────────────────────────────────┐{RESET}",
              flush=True)
        tpcds_image = TIER_IMAGE_TAGS["tpcds"][tier]
        log(f"Imagem : {tpcds_image}", indent=2)
        log(f"Recursos: {tier_config['cpu']} vCPU | {tier_config['memory_mb']} MB RAM | "
            f"shm={tier_config['shm_size_mb']} MB", indent=2)

        t0 = time.perf_counter()

        def _tpcds_log_fn(msg: str) -> None:
            log(f"[TPC-DS] {msg}", level="DIM", indent=2)

        tpcds_name = f"tpcds_bench_{task['id']}"
        _force_remove_container(tpcds_name)   # limpa resíduo de tentativa anterior
        log("Iniciando container...", indent=2)
        tpcds_container = start_postgres_container(
            tier_config    = tier_config,
            pg_config      = pg_config,
            db_name        = "tpcds",
            image          = tpcds_image,
            container_name = tpcds_name,
            max_wait_s     = 120,
            log_fn         = _tpcds_log_fn,
        )
        log(f"Container pronto em {fmt_duration(time.perf_counter() - t0)}", level="OK", indent=2)

        try:
            tpcds_result = run_tpcds_benchmark(
                tpcds_container,
                query_callback=tpcds_callback,
                hw_snapshot_fn=lambda: collector.latest,
                task_deadline=task_deadline,
            )
        except TaskTimeoutError as exc:
            # TPC-H já completou — re-levanta com resultado parcial para que
            # cli/run.py possa salvar o summary do TPC-H antes de abandonar.
            raise TaskTimeoutError(str(exc), partial_tpch_result=tpch_result) from exc
        finally:
            remove_postgres_container(tpcds_container)
            log("Container removido.", level="DIM", indent=2)

    finally:
        # Garante que o collector sempre para, mesmo em exceção
        hw_metrics = collector.stop()

    # Log rápido das métricas de hardware
    s = hw_metrics.get("summary", {})
    cpu_max = s.get("cpu_temp_tctl_c_max")
    if cpu_max is not None:
        energy_str = (f" | Energia {s['rapl_energy_total_j']:.0f}J"
                      if s.get("rapl_energy_total_j") else "")
        log(
            f"Hardware: CPU pico {cpu_max}°C | "
            f"CPU médio {s.get('cpu_percent_avg', '?')}% | "
            f"RAM {s.get('mem_percent_avg', '?')}%"
            + energy_str,
            level="DIM", indent=2,
        )

    print(flush=True)  # linha em branco após cada tarefa
    return tpch_result, tpcds_result, hw_metrics
