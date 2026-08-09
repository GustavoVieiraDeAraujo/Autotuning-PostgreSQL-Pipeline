"""
Comparação: config de fábrica do PostgreSQL 17 vs. config recomendada pelo modelo.

Diferente de cost_analysis.py (que compara configs REAIS observadas no dataset —
pior vs. mediana vs. melhor, tudo medido), este script injeta a config de
FÁBRICA do PostgreSQL 17 (nunca rodada de verdade nos benchmarks) como mais um
candidato, e usa o modelo pra prever onde ela ficaria posicionada frente às
configs reais do dataset. É uma estimativa (extrapolação em alguns parâmetros
fora do espaço de busca), não uma medição — o script deixa isso explícito.

Os valores de fábrica foram extraídos de um container postgres:17 vanilla via
`SELECT name, boot_val, unit FROM pg_settings`, não digitados de memória.

Uso:
    .venv/bin/python ml/baseline_comparison.py [--tier low|medium|high|all]
"""

import argparse

import pandas as pd

from ml.config import FEATURES_CSV, TIER_HARDWARE
from ml.cost_analysis import TIER_PRICE, TIER_INSTANCE, BRL_PER_USD
from ml.recommend import predict_scores, _load_specialists
from sampler.space_loader import load_stage_spaces

COMBO = "s1_s2_s3"  # config de fábrica define todos os 33 parâmetros

# ─────────────────────────────────────────────────────────────────────────
# Config de fábrica do PostgreSQL 17 — boot_val real, via container vanilla:
#   docker run --rm -d -e POSTGRES_PASSWORD=x postgres:17
#   SELECT name, boot_val, unit FROM pg_settings WHERE name IN (...)
# Convertida pra o mesmo formato do pg_config dos JSONs de benchmark.
# ─────────────────────────────────────────────────────────────────────────
DEFAULT_PG_CONFIG: dict = {
    # Stage 1
    "jit": "on",
    "random_page_cost": 4.0,
    "default_statistics_target": 100,
    "max_parallel_workers": 8,
    "max_parallel_workers_per_gather": 2,
    "shared_buffers": "128MB",
    "effective_cache_size": "4096MB",
    "work_mem": "4MB",
    "enable_hashagg": "on",
    "enable_bitmapscan": "on",
    "enable_nestloop": "on",
    "enable_parallel_hash": "on",
    "enable_sort": "on",
    # Stage 2
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000,
    "parallel_tuple_cost": 0.1,
    "min_parallel_table_scan_size": "8MB",
    "min_parallel_index_scan_size": "512kB",
    "join_collapse_limit": 8,
    "from_collapse_limit": 8,
    "hash_mem_multiplier": 2.0,
    "enable_mergejoin": "on",
    "enable_hashjoin": "on",
    # Stage 3
    "enable_memoize": "on",
    "enable_gathermerge": "on",
    "enable_incremental_sort": "on",
    "enable_material": "on",
    "enable_indexscan": "on",
    "enable_indexonlyscan": "on",
    "enable_parallel_append": "on",
    "parallel_leader_participation": "on",
}


def _out_of_range_params(tier: str) -> list[str]:
    """Lista os parâmetros de fábrica fora do espaço de busca do tier (LHS)."""
    from ml.recommend import _encode_param, _parse_memory_mb
    from ml.config import BOOL_PARAMS, MEMORY_PARAMS

    space = load_stage_spaces([1, 2, 3], tier)
    flat_space = {k: v for stage in space.values() for k, v in stage.items()}

    out: list[str] = []
    for key, default_val in DEFAULT_PG_CONFIG.items():
        spec = flat_space.get(key)
        if spec is None:
            continue
        mode = spec["choices"]["mode"]
        if mode == "range":
            rng = spec["choices"]["values"]
            v = float(default_val)
            if not (rng["min"] <= v <= rng["max"]):
                out.append(f"{key}={default_val} (espaço: {rng['min']}–{rng['max']})")
        else:  # discrete
            vals = spec["choices"]["values"]
            if key in MEMORY_PARAMS:
                dv = _parse_memory_mb(default_val)
                choices_mb = [_parse_memory_mb(v) for v in vals]
                if dv not in choices_mb:
                    out.append(f"{key}={default_val} (espaço: {vals})")
            elif key in BOOL_PARAMS:
                dv = _encode_param(key, default_val)
                choices_enc = [_encode_param(key, v) for v in vals]
                if dv not in choices_enc:
                    out.append(f"{key}={default_val} (espaço: {vals})")
            else:
                dv = _encode_param(key, default_val)
                choices_enc = [_encode_param(key, v) for v in vals]
                if dv not in choices_enc:
                    out.append(f"{key}={default_val} (espaço: {vals})")
    return out


