"""
Avaliação do meta-modelo — ablação por stage, SHAP e qualidade de ranking.

Lê os modelos treinados em data/models/ e os dados de data/processed/features.csv,
e produz três relatórios:

  1. Tabela de ablação — RMSE e Spearman ρ de M1 (geo_mean_tpch) treinado com
     S1 / S1+S2 / S1+S2+S3. Resultado central do estudo de ablação do TCC.

  2. SHAP — importância de features por stage para M1. Gráfico ASCII no
     terminal e dados salvos em data/models/shap_importance.json.

  3. Qualidade de ranking — dado o modelo M1, com que frequência a melhor
     config predita está entre as top-3 reais de cada (tier, combo)?

Uso:
    .venv/bin/python ml/evaluate.py
    .venv/bin/python ml/evaluate.py --no-shap    # pula SHAP (mais rápido)
    .venv/bin/python ml/evaluate.py --retrain    # re-treina em vez de carregar modelos
"""

import argparse
import json
import math
import warnings

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import mean_squared_error
from sklearn.model_selection import KFold, cross_val_predict
import xgboost as xgb

warnings.filterwarnings("ignore", category=UserWarning)

from ml.config import (
    FEATURES_CSV, MODELS_DIR, ALL_FEATURES,
    S1_PARAMS, S2_PARAMS, S3_PARAMS,
    TARGETS, SCORE_WEIGHTS, XGB_PARAMS, CV_FOLDS, CV_SEED,
    ABLATION_GROUPS,
)
from ml.train import (
    apply_transform, invert_transform, compute_score,
    train_specialist, load_model, print_metric,
)

ROOT = FEATURES_CSV.parents[2]  # data/processed/features.csv → project root


# ─────────────────────────────────────────────────────────────────────────
# 1. Ablação
# ─────────────────────────────────────────────────────────────────────────

