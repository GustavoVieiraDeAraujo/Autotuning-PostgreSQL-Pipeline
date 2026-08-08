"""
Extração de features para treinamento de modelos ML.

Lê todos os arquivos JSON de resultado em data/raw/ e produz
um CSV flat onde cada linha é uma tarefa e cada coluna é um parâmetro do
pg_config (feature) ou uma métrica de desempenho (target).

Tratamento de tarefas incompletas
----------------------------------
  done      → tpch_total_ms e tpcds_total_ms usam o valor registrado
  abandoned → soma exec_ms das queries concluídas + imputa _IMPUTE_TIMEOUT_MS
              para cada query que não chegou a rodar (a tarefa foi cortada pelo
              timeout do tier, não pela query em si — as queries restantes
              teriam sido lentas também)
  running   → ignoradas (em execução)

Imputação de falhas por query (dentro das queries concluídas)
--------------------------------------------------------------
  "ok"        → exec_ms real (sem imputação)
  "timeout"   → exec_ms = 900000 ms (15 min, limite por query)
  "oom"       → exec_ms = 1350000 ms (1.5× timeout — pior que timeout)
  "technical" → exec_ms = 900000 ms (tratado como timeout; falha de infra
                rara, mas não queremos ignorar a query no somatório)

Colunas de target por query
----------------------------
  tpch_q{N}_ms         (N = 1..22, None para Q17 e Q20 — sempre puladas)
  tpcds_q{N}_ms        (N = 1..99, None para Q95 — sempre pulada)
  tpch_q{N}_timed_out  (0 = medição real, 1 = imputado/timeout, None = pulada)
  tpcds_q{N}_timed_out (0 = medição real, 1 = imputado/timeout, None = pulada)

  Para tasks done: valor direto de summary["exec_ms_per_query"]
  Para tasks abandoned: valor de queries já executadas ou _IMPUTE_TIMEOUT_MS
    para as queries que não chegaram a rodar.

  Os flags _timed_out implementam o padrão "missing indicator": permitem ao
  meta-modelo distinguir entre uma medição real de 900s e um teto imputado
  de 900s. Configs que evitam timeouts são detectáveis mesmo quando o exec_ms
  tem o mesmo valor de teto.

Colunas de cache hit ratio
---------------------------
  tpch_cache_hit_ratio   — overall_cache_hit_ratio da summary (None se abandonada)
  tpcds_cache_hit_ratio  — overall_cache_hit_ratio da summary (None se abandonada)

Colunas de hardware (últimas 3 do vetor X)
-------------------------------------------
  vcpus      — número de vCPUs do container (2/4/6 para low/medium/high)
  memory_mb  — RAM alocada em MB (2048/4096/5120)
  sf         — scale factor do dataset (1/2/4)
  Essas colunas são constantes por tier mas essenciais para que o meta-modelo
  generalize entre tiers sem confundir impacto de config com impacto de hardware.

Saída
-----
  data/processed/features.csv   — uma linha por tarefa, pronta para pd.read_csv()

Uso
---
  python ml/extract_features.py                    # gera data/processed/features.csv
  python ml/extract_features.py --output foo.csv   # caminho alternativo
  python ml/extract_features.py --summary          # imprime estatísticas
"""

import argparse
import csv
import json
import sys
from pathlib import Path

from ml.config import BOOL_PARAMS, MEMORY_PARAMS, TIER_HARDWARE

# ---------------------------------------------------------------------------
# Caminhos padrão
# ---------------------------------------------------------------------------

_ROOT        = Path(__file__).parent.parent
_RESULTS_DIR = _ROOT / "data" / "raw"
_OUTPUT_CSV  = _ROOT / "data" / "processed" / "features.csv"

# Aliases locais — o resto do módulo usa os nomes com underscore prefixo.
_TIER_HARDWARE  = TIER_HARDWARE

# ---------------------------------------------------------------------------
# Constantes de imputação (espelham query_executor.py)
# ---------------------------------------------------------------------------

