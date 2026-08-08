"""
Análise de custo-efetividade: config padrão vs meta-modelo.

IMPORTANTE — scale factors por tier:
    low=SF1, medium=SF2, high=SF4

Tempos de execução NÃO são diretamente comparáveis entre tiers
porque cada tier processa volumes de dados diferentes.

Duas análises independentes:
  A) Dentro do mesmo tier: quanto o tuning (meta-modelo) economiza
     frente a uma configuração ruim/aleatória?
  B) Entre tiers: custo por unidade de SF (custo-eficiência de hardware),
     usando a melhor configuração encontrada em cada tier.

Uso:
    python ml/cost_analysis.py [--features data/processed/features.csv]
"""

import argparse
import pandas as pd
import numpy as np
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────────
# EC2 on-demand us-east-1 (mai/2026) — compute-optimized (c5)
# ──────────────────────────────────────────────────────────────────────────────
TIER_PRICE = {
    "low":    0.085,   # c5.large  (2 vCPU,  4 GB)
    "medium": 0.170,   # c5.xlarge (4 vCPU,  8 GB)
    "high":   0.340,   # c5.2xlarge(8 vCPU, 16 GB)
}
TIER_INSTANCE = {
    "low":    "c5.large  (2 vCPU)",
    "medium": "c5.xlarge (4 vCPU)",
    "high":   "c5.2xlarge(8 vCPU)",
}
BRL_PER_USD = 5.75
EXEC_PER_MONTH = 30   # relatório diário


def load_data(features_path: Path) -> pd.DataFrame:
    df = pd.read_csv(features_path)
    df = df[df["status"] == "done"].copy()
    df["cost_usd"] = (df["duration_s"] / 3600) * df["tier"].map(TIER_PRICE)
    df["cost_brl"] = df["cost_usd"] * BRL_PER_USD
    # custo normalizado por SF (permite comparação entre tiers)
    df["cost_per_sf"] = df["cost_usd"] / df["sf"]
    return df


def pick_row(df: pd.DataFrame, tier: str, pct: float) -> pd.Series:
    """Linha no percentil pct de (duration_s) dentro de um tier."""
    sub = df[df["tier"] == tier].sort_values("duration_s")
    idx = int(np.clip(pct * (len(sub) - 1), 0, len(sub) - 1))
    return sub.iloc[idx]


def best_by_geo(df: pd.DataFrame, tier: str) -> pd.Series:
    """Configuração com menor geo_mean_tpch no tier (melhor desempenho real)."""
    sub = df[df["tier"] == tier]
    return sub.loc[sub["tpch_geo_mean_ms"].idxmin()]


def summarize(row: pd.Series, label: str, baseline_cost: float | None = None) -> dict:
    c = row["cost_usd"]
    savings = f"  ← {(1 - c/baseline_cost)*100:.0f}% mais barato" if baseline_cost and c < baseline_cost else ""
    return {
        "label":       label,
        "tier":        row["tier"],
        "instance":    TIER_INSTANCE[row["tier"]],
        "combo":       row["combination"],
        "sf":          int(row["sf"]),
        "duration_min": row["duration_s"] / 60,
        "tpch_s":      row["tpch_geo_mean_ms"] / 1000,
        "tpcds_s":     row["tpcds_geo_mean_ms"] / 1000,
        "cost_run_usd": c,
        "cost_run_brl": row["cost_brl"],
        "cost_month_brl": row["cost_brl"] * EXEC_PER_MONTH,
        "cost_per_sf":  row["cost_per_sf"],
        "savings":     savings,
    }


def print_row(s: dict):
    print(f"\n  {'─'*56}")
    print(f"  {s['label']}")
    print(f"  {'─'*56}")
    print(f"  Instância : {s['instance']}  |  Scale Factor: SF={s['sf']}")
    print(f"  Stage     : {s['combo']}")
    print(f"  Duração   : {s['duration_min']:.1f} min")
    print(f"  TPC-H geo : {s['tpch_s']:.2f} s   TPC-DS geo: {s['tpcds_s']:.2f} s")
    print(f"  Custo/run : USD {s['cost_run_usd']:.4f}  /  R$ {s['cost_run_brl']:.3f}")
    print(f"  Custo/mês : R$ {s['cost_month_brl']:.2f}{s['savings']}")


