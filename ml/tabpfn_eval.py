"""
Teste de viabilidade: TabICL (foundation model tabular, in-context, sem treino
especializado) vs. os 4 especialistas XGBoost já treinados pra este TCC.

Usa exatamente a mesma metodologia de ml/train.py (KFold-5, seed 42, mesma
transformação do target) pra que RMSE/ρ sejam diretamente comparáveis aos já
salvos em data/models/train_metrics.json.

Testa 2 estratégias de valor ausente (TabICL não roteia NaN nativamente como
árvore de decisão faz):
    naive  — substitui por -1.0 (sentinela fora do range real)
    median — imputação por mediana da coluna + coluna indicadora binária
             "era ausente" (preserva o sinal que o XGBoost já explora nativo)

Uso:
    .venv/bin/python ml/tabpfn_eval.py
"""

import json
import math
import time
import warnings

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import mean_squared_error
from sklearn.model_selection import KFold, cross_val_predict
from tabicl import TabICLRegressor

warnings.filterwarnings("ignore")

from ml.config import ALL_FEATURES, CV_FOLDS, CV_SEED, FEATURES_CSV, MODELS_DIR, TARGETS
from ml.train import apply_transform, invert_transform


def _fill_naive(X: pd.DataFrame) -> np.ndarray:
    return X.fillna(-1.0).values


def _fill_median_with_indicators(X: pd.DataFrame) -> np.ndarray:
    X = X.copy()
    parts = [X.fillna(X.median(numeric_only=True))]
    for col in X.columns[X.isna().any()]:
        parts.append(X[col].isna().astype(float).rename(f"{col}__was_missing"))
    return pd.concat(parts, axis=1).values


STRATEGIES = {
    "naive (-1.0)": _fill_naive,
    "median + indicador ausente": _fill_median_with_indicators,
}


def main() -> None:
    df = pd.read_csv(FEATURES_CSV, low_memory=False)

    with open(MODELS_DIR / "train_metrics.json") as f:
        xgb_metrics = json.load(f)

    cv = KFold(n_splits=CV_FOLDS, shuffle=True, random_state=CV_SEED)

    print("═" * 78)
    print("  TabICL (zero-shot, sem tuning) vs. XGBoost especialista (já treinado)")
    print("═" * 78)

    for target_name, cfg in TARGETS.items():
        if target_name == "ranker":
            continue
        col, transform = cfg["column"], cfg["transform"]
        mask = df[col].notna()
        X = df.loc[mask, ALL_FEATURES]
        y = df.loc[mask, col]
        yt = apply_transform(y, transform)

        xgb = xgb_metrics[target_name]
        print(f"\n── {target_name} ({col}) — n={len(X)} ──")
        print(f"  {'estratégia':<28} {'RMSE':>12} {'ρ':>8} {'tempo':>8}")
        print(f"  {'─'*58}")
        print(f"  {'XGBoost (referência)':<28} {xgb['rmse']:>12.2f} {xgb['spearman_rho']:>8.3f} {'—':>8}")

        for strat_name, fill_fn in STRATEGIES.items():
            X_filled = fill_fn(X)
            t0 = time.time()
            reg = TabICLRegressor(random_state=CV_SEED, device="cpu")
            oof_t = cross_val_predict(reg, X_filled, yt, cv=cv)
            elapsed = time.time() - t0

            oof_raw = invert_transform(oof_t, transform)
            rmse = math.sqrt(mean_squared_error(y, oof_raw))
            rho, _ = spearmanr(y, oof_raw)
            print(f"  {strat_name:<28} {rmse:>12.2f} {rho:>8.3f} {elapsed:>7.1f}s")

    print("\n" + "═" * 78)


if __name__ == "__main__":
    main()