def run_ablation(df: pd.DataFrame) -> dict:
    """
    Treina M1 (geo_mean_tpch) com diferentes conjuntos de features e compara
    RMSE e Spearman ρ. Resultado central do estudo de ablação.
    """
    print("\n── Ablação: impacto de adicionar stages ao modelo de performance ──")
    print(f"  {'Grupo':<22} {'Features':>8}  {'RMSE (ms)':>10}  {'Spearman ρ':>10}")
    print("  " + "─" * 58)

    col       = TARGETS["geo_mean_tpch"]["column"]
    transform = TARGETS["geo_mean_tpch"]["transform"]
    cv        = KFold(n_splits=CV_FOLDS, shuffle=True, random_state=CV_SEED)
    mask      = df[col].notna()
    ym        = df.loc[mask, col]
    yt        = apply_transform(ym, transform)

    results = {}
    for label, feat_cols in ABLATION_GROUPS.items():
        cols_present = [c for c in feat_cols if c in df.columns]
        Xg      = df.loc[mask, cols_present]
        model   = xgb.XGBRegressor(**XGB_PARAMS)
        oof_t   = cross_val_predict(model, Xg, yt, cv=cv)
        oof_raw = invert_transform(oof_t, transform)
        rmse    = math.sqrt(mean_squared_error(ym, oof_raw))
        rho, _  = spearmanr(ym, oof_raw)
        n_feat  = len(cols_present)

        bar = "█" * int(rho * 25)
        print(f"  {label:<22} {n_feat:>8}  {rmse:>10.1f}  {rho:>10.3f}  {bar}")
        results[label] = {"n_features": n_feat, "rmse": round(rmse, 2), "spearman_rho": round(rho, 4)}

    ablation_path = MODELS_DIR / "ablation_results.json"
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    with open(ablation_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  Salvo: {ablation_path.relative_to(ROOT)}")
    return results


# ─────────────────────────────────────────────────────────────────────────
# 2. SHAP
# ─────────────────────────────────────────────────────────────────────────

def run_shap(df: pd.DataFrame, model: xgb.XGBRegressor, top_n: int = 18) -> dict:
    """
    Calcula SHAP values para M1 (geo_mean_tpch) e reporta importância por
    feature e por stage. Salva resultados em data/models/shap_importance.json.
    """
    try:
        import shap
    except ImportError:
        print("  shap não instalado. Pular.")
        return {}

    col  = TARGETS["geo_mean_tpch"]["column"]
    mask = df[col].notna()
    Xm   = df.loc[mask, ALL_FEATURES]

    explainer  = shap.TreeExplainer(model)
    sv         = explainer.shap_values(Xm)
    importance = pd.Series(np.abs(sv).mean(axis=0), index=Xm.columns).sort_values(ascending=False)

    print(f"\n── SHAP top-{top_n} features — M1 (geo_mean TPC-H) ──")
    print(f"  {'Feature':<38} {'SHAP':>7}  Stage")
    print("  " + "─" * 55)
    max_v = importance.iloc[0]
    for feat, val in importance.head(top_n).items():
        stage = ("S1" if feat in S1_PARAMS else
                 "S2" if feat in S2_PARAMS else
                 "S3" if feat in S3_PARAMS else "HW")
        bar = "█" * int(val / max_v * 22)
        print(f"  {feat.replace('cfg_', ''):38s} {val:7.4f}  {stage}  {bar}")

    # Importância agregada por stage
    by_stage = {"S1": 0.0, "S2": 0.0, "S3": 0.0, "HW": 0.0}
    for feat, val in importance.items():
        s = ("S1" if feat in S1_PARAMS else
             "S2" if feat in S2_PARAMS else
             "S3" if feat in S3_PARAMS else "HW")
        by_stage[s] += val
    total = sum(by_stage.values())

    print(f"\n  Importância por stage (% do total SHAP):")
    for stage, val in by_stage.items():
        pct = val / total * 100
        bar = "█" * int(pct / 100 * 30)
        print(f"    {stage}: {pct:5.1f}%  {bar}")

    result = {
        "per_feature": {f: round(float(v), 6) for f, v in importance.items()},
        "per_stage":   {s: round(v / total * 100, 2) for s, v in by_stage.items()},
    }
    shap_path = MODELS_DIR / "shap_importance.json"
    with open(shap_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\n  Salvo: {shap_path.relative_to(ROOT)}")
    return result


# ─────────────────────────────────────────────────────────────────────────
# 3. Otimização dos pesos do score
# ─────────────────────────────────────────────────────────────────────────

def optimize_score_weights(
    df: pd.DataFrame,
    oof_geo: pd.Series,
    oof_cache: pd.Series,
) -> dict[str, float]:
    """
    Busca os pesos ótimos (w_geo, w_cache) que maximizam o Spearman ρ
    entre o score predito e o score real, usando as OOF predictions.

    Busca em grade: w_geo ∈ [0.40, 0.95] passo 0.05, w_cache = 1 - w_geo.
    Usa OOF predictions (honestas, sem data leakage).

    Retorna dict com os pesos ótimos encontrados.
    """
    from ml.config import TARGETS

    common = oof_geo.index.intersection(oof_cache.index)
    df_c   = df.loc[common].copy()
    df_c["pred_geo"]   = oof_geo.loc[common]
    df_c["pred_cache"] = oof_cache.loc[common]

    # Score real com pesos padrão (para comparação)
    score_real = pd.Series(np.nan, index=common)
    for (_, _), grp in df_c.groupby(["tier", "combination"]):
        idx = grp.index
        geo_real   = df.loc[idx, TARGETS["geo_mean_tpch"]["column"]]
        cache_real = df.loc[idx, TARGETS["cache_hit_tpch"]["column"]]
        if geo_real.isna().any() or cache_real.isna().any():
            continue
        score_real.loc[idx] = (
            (1.0 / geo_real.clip(1e-9)).rank(pct=True) * SCORE_WEIGHTS["geo_mean_tpch"] +
            cache_real.rank(pct=True) * SCORE_WEIGHTS["cache_hit_tpch"]
        ).values

    valid = score_real.notna()

    best_rho   = -1.0
    best_w_geo = SCORE_WEIGHTS["geo_mean_tpch"]
    results    = []

    for w_geo_int in range(40, 96, 5):
        w_geo   = w_geo_int / 100
        w_cache = round(1.0 - w_geo, 2)

        score_pred = pd.Series(np.nan, index=common)
        for (_, _), grp in df_c.groupby(["tier", "combination"]):
            idx = grp.index
            sp = (w_geo   * (1.0 / grp["pred_geo"].clip(1e-9)).rank(pct=True) +
                  w_cache * grp["pred_cache"].rank(pct=True))
            score_pred.loc[idx] = sp.values

        rho, _ = spearmanr(score_real[valid], score_pred[valid])
        results.append((w_geo, w_cache, rho))
        if rho > best_rho:
            best_rho   = rho
            best_w_geo = w_geo

    best_w_cache = round(1.0 - best_w_geo, 2)

    print(f"\n── Otimização de pesos do score ──")
    print(f"  {'w_geo':>6}  {'w_cache':>7}  {'ρ':>7}")
    print("  " + "─" * 24)
    for w_g, w_c, r in results:
        marker = " ← ótimo" if w_g == best_w_geo else ""
        print(f"  {w_g:6.2f}  {w_c:7.2f}  {r:7.4f}{marker}")

    _w_geo = SCORE_WEIGHTS["geo_mean_tpch"]
    _w_cache = SCORE_WEIGHTS["cache_hit_tpch"]
    print(f"\n  Pesos padrão  ({_w_geo}/{_w_cache}): ρ={next(r for g,c,r in results if g==_w_geo):.4f}")
    print(f"  Pesos ótimos  ({best_w_geo:.2f}/{best_w_cache:.2f}): ρ={best_rho:.4f}")

    opt_weights = {"geo_mean_tpch": best_w_geo, "cache_hit_tpch": best_w_cache}
    weights_path = MODELS_DIR / "optimal_score_weights.json"
    with open(weights_path, "w") as f:
        json.dump(opt_weights, f, indent=2)
    print(f"\n  Salvo: {weights_path.relative_to(ROOT)}")
    return opt_weights


# ─────────────────────────────────────────────────────────────────────────
# 4. Qualidade do ranking
# ─────────────────────────────────────────────────────────────────────────

def run_ranking_quality(
    df: pd.DataFrame,
    score: pd.Series,
    oof_geo: pd.Series,
    oof_cache: pd.Series,
) -> dict:
    """
    Para cada grupo (tier, combo), avalia se o score predito identifica
    as melhores configs reais. Métricas:

    - Precisão@1→top3: a melhor config predita está no top-3 real?
    - Overlap top-3: quantas das 3 melhores preditas estão nas 3 melhores reais?
    - Score ρ: Spearman ρ entre score predito e score real dentro do grupo.
    """
    print("\n── Qualidade de ranking (score direto das predições OOF) ──")

    common = oof_geo.index.intersection(oof_cache.index).intersection(score.dropna().index)
    df_c   = df.loc[common].copy()
    df_c["pred_geo"]   = oof_geo.loc[common]
    df_c["pred_cache"] = oof_cache.loc[common]

    # Score predito: aplicar a fórmula do score nas predições OOF
    score_pred = pd.Series(np.nan, index=common)
    for (_, _), grp in df_c.groupby(["tier", "combination"]):
        idx = grp.index
        sp  = (SCORE_WEIGHTS["geo_mean_tpch"] * (1.0 / grp["pred_geo"].clip(lower=1e-9)).rank(pct=True) +
               SCORE_WEIGHTS["cache_hit_tpch"] * grp["pred_cache"].rank(pct=True))
        score_pred.loc[idx] = sp.values

    rho_global, _ = spearmanr(score.loc[common], score_pred.loc[common])

    p1_hits, p3_hits, total = 0, 0, 0
    group_rhos: list[float] = []

    for (tier, combo), grp in df_c.groupby(["tier", "combination"]):
        if len(grp) < 4:
            continue
        idx        = grp.index
        s_real     = score.loc[idx]
        s_pred     = score_pred.loc[idx]

        top1_pred  = s_pred.idxmax()
        top3_pred  = set(s_pred.nlargest(3).index)
        top3_real  = set(s_real.nlargest(3).index)

        p1_hits   += int(top1_pred in top3_real)
        p3_hits   += len(top3_pred & top3_real)
        total     += 1

        rho_g, _  = spearmanr(s_real, s_pred)
        if not np.isnan(rho_g):
            group_rhos.append(rho_g)

    avg_group_rho = float(np.mean(group_rhos)) if group_rhos else 0.0

    print(f"  Score global ρ       : {rho_global:.3f}")
    print(f"  Score intra-grupo ρ  : {avg_group_rho:.3f} (média por grupo)")
    print(f"  Precisão@1→top3      : {p1_hits}/{total} ({p1_hits/total*100:.0f}%) "
          f"— melhor predito está no top-3 real")
    print(f"  Overlap top-3        : {p3_hits/(total*3)*100:.0f}% de concordância")

    result = {
        "score_global_rho":    round(rho_global, 4),
        "score_intra_rho_avg": round(avg_group_rho, 4),
        "precision_at1_top3":  round(p1_hits / total, 4) if total else 0,
        "top3_overlap_pct":    round(p3_hits / (total * 3) * 100, 2) if total else 0,
        "n_groups":            total,
    }
    rank_path = MODELS_DIR / "ranking_quality.json"
    with open(rank_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\n  Salvo: {rank_path.relative_to(ROOT)}")
    return result


# ─────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────

def run(run_shap_flag: bool = True, retrain: bool = False) -> None:
    print("═" * 60)
    print("  AVALIAÇÃO — Meta-Modelo PostgreSQL Autotuning")
    print("═" * 60)

    df    = pd.read_csv(FEATURES_CSV, low_memory=False)
    X     = df[ALL_FEATURES]
    score = compute_score(df)

    # Carrega ou re-treina M1 e M3
    model_name_m1 = TARGETS["geo_mean_tpch"]["model_name"]
    model_name_m3 = TARGETS["cache_hit_tpch"]["model_name"]

    m1_path = MODELS_DIR / f"{model_name_m1}.ubj"
    m3_path = MODELS_DIR / f"{model_name_m3}.ubj"

    oof_path = MODELS_DIR / "oof_predictions.csv"

    if retrain or not m1_path.exists():
        print("\nTreinando M1 e M3 (--retrain ou modelos ausentes)...")
        oof_m1, m1, _ = train_specialist(df, X, "geo_mean_tpch")
        oof_m3, m3, _ = train_specialist(df, X, "cache_hit_tpch")
    elif oof_path.exists():
        # Carrega OOF salvo pelo train.py — evita re-rodar cross_val_predict
        print(f"\nCarregando modelos e OOF de {MODELS_DIR.relative_to(ROOT)}...")
        m1  = load_model(model_name_m1)
        m3  = load_model(model_name_m3)
        oof = pd.read_csv(oof_path, index_col="row_idx")

        oof_m1 = oof["geo_mean_tpch"].dropna()
        oof_m3 = oof["cache_hit_tpch"].dropna()

        for tname, oof_s, unit in [
            ("geo_mean_tpch",  oof_m1, " ms"),
            ("cache_hit_tpch", oof_m3, "%"),
        ]:
            cfg  = TARGETS[tname]
            mask = df[cfg["column"]].notna()
            ym   = df.loc[mask, cfg["column"]]
            common = ym.index.intersection(oof_s.index)
            rho, _ = spearmanr(ym.loc[common], oof_s.loc[common])
            rmse   = math.sqrt(mean_squared_error(ym.loc[common], oof_s.loc[common]))
            print_metric(cfg["model_name"], rmse, rho, len(common), unit)
    else:
        # OOF não encontrado — re-treina (run train.py primeiro)
        print("\nOOF não encontrado. Re-treinando M1 e M3...")
        print("  Dica: execute 'python ml/train.py' antes para evitar isso.")
        oof_m1, m1, _ = train_specialist(df, X, "geo_mean_tpch")
        oof_m3, m3, _ = train_specialist(df, X, "cache_hit_tpch")

    # ── Relatórios ────────────────────────────────────────────────────
    run_ablation(df)
    if run_shap_flag:
        run_shap(df, m1)
    opt_weights = optimize_score_weights(df, oof_m1, oof_m3)
    run_ranking_quality(df, score, oof_m1, oof_m3)

    # Qualidade com pesos otimizados (comparação)
    score_opt = compute_score(df, weights=opt_weights)
    print("\n── Qualidade com pesos OTIMIZADOS (comparação) ──")
    run_ranking_quality(df, score_opt, oof_m1, oof_m3)

    print("\n" + "═" * 60)
    print("  Avaliação concluída.")
    print("═" * 60)


def main() -> None:
    p = argparse.ArgumentParser(description="Avalia o meta-modelo treinado.")
    p.add_argument("--no-shap",  action="store_true", help="Pula análise SHAP.")
    p.add_argument("--retrain",  action="store_true", help="Re-treina modelos em vez de carregar.")
    args = p.parse_args()
    run(run_shap_flag=not args.no_shap, retrain=args.retrain)


if __name__ == "__main__":
    main()