def analysis_within_tier(df: pd.DataFrame, tier: str):
    print(f"\n{'═'*60}")
    print(f"  ANÁLISE A — Tuning dentro do {tier.upper()} tier (SF={int(df[df['tier']==tier]['sf'].iloc[0])})")
    print(f"  Mesma instância, configs diferentes → comparação direta")
    print(f"{'═'*60}")

    bad    = summarize(pick_row(df, tier, 0.90), f"Config ruim (p90 duração)  [{tier}]")
    median = summarize(pick_row(df, tier, 0.50), f"Config mediana / random    [{tier}]", bad["cost_run_usd"])
    best   = summarize(best_by_geo(df, tier),    f"Melhor config real (ótimo) [{tier}]", bad["cost_run_usd"])

    baseline = bad["cost_run_usd"]
    for s in [bad, median, best]:
        print_row(s)

    delta_best = (1 - best["cost_run_usd"] / bad["cost_run_usd"]) * 100
    delta_med  = (1 - median["cost_run_usd"] / bad["cost_run_usd"]) * 100
    speedup    = bad["tpch_s"] / best["tpch_s"]

    print(f"""
  RESULTADO [{tier}]
  ─────────────────────────────────────────────
  Config ruim → melhor config: {delta_best:.0f}% de redução de custo
  Config aleatória → melhor  : {delta_med:.0f}% de redução de custo
  Speedup TPC-H (ruim → ótimo): {speedup:.1f}×
  Economia mensal (ruim → ótimo): R$ {(bad['cost_month_brl'] - best['cost_month_brl']):.2f}
""")

    return bad, median, best


def analysis_across_tiers(df: pd.DataFrame):
    print(f"\n{'═'*60}")
    print(f"  ANÁLISE B — Eficiência entre tiers (normalizado por SF)")
    print(f"  SF diferentes → comparação por 'custo por unidade de dado'")
    print(f"{'═'*60}")
    print(f"  Nota: SF=1→1GB, SF=2→2GB, SF=4→4GB de dados TPC")

    rows = []
    for tier in ["low", "medium", "high"]:
        best_row = best_by_geo(df, tier)
        s = summarize(best_row, f"Melhor config [{tier}]")
        rows.append(s)
        print(f"\n  {s['label']}")
        print(f"    Instância  : {s['instance']}  SF={s['sf']}")
        print(f"    Custo/run  : USD {s['cost_run_usd']:.4f}")
        print(f"    Custo/SF   : USD {s['cost_per_sf']:.4f}  ← métrica comparável")
        print(f"    Custo/mês  : R$ {s['cost_month_brl']:.2f}")
        print(f"    TPC-H geo  : {s['tpch_s']:.2f}s  TPC-DS geo: {s['tpcds_s']:.2f}s")

    print(f"\n  COMPARAÇÃO (custo/SF — menor é mais eficiente)")
    print(f"  {'Tier':<10} {'$/run':>8} {'$/SF':>8} {'R$/mês':>8} {'TPC-H(s)':>10}")
    print(f"  {'─'*52}")
    for s in rows:
        print(f"  {s['tier']:<10} ${s['cost_run_usd']:>6.4f}  ${s['cost_per_sf']:>6.4f}  R${s['cost_month_brl']:>6.2f}  {s['tpch_s']:>8.2f}s")

    best_sf = min(rows, key=lambda x: x["cost_per_sf"])
    print(f"\n  Tier mais custo-eficiente por SF processado: {best_sf['tier'].upper()}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", default="data/processed/features.csv")
    parser.add_argument("--tier",     default=None,
                        help="Só analisa um tier (low/medium/high). Padrão: todos.")
    args = parser.parse_args()

    df = load_data(Path(args.features))

    print("\n" + "═" * 60)
    print("  CUSTO-EFETIVIDADE — OLAP PostgreSQL em Nuvem")
    print("═" * 60)
    print(f"  Dataset : {len(df)} execuções | Câmbio: 1 USD = R$ {BRL_PER_USD}")
    print(f"  Preços  : low=${TIER_PRICE['low']}/hr  medium=${TIER_PRICE['medium']}/hr  high=${TIER_PRICE['high']}/hr")
    print(f"  SFs     : low=1  medium=2  high=4  (NÃO comparáveis diretamente)")

    tiers = [args.tier] if args.tier else ["low", "medium", "high"]
    for tier in tiers:
        analysis_within_tier(df, tier)

    analysis_across_tiers(df)


if __name__ == "__main__":
    main()
