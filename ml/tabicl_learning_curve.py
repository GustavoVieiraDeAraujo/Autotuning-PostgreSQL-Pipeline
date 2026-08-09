"""
Curva de aprendizado: TabICL (foundation model, in-context) vs. XGBoost
especialista, variando o TAMANHO do dataset de treino — testa a pergunta que
realmente importa pro TCC: um foundation model precisa de menos dados
coletados pra chegar na mesma acurácia?

Metodologia:
    1. Separa um conjunto de teste FIXO (20% do dataset, mesmo em todo cenário,
       nunca visto no treino em nenhum tamanho de subamostra).
    2. Do restante (pool de treino, ~80%), sorteia subamostras de tamanho
       crescente (15, 20, 30, 50, 75, 100, 150, 200, 300, 400, todas).
    3. Pra cada tamanho, repete N_REPEATS vezes com sorteios diferentes
       (exceto "todas", que é determinístico) — reporta média ± desvio padrão.
    4. Mesmos hiperparâmetros de produção pros dois modelos (XGB_PARAMS de
       ml/config.py; TabICL sem nenhum ajuste).
    5. Roda pros 4 alvos do TCC (não só o "manchete"), salva tudo em CSV.

Uso:
    .venv/bin/python ml/tabicl_learning_curve.py
"""

import math
import time
import warnings

import numpy as np
import pandas as pd
import xgboost as xgb
from scipy.stats import spearmanr
from sklearn.metrics import mean_squared_error
from sklearn.model_selection import train_test_split
from tabicl import TabICLRegressor

warnings.filterwarnings("ignore")

from ml.config import ALL_FEATURES, CV_SEED, FEATURES_CSV, MODELS_DIR, TARGETS, XGB_PARAMS
from ml.train import apply_transform, invert_transform

TRAIN_SIZES = [15, 20, 30, 50, 75, 100, 150, 200, 300, 400, None]  # None = todo o pool de treino
N_REPEATS = 10
TEST_FRACTION = 0.20
OUT_CSV = MODELS_DIR / "tabicl_learning_curve.csv"


def _eval(model, X_train, yt_train, X_test, y_test, transform) -> tuple[float, float]:
    model.fit(X_train, yt_train)
    pred_t = model.predict(X_test)
    pred_raw = invert_transform(pred_t, transform)
    rmse = math.sqrt(mean_squared_error(y_test, pred_raw))
    rho, _ = spearmanr(y_test, pred_raw)
    return rmse, rho


def run_target(target_name: str, df: pd.DataFrame, rows: list[dict]) -> None:
    cfg = TARGETS[target_name]
    col, transform = cfg["column"], cfg["transform"]

    mask = df[col].notna()
    X_all = df.loc[mask, ALL_FEATURES].fillna(-1.0).reset_index(drop=True)
    y_all = df.loc[mask, col].reset_index(drop=True)

    X_pool, X_test, y_pool, y_test = train_test_split(
        X_all, y_all, test_size=TEST_FRACTION, random_state=CV_SEED
    )
    print(f"\n{'='*78}\n  {target_name} ({col})  —  pool={len(X_pool)}  teste_fixo={len(X_test)}\n{'='*78}")
    print(f"{'n_treino':>10} {'modelo':<10} {'RMSE (média±dp)':>22} {'ρ (média±dp)':>18} {'tempo':>8}")
    print("─" * 78)

    rng = np.random.default_rng(CV_SEED)

    for size in TRAIN_SIZES:
        n = size if size is not None else len(X_pool)
        repeats = 1 if size is None else N_REPEATS

        xgb_rmses, xgb_rhos = [], []
        tab_rmses, tab_rhos = [], []
        t_xgb_total, t_tab_total = 0.0, 0.0

        for r in range(repeats):
            if size is None:
                idx = np.arange(len(X_pool))
            else:
                idx = rng.choice(len(X_pool), size=n, replace=False)
            X_sub = X_pool.iloc[idx]
            y_sub = y_pool.iloc[idx]
            yt_sub = apply_transform(y_sub, transform)

            t0 = time.time()
            model_xgb = xgb.XGBRegressor(**XGB_PARAMS)
            rmse, rho = _eval(model_xgb, X_sub.values, yt_sub, X_test.values, y_test, transform)
            t_xgb_total += time.time() - t0
            xgb_rmses.append(rmse)
            xgb_rhos.append(rho)
            rows.append(dict(target=target_name, n_train=n, repeat=r, model="xgboost", rmse=rmse, rho=rho))

            t0 = time.time()
            model_tab = TabICLRegressor(random_state=CV_SEED, device="cpu")
            rmse, rho = _eval(model_tab, X_sub.values, yt_sub, X_test.values, y_test, transform)
            t_tab_total += time.time() - t0
            tab_rmses.append(rmse)
            tab_rhos.append(rho)
            rows.append(dict(target=target_name, n_train=n, repeat=r, model="tabicl", rmse=rmse, rho=rho))

        print(f"{n:>10} {'XGBoost':<10} "
              f"{np.mean(xgb_rmses):>10.1f}±{np.std(xgb_rmses):<9.1f} "
              f"{np.mean(xgb_rhos):>8.3f}±{np.std(xgb_rhos):<7.3f} "
              f"{t_xgb_total:>7.1f}s")
        print(f"{'':>10} {'TabICL':<10} "
              f"{np.mean(tab_rmses):>10.1f}±{np.std(tab_rmses):<9.1f} "
              f"{np.mean(tab_rhos):>8.3f}±{np.std(tab_rhos):<7.3f} "
              f"{t_tab_total:>7.1f}s")
        print("─" * 78)


def main() -> None:
    df = pd.read_csv(FEATURES_CSV, low_memory=False)
    rows: list[dict] = []

    for target_name in TARGETS:
        if target_name == "ranker":
            continue
        run_target(target_name, df, rows)
        pd.DataFrame(rows).to_csv(OUT_CSV, index=False)  # salva incrementalmente

    print(f"\nResultados completos salvos em {OUT_CSV}")


if __name__ == "__main__":
    main()
