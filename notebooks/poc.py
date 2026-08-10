"""
POC: Prova de Conceito do Meta-Modelo de Autotuning PostgreSQL

Treina os 4 especialistas XGBoost + ranker LightGBM + meta-modelo Ridge
sobre os dados atuais e reporta métricas de validação cruzada.

Uso:
    .venv/bin/python ml/poc.py
    .venv/bin/python ml/poc.py --no-shap   # pula SHAP (mais rápido)
"""

import argparse
import math
import warnings

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold, cross_val_predict
from sklearn.metrics import mean_squared_error
from sklearn.exceptions import UndefinedMetricWarning
import xgboost as xgb
try:
    import lightgbm as lgb
    _HAS_LGB = True
except OSError:
    _HAS_LGB = False
    print("[WARN] LightGBM indisponível (libgomp.so.1 ausente). Ranker pulado.")

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=UndefinedMetricWarning)

from ml.config import (
    FEATURES_CSV, S1_PARAMS, S2_PARAMS, S3_PARAMS,
    HW_COLS, ALL_FEATURES, XGB_PARAMS, CV_FOLDS, CV_SEED,
    SCORE_WEIGHTS,
)

CV = KFold(n_splits=CV_FOLDS, shuffle=True, random_state=CV_SEED)

# ─────────────────────────────────────────────────────────────────────────
# 1. Carga de dados
# ─────────────────────────────────────────────────────────────────────────

def load_data() -> pd.DataFrame:
    """Carrega features.csv e renomeia colunas para nomes curtos usados neste módulo."""
    df = pd.read_csv(FEATURES_CSV, low_memory=False)
    return df.rename(columns={
        "tpch_geo_mean_ms":         "geo_mean_tpch",
        "tpcds_geo_mean_ms":        "geo_mean_tpcds",
        "tpcds_queries_with_spill": "spill_tpcds",
    })


def rank_normalize(series: pd.Series) -> pd.Series:
    """Rank percentual dentro do grupo: 0=pior, 1=melhor."""
    return series.rank(pct=True)


def compute_score(df: pd.DataFrame) -> pd.Series:
    """
    Score composto por (tier, combination): pesos de ml/config.py:SCORE_WEIGHTS.
    Grupos separados garantem comparação justa (hardware igual, combo igual).
    """
    w_geo   = SCORE_WEIGHTS["geo_mean_tpch"]
    w_cache = SCORE_WEIGHTS["cache_hit_tpch"]
    score = pd.Series(np.nan, index=df.index)
    for (_, _), grp in df.groupby(["tier", "combination"]):
        idx = grp.index
        s = (
            w_geo   * rank_normalize(1.0 / grp["geo_mean_tpch"]) +
            w_cache * rank_normalize(grp["tpch_cache_hit_ratio"])
        )
        score.loc[idx] = s.values
    return score


# ─────────────────────────────────────────────────────────────────────────
# 2. Treino dos especialistas
# ─────────────────────────────────────────────────────────────────────────

def train_specialist(
    X: pd.DataFrame,
    y: pd.Series,
    name: str,
    transform: str = "log",
) -> tuple[np.ndarray, xgb.XGBRegressor]:
    """
    Treina XGBRegressor com KFold(5) e retorna (OOF predictions, modelo final).
    OOF predictions estão no espaço original (inverse transform aplicado).
    """
    mask = y.notna()
    Xm, ym = X.loc[mask], y.loc[mask]

    if transform == "log":
        yt = np.log(ym.clip(lower=1e-3))
    elif transform == "log1p":
        yt = np.log1p(ym)
    else:
        yt = ym.values

    model = xgb.XGBRegressor(**XGB_PARAMS)
    oof_t = cross_val_predict(model, Xm, yt, cv=CV)

    if transform == "log":
        oof = np.exp(oof_t)
    elif transform == "log1p":
        oof = np.expm1(oof_t)
    else:
        oof = oof_t

    model.fit(Xm, yt)

    rmse_orig = math.sqrt(mean_squared_error(ym, oof))
    rho, _    = spearmanr(ym, oof)

    print(f"  {name:22s}: RMSE={rmse_orig:10.2f} | Spearman ρ={rho:.3f} | n={len(ym)}")
    return oof, model, mask


# ─────────────────────────────────────────────────────────────────────────
# 3. Ranker LightGBM
# ─────────────────────────────────────────────────────────────────────────

