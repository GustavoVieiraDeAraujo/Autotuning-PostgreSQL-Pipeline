"""
Execução de queries e coleta de métricas dentro de containers PostgreSQL.

Módulo compartilhado pelos benchmarks TPC-H e TPC-DS. Centraliza toda a
lógica de:
  - Execução de SQL via psql (com EXPLAIN ANALYZE BUFFERS)
  - Parsing do plano JSON do EXPLAIN
  - Coleta de estatísticas do PostgreSQL (bgwriter, database, tables, WAL)
  - Agregação das métricas por query em um sumário (geo-média, cache hit, spill)

Funções públicas
----------------
    reset_stats(container, db_name)
        Zera contadores de estatísticas antes do benchmark.

    run_query(container, query_dict, db_name)
        Executa uma query e retorna métricas detalhadas.

    run_all_queries(container, queries, db_name, query_callback)
        Executa uma lista de queries em sequência com spinner de progresso.

    collect_pg_stats(container, db_name)
        Coleta estatísticas globais do PostgreSQL após o benchmark.

    run_benchmark(container, queries, db_name, query_callback)
        Pipeline completo: reset → run all → collect stats.

    compute_summary(queries)
        Agrega as métricas de uma lista de queries em um sumário.
"""

import itertools
import json
import math
import re
import statistics
import threading
import time
from collections.abc import Callable
from typing import Any

_RESET  = "\033[0m"
_GREEN  = "\033[32m"
_RED    = "\033[31m"
_YELLOW = "\033[33m"
_CYAN   = "\033[36m"
_DIM    = "\033[2m"
_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# Timeout por query: statement_timeout cancela no PostgreSQL; o loop do spinner
# tem um hard-wall Python-level como failsafe caso o statement_timeout falhe.
_QUERY_TIMEOUT_MS  = 900_000         # 15 min — statement_timeout (PostgreSQL)
_QUERY_TIMEOUT_S   = _QUERY_TIMEOUT_MS // 1000
_QUERY_HARDWALL_S  = _QUERY_TIMEOUT_S + 30  # grace period antes do kill forçado

# Valor imputado para OOM e timeout no vetor ML — maior que qualquer exec real,
# sinaliza "este config é tão ruim que destruiu o processo".
# OOM usa 1.5× timeout: é pior (config usou memória até ser morta pelo kernel).
_IMPUTE_TIMEOUT_MS = float(_QUERY_TIMEOUT_MS)
_IMPUTE_OOM_MS     = float(_QUERY_TIMEOUT_MS * 1.5)

# failure_reason values usados em failure_reason_per_query:
#   "ok"        → success=True, usar exec_ms diretamente
#   "timeout"   → statement_timeout disparou; exec_ms = _IMPUTE_TIMEOUT_MS (lower bound)
#   "oom"       → container morto pelo OOM killer (exit 137); exec_ms = _IMPUTE_OOM_MS
#   "technical" → falha não relacionada à config (conexão, parse, Docker); excluir do treino

from docker.models.containers import Container

POSTGRES_USER = "postgres"


class TaskTimeoutError(Exception):
    """Limite de tempo total da tarefa excedido.

    Levantado quando o benchmark ultrapassa o teto por tier (2h/4h/8h).
    A tarefa deve ser marcada como ``abandoned`` sem retry — é uma decisão
    administrativa para evitar que uma configuração patológica bloqueie o
    runner por horas.

    Atributos:
        partial_tpch_result: Resultado completo do TPC-H se ele já terminou
            antes do timeout. None se o timeout ocorreu durante o TPC-H.
    """

    def __init__(self, message: str, partial_tpch_result: dict | None = None) -> None:
        super().__init__(message)
        self.partial_tpch_result = partial_tpch_result


# ---------------------------------------------------------------------------
# Reinicialização de estatísticas
# ---------------------------------------------------------------------------

def reset_stats(container: Container, db_name: str) -> None:
    """Zera as estatísticas do PostgreSQL antes do benchmark.

    Limpa pg_stat_statements (histórico de queries), pg_stat_bgwriter
    (contadores de I/O e checkpoints) e pg_stat_database para que as
    métricas coletadas reflitam apenas o benchmark atual.

    Args:
        container: Container PostgreSQL em execução.
        db_name:   Nome do banco de dados ("tpch" ou "tpcds").
    """
    cmds = [
        "SELECT pg_stat_reset();",
        "SELECT pg_stat_reset_shared('bgwriter');",
    ]
    # pg_stat_statements pode não estar instalada — ignora se não existir
    stmts_reset = (
        "SELECT pg_stat_statements_reset() "
        "WHERE EXISTS ("
        "  SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'"
        ");"
    )
    for sql in [*cmds, stmts_reset]:
        _psql(container, db_name, sql, ignore_error=True)


