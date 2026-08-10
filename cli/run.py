"""
Runner principal dos benchmarks TPC-H e TPC-DS.

Para cada tarefa da fila executa:
  1. Container TPC-H  → benchmark completo (22 queries)
  2. Container TPC-DS → benchmark completo (99 queries)
  3. Marca a tarefa como done com métricas de ambos os benchmarks

Uso
---
    python cli/run.py                   # executa toda a fila
    python cli/run.py --retry-failed    # reexecuta tarefas com falha
    python cli/run.py --dry-run         # mostra o plano sem executar
    python cli/run.py --status          # mostra estado da fila

Interrupção
-----------
    Ctrl+C  →  finaliza a tarefa atual e para.
               A fila persiste: retomar rodando o script novamente.

Resultados
----------
    Fila e resultados vivem no Postgres de controle (ver db/schema.sql),
    não mais em arquivos locais: permite múltiplos workers `cli/run.py`,
    inclusive em máquinas diferentes, disputando a mesma fila com segurança.
    Configuração via variável de ambiente ``DATABASE_URL`` (ver utils/db.py).
"""

import argparse
import json
import signal
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

_ROOT       = Path(__file__).parent.parent
_DATA_DIR   = _ROOT / "data"      # usado só p/ checagem de espaço em disco local
_LOG_PATH   = _ROOT / "logs" / "runner.log"
_LOCK_PATH  = _ROOT / "data" / ".runner.lock"
_DOCKER_CFG = _ROOT / "specs" / "docker.json"

from benchmarks.image_builder import TIER_IMAGE_TAGS, image_exists
from taskqueue import ExecutionQueue
from runner.preflight import run_preflight_checks                    # noqa: E402
from runner.result_writer import (                                   # noqa: E402
    append_query_result,
    finalize_benchmark_section,
    finalize_task_result,
    init_task_result,
    save_hw_metrics,
)
from runner.task_executor import run_task, InvalidConfigError, TaskTimeoutError  # noqa: E402
from utils.db import connect as db_connect                          # noqa: E402
from utils.docker_cleanup import auto_prune_if_needed, PRUNE_EVERY_N_TASKS  # noqa: E402
from utils.formatting import fmt_duration, fmt_eta                  # noqa: E402
from utils.logging import TeeWriter, banner, log, sep               # noqa: E402

_MAX_RETRIES = 3


# ---------------------------------------------------------------------------
# SIGINT
# ---------------------------------------------------------------------------

def _handle_sigint(signum, frame):  # noqa: ANN001
    print(
        "\n\033[33m[runner] Interrompido: encerrando agora. "
        "A task atual voltará para pending na próxima execução.\033[0m",
        flush=True,
    )
    sys.exit(1)


