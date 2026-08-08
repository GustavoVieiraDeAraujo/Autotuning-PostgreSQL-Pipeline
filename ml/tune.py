"""
Otimização de hiperparâmetros via Optuna para os especialistas XGBoost.

Busca os melhores parâmetros para cada especialista e salva o resultado
em data/models/best_params.json. Após rodar, execute ml/train.py para
re-treinar com os parâmetros encontrados.

Uso:
    .venv/bin/python ml/tune.py                        # otimiza todos
    .venv/bin/python ml/tune.py --target geo_mean_tpch # otimiza 1
    .venv/bin/python ml/tune.py --trials 50            # mais trials (padrão: 80)
    .venv/bin/python ml/tune.py --trials 20 --fast     # rápido para testar
"""

import argparse
import json
import math
import warnings

import pandas as pd
import optuna
from scipy.stats import spearmanr
from sklearn.metrics import mean_squared_error
from sklearn.model_selection import KFold, cross_val_predict
import xgboost as xgb

warnings.filterwarnings("ignore")
optuna.logging.set_verbosity(optuna.logging.WARNING)

from ml.config import (
    FEATURES_CSV, MODELS_DIR, ALL_FEATURES, TARGETS,
    CV_FOLDS, CV_SEED,
)
from ml.train import apply_transform, invert_transform

ROOT = FEATURES_CSV.parent.parent


# ─────────────────────────────────────────────────────────────────────────
# Objetivo Optuna — minimiza RMSE no espaço original via KFold OOF
# ─────────────────────────────────────────────────────────────────────────

def _make_objective(Xm: pd.DataFrame, ym: pd.Series, transform: str):
    cv = KFold(n_splits=CV_FOLDS, shuffle=True, random_state=CV_SEED)
    yt = apply_transform(ym, transform)

    def objective(trial: optuna.Trial) -> float:
        params = dict(
            n_estimators      = trial.suggest_int   ("n_estimators",       200, 800),
            max_depth         = trial.suggest_int   ("max_depth",          3,   6),
            learning_rate     = trial.suggest_float ("learning_rate",      0.01, 0.15, log=True),
            subsample         = trial.suggest_float ("subsample",          0.6,  1.0),
            colsample_bytree  = trial.suggest_float ("colsample_bytree",   0.5,  1.0),
            min_child_weight  = trial.suggest_int   ("min_child_weight",   1,   15),
            reg_lambda        = trial.suggest_float ("reg_lambda",         0.5,  8.0),
            reg_alpha         = trial.suggest_float ("reg_alpha",          0.0,  2.0),
            gamma             = trial.suggest_float ("gamma",              0.0,  1.0),
            tree_method       = "hist",
            random_state      = 42,
            verbosity         = 0,
        )
        model   = xgb.XGBRegressor(**params)
        oof_t   = cross_val_predict(model, Xm, yt, cv=cv)
        oof_raw = invert_transform(oof_t, transform)
        return math.sqrt(mean_squared_error(ym, oof_raw))

    return objective


# ─────────────────────────────────────────────────────────────────────────
# Tune por target
# ─────────────────────────────────────────────────────────────────────────

def tune_target(
    df: pd.DataFrame,
    target_name: str,
    n_trials: int = 80,
) -> dict:
    cfg       = TARGETS[target_name]
    col       = cfg["column"]
    transform = cfg["transform"]

    mask = df[col].notna()
    Xm   = df.loc[mask, ALL_FEATURES]
    ym   = df.loc[mask, col]

    objective = _make_objective(Xm, ym, transform)

    study = optuna.create_study(
        direction  = "minimize",
        sampler    = optuna.samplers.TPESampler(seed=42),
        pruner     = optuna.pruners.MedianPruner(n_startup_trials=10),
    )
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)

    best   = study.best_params
    rmse   = study.best_value

    # Calcular Spearman ρ com os melhores parâmetros
    cv    = KFold(n_splits=CV_FOLDS, shuffle=True, random_state=CV_SEED)
    yt    = apply_transform(ym, transform)
    model = xgb.XGBRegressor(**{**best, "tree_method": "hist", "random_state": 42, "verbosity": 0})
    oof_t = cross_val_predict(model, Xm, yt, cv=cv)
    oof   = invert_transform(oof_t, transform)
    rho, _ = spearmanr(ym, oof)

    return {"params": best, "rmse": round(rmse, 4), "spearman_rho": round(rho, 4)}


# ─────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────

def run(targets: list[str], n_trials: int = 80) -> dict:
    print("═" * 60)
    print("  OPTUNA — Otimização de hiperparâmetros XGBoost")
    print("═" * 60)
    print(f"  Trials por modelo : {n_trials}")
    print(f"  Modelos           : {targets}")
    print()

    df = pd.read_csv(FEATURES_CSV, low_memory=False)

    # Carrega parâmetros anteriores se existirem
    params_path = MODELS_DIR / "best_params.json"
    all_best: dict = {}
    if params_path.exists():
        with open(params_path) as f:
            all_best = json.load(f)

    for tname in targets:
        cfg = TARGETS[tname]
        print(f"  [{tname}] buscando em {n_trials} trials...", end=" ", flush=True)

        result = tune_target(df, tname, n_trials)

        # Baseline (parâmetros padrão)
        from ml.config import XGB_PARAMS as BASE
        mask = df[cfg["column"]].notna()
        Xm   = df.loc[mask, ALL_FEATURES]
        ym   = df.loc[mask, cfg["column"]]
        yt   = apply_transform(ym, cfg["transform"])
        cv   = KFold(n_splits=CV_FOLDS, shuffle=True, random_state=CV_SEED)
        base_oof = invert_transform(
            cross_val_predict(xgb.XGBRegressor(**BASE), Xm, yt, cv=cv),
            cfg["transform"]
        )
        base_rmse = math.sqrt(mean_squared_error(ym, base_oof))
        gain_pct  = (base_rmse - result["rmse"]) / base_rmse * 100

        print(f"feito.")
        print(f"    Baseline : RMSE={base_rmse:.1f}  ρ={spearmanr(ym, base_oof)[0]:.4f}")
        print(f"    Optuna   : RMSE={result['rmse']:.1f}  ρ={result['spearman_rho']:.4f}  "
              f"(melhora: {gain_pct:+.1f}%)")
        print(f"    Params   : depth={result['params']['max_depth']} "
              f"lr={result['params']['learning_rate']:.3f} "
              f"n_est={result['params']['n_estimators']} "
              f"mcw={result['params']['min_child_weight']}")
        print()

        all_best[tname] = result

    # Salva
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    with open(params_path, "w") as f:
        json.dump(all_best, f, indent=2)
    print(f"  Parâmetros salvos: {params_path.relative_to(ROOT)}")
    print()
    print("  Próximo passo: re-treinar com os novos parâmetros")
    print("    .venv/bin/python ml/train.py")
    print("═" * 60)

    return all_best


def main() -> None:
    p = argparse.ArgumentParser(description="Otimiza hiperparâmetros XGBoost via Optuna.")
    p.add_argument("--target",  nargs="+", choices=list(TARGETS),
                   default=list(TARGETS),
                   help="Targets a otimizar (padrão: todos).")
    p.add_argument("--trials",  type=int, default=80,
                   help="Número de trials por modelo (padrão: 80).")
    p.add_argument("--fast",    action="store_true",
                   help="Modo rápido: 20 trials por modelo.")
    args = p.parse_args()

    n_trials = 20 if args.fast else args.trials
    run(targets=args.target, n_trials=n_trials)


if __name__ == "__main__":
    main()