def train_ranker(df: pd.DataFrame, X: pd.DataFrame, score: pd.Series):
    """
    LightGBM LambdaRank sobre grupos (tier, combination).
    Retorna predições OOF de relevância.
    """
    valid = score.notna()
    df_v  = df.loc[valid].copy()
    X_v   = X.loc[valid]
    s_v   = score.loc[valid]

    # Ordena por grupo (obrigatório para LightGBM Ranker)
    df_v["_group"] = df_v["tier"] + "/" + df_v["combination"]
    order = df_v.sort_values("_group").index
    df_s  = df_v.loc[order]
    X_s   = X_v.loc[order]
    s_s   = s_v.loc[order]

    # Tamanhos dos grupos
    group_sizes = df_s.groupby("_group", sort=True).size().tolist()

    # Relevância quantizada 0-9 (LightGBM ranker precisa de inteiros)
    relevance = (s_s.rank(pct=True) * 9).astype(int).clip(0, 9)

    lgb_params = dict(
        objective="lambdarank",
        metric="ndcg",
        ndcg_eval_at=[5],
        n_estimators=300,
        num_leaves=31,
        learning_rate=0.05,
        min_child_samples=3,
        verbose=-1,
        random_state=42,
    )
    ranker = lgb.LGBMRanker(**lgb_params)
    ranker.fit(X_s, relevance, group=group_sizes)

    oof_rank = ranker.predict(X_s)

    rho, _ = spearmanr(s_s, oof_rank)
    print(f"  {'ranker_lgbm':22s}: Spearman ρ={rho:.3f} | grupos={len(group_sizes)} | n={len(s_s)}")
    return ranker, order


# ─────────────────────────────────────────────────────────────────────────
# 4. Meta-modelo Ridge
# ─────────────────────────────────────────────────────────────────────────

def train_meta(oof_preds: dict[str, np.ndarray], score: pd.Series, masks: dict):
    """
    Ridge sobre OOF predictions dos 4 especialistas.
    Treina apenas nas rows com todos os targets disponíveis (done tasks).
    """
    # Alinha todos os OOF no índice do score
    common_idx = score.dropna().index
    # Filtra para rows onde todos os especialistas têm predição
    rows = []
    for i in common_idx:
        row = {}
        valid = True
        for name, (oof, mask) in oof_preds.items():
            if i in mask[mask].index:
                loc = list(mask[mask].index).index(i)
                row[name] = oof[loc]
            else:
                valid = False
                break
        if valid:
            rows.append((i, row))

    if not rows:
        print("  meta_ridge: sem dados suficientes para treinar")
        return None

    idx_meta = [r[0] for r in rows]
    X_meta   = pd.DataFrame([r[1] for r in rows], index=idx_meta)
    y_meta   = score.loc[idx_meta]

    meta = Ridge(alpha=1.0)
    oof_meta = cross_val_predict(meta, X_meta, y_meta, cv=CV)
    meta.fit(X_meta, y_meta)

    rho, _ = spearmanr(y_meta, oof_meta)
    rmse   = math.sqrt(mean_squared_error(y_meta, oof_meta))
    print(f"  {'meta_ridge':22s}: RMSE={rmse:.4f} | Spearman ρ={rho:.3f} | n={len(y_meta)}")
    print(f"    Coefs: " + " ".join(f"{k}={v:.3f}" for k, v in zip(X_meta.columns, meta.coef_)))
    return meta


# ─────────────────────────────────────────────────────────────────────────
# 5. Ablação por stage
# ─────────────────────────────────────────────────────────────────────────

def ablation_study(df: pd.DataFrame, X: pd.DataFrame, y_geo: pd.Series):
    """
    Compara RMSE e Spearman ρ treinando M1 (geo_mean_tpch) com diferentes
    conjuntos de features: resultado central do estudo de ablação do TCC.
    """
    print("\n── Ablação: impacto de adicionar stages ao modelo de performance ──")
    groups = {
        "S1 only  (13 feat)": S1_PARAMS + HW_COLS,
        "S1+S2    (25 feat)": S1_PARAMS + S2_PARAMS + HW_COLS,
        "S1+S2+S3 (33 feat)": S1_PARAMS + S2_PARAMS + S3_PARAMS + HW_COLS,
    }
    valid = y_geo.notna()
    yt    = np.log(y_geo[valid].clip(lower=1))

    for label, feat_cols in groups.items():
        cols = [c for c in feat_cols if c in X.columns]
        Xg   = X.loc[valid, cols]
        m    = xgb.XGBRegressor(**XGB_PARAMS)
        oof  = cross_val_predict(m, Xg, yt, cv=CV)
        rmse = math.sqrt(mean_squared_error(np.exp(yt), np.exp(oof)))
        rho, _ = spearmanr(yt, oof)
        print(f"  {label}: RMSE={rmse:8.1f}ms | Spearman ρ={rho:.3f}")


# ─────────────────────────────────────────────────────────────────────────
# 6. SHAP: importância das features
# ─────────────────────────────────────────────────────────────────────────

def shap_report(model: xgb.XGBRegressor, X: pd.DataFrame, y: pd.Series, top_n: int = 15):
    """Imprime top-N features por SHAP mean |value|."""
    try:
        import shap
    except ImportError:
        print("  shap não instalado: pular")
        return

    mask  = y.notna()
    Xm    = X.loc[mask]
    explainer = shap.TreeExplainer(model)
    sv        = explainer.shap_values(Xm)
    importance = pd.Series(
        np.abs(sv).mean(axis=0),
        index=Xm.columns,
    ).sort_values(ascending=False)

    print(f"\n── SHAP top-{top_n} features (M1: geo_mean_tpch) ──")
    for feat, val in importance.head(top_n).items():
        stage = "S1" if feat in S1_PARAMS else ("S2" if feat in S2_PARAMS else ("S3" if feat in S3_PARAMS else "HW"))
        bar = "█" * int(val / importance.iloc[0] * 20)
        print(f"  [{stage}] {feat.replace('cfg_',''):32s} {val:.4f}  {bar}")