_IMPUTE_TIMEOUT_MS  = 900_000.0        # 15 min — statement_timeout por query
_IMPUTE_OOM_MS      = 900_000.0 * 1.5  # pior que timeout: kernel matou o processo

# Q17 e Q20 (TPC-H) e Q95 (TPC-DS) são puladas permanentemente — sempre timeout.
# O total de queries ativas usadas na imputação de tasks abandonadas reflete isso.
_TPCH_TOTAL_QUERIES  = 20   # 22 − Q17 − Q20
_TPCDS_TOTAL_QUERIES = 98   # 99 − Q95

# Ranges completos para colunas por query no CSV (puladas sempre ficam None)
_TPCH_ALL_IDS  = list(range(1, 23))    # Q1..Q22
_TPCDS_ALL_IDS = list(range(1, 100))   # Q1..Q99
_TPCH_SKIP     = frozenset({17, 20})
_TPCDS_SKIP    = frozenset({95})

# ---------------------------------------------------------------------------
# Mapeamento de parâmetros
# ---------------------------------------------------------------------------

# Aliases — definidos em ml/config.py, importados no topo do módulo.
_BOOL_PARAMS   = BOOL_PARAMS
_MEMORY_PARAMS = MEMORY_PARAMS

# Ordem canônica das colunas de feature (prefixo "cfg_" no CSV)
_ALL_PARAMS: list[str] = [
    # --- Etapa 1 — memória, paralelismo, toggles básicos ---
    "jit",
    "seq_page_cost",
    "random_page_cost",
    "default_statistics_target",
    "max_parallel_workers",
    "max_parallel_workers_per_gather",
    "max_worker_processes",
    "shared_buffers",
    "effective_cache_size",
    "work_mem",
    "enable_hashagg",
    "enable_bitmapscan",
    "enable_nestloop",
    "enable_parallel_hash",
    "enable_sort",
    "synchronous_commit",
    # --- Etapa 2 — custos CPU, collapse limits, hash memory, toggles join ---
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
    "min_parallel_table_scan_size",
    "min_parallel_index_scan_size",
    "join_collapse_limit",
    "from_collapse_limit",
    "hash_mem_multiplier",
    "enable_mergejoin",
    "enable_hashjoin",
    # --- Etapa 3 — execução avançada, I/O background ---
    "enable_memoize",
    "enable_gathermerge",
    "enable_incremental_sort",
    "enable_material",
    "enable_indexscan",
    "enable_indexonlyscan",
    "enable_parallel_append",
    "parallel_leader_participation",
]


# ---------------------------------------------------------------------------
# Conversão de valores
# ---------------------------------------------------------------------------

def _parse_memory_mb(value) -> float:
    """Converte string de memória PostgreSQL para MB (float)."""
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if s.endswith("GB"):
        return float(s[:-2]) * 1024.0
    if s.endswith("MB"):
        return float(s[:-2])
    if s.endswith("kB"):
        return float(s[:-2]) / 1024.0
    return float(s)


def _encode(key: str, value) -> float | None:
    """Converte um valor de pg_config para float, ou None se ausente."""
    if value is None:
        return None
    if key in _BOOL_PARAMS:
        if isinstance(value, (int, float)):
            return float(value)
        return 1.0 if str(value).strip().lower() in ("on", "1", "true") else 0.0
    if key in _MEMORY_PARAMS:
        return _parse_memory_mb(value)
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Cálculo de target a partir das queries
# ---------------------------------------------------------------------------

def _query_exec_ms(q: dict) -> float:
    """
    Retorna o exec_ms efetivo de uma query já registrada.

    Se exec_ms já está imputado no JSON (timeout/oom), usa esse valor.
    Para falhas técnicas (exec_ms=None), usa _IMPUTE_TIMEOUT_MS como
    conservador — a query não completou por razão não relacionada à config,
    mas não queremos ignorá-la no somatório do target.
    """
    v = q.get("exec_ms")
    if v is not None:
        return float(v)
    return _IMPUTE_TIMEOUT_MS