def _real_candidates(df: pd.DataFrame, tier: str) -> tuple[list[dict], pd.DataFrame]:
    """Reconstrói os pg_configs reais do dataset pra um tier, combo=s1_s2_s3."""
    subset = df[(df["tier"] == tier) & (df["combination"] == COMBO)].reset_index(drop=True)
    cfg_cols = [c for c in df.columns if c.startswith("cfg_")]
    candidates: list[dict] = []
    for _, row in subset.iterrows():
        cfg: dict = {}
        for col in cfg_cols:
            v = row[col]
            if pd.notna(v):
                cfg[col.removeprefix("cfg_")] = v
        candidates.append(cfg)
    return candidates, subset


def compare_tier(tier: str, models: dict) -> None:
    df = pd.read_csv(FEATURES_CSV, low_memory=False)
    real_candidates, subset = _real_candidates(df, tier)

    if not real_candidates:
        print(f"\n[{tier}] Sem dados para combo={COMBO} — pulando.")
        return

    out_of_range = _out_of_range_params(tier)

    all_candidates = real_candidates + [DEFAULT_PG_CONFIG]
    default_idx = len(all_candidates) - 1

    result = predict_scores(all_candidates, tier, COMBO, models=models)
    result = result.sort_values("score", ascending=False).reset_index(drop=True)

    n = len(result)
    default_row = result[result["candidate_idx"] == default_idx].iloc[0]
    default_rank = int(result.index[result["candidate_idx"] == default_idx][0]) + 1
    default_pct = 100 * (1 - (default_rank - 1) / max(n - 1, 1))

    real_result = result[result["candidate_idx"] != default_idx].reset_index(drop=True)
    best_row = real_result.iloc[0]
    best_idx = int(best_row["candidate_idx"])
    best_real_geo_ms = subset.iloc[best_idx]["tpch_geo_mean_ms"]  # medido de verdade

    price = TIER_PRICE[tier]
    hw = TIER_HARDWARE[tier]

    print(f"\n{'═'*64}")
    print(f"  {tier.upper()}  ({TIER_INSTANCE[tier]}, SF={hw['sf']})  —  {n} configs candidatas")
    print(f"{'═'*64}")

    if out_of_range:
        print(f"  Parâmetros de fábrica FORA do espaço de busca deste tier ({len(out_of_range)}/33):")
        for p in out_of_range:
            print(f"    - {p}")
        print(f"  → a predição para estes parâmetros é extrapolação da árvore XGBoost")
        print(f"    (limitada aos valores-folha vistos no treino, não é linear).")
    else:
        print(f"  Todos os parâmetros de fábrica caem dentro do espaço de busca deste tier.")

    print(f"\n  Config de fábrica PG17 (predição do modelo):")
    print(f"    Posição no ranking : #{default_rank} de {n}  (percentil {default_pct:.0f}%)")
    print(f"    TPC-H geo previsto : {default_row['pred_geo_tpch_ms']:.1f} ms")
    print(f"    Cache hit previsto : {default_row['pred_cache_pct']:.1f}%")

    print(f"\n  Melhor config REAL do dataset (medida, não prevista):")
    print(f"    TPC-H geo medido   : {best_real_geo_ms:.1f} ms")
    print(f"    TPC-H geo previsto : {best_row['pred_geo_tpch_ms']:.1f} ms  (confere modelo x real)")

    speedup = default_row["pred_geo_tpch_ms"] / best_real_geo_ms
    cost_default_per_run = (default_row["pred_geo_tpch_ms"] / 1000 / 3600) * price
    cost_best_per_run = (best_real_geo_ms / 1000 / 3600) * price
    print(f"\n  RESULTADO [{tier}]")
    print(f"  {'─'*40}")
    if default_rank == 1:
        print(f"  ATENÇÃO: o modelo prevê a config de fábrica como a MELHOR das {n}")
        print(f"  candidatas neste tier — checar manualmente antes de citar (pode ser")
        print(f"  extrapolação da árvore em parâmetro fora do espaço de busca, não um")
        print(f"  resultado real de que 'não tunar' é ótimo).")
    print(f"  Speedup previsto (fábrica → recomendado)  : {speedup:.1f}×")
    print(f"  Custo/execução fábrica (previsto)         : USD {cost_default_per_run:.6f}")
    print(f"  Custo/execução recomendado (medido)       : USD {cost_best_per_run:.6f}")
    print(f"  Nota: custo aqui é só o tempo de query, não o custo fixo da instância/hora.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tier", default="all", choices=["low", "medium", "high", "all"])
    args = p.parse_args()

    print("═" * 64)
    print("  CONFIG DE FÁBRICA (PostgreSQL 17) vs. RECOMENDAÇÃO DO MODELO")
    print("  Câmbio: 1 USD = R$", BRL_PER_USD)
    print("═" * 64)

    models = _load_specialists()
    tiers = ["low", "medium", "high"] if args.tier == "all" else [args.tier]
    for tier in tiers:
        compare_tier(tier, models)


if __name__ == "__main__":
    main()
