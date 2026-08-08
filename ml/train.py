"""
Treinamento dos modelos do meta-modelo de autotuning PostgreSQL.

Treina os 4 especialistas XGBoost (nível 1) e o ranker XGBoost (nível 2),
salva todos os modelos e as predições OOF para uso no evaluate.py.

O ranker usa XGBoost rank:ndcg — não depende de libgomp1.

Saída em data/models/:
    m1_geo_tpch.ubj       — XGBoost M1 (performance TPC-H)
    m2_geo_tpcds.ubj      — XGBoost M2 (performance TPC-DS)
    m3_cache_tpch.ubj     — XGBoost M3 (cache hit TPC-H)
    m4_spill_tpcds.ubj    — XGBoost M4 (spill TPC-DS)
    ranker.ubj            — XGBoost Ranker (rank:ndcg)
    oof_predictions.csv   — predições out-of-fold de todos os especialistas
    train_metrics.json    — RMSE e Spearman ρ de cada modelo

Uso:
    .venv/bin/python ml/train.py
    .venv/bin/python ml/train.py --no-ranker   # pula ranker
"""

import argparse
import json
import math
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import mean_squared_error
from sklearn.model_selection import KFold, cross_val_predict
import xgboost as xgb

warnings.filterwarnings("ignore", category=UserWarning)

from ml.config import (
    FEATURES_CSV, MODELS_DIR, ALL_FEATURES, TARGETS,
    SCORE_WEIGHTS, XGB_PARAMS, XGB_RANKER_PARAMS,
    CV_FOLDS, CV_SEED,
)

ROOT = FEATURES_CSV.parents[2]  # data/processed/features.csv → project root


# ─────────────────────────────────────────────────────────────────────────
# Utilitários
# ─────────────────────────────────────────────────────────────────────────

def apply_transform(y: pd.Series, transform: str) -> np.ndarray:
    if transform == "log":
        return np.log(y.clip(lower=1.0))
    if transform == "log1p":
        return np.log1p(y)
    return y.values


def invert_transform(y_t: np.ndarray, transform: str) -> np.ndarray:
    if transform == "log":
        return np.exp(y_t)
    if transform == "log1p":
        return np.expm1(y_t)
    return y_t


def rank_normalize(series: pd.Series) -> pd.Series:
    return series.rank(pct=True)


def compute_score(df: pd.DataFrame, weights: dict[str, float] | None = None) -> pd.Series:
    """
    Score composto por grupo (tier, combination).

    weights: substitui SCORE_WEIGHTS quando fornecido — usado pela otimização
             de pesos em evaluate.py. Deve somar 1.0.
    """
    w = weights if weights is not None else SCORE_WEIGHTS
    score = pd.Series(np.nan, index=df.index)
    for (_, _), grp in df.groupby(["tier", "combination"]):
        idx = grp.index
        s   = pd.Series(0.0, index=idx)
        for tname, weight in w.items():
            cfg = TARGETS[tname]
            col = cfg["column"]
            if col not in df.columns or grp[col].isna().all():
                continue
            vals = grp[col]
            if cfg["direction"] == "minimize":
                s += weight * rank_normalize(1.0 / vals.clip(lower=1e-9))
            else:
                s += weight * rank_normalize(vals)
        score.loc[idx] = s.values
    return score


def print_metric(name: str, rmse: float, rho: float, n: int, unit: str = "") -> None:
    bar = "█" * int(rho * 20)
    print(f"  {name:22s}: RMSE={rmse:10.3f}{unit} | ρ={rho:.3f} | n={n}  {bar}")


# ─────────────────────────────────────────────────────────────────────────
# Especialistas
# ─────────────────────────────────────────────────────────────────────────

def _load_best_params(target_name: str) -> dict:
    """
    Retorna os melhores hiperparâmetros encontrados pelo tune.py,
    ou os parâmetros padrão de config.py se tune.py ainda não foi rodado.
    """
    params_path = MODELS_DIR / "best_params.json"
    if params_path.exists():
        with open(params_path) as f:
            best = json.load(f)
        if target_name in best:
            p = best[target_name]["params"]
            return {**p, "tree_method": "hist", "random_state": 42, "verbosity": 0}
    return XGB_PARAMS


def train_specialist(
    df: pd.DataFrame,
    X: pd.DataFrame,
    target_name: str,
) -> tuple[pd.Series, xgb.XGBRegressor, dict]:
    """
    Treina XGBRegressor com KFold e retorna (OOF predictions, modelo, métricas).
    Usa hiperparâmetros do tune.py se disponíveis, senão usa os padrão.
    """
    cfg       = TARGETS[target_name]
    col       = cfg["column"]
    transform = cfg["transform"]
    params    = _load_best_params(target_name)

    mask = df[col].notna()
    Xm   = X.loc[mask]
    ym   = df.loc[mask, col]
    yt   = apply_transform(ym, transform)

    cv      = KFold(n_splits=CV_FOLDS, shuffle=True, random_state=CV_SEED)
    model   = xgb.XGBRegressor(**params)
    oof_t   = cross_val_predict(model, Xm, yt, cv=cv)
    oof_raw = invert_transform(oof_t, transform)

    model.fit(Xm, yt)

    rmse    = math.sqrt(mean_squared_error(ym, oof_raw))
    rho, _  = spearmanr(ym, oof_raw)
    metrics = {"rmse": round(rmse, 4), "spearman_rho": round(rho, 4), "n": int(mask.sum())}

    return pd.Series(oof_raw, index=Xm.index, name=target_name), model, metrics