def _compute_total_ms(bench: dict, total_queries: int) -> float:
    """
    Calcula o total_ms de uma seção de benchmark.

    Para tasks concluídas (total_ms registrado): retorna o valor diretamente.
    Para tasks abandonadas (total_ms=None): soma exec_ms das queries concluídas
    e imputa _IMPUTE_TIMEOUT_MS para cada query que não chegou a iniciar.
    """
    recorded = bench.get("total_ms")
    if recorded is not None:
        return float(recorded)

    queries   = bench.get("queries", [])
    completed = sum(_query_exec_ms(q) for q in queries)
    missing   = total_queries - len(queries)
    return completed + missing * _IMPUTE_TIMEOUT_MS


def _per_query_ms(bench: dict, all_ids: list[int], skip_ids: frozenset[int]) -> dict[int, float | None]:
    """
    Retorna um dict {query_id: exec_ms} para todas as queries do benchmark.

    Queries puladas permanentemente (skip_ids) → None.
    Para tasks done: usa summary["exec_ms_per_query"].
    Para tasks abandoned: usa queries individuais; imputa _IMPUTE_TIMEOUT_MS
      para queries que não chegaram a rodar.
    """
    result: dict[int, float | None] = {qid: None for qid in all_ids}

    # Queries puladas permanentemente ficam None
    for qid in skip_ids:
        result[qid] = None

    summary = bench.get("summary", {})
    exec_ms_map = summary.get("exec_ms_per_query") if summary else None

    if exec_ms_map is not None:
        # Task done: pull directly from summary
        for qid in all_ids:
            if qid in skip_ids:
                continue
            key = f"q{qid}"
            v   = exec_ms_map.get(key)
            result[qid] = round(float(v), 3) if v is not None else _IMPUTE_TIMEOUT_MS
    else:
        # Task abandoned: build from individual queries list
        queries    = bench.get("queries", [])
        seen: dict[int, float] = {q["id"]: _query_exec_ms(q) for q in queries}
        for qid in all_ids:
            if qid in skip_ids:
                continue
            result[qid] = round(seen.get(qid, _IMPUTE_TIMEOUT_MS), 3)

    return result


def _per_query_timed_out(
    bench: dict,
    all_ids: list[int],
    skip_ids: frozenset[int],
) -> dict[int, int | None]:
    """
    Retorna um dict {query_id: 0|1|None} indicando se a medição é imputada.

    1 = valor imputado (timeout, oom, não executou — não é medição real)
    0 = medição real ("ok" ou "technical" com exec_ms registrado)
    None = query pulada permanentemente (skip_ids)

    Usado como missing indicator: permite ao ML distinguir entre um valor
    real de 900s e um teto imputado de 900s.
    """
    result: dict[int, int | None] = {qid: None for qid in all_ids}

    for qid in skip_ids:
        result[qid] = None

    summary = bench.get("summary", {})
    failure_map = summary.get("failure_reason_per_query") if summary else None

    if failure_map is not None:
        # Task done: usa failure_reason_per_query da summary
        for qid in all_ids:
            if qid in skip_ids:
                continue
            key = f"q{qid}"
            reason = failure_map.get(key)
            if reason is None:
                # Query não chegou a rodar (task abandonada mas summary parcial)
                result[qid] = 1
            else:
                result[qid] = 0 if reason == "ok" else 1
    else:
        # Task abandoned: queries não executadas são imputadas
        queries    = bench.get("queries", [])
        seen_ok: set[int] = {
            q["id"] for q in queries if q.get("failure_reason") == "ok"
        }
        for qid in all_ids:
            if qid in skip_ids:
                continue
            result[qid] = 0 if qid in seen_ok else 1

    return result


# ---------------------------------------------------------------------------
# Extração principal
# ---------------------------------------------------------------------------