# ---------------------------------------------------------------------------
# Execução de uma query individual
# ---------------------------------------------------------------------------

def run_query(
    container: Container,
    query_dict: dict,
    db_name: str,
) -> dict[str, Any]:
    """Executa uma query e retorna métricas detalhadas de tempo e buffers.

    Usa ``EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`` para capturar tempo
    de execução e hits/misses de buffer diretamente do plano de execução.

    Para queries com ``setup``/``teardown`` (ex: TPC-H Q15), executa os
    passos auxiliares antes e depois da query principal.

    Args:
        container:  Container PostgreSQL em execução.
        query_dict: Dict com as chaves ``id``, ``name``, ``sql`` e,
                    opcionalmente, ``setup`` e ``teardown``.
        db_name:    Nome do banco de dados ("tpch" ou "tpcds").

    Returns:
        Dict com as seguintes chaves:
            id                   — número da query
            name                 — nome descritivo
            success              — True se executou sem erro
            failure_reason       — "ok" | "timeout" | "oom" | "technical"
            error                — mensagem de erro (se success=False)
            wall_ms              — tempo total medido pelo Python (ms)
            plan_ms              — tempo de planejamento do plano JSON (ms)
            exec_ms              — tempo de execução real (ms); ou valor imputado
                                   (_IMPUTE_TIMEOUT_MS / _IMPUTE_OOM_MS) se falha
                                   relacionada à config; None se falha técnica
            rows                 — linhas retornadas pelo nó raiz
            shared_hit           — buffer hits em shared_buffers
            shared_read          — leituras de disco (cache miss)
            shared_written       — blocos escritos durante a query
            shared_dirtied       — blocos sujos (modificados, não escritos)
            temp_read_blocks     — blocos temporários lidos (spill)
            temp_written_blocks  — blocos temporários escritos (spill)
            cache_hit_ratio      — shared_hit / (shared_hit + shared_read) × 100
            workers_planned      — workers planejados para paralelismo
            workers_launched     — workers efetivamente usados em runtime

        failure_reason e estratégia de imputação para ML:
            "ok"        → exec_ms real; incluir normalmente no treino.
            "timeout"   → exec_ms = _IMPUTE_TIMEOUT_MS (lower bound configurável);
                          incluir no treino — a config É responsável pela lentidão.
            "oom"       → exec_ms = _IMPUTE_OOM_MS (1.5× timeout, pior que timeout);
                          incluir no treino — a config consumiu memória até ser morta.
            "technical" → exec_ms = None; excluir do treino — falha de infraestrutura,
                          não relacionada à config (conexão, parse, bug no runner).
    """
    qid      = query_dict["id"]
    name     = query_dict["name"]
    sql      = query_dict["sql"]
    setup    = query_dict.get("setup")
    teardown = query_dict.get("teardown")

    result: dict[str, Any] = {
        "id": qid, "name": name, "success": False,
        "failure_reason": None,   # "ok" | "timeout" | "oom" | "technical"
        "error": None,
        "wall_ms": None, "plan_ms": None, "exec_ms": None,
        "rows": None,
        "shared_hit": None, "shared_read": None,
        "shared_written": None, "shared_dirtied": None,
        "temp_read_blocks": None, "temp_written_blocks": None,
        "cache_hit_ratio": None,
        "workers_planned": None, "workers_launched": None,
    }

    t0 = time.perf_counter()
    try:
        if setup:
            _psql(container, db_name, setup)

        explain_sql = f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}"
        output  = _psql(container, db_name, explain_sql, timeout_ms=_QUERY_TIMEOUT_MS)
        wall_ms = (time.perf_counter() - t0) * 1000

        plan = _parse_explain_json(output)
        sh   = _safe(plan, ["Plan", "Shared Hit Blocks"])  or 0
        sr   = _safe(plan, ["Plan", "Shared Read Blocks"]) or 0

        result.update({
            "success":             True,
            "failure_reason":      "ok",
            "wall_ms":             round(wall_ms, 3),
            "plan_ms":             _safe(plan, "Planning Time"),
            "exec_ms":             _safe(plan, "Execution Time"),
            "rows":                _safe(plan, ["Plan", "Actual Rows"]),
            "shared_hit":          sh,
            "shared_read":         sr,
            "shared_written":      _safe(plan, ["Plan", "Shared Written Blocks"]),
            "shared_dirtied":      _safe(plan, ["Plan", "Shared Dirtied Blocks"]),
            "temp_read_blocks":    _safe(plan, ["Plan", "Temp Read Blocks"]),
            "temp_written_blocks": _safe(plan, ["Plan", "Temp Written Blocks"]),
            "cache_hit_ratio":     round(sh / (sh + sr) * 100, 2) if (sh + sr) > 0 else None,
            "workers_planned":     _safe(plan, ["Plan", "Workers Planned"]),
            "workers_launched":    _safe(plan, ["Plan", "Workers Launched"]),
        })

    except OOMKillError as exc:
        # Container morto pelo kernel por excesso de memória — culpa da config.
        result["error"]          = str(exc)
        result["wall_ms"]        = round((time.perf_counter() - t0) * 1000, 3)
        result["failure_reason"] = "oom"
        result["exec_ms"]        = _IMPUTE_OOM_MS

    except Exception as exc:  # noqa: BLE001
        err_str = str(exc)
        wall_ms = round((time.perf_counter() - t0) * 1000, 3)
        result["error"]   = err_str
        result["wall_ms"] = wall_ms

        if "statement timeout" in err_str.lower() or "canceling statement" in err_str.lower():
            # PostgreSQL cancelou via statement_timeout — culpa da config.
            result["failure_reason"] = "timeout"
            result["exec_ms"]        = _IMPUTE_TIMEOUT_MS
        else:
            # Falha de infraestrutura: conexão perdida, erro de parse, bug no runner.
            # Não tem relação com a config — excluir do treino ML.
            result["failure_reason"] = "technical"

    finally:
        if teardown:
            _psql(container, db_name, teardown, ignore_error=True)

    return result