# ─────────────────────────────────────────────────────────────────────────
# Ranker XGBoost (rank:ndcg)
# ─────────────────────────────────────────────────────────────────────────

def train_ranker(
    df: pd.DataFrame,
    X: pd.DataFrame,
    score: pd.Series,
) -> tuple[xgb.XGBRanker, dict]:
    """
    Treina XGBoost Ranker (rank:ndcg) sobre grupos (tier, combination).
    Não depende de libgomp1 — usa XGBoost puro.
    """
    valid = score.notna()
    df_v  = df.loc[valid].copy()
    X_v   = X.loc[valid]
    s_v   = score.loc[valid]

    # Ordenar por grupo (obrigatório para XGBRanker — qid deve ser não-decrescente)
    df_v["_group"] = df_v["tier"] + "/" + df_v["combination"]
    order = df_v.sort_values("_group").index
    df_s  = df_v.loc[order]
    X_s   = X_v.loc[order]
    s_s   = s_v.loc[order]

    # qid: inteiro por grupo, monotonicamente não-decrescente
    group_labels  = df_s["_group"].values
    unique_groups = {g: i for i, g in enumerate(dict.fromkeys(group_labels))}
    qid = np.array([unique_groups[g] for g in group_labels])

    # Relevância quantizada 0–9
    relevance = (s_s.rank(pct=True) * 9).astype(int).clip(0, 9).values

    ranker = xgb.XGBRanker(**XGB_RANKER_PARAMS)
    ranker.fit(X_s.values, relevance, qid=qid)

    pred   = ranker.predict(X_s.values)
    rho, _ = spearmanr(s_s.values, pred)
    metrics = {
        "spearman_rho": round(float(rho), 4),
        "n_groups":     len(unique_groups),
        "n":            int(valid.sum()),
    }
    return ranker, metrics


# ─────────────────────────────────────────────────────────────────────────
# Salvar / carregar modelos
# ─────────────────────────────────────────────────────────────────────────

def save_model(model: xgb.XGBRegressor | xgb.XGBRanker, name: str) -> Path:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    path = MODELS_DIR / f"{name}.ubj"
    model.save_model(str(path))
    return path


def load_model(name: str) -> xgb.XGBRegressor:
    path  = MODELS_DIR / f"{name}.ubj"
    model = xgb.XGBRegressor()
    model.load_model(str(path))
    return model


def load_ranker(name: str = "ranker") -> xgb.XGBRanker:
    path   = MODELS_DIR / f"{name}.ubj"
    ranker = xgb.XGBRanker()
    ranker.load_model(str(path))
    return ranker


# ─────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────

def run(skip_ranker: bool = False) -> dict:
    print("═" * 60)
    print("  TREINO — Meta-Modelo PostgreSQL Autotuning")
    print("═" * 60)

    print(f"\n[1/3] Carregando {FEATURES_CSV.name}...")
    df = pd.read_csv(FEATURES_CSV, low_memory=False)
    X  = df[ALL_FEATURES]
    print(f"  {len(df)} tarefas | features={len(ALL_FEATURES)}")

    score = compute_score(df)
    print(f"  Score composto: {score.notna().sum()} tasks válidas")

    # ── Especialistas ───────────────────────────────────────────────────
    print("\n[2/3] Treinando especialistas XGBoost (KFold-5)...")
    all_metrics: dict[str, dict] = {}
    oof_frames:  list[pd.Series] = []

    for tname in TARGETS:
        oof, model, metrics = train_specialist(df, X, tname)
        cfg = TARGETS[tname]
        unit = " ms" if "geo_mean" in tname else ("%" if "cache" in tname else "")
        print_metric(cfg["model_name"], metrics["rmse"], metrics["spearman_rho"], metrics["n"], unit)
        save_model(model, cfg["model_name"])
        all_metrics[tname] = metrics
        oof_frames.append(oof)

    oof_df = pd.concat(oof_frames, axis=1)
    oof_df.index.name = "row_idx"
    oof_path = MODELS_DIR / "oof_predictions.csv"
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    oof_df.to_csv(oof_path)
    print(f"\n  OOF salvo: {oof_path.relative_to(ROOT)}")

    # ── Ranker ──────────────────────────────────────────────────────────
    print("\n[3/3] Treinando ranker XGBoost (rank:ndcg)...")
    if not skip_ranker:
        ranker, rmetrics = train_ranker(df, X, score)
        save_model(ranker, "ranker")
        all_metrics["ranker"] = rmetrics
        print(f"  ranker_xgb            : ρ={rmetrics['spearman_rho']:.3f} | "
              f"grupos={rmetrics['n_groups']} | n={rmetrics['n']}")
    else:
        print("  ranker: pulado (--no-ranker)")

    metrics_path = MODELS_DIR / "train_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(all_metrics, f, indent=2)
    print(f"\n  Métricas salvas: {metrics_path.name}")

    print("\n" + "═" * 60)
    print("  Treino concluído. Próximo: python ml/evaluate.py")
    print("═" * 60)

    return all_metrics


def main() -> None:
    p = argparse.ArgumentParser(description="Treina os modelos do meta-modelo.")
    p.add_argument("--no-ranker", action="store_true", help="Pula o ranker XGBoost.")
    args = p.parse_args()
    run(skip_ranker=args.no_ranker)


if __name__ == "__main__":
    main()