def extract(results_dirs: Path | list[Path], output_csv: Path) -> list[dict]:
    """
    Varre um ou mais diretórios recursivamente, processa todos os task_*.json
    e retorna a lista de dicts prontos para escrita em CSV.

    Ignora tarefas com status "running" (em execução).
    """
    if isinstance(results_dirs, Path):
        results_dirs = [results_dirs]

    rows: list[dict] = []
    seen_paths: set[Path] = set()

    all_files = []
    for d in results_dirs:
        for f in d.rglob("task_*.json"):
            rp = f.resolve()
            if rp not in seen_paths:
                seen_paths.add(rp)
                all_files.append(f)

    for task_file in sorted(all_files):
        try:
            with open(task_file, encoding="utf-8") as f:
                d = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        status = d.get("status", "unknown")
        if status == "running":
            continue

        tpch_bench  = d.get("tpc_h",  {})
        tpcds_bench = d.get("tpc_ds", {})
        cfg         = d.get("pg_config", {})

        tpch_summary  = tpch_bench.get("summary",  {}) or {}
        tpcds_summary = tpcds_bench.get("summary", {}) or {}

        row: dict = {
            # Metadados
            "task_id":         d.get("task_id"),
            "tier":            d.get("tier", ""),
            "combination":     d.get("combination", ""),
            "status":          status,
            "duration_s":      d.get("duration_s"),
            "abandoned_reason": d.get("abandoned_reason", ""),
            # Targets agregados
            "tpch_n_success":             tpch_bench.get("n_success", 0),
            "tpch_n_failed":              tpch_bench.get("n_failed",  0),
            "tpch_total_ms":              round(_compute_total_ms(tpch_bench,  _TPCH_TOTAL_QUERIES),  3),
            "tpch_geo_mean_ms":           tpch_summary.get("geo_mean_exec_ms"),
            "tpch_cache_hit_ratio":       tpch_summary.get("overall_cache_hit_ratio"),
            "tpch_queries_with_spill":    tpch_summary.get("queries_with_spill"),
            "tpcds_n_success":            tpcds_bench.get("n_success", 0),
            "tpcds_n_failed":             tpcds_bench.get("n_failed",  0),
            "tpcds_total_ms":             round(_compute_total_ms(tpcds_bench, _TPCDS_TOTAL_QUERIES), 3),
            "tpcds_geo_mean_ms":          tpcds_summary.get("geo_mean_exec_ms"),
            "tpcds_cache_hit_ratio":      tpcds_summary.get("overall_cache_hit_ratio"),
            "tpcds_queries_with_spill":   tpcds_summary.get("queries_with_spill"),
        }

        # Targets por query — tpch_q{N}_ms / tpcds_q{N}_ms
        tpch_per_q  = _per_query_ms(tpch_bench,  _TPCH_ALL_IDS,  _TPCH_SKIP)
        tpcds_per_q = _per_query_ms(tpcds_bench, _TPCDS_ALL_IDS, _TPCDS_SKIP)
        for qid in _TPCH_ALL_IDS:
            row[f"tpch_q{qid}_ms"] = tpch_per_q[qid]
        for qid in _TPCDS_ALL_IDS:
            row[f"tpcds_q{qid}_ms"] = tpcds_per_q[qid]

        # Flags de imputação — tpch_q{N}_timed_out / tpcds_q{N}_timed_out
        # 1 = valor imputado (timeout/oom/não executou), 0 = medição real, None = query pulada
        tpch_to  = _per_query_timed_out(tpch_bench,  _TPCH_ALL_IDS,  _TPCH_SKIP)
        tpcds_to = _per_query_timed_out(tpcds_bench, _TPCDS_ALL_IDS, _TPCDS_SKIP)
        for qid in _TPCH_ALL_IDS:
            row[f"tpch_q{qid}_timed_out"] = tpch_to[qid]
        for qid in _TPCDS_ALL_IDS:
            row[f"tpcds_q{qid}_timed_out"] = tpcds_to[qid]

        # Features: um float por parâmetro (None = parâmetro não presente nesta etapa)
        for param in _ALL_PARAMS:
            row[f"cfg_{param}"] = _encode(param, cfg.get(param))

        # Hardware context — últimas 3 colunas do vetor X.
        # Permitem ao meta-modelo distinguir o impacto de uma config no LOW
        # (2vCPU/2GB/SF=1) do impacto no HIGH (6vCPU/5GB/SF=4).
        hw = _TIER_HARDWARE.get(d.get("tier", ""), {})
        row["vcpus"]     = hw.get("vcpus")
        row["memory_mb"] = hw.get("memory_mb")
        row["sf"]        = hw.get("sf")

        rows.append(row)

    if not rows:
        return rows

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return rows