# ---------------------------------------------------------------------------
# Cancelamento forçado de backend PostgreSQL (failsafe para statement_timeout)
# ---------------------------------------------------------------------------

def _cancel_active_backend(container: Container, db_name: str, terminate: bool = False) -> None:
    """Cancela ou termina o backend PostgreSQL mais antigo que está ativo.

    Usado como failsafe quando statement_timeout não dispara a tempo.
    Primeiro tenta pg_cancel_backend (graceful SIGINT); se terminate=True usa
    pg_terminate_backend (SIGTERM — encerra a sessão).
    """
    fn  = "pg_terminate_backend" if terminate else "pg_cancel_backend"
    sql = (
        f"SELECT {fn}(pid) FROM pg_stat_activity "
        f"WHERE state = 'active' "
        f"  AND backend_type = 'client backend' "
        f"  AND pid <> pg_backend_pid() "
        f"ORDER BY query_start "
        f"LIMIT 1;"
    )
    try:
        _psql(container, db_name, sql, ignore_error=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Execução em lote com spinner de progresso
# ---------------------------------------------------------------------------

def _fmt_wall(wall_s: float) -> str:
    """Formata tempo de parede em unidade legível: ms, s ou m:ss."""
    if wall_s < 1.0:
        return f"{wall_s * 1000:.0f}ms"
    if wall_s < 60:
        return f"{wall_s:.1f}s"
    m, s = divmod(int(wall_s), 60)
    return f"{m}m{s:02d}s"


def run_all_queries(
    container: Container,
    queries: list[dict],
    db_name: str,
    query_callback: Callable[[dict], None] | None = None,
    hw_snapshot_fn: Callable[[], dict | None] | None = None,
    task_deadline: float | None = None,
) -> list[dict[str, Any]]:
    """Executa uma lista de queries em sequência com spinner de progresso ao vivo.

    Enquanto cada query roda em uma thread separada, exibe um spinner animado
    com o tempo decorrido e, se disponível, métricas de hardware em tempo real.
    Ao terminar, substitui pela linha final com símbolo de resultado:
        ✓  — sucesso
        ⏱  — timeout (statement_timeout do PostgreSQL)
        †  — OOM (container morto pelo kernel)
        ✗  — erro técnico (sintaxe, infra, conexão)

    Timeout: o PostgreSQL cancela a query via statement_timeout (_QUERY_TIMEOUT_MS).
    Como failsafe, se o thread ainda estiver vivo após _QUERY_HARDWALL_S segundos,
    chama pg_cancel_backend diretamente; após mais 15s, pg_terminate_backend.

    Args:
        container:       Container PostgreSQL em execução com os dados carregados.
        queries:         Lista de dicts de query (``id``, ``name``, ``sql``, ...).
        db_name:         Nome do banco de dados ("tpch" ou "tpcds").
        query_callback:  Callable opcional ``(query_result: dict) -> None``
                         invocado imediatamente após cada query terminar.
        hw_snapshot_fn:  Callable opcional ``() -> dict | None`` que retorna o
                         snapshot mais recente do MetricsCollector. Exibido no
                         spinner em tempo real (CPU%, temperatura da CPU).

    Returns:
        Lista de dicts, um por query (veja ``run_query()``).
    """
    # Larguras fixas das colunas — baseadas no máximo de 15 min de timeout:
    #   exec_ms: "900000.0ms" = 10 chars → {:>9.1f}ms
    #   wall:    "15m00s"     =  6 chars → {:>6}  em parênteses = 8 chars
    #   hw:      "[CPU:99% | 99°C]" = 16 chars (fixo, sem RAM)
    _COL_NAME    = 46   # nome da query, padded à direita
    _COL_EXEC    =  9   # dígitos do exec_ms antes de "ms" (right-align)
    _COL_WALL    =  6   # chars do wall time (right-align dentro de parênteses)
    _ERASE_EOL   = "\033[K"   # apaga até o fim da linha — elimina resíduo do spinner

    results = []
    total   = len(queries)

    for query_dict in queries:
        qid    = query_dict["id"]
        name   = query_dict["name"]
        prefix = f"  Q{qid:02d}/{total}  {name:<{_COL_NAME}}"

        result_box: list[dict | None] = [None]

        def _run(qd: dict = query_dict) -> None:
            result_box[0] = run_query(container, qd, db_name)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

        spinner   = itertools.cycle(_SPINNER_FRAMES)
        t0        = time.perf_counter()
        cancelled = False
        hw_str    = ""
        last_hw_t = -999.0   # força leitura inicial imediata

        while thread.is_alive():
            elapsed = time.perf_counter() - t0

            # Atualiza CPU/temp a cada ~2s (sem RAM — não é necessário no spinner)
            if hw_snapshot_fn and (elapsed - last_hw_t >= 2.0):
                hw = hw_snapshot_fn()
                if hw:
                    parts: list[str] = []
                    cpu  = hw.get("cpu_percent")
                    temp = hw.get("cpu_temp_tctl_c")
                    if cpu  is not None: parts.append(f"CPU:{cpu:.0f}%")
                    if temp is not None: parts.append(f"{temp:.0f}°C")
                    hw_str = f"  {_DIM}[{' | '.join(parts)}]{_RESET}" if parts else ""
                last_hw_t = elapsed

            print(
                f"\r{prefix}{_YELLOW}{next(spinner)}{_RESET}"
                f"  {_DIM}{_fmt_wall(elapsed):>{_COL_WALL}}{_RESET}"
                f"{hw_str}{_ERASE_EOL}",
                end="", flush=True,
            )

            # Hard-wall: statement_timeout deveria ter disparado, mas não disparou.
            if not cancelled and elapsed >= _QUERY_HARDWALL_S:
                print(
                    f"\r{prefix}{_RED}!{_RESET}"
                    f"  {_DIM}hard-wall {elapsed:.0f}s — cancelando backend...{_RESET}"
                    f"{_ERASE_EOL}",
                    flush=True,
                )
                _cancel_active_backend(container, db_name, terminate=False)
                cancelled = True
            elif cancelled and elapsed >= _QUERY_HARDWALL_S + 15:
                _cancel_active_backend(container, db_name, terminate=True)

            time.sleep(0.1)

        thread.join(timeout=30)
        r       = result_box[0]
        elapsed = time.perf_counter() - t0

        # Failsafe: thread não terminou no join (Docker daemon travado ou similar).
        # Sintetiza um resultado técnico para não derrubar o runner.
        if r is None:
            r = {
                "id": query_dict["id"], "name": query_dict["name"],
                "success": False, "failure_reason": "technical",
                "error": f"Thread não terminou em 30s após spinner (elapsed={elapsed:.0f}s)",
                "wall_ms": round(elapsed * 1000, 3), "plan_ms": None, "exec_ms": None,
                "rows": None, "shared_hit": None, "shared_read": None,
                "shared_written": None, "shared_dirtied": None,
                "temp_read_blocks": None, "temp_written_blocks": None,
                "cache_hit_ratio": None, "workers_planned": None, "workers_launched": None,
            }

        # Coluna de tempo: sempre 11 chars visíveis ({_COL_EXEC}.1f + "ms" ou label)
        reason = r.get("failure_reason")
        if r["success"]:
            time_col = f"{r['exec_ms']:>{_COL_EXEC}.1f}ms"
            sym      = f"{_GREEN}✓{_RESET}"
        elif reason == "timeout":
            time_col = f"{'timeout':>{_COL_EXEC + 2}}"   # +2 para alinhar com "Xms"
            sym      = f"{_YELLOW}⏱{_RESET}"
        elif reason == "oom":
            time_col = f"{'OOM':>{_COL_EXEC + 2}}"
            sym      = f"{_RED}†{_RESET}"
        else:
            short_err = (r["error"] or "").splitlines()[0][:55]
            time_col  = short_err
            sym       = f"{_RED}✗{_RESET}"

        # Para sucesso: mostra overhead = wall - exec - plan (custo fixo do Docker/psql).
        # Para falhas sem exec_ms: mostra wall time diretamente.
        if r["success"]:
            plan_ms     = r.get("plan_ms") or 0.0
            overhead_ms = max(0.0, elapsed * 1000 - r["exec_ms"] - plan_ms)
            extra_col   = f"{_DIM}(+{overhead_ms:.0f}ms ovhd){_RESET}"
        else:
            extra_col   = f"{_DIM}({_fmt_wall(elapsed):>{_COL_WALL}}){_RESET}"

        print(
            f"\r{prefix}{sym}  {time_col}  {extra_col}{_ERASE_EOL}"
        )
        results.append(r)

        if query_callback is not None:
            query_callback(r)

        # Verifica teto de tempo da tarefa após cada query
        if task_deadline is not None and time.monotonic() > task_deadline:
            raise TaskTimeoutError(
                f"Limite de tempo da tarefa excedido após {len(results)}/{len(queries)} queries "
                f"({db_name.upper()})."
            )

    return results


def run_benchmark(
    container: Container,
    queries: list[dict],
    db_name: str,
    query_callback: Callable[[dict], None] | None = None,
    hw_snapshot_fn: Callable[[], dict | None] | None = None,
    task_deadline: float | None = None,
) -> dict[str, Any]:
    """Pipeline completo de benchmark.

    Sequência:
        1. ``reset_stats()``      — zera contadores
        2. ``run_all_queries()``  — executa todas as queries com spinner
        3. ``collect_pg_stats()`` — captura estatísticas globais

    Args:
        container:       Container PostgreSQL em execução com dados carregados.
        queries:         Lista de dicts de query (veja ``run_all_queries()``).
        db_name:         Nome do banco de dados ("tpch" ou "tpcds").
        query_callback:  Callable opcional ``(query_result: dict) -> None``
                         repassado para ``run_all_queries()``.
        hw_snapshot_fn:  Callable opcional ``() -> dict | None`` repassado para
                         ``run_all_queries()`` para exibir hw em tempo real.

    Returns:
        Dict com:
            queries   — lista de dicts com métricas por query
            summary   — métricas agregadas (veja ``compute_summary()``)
            pg_stats  — estatísticas do PostgreSQL
            total_ms  — tempo total do benchmark (ms)
            n_success — número de queries bem-sucedidas
            n_failed  — número de queries com erro
    """
    n_total = len(queries)
    label   = db_name.upper()

    print(f"[benchmark] Zerando estatísticas...")
    reset_stats(container, db_name)

    print(f"[benchmark] Executando {n_total} queries {label}...")
    t0       = time.perf_counter()
    results  = run_all_queries(container, queries, db_name, query_callback, hw_snapshot_fn,
                               task_deadline=task_deadline)
    total_ms = (time.perf_counter() - t0) * 1000

    print("[benchmark] Coletando estatísticas do PostgreSQL...")
    pg_stats  = collect_pg_stats(container, db_name)

    n_success = sum(1 for q in results if q["success"])
    n_failed  = n_total - n_success

    print(f"[benchmark] Concluído: {n_success}/{n_total} OK em {_fmt_wall(total_ms / 1000)}")

    return {
        "queries":   results,
        "summary":   compute_summary(results),
        "pg_stats":  pg_stats,
        "total_ms":  round(total_ms, 3),
        "n_success": n_success,
        "n_failed":  n_failed,
    }


# ---------------------------------------------------------------------------
# Coleta de estatísticas do PostgreSQL
# ---------------------------------------------------------------------------

def collect_pg_stats(container: Container, db_name: str) -> dict[str, Any]:
    """Coleta estatísticas do PostgreSQL após o benchmark.

    Coleta dados de cinco visões do sistema, refletindo a carga gerada
    desde o último ``reset_stats()``:

    - **bgwriter**   — checkpoints, buffers escritos, I/O de background
    - **database**   — cache hit ratio, tuplas lidas/retornadas, temp files
    - **tables**     — seq_scan vs idx_scan, tuplas lidas por tabela
    - **tables_io**  — heap/index block reads vs hits por tabela
    - **wal**        — bytes de WAL gerados (PG14+)
    - **settings**   — valores GUC efetivos dos parâmetros configurados

    Args:
        container: Container PostgreSQL em execução.
        db_name:   Nome do banco de dados ("tpch" ou "tpcds").

    Returns:
        Dict com as seis chaves acima.
    """
    return {
        "bgwriter":  _collect_bgwriter(container, db_name),
        "database":  _collect_database(container, db_name),
        "tables":    _collect_tables(container, db_name),
        "tables_io": _collect_tables_io(container, db_name),
        "wal":       _collect_wal(container, db_name),
        "settings":  _collect_settings(container, db_name),
    }


# ---------------------------------------------------------------------------
# Agregação de métricas
# ---------------------------------------------------------------------------

def compute_summary(queries: list[dict]) -> dict[str, Any]:
    """Calcula métricas agregadas sobre um conjunto de queries.

    Agrupa as métricas em três categorias:
    - **tempo**: total, geo-média, mediana, p95, max, min, desvio padrão
    - **I/O de buffer**: cache hit ratio global, spill para disco
    - **paralelismo**: queries com workers, média de workers usados

    Também produz vetores ``exec_ms_per_query`` e ``wall_ms_per_query``
    indexados pelo ID da query, para análise individual.

    Args:
        queries: Lista de dicts retornada por chamadas a ``run_query()``.

    Returns:
        Dict com todas as métricas agregadas. Campos são ``None`` quando
        não há dados suficientes (ex: todas as queries falharam).
    """
    # Separa por failure_reason para contagem e cálculos corretos
    successful    = [q for q in queries if q.get("failure_reason") == "ok"]
    timed_out_qs  = [q for q in queries if q.get("failure_reason") == "timeout"]
    oom_qs        = [q for q in queries if q.get("failure_reason") == "oom"]
    technical_qs  = [q for q in queries if q.get("failure_reason") == "technical"]

    # exec_times: inclui "ok", "timeout" e "oom" (todos têm exec_ms não-None).
    # Exclui "technical" (exec_ms=None, falha de infraestrutura, não treinável).
    exec_times = [
        q["exec_ms"] for q in queries
        if q.get("exec_ms") is not None and q.get("failure_reason") in ("ok", "timeout", "oom")
    ]

    # Métricas de tempo
    total_exec_ms  = round(sum(exec_times), 3)           if exec_times else None
    max_exec_ms    = round(max(exec_times), 3)           if exec_times else None
    min_exec_ms    = round(min(exec_times), 3)           if exec_times else None
    median_exec_ms = round(statistics.median(exec_times), 3) if exec_times else None
    stddev_exec_ms = round(statistics.stdev(exec_times), 3)  if len(exec_times) > 1 else 0.0
    p95_exec_ms    = round(sorted(exec_times)[int(len(exec_times) * 0.95)], 3) if exec_times else None

    # Geo-média: mais robusta que aritmética para tempos com outliers
    if exec_times and all(t > 0 for t in exec_times):
        geo_mean_exec_ms = round(
            math.exp(sum(math.log(t) for t in exec_times) / len(exec_times)), 3
        )
    else:
        geo_mean_exec_ms = None

    # Métricas de buffer / I/O
    total_shared_hit   = sum(q.get("shared_hit")  or 0 for q in successful)
    total_shared_read  = sum(q.get("shared_read") or 0 for q in successful)
    total_temp_read    = sum(q.get("temp_read_blocks")    or 0 for q in successful)
    total_temp_written = sum(q.get("temp_written_blocks") or 0 for q in successful)

    overall_cache_hit_ratio = (
        round(total_shared_hit / (total_shared_hit + total_shared_read) * 100, 2)
        if (total_shared_hit + total_shared_read) > 0 else None
    )
    queries_with_spill = sum(
        1 for q in successful if (q.get("temp_written_blocks") or 0) > 0
    )

    # Métricas de paralelismo
    workers_per_query    = [q.get("workers_launched") or 0 for q in successful]
    queries_parallel     = sum(1 for w in workers_per_query if w > 0)
    avg_workers_launched = (
        round(sum(workers_per_query) / len(workers_per_query), 2)
        if workers_per_query else 0.0
    )

    # ── Vetores por query para feature engineering ──────────────────────────
    #
    # failure_reason_per_query  →  "ok" | "timeout" | "oom" | "technical"
    #   Instrução para o feature extractor:
    #     "ok"        usar exec_ms diretamente
    #     "timeout"   usar exec_ms (_IMPUTE_TIMEOUT_MS); adicionar flag is_censored=1
    #     "oom"       usar exec_ms (_IMPUTE_OOM_MS);     adicionar flag is_censored=1
    #     "technical" exec_ms=None → excluir linha do treino (preencher com NaN/drop)
    #
    # exec_ms_per_query  →  valor numérico sempre presente para "ok"/"timeout"/"oom";
    #                        None para "technical" (o feature extractor lida com isso).
    exec_ms_per_query        = {f"q{q['id']}": q.get("exec_ms")          for q in queries}
    wall_ms_per_query        = {f"q{q['id']}": q.get("wall_ms")          for q in queries}
    failure_reason_per_query = {f"q{q['id']}": q.get("failure_reason")   for q in queries}

    return {
        "n_queries_successful": len(successful),
        "n_queries_timed_out":  len(timed_out_qs),
        "n_queries_oom":        len(oom_qs),
        "n_queries_technical":  len(technical_qs),

        "total_exec_ms":    total_exec_ms,
        "geo_mean_exec_ms": geo_mean_exec_ms,
        "median_exec_ms":   median_exec_ms,
        "p95_exec_ms":      p95_exec_ms,
        "max_exec_ms":      max_exec_ms,
        "min_exec_ms":      min_exec_ms,
        "stddev_exec_ms":   stddev_exec_ms,

        "total_shared_hit":          total_shared_hit,
        "total_shared_read":         total_shared_read,
        "overall_cache_hit_ratio":   overall_cache_hit_ratio,
        "total_temp_read_blocks":    total_temp_read,
        "total_temp_written_blocks": total_temp_written,
        "queries_with_spill":        queries_with_spill,

        "queries_with_parallelism": queries_parallel,
        "avg_workers_launched":     avg_workers_launched,

        "exec_ms_per_query":        exec_ms_per_query,
        "wall_ms_per_query":        wall_ms_per_query,
        "failure_reason_per_query": failure_reason_per_query,
    }


# ---------------------------------------------------------------------------
# Coletores de pg_stat_* (internos)
# ---------------------------------------------------------------------------

def _collect_bgwriter(container: Container, db_name: str) -> dict:
    sql = (
        "SELECT row_to_json(s) FROM ("
        "  SELECT *, now() AS collected_at FROM pg_stat_bgwriter"
        ") s;"
    )
    return _fetch_json_object(container, db_name, sql)


def _collect_database(container: Container, db_name: str) -> dict:
    sql = (
        "SELECT row_to_json(s) FROM ("
        "  SELECT datname, numbackends, xact_commit, xact_rollback,"
        "         blks_read, blks_hit,"
        "         CASE WHEN blks_hit + blks_read = 0 THEN NULL"
        "              ELSE round(blks_hit::numeric / (blks_hit + blks_read) * 100, 2)"
        "         END AS cache_hit_pct,"
        "         tup_returned, tup_fetched, tup_inserted, tup_updated, tup_deleted,"
        "         conflicts, temp_files, temp_bytes, deadlocks,"
        "         blk_read_time, blk_write_time"
        "  FROM pg_stat_database"
        f" WHERE datname = '{db_name}'"
        ") s;"
    )
    return _fetch_json_object(container, db_name, sql)


def _collect_tables(container: Container, db_name: str) -> list:
    sql = (
        "SELECT coalesce(json_agg(s), '[]') FROM ("
        "  SELECT relname, seq_scan, seq_tup_read, idx_scan, idx_tup_fetch,"
        "         n_tup_ins, n_tup_upd, n_tup_del, n_tup_hot_upd,"
        "         n_live_tup, n_dead_tup,"
        "         CASE WHEN seq_scan + coalesce(idx_scan,0) = 0 THEN NULL"
        "              ELSE round(coalesce(idx_scan,0)::numeric"
        "                         / (seq_scan + coalesce(idx_scan,0)) * 100, 2)"
        "         END AS idx_scan_pct"
        "  FROM pg_stat_user_tables"
        "  ORDER BY seq_tup_read DESC"
        ") s;"
    )
    return _fetch_json_array(container, db_name, sql)


def _collect_tables_io(container: Container, db_name: str) -> list:
    sql = (
        "SELECT coalesce(json_agg(s), '[]') FROM ("
        "  SELECT relname,"
        "         heap_blks_read, heap_blks_hit,"
        "         CASE WHEN heap_blks_hit + heap_blks_read = 0 THEN NULL"
        "              ELSE round(heap_blks_hit::numeric"
        "                         / (heap_blks_hit + heap_blks_read) * 100, 2)"
        "         END AS heap_hit_pct,"
        "         idx_blks_read, idx_blks_hit,"
        "         toast_blks_read, toast_blks_hit"
        "  FROM pg_statio_user_tables"
        "  ORDER BY heap_blks_read + coalesce(idx_blks_read, 0) DESC"
        ") s;"
    )
    return _fetch_json_array(container, db_name, sql)


def _collect_wal(container: Container, db_name: str) -> dict:
    """Coleta estatísticas de WAL (pg_stat_wal, disponível no PG14+).

    Retorna dict vazio em versões anteriores do PostgreSQL.
    Métricas relevantes: wal_bytes (total gerado), wal_fpi (full-page images
    — indicador de checkpoints frequentes).
    """
    sql = (
        "SELECT row_to_json(s) FROM ("
        "  SELECT wal_records, wal_fpi, wal_bytes, wal_buffers_full,"
        "         wal_write, wal_sync, wal_write_time, wal_sync_time,"
        "         now() AS collected_at"
        "  FROM pg_stat_wal"
        ") s;"
    )
    return _fetch_json_object(container, db_name, sql)


def _collect_settings(container: Container, db_name: str) -> dict:
    """Coleta os valores GUC efetivos dos parâmetros configurados."""
    sql = (
        "SELECT json_object_agg(name, setting) FROM pg_settings"
        " WHERE source != 'default'"
        "    OR name = ANY(ARRAY["
        "         'shared_buffers','work_mem','maintenance_work_mem',"
        "         'effective_cache_size','wal_buffers',"
        "         'checkpoint_completion_target','max_wal_size','min_wal_size',"
        "         'random_page_cost','seq_page_cost','cpu_tuple_cost',"
        "         'cpu_index_tuple_cost','cpu_operator_cost','effective_io_concurrency',"
        "         'max_worker_processes','max_parallel_workers',"
        "         'max_parallel_workers_per_gather','parallel_tuple_cost',"
        "         'parallel_setup_cost','jit','bgwriter_lru_maxpages',"
        "         'bgwriter_delay','bgwriter_lru_multiplier',"
        "         'autovacuum_vacuum_cost_delay','autovacuum_vacuum_scale_factor',"
        "         'autovacuum_analyze_scale_factor'"
        "       ]);"
    )
    return _fetch_json_object(container, db_name, sql)


# ---------------------------------------------------------------------------
# Helpers de baixo nível
# ---------------------------------------------------------------------------

def _psql(
    container: Container,
    db_name: str,
    sql: str,
    ignore_error: bool = False,
    timeout_ms: int | None = None,
) -> str:
    """Executa SQL dentro do container via psql e retorna stdout.

    Args:
        container:    Container PostgreSQL em execução.
        db_name:      Banco de dados alvo.
        sql:          Comando SQL a executar.
        ignore_error: Se True, não levanta exceção em caso de erro.
        timeout_ms:   Se fornecido, injeta ``SET statement_timeout`` antes do SQL.

    Returns:
        Saída padrão do psql (sem cabeçalhos, sem padding).

    Raises:
        RuntimeError: Se psql retornar exit_code != 0 e ignore_error=False.
    """
    cmd = ["psql", "-U", POSTGRES_USER, "-d", db_name, "-t", "-A"]
    if timeout_ms is not None:
        # Sufixo 'ms' explícito para evitar ambiguidade de unidade no PostgreSQL
        cmd += ["-c", f"SET statement_timeout = '{timeout_ms}ms';"]
    cmd += ["-c", sql]

    result = container.exec_run(cmd, demux=False)
    output = result.output.decode(errors="replace") if result.output else ""
    if result.exit_code != 0 and not ignore_error:
        if result.exit_code == 137:
            raise OOMKillError(
                f"Container morto pelo OOM killer (exit 137) ao executar SQL:\n{sql}"
            )
        raise RuntimeError(
            f"psql falhou (exit {result.exit_code}) para SQL:\n{sql}\n"
            f"Output:\n{output}"
        )
    return output


class OOMKillError(Exception):
    """Levantado quando o container é morto pelo OOM killer (exit code 137)."""


def _parse_explain_json(output: str) -> dict:
    """Extrai o dict do plano EXPLAIN FORMAT JSON da saída do psql.

    Args:
        output: Saída bruta do psql com o plano em JSON.

    Returns:
        Dict do primeiro (e único) plano no array retornado.

    Raises:
        ValueError: Se o JSON não puder ser extraído da saída.
    """
    text  = output.strip()
    match = re.search(r"(\[.*\])", text, re.DOTALL)
    if not match:
        raise ValueError(f"Não foi possível extrair JSON do EXPLAIN:\n{text}")
    plans = json.loads(match.group(1))
    return plans[0]


def _safe(obj: Any, key: Any) -> Any:
    """Acessa uma chave (ou caminho de chaves) em um dict sem levantar exceção.

    Args:
        obj: Dict a acessar.
        key: Chave simples ou lista de chaves para acesso aninhado.

    Returns:
        Valor encontrado, ou None se o caminho não existir.
    """
    if isinstance(key, list):
        for k in key:
            if not isinstance(obj, dict):
                return None
            obj = obj.get(k)
        return obj
    return obj.get(key) if isinstance(obj, dict) else None


def _fetch_json_object(container: Container, db_name: str, sql: str) -> dict:
    """Executa SQL e retorna o resultado como dict JSON."""
    output = _psql(container, db_name, sql, ignore_error=True)
    start  = output.find("{")
    if start != -1:
        try:
            return json.loads(output[start:].strip())
        except json.JSONDecodeError:
            pass
    return {}


def _fetch_json_array(container: Container, db_name: str, sql: str) -> list:
    """Executa SQL e retorna o resultado como lista JSON."""
    output = _psql(container, db_name, sql, ignore_error=True)
    start  = output.find("[")
    if start != -1:
        try:
            return json.loads(output[start:].strip())
        except json.JSONDecodeError:
            pass
    return []