signal.signal(signal.SIGINT, _handle_sigint)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_tier_configs() -> dict:
    with open(_DOCKER_CFG, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Loop principal
# ---------------------------------------------------------------------------

def _run(retry_failed: bool = False, dry_run: bool = False) -> None:
    banner("BENCHMARK RUNNER: TPC-H + TPC-DS PostgreSQL Autotuning")

    # ── Fila ──────────────────────────────────────────────────────────────
    queue = ExecutionQueue()

    if queue.is_empty():
        log("Fila vazia: nenhuma tarefa foi gerada ainda.", level="ERROR")
        log("Gere as configurações via interface web ou 'make generate'.", level="WARN")
        sys.exit(1)

    if retry_failed:
        n = queue.retry_failed()
        log(f"{n} tarefas com falha reenfileiradas.", level="WARN")

    stats     = queue.stats()
    total     = len(queue)
    n_pending = stats["pending"]
    n_done    = stats["done"]
    n_failed  = stats["failed"]

    log(f"Total: {total} tarefas | {n_done} concluídas | "
        f"{n_pending} pendentes | {n_failed} com falha")

    if n_pending == 0:
        log("Nenhuma tarefa pendente. Use --retry-failed para reexecutar falhas.",
            level="WARN")
        return

    # ── Imagens Docker ─────────────────────────────────────────────────────
    sep()
    log("Verificando imagens Docker...")
    missing = [
        tag
        for bench_tags in TIER_IMAGE_TAGS.values()
        for tag in bench_tags.values()
        if not image_exists(tag)
    ]
    if missing:
        log("Imagens ausentes: execute 'python cli/prepare.py' antes do runner:",
            level="ERROR")
        for tag in missing:
            log(f"  • {tag}", level="ERROR", indent=1)
        sys.exit(1)
    log("Todas as 6 imagens disponíveis.", level="OK")

    # ── Configs de tier ────────────────────────────────────────────────────
    tier_configs = _load_tier_configs()

    if dry_run:
        sep()
        log("DRY RUN: plano de execução:", level="HEAD")
        for i, task in enumerate(queue.pending()[:10], 1):
            log(f"  {i:3d}. tier={task['tier']:<7} combo={task['combination']:<12} "
                f"id={task['id']}", level="DIM")
        if n_pending > 10:
            log(f"  ... e mais {n_pending - 10} tarefas.", level="DIM")
        log("Cada tarefa executa: TPC-H (20 queries) → TPC-DS (98 queries)", level="DIM")
        return

    # ── Preflight ───────────────────────────────────────────────────────────
    sep()
    log("Verificações pré-execução (preflight)...", level="HEAD")
    run_preflight_checks(tier_configs, list(queue), _DATA_DIR)

    # ── Loop de execução ───────────────────────────────────────────────────
    sep()
    log(f"Iniciando execução de {n_pending} tarefas (TPC-H + TPC-DS por tarefa).")

    t_run_start     = time.perf_counter()
    tasks_done      = 0
    tasks_abandoned = 0

    while True:
        task = queue.next()
        if task is None:
            break

        tid        = task["id"]
        attempt    = task.get("retry_count", 0) + 1
        task_num   = n_done + tasks_done + tasks_abandoned + 1
        attempt_label = f"  [tentativa {attempt}/{_MAX_RETRIES}]" if attempt > 1 else ""
        sep("─")
        log(
            f"TAREFA {task_num}/{total}"
            f"  tier={task['tier']:<6}"
            f"  combo={task['combination']:<12}"
            f"  id={tid}"
            f"{attempt_label}",
            level="HEAD",
        )
        sep("─")

        started_at = datetime.now(timezone.utc).isoformat()
        t_task     = time.perf_counter()
        result_id  = init_task_result(task, started_at)

        def _on_tpch(qr: dict, _id: int = result_id) -> None:
            append_query_result(_id, qr, "tpc_h")

        def _on_tpcds(qr: dict, _id: int = result_id) -> None:
            append_query_result(_id, qr, "tpc_ds")

        try:
            tpch_result, tpcds_result, hw_metrics = run_task(
                task, tier_configs,
                tpch_callback  = _on_tpch,
                tpcds_callback = _on_tpcds,
            )

            duration_s  = time.perf_counter() - t_task
            finished_at = datetime.now(timezone.utc).isoformat()

            finalize_benchmark_section(result_id, "tpc_h",  tpch_result)
            finalize_benchmark_section(result_id, "tpc_ds", tpcds_result)
            save_hw_metrics(result_id, hw_metrics)
            finalize_task_result(result_id, finished_at, duration_s)

            # ── Métricas TPC-H ────────────────────────────────────────────
            tpch_ok    = tpch_result["n_success"]
            tpch_fail  = tpch_result["n_failed"]
            tpch_ms    = tpch_result["total_ms"]
            tpch_sum   = tpch_result.get("summary", {})
            tpch_geo   = tpch_sum.get("geo_mean_exec_ms")
            tpch_cache = tpch_sum.get("overall_cache_hit_ratio")
            tpch_spill = tpch_sum.get("queries_with_spill", 0)
            geo_str    = f"{tpch_geo:.0f}ms" if tpch_geo   is not None else "N/A"
            cache_str  = f"{tpch_cache}%"    if tpch_cache is not None else "N/A"

            log(
                f"TPC-H  : {tpch_ok}/20 OK | {tpch_fail} erro(s) | "
                f"{fmt_duration(tpch_ms / 1000)} | "
                f"geo={geo_str} | cache={cache_str} | spill={tpch_spill}q",
                level="OK" if tpch_fail == 0 else "WARN", indent=1,
            )

            # ── Métricas TPC-DS ───────────────────────────────────────────
            tpcds_ok    = tpcds_result["n_success"]
            tpcds_fail  = tpcds_result["n_failed"]
            tpcds_ms    = tpcds_result["total_ms"]
            tpcds_sum   = tpcds_result.get("summary", {})
            tpcds_geo   = tpcds_sum.get("geo_mean_exec_ms")
            tpcds_cache = tpcds_sum.get("overall_cache_hit_ratio")
            tpcds_spill = tpcds_sum.get("queries_with_spill", 0)
            geo_str2    = f"{tpcds_geo:.0f}ms" if tpcds_geo   is not None else "N/A"
            cache_str2  = f"{tpcds_cache}%"    if tpcds_cache is not None else "N/A"

            log(
                f"TPC-DS : {tpcds_ok}/98 OK | {tpcds_fail} erro(s) | "
                f"{fmt_duration(tpcds_ms / 1000)} | "
                f"geo={geo_str2} | cache={cache_str2} | spill={tpcds_spill}q",
                level="OK" if tpcds_fail == 0 else "WARN", indent=1,
            )

            log(f"Tarefa concluída em {fmt_duration(duration_s)}", level="OK", indent=1)
            log(f"Resultado salvo no banco (task_id={result_id})", indent=1)

            queue.mark_done(tid, {
                "tpc_h_n_success":  tpch_ok,
                "tpc_h_n_failed":   tpch_fail,
                "tpc_h_total_ms":   tpch_ms,
                "tpc_ds_n_success": tpcds_ok,
                "tpc_ds_n_failed":  tpcds_fail,
                "tpc_ds_total_ms":  tpcds_ms,
                "duration_s":       round(duration_s, 3),
            })
            tasks_done += 1

        except InvalidConfigError as exc:
            # Falha determinística: PostgreSQL rejeitou o parâmetro.
            # Repetir não vai ajudar: abandona imediatamente.
            duration_s  = time.perf_counter() - t_task
            finished_at = datetime.now(timezone.utc).isoformat()
            err_msg     = str(exc)
            log(f"CONFIG INVÁLIDA após {fmt_duration(duration_s)}: sem retry:",
                level="ERROR", indent=1)
            for line in err_msg.strip().splitlines():
                log(line, level="ERROR", indent=2)
            finalize_task_result(result_id, finished_at, duration_s)
            queue.mark_abandoned(tid, err_msg, reason="invalid_config")
            tasks_abandoned += 1

        except TaskTimeoutError as exc:
            # Limite de tempo da tarefa excedido: abandona sem retry.
            duration_s  = time.perf_counter() - t_task
            finished_at = datetime.now(timezone.utc).isoformat()
            err_msg     = str(exc)
            log(f"TIMEOUT DE TAREFA após {fmt_duration(duration_s)}: sem retry:",
                level="ERROR", indent=1)
            log(err_msg, level="ERROR", indent=2)
            # Se o timeout ocorreu durante TPC-DS, TPC-H já concluiu.
            # Salva o summary do TPC-H para não perder os dados coletados.
            if exc.partial_tpch_result is not None:
                try:
                    finalize_benchmark_section(result_id, "tpc_h", exc.partial_tpch_result)
                    log("TPC-H summary salvo antes do abandono.", level="DIM", indent=2)
                except Exception:  # noqa: BLE001
                    pass
            finalize_task_result(result_id, finished_at, duration_s)
            queue.mark_abandoned(tid, err_msg, reason="timeout")
            tasks_abandoned += 1

        except Exception:  # noqa: BLE001
            # Falha possivelmente transitória (infra, Docker, disco).
            # Recoloca na fila até _MAX_RETRIES tentativas.
            duration_s = time.perf_counter() - t_task
            err_msg    = traceback.format_exc()
            log(f"ERRO após {fmt_duration(duration_s)}:", level="ERROR", indent=1)
            for line in err_msg.strip().splitlines():
                log(line, level="ERROR", indent=2)

            if attempt < _MAX_RETRIES:
                log(
                    f"Tentativa {attempt}/{_MAX_RETRIES} falhou: reenfileirando...",
                    level="WARN", indent=1,
                )
                queue.requeue_with_retry(tid, err_msg)
            else:
                finished_at = datetime.now(timezone.utc).isoformat()
                log(
                    f"Tentativa {attempt}/{_MAX_RETRIES} falhou: abandonando tarefa.",
                    level="ERROR", indent=1,
                )
                finalize_task_result(result_id, finished_at, duration_s)
                queue.mark_abandoned(tid, err_msg, reason="max_retries")
                tasks_abandoned += 1

        # Limpeza periódica de disco (a cada PRUNE_EVERY_N_TASKS tarefas)
        done_so_far = tasks_done + tasks_abandoned
        if done_so_far > 0 and done_so_far % PRUNE_EVERY_N_TASKS == 0:
            auto_prune_if_needed(path=_DATA_DIR)

        # ETA
        elapsed     = time.perf_counter() - t_run_start
        if done_so_far > 0 and (n_pending - done_so_far) > 0:
            log(
                f"Progresso: {done_so_far}/{n_pending} | "
                f"ETA: ~{fmt_eta(elapsed, done_so_far, n_pending)} | "
                f"Decorrido: {fmt_duration(elapsed)}",
                level="DIM",
            )

    # ── Resumo final ───────────────────────────────────────────────────────
    sep("═")
    elapsed_total = time.perf_counter() - t_run_start
    final_stats   = queue.stats()

    log("Execução concluída.", level="OK")
    log(
        f"Concluídas: {final_stats['done']} | "
        f"Falhas: {final_stats['failed']} | "
        f"Abandonadas: {final_stats['abandoned']} | "
        f"Pendentes: {final_stats['pending']} | "
        f"Tempo total: {fmt_duration(elapsed_total)}"
    )
    sep("═")


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def _status() -> None:
    banner("STATUS: Benchmark Runner (TPC-H + TPC-DS)")

    queue = ExecutionQueue()

    if queue.is_empty():
        log("Fila vazia: nenhuma tarefa foi gerada ainda.", level="WARN")
        log("Gere as configurações via interface web ou 'make generate'.", level="DIM")
        return

    s     = queue.stats()
    total = len(queue)

    sep()
    log("Fila de execução:", level="HEAD")
    log(f"  Total     : {total}", indent=1)
    log(f"  Pendentes : {s['pending']}",    level="WARN"  if s["pending"]   else "DIM", indent=1)
    log(f"  Executando: {s['running']}",    level="WARN"  if s["running"]   else "DIM", indent=1)
    log(f"  Concluídas: {s['done']}",       level="OK"    if s["done"]      else "DIM", indent=1)
    log(f"  Falhas    : {s['failed']}",     level="ERROR" if s["failed"]    else "DIM", indent=1)
    log(f"  Abandonadas:{s['abandoned']}",  level="ERROR" if s["abandoned"] else "DIM", indent=1)

    if total > 0:
        pct    = s["done"] / total * 100
        bar_w  = 40
        filled = int(bar_w * s["done"] / total)
        bar    = "█" * filled + "░" * (bar_w - filled)
        log(f"  Progresso : [{bar}] {pct:.1f}%", indent=1)

    done_tasks = queue.done()
    if not done_tasks:
        sep("═")
        return

    sep()
    log("Resultados por tier:", level="HEAD")

    by_tier: dict[str, list] = {}
    for t in done_tasks:
        by_tier.setdefault(t["tier"], []).append(t)

    with db_connect() as conn:
        result_rows = conn.execute(
            "SELECT task_id, tpc_h->>'total_ms' AS tpch_ms, tpc_ds->>'total_ms' AS tpcds_ms "
            "FROM task_results"
        ).fetchall()
    ms_by_task_id = {r["task_id"]: r for r in result_rows}

    for tier in ["low", "medium", "high"]:
        tasks = by_tier.get(tier, [])
        if not tasks:
            continue
        tpch_ms_list:  list[float] = []
        tpcds_ms_list: list[float] = []
        for t in tasks:
            r = ms_by_task_id.get(t["id"])
            if r is None:
                continue
            if r["tpch_ms"]:
                tpch_ms_list.append(float(r["tpch_ms"]))
            if r["tpcds_ms"]:
                tpcds_ms_list.append(float(r["tpcds_ms"]))

        tpch_avg  = fmt_duration(sum(tpch_ms_list)  / len(tpch_ms_list)  / 1000) if tpch_ms_list  else "–"
        tpcds_avg = fmt_duration(sum(tpcds_ms_list) / len(tpcds_ms_list) / 1000) if tpcds_ms_list else "–"
        log(
            f"  [{tier:<6}] {len(tasks):3d} tarefas | "
            f"TPC-H médio: {tpch_avg} | TPC-DS médio: {tpcds_avg}",
            indent=1,
        )

    sep("═")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(retry_failed: bool = False, dry_run: bool = False) -> None:
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    _LOCK_PATH.touch()
    tee        = TeeWriter(sys.stdout, _LOG_PATH)
    sys.stdout = tee  # type: ignore[assignment]
    try:
        _run(retry_failed=retry_failed, dry_run=dry_run)
    finally:
        sys.stdout = tee._stream
        tee.close()
        _LOCK_PATH.unlink(missing_ok=True)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Executa TPC-H + TPC-DS para todas as configurações da fila.",
    )
    p.add_argument("--retry-failed", action="store_true",
                   help="Reexecuta tarefas que falharam anteriormente.")
    p.add_argument("--dry-run", action="store_true",
                   help="Mostra o plano de execução sem rodar nenhum benchmark.")
    p.add_argument("--status", action="store_true",
                   help="Exibe o estado da fila e resumo dos resultados.")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.status:
        _status()
    else:
        run(retry_failed=args.retry_failed, dry_run=args.dry_run)