# ---------------------------------------------------------------------------
# Sumário de diagnóstico
# ---------------------------------------------------------------------------

def _print_summary(rows: list[dict]) -> None:
    """Imprime estatísticas básicas do dataset extraído."""
    if not rows:
        print("Nenhuma tarefa encontrada.")
        return

    from collections import Counter

    total     = len(rows)
    by_status = Counter(r["status"]    for r in rows)
    by_tier   = Counter(r["tier"]      for r in rows)
    by_combo  = Counter(r["combination"] for r in rows)

    done_rows = [r for r in rows if r["status"] == "done"]

    print(f"\n{'='*60}")
    print(f"  Dataset: {total} tarefas")
    print(f"{'='*60}")
    print(f"\nStatus:")
    for s, n in sorted(by_status.items()):
        print(f"  {s:<12} {n:>4}")

    print(f"\nTier:")
    for t, n in sorted(by_tier.items()):
        print(f"  {t:<12} {n:>4}")

    print(f"\nCombinação de etapas:")
    for c, n in sorted(by_combo.items()):
        print(f"  {c:<12} {n:>4}")

    if done_rows:
        tpch_vals  = [r["tpch_total_ms"]  for r in done_rows]
        tpcds_vals = [r["tpcds_total_ms"] for r in done_rows]

        def _stats(vals: list[float]) -> str:
            mn, mx, avg = min(vals), max(vals), sum(vals) / len(vals)
            return f"min={mn/1000:.0f}s  avg={avg/1000:.0f}s  max={mx/1000:.0f}s"

        print(f"\nTargets (tasks done={len(done_rows)}):")
        print(f"  TPC-H   {_stats(tpch_vals)}")
        print(f"  TPC-DS  {_stats(tpcds_vals)}")

    # Features com NaN (parâmetros ausentes por etapa)
    all_params = [k for k in rows[0] if k.startswith("cfg_")]
    missing_pct = {
        p: sum(1 for r in rows if r[p] is None) / total * 100
        for p in all_params
        if any(r[p] is None for r in rows)
    }
    if missing_pct:
        print(f"\nFeatures com valores ausentes (parâmetro não amostrado nessa etapa):")
        for p, pct in sorted(missing_pct.items(), key=lambda x: -x[1])[:10]:
            print(f"  {p:<40} {pct:5.1f}% ausente")
        if len(missing_pct) > 10:
            print(f"  ... e mais {len(missing_pct)-10} parâmetros")

    print(f"\nCSV: {_OUTPUT_CSV}\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extrai features de resultado para treinamento ML."
    )
    parser.add_argument(
        "--results-dirs", type=Path, nargs="+", default=[_RESULTS_DIR],
        metavar="DIR",
        help=(
            "Um ou mais diretórios raiz de resultados. "
            f"Padrão: {_RESULTS_DIR}. "
            "Exemplo para combinar rodadas: "
            "--results-dirs data/raw ../Results/rodada1/benchmark_results"
        ),
    )
    parser.add_argument(
        "--output", type=Path, default=_OUTPUT_CSV,
        help=f"Arquivo CSV de saída (padrão: {_OUTPUT_CSV})",
    )
    parser.add_argument(
        "--summary", action="store_true",
        help="Imprime estatísticas do dataset após extração",
    )
    args = parser.parse_args()

    rows = extract(args.results_dirs, args.output)

    if not rows:
        print("Nenhuma tarefa concluída encontrada.", file=sys.stderr)
        sys.exit(1)

    print(f"Extraídas {len(rows)} tarefas → {args.output}")

    if args.summary:
        _print_summary(rows)


if __name__ == "__main__":
    main()