# ─────────────────────────────────────────────────────────────────────────
# 7. Verificação de qualidade do ranking
# ─────────────────────────────────────────────────────────────────────────

def ranking_quality(df: pd.DataFrame, score: pd.Series, oof_geo: np.ndarray, geo_mask):
    """
    Para cada grupo (tier, combo): a config com menor geo_mean predito
    está no top-K real? Mede precisão@1 e precisão@3.
    """
    valid_idx  = geo_mask[geo_mask].index
    geo_pred   = pd.Series(oof_geo, index=valid_idx)
    true_score = score.loc[valid_idx]

    p1_hits, p3_hits, total = 0, 0, 0
    for (tier, combo), grp in df.loc[valid_idx].groupby(["tier", "combination"]):
        if len(grp) < 4:
            continue
        idx = grp.index
        pred_rank  = geo_pred.loc[idx].rank()          # menor geo = rank 1 = melhor
        true_rank  = (1 - true_score.loc[idx]).rank()  # maior score = menor rank = melhor
        best_pred  = pred_rank.idxmin()
        top3_pred  = set(pred_rank.nsmallest(3).index)
        top3_true  = set(true_rank.nsmallest(3).index)
        p1_hits   += int(true_rank[best_pred] <= 3)    # best pred está no top-3 real?
        p3_hits   += len(top3_pred & top3_true)        # interseção top-3
        total     += 1

    if total:
        print(f"\n── Qualidade de ranking (M1 geo_mean → ranking real) ──")
        print(f"  Precisão@1→top3 : {p1_hits}/{total} grupos ({p1_hits/total*100:.0f}%): melhor predito está no top-3 real")
        print(f"  Overlap top-3   : {p3_hits/(total*3)*100:.0f}% de concordância média entre top-3 predito e real")


# ─────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────

def main(run_shap: bool = True):
    print("═" * 60)
    print("  POC: Meta-Modelo PostgreSQL Autotuning")
    print("═" * 60)

    # ── Dados ──────────────────────────────────────────────────────────
    print("\n[1/5] Carregando dados...")
    df = load_data()
    print(f"  {len(df)} tarefas | {df['status'].value_counts().to_dict()}")

    X     = df[ALL_FEATURES].copy()
    score = compute_score(df)
    print(f"  Score composto calculado: {score.notna().sum()} tasks válidas")

    # ── Especialistas ───────────────────────────────────────────────────
    print("\n[2/5] Treinando especialistas (KFold-5)...")
    oof_preds = {}

    oof_geo_h,  m1, mask_geo_h  = train_specialist(X, df["geo_mean_tpch"],        "M1_geo_tpch",   "log")
    oof_geo_ds, m2, mask_geo_ds = train_specialist(X, df["geo_mean_tpcds"],       "M2_geo_tpcds",  "log")
    oof_cache,  m3, mask_cache  = train_specialist(X, df["tpch_cache_hit_ratio"], "M3_cache_tpch", "none")
    oof_spill,  m4, mask_spill  = train_specialist(X, df["spill_tpcds"],          "M4_spill_tpcds","log1p")

    oof_preds = {
        "oof_geo_h":  (oof_geo_h,  mask_geo_h),
        "oof_geo_ds": (oof_geo_ds, mask_geo_ds),
        "oof_cache":  (oof_cache,  mask_cache),
        "oof_spill":  (oof_spill,  mask_spill),
    }

    # ── Ranker ──────────────────────────────────────────────────────────
    print("\n[3/5] Treinando ranker LightGBM...")
    if _HAS_LGB:
        ranker, _ = train_ranker(df, X, score)
    else:
        print("  ranker_lgbm: pulado (libgomp ausente: instale com: sudo apt install libgomp1)")

    # ── Meta-modelo ─────────────────────────────────────────────────────
    print("\n[4/5] Treinando meta-modelo Ridge...")
    train_meta(oof_preds, score, {})

    # ── Análises ────────────────────────────────────────────────────────
    print("\n[5/5] Análises de qualidade...")
    ablation_study(df, X, df["geo_mean_tpch"])
    ranking_quality(df, score, oof_geo_h, mask_geo_h)

    if run_shap:
        shap_report(m1, X, df["geo_mean_tpch"])

    print("\n" + "═" * 60)
    print("  POC concluída.")
    print("═" * 60)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--no-shap", action="store_true", help="Pula SHAP (mais rápido)")
    args = p.parse_args()
    main(run_shap=not args.no_shap)
