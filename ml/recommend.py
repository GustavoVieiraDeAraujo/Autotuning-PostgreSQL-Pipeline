"""
Recomendação de configurações PostgreSQL usando os modelos treinados.

Dado um tier e um conjunto de configs candidatas, prediz o score composto
de cada config e retorna as top-K ordenadas do melhor para o pior.

O score composto usa a MESMA fórmula do treino, aplicada às predições:
    score = 0.65 × rank_norm(1/ŷ_geo_tpch) + 0.35 × rank_norm(ŷ_cache_tpch)

O ranking é RELATIVO ao conjunto de candidatos fornecido: quanto mais
configs candidatas, mais precisa é a comparação. O mínimo recomendado é
5 configs para que o rank normalizado tenha resolução suficiente.

Uso básico:
    .venv/bin/python ml/recommend.py --tier high --combo s1_s2 --top-k 3

Uso programático:
    from ml.recommend import recommend
    top_k = recommend(tier="high", combination="s1_s2", candidates=my_configs, k=3)

Formato dos candidatos:
    Lista de dicts com os parâmetros PostgreSQL no mesmo formato do pg_config
    dos arquivos JSON de resultado. Parâmetros ausentes ficam como NaN (ok para XGBoost).

Exemplo de candidato:
    {
        "shared_buffers": "1GB",
        "work_mem": "64MB",
        "enable_hashjoin": "on",
        "random_page_cost": 1.5,
        ...
    }
"""

import argparse
import json
import warnings
from typing import Any

import numpy as np
import pandas as pd
import xgboost as xgb

warnings.filterwarnings("ignore", category=UserWarning)

from ml.config import (
    MODELS_DIR, ALL_FEATURES, TARGETS, SCORE_WEIGHTS,
    TIER_HARDWARE, COMBO_STAGES,
    BOOL_PARAMS, MEMORY_PARAMS,
)
from ml.train import load_model


# ─────────────────────────────────────────────────────────────────────────
# Codificação de parâmetros (idêntica ao extract_features.py)
# ─────────────────────────────────────────────────────────────────────────

def _parse_memory_mb(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().upper()
    if s.endswith("GB"):
        return float(s[:-2]) * 1024.0
    if s.endswith("MB"):
        return float(s[:-2])
    if s.endswith("KB"):
        return float(s[:-2]) / 1024.0
    try:
        return float(s)
    except ValueError:
        return None


def _encode_param(key: str, value: Any) -> float | None:
    if value is None:
        return None
    bare_key = key.removeprefix("cfg_")
    if bare_key in BOOL_PARAMS:
        if isinstance(value, (int, float)):
            return float(bool(value))
        return 1.0 if str(value).strip().lower() in ("on", "1", "true") else 0.0
    if bare_key in MEMORY_PARAMS:
        return _parse_memory_mb(value)
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _config_to_row(pg_config: dict, tier: str) -> dict[str, float | None]:
    """Converte um dict pg_config + tier em uma linha de feature."""
    hw = TIER_HARDWARE.get(tier, {})
    row: dict[str, float | None] = {}
    for col in ALL_FEATURES:
        if col in ("vcpus", "memory_mb", "sf"):
            row[col] = float(hw.get(col, np.nan))
        else:
            bare = col.removeprefix("cfg_")
            row[col] = _encode_param(col, pg_config.get(bare))
    return row


# ─────────────────────────────────────────────────────────────────────────
# Predição
# ─────────────────────────────────────────────────────────────────────────

def _load_score_weights() -> dict[str, float]:
    """Carrega pesos otimizados se disponíveis, senão usa os padrão."""
    weights_path = MODELS_DIR / "optimal_score_weights.json"
    if weights_path.exists():
        with open(weights_path) as f:
            return json.load(f)
    return SCORE_WEIGHTS


def _load_specialists() -> dict[str, xgb.XGBRegressor]:
    """Carrega todos os especialistas salvos."""
    models: dict[str, xgb.XGBRegressor] = {}
    for tname, cfg in TARGETS.items():
        path = MODELS_DIR / f"{cfg['model_name']}.ubj"
        if not path.exists():
            raise FileNotFoundError(
                f"Modelo '{cfg['model_name']}.ubj' não encontrado. "
                f"Execute: .venv/bin/python ml/train.py"
            )
        models[tname] = load_model(cfg["model_name"])
    return models


def predict_scores(
    candidates: list[dict],
    tier: str,
    combination: str,
    models: dict[str, xgb.XGBRegressor] | None = None,
) -> pd.DataFrame:
    """
    Prediz o score composto para cada config candidata.

    Args:
        candidates  : lista de dicts pg_config
        tier        : "low", "medium" ou "high"
        combination : "s1", "s1_s2", etc.
        models      : dict de modelos pré-carregados (evita recarregar a cada chamada)

    Returns:
        DataFrame com colunas pred_geo_ms, pred_cache_pct, pred_spill,
        pred_geo_tpcds_ms e score, ordenado do maior score para o menor.
    """
    if not candidates:
        raise ValueError("Lista de candidatos vazia.")

    if models is None:
        models = _load_specialists()

    # Monta X
    rows = [_config_to_row(c, tier) for c in candidates]
    X    = pd.DataFrame(rows, columns=ALL_FEATURES)

    # Prediz cada target
    preds: dict[str, np.ndarray] = {}
    for tname, cfg in TARGETS.items():
        model  = models[tname]
        yt     = model.predict(X.values)
        transform = cfg["transform"]
        if transform == "log":
            preds[tname] = np.exp(yt)
        elif transform == "log1p":
            preds[tname] = np.expm1(yt)
        else:
            preds[tname] = yt

    # Score relativo ao conjunto de candidatos
    # Carrega pesos otimizados se disponíveis
    weights = _load_score_weights()
    n = len(candidates)

    def rank_norm(arr: np.ndarray, direction: str) -> np.ndarray:
        if direction == "minimize":
            vals = 1.0 / np.clip(arr, 1e-9, None)
        else:
            vals = arr
        # Rank percentual: 0=pior, 1=melhor
        order = np.argsort(np.argsort(vals))
        return order / max(n - 1, 1)

    score = np.zeros(n)
    for tname, weight in weights.items():
        cfg    = TARGETS[tname]
        score += weight * rank_norm(preds[tname], cfg["direction"])

    result = pd.DataFrame({
        "candidate_idx":    range(n),
        "pred_geo_tpch_ms": np.round(preds["geo_mean_tpch"], 1),
        "pred_geo_tpcds_ms":np.round(preds["geo_mean_tpcds"], 1),
        "pred_cache_pct":   np.round(preds["cache_hit_tpch"], 2),
        "pred_spill_tpcds": np.round(preds["spill_tpcds"], 2),
        "score":            np.round(score, 4),
    }).sort_values("score", ascending=False).reset_index(drop=True)

    return result


def recommend(
    tier: str,
    combination: str,
    candidates: list[dict],
    k: int = 5,
    models: dict[str, xgb.XGBRegressor] | None = None,
    verbose: bool = True,
) -> list[dict]:
    """
    Recomenda as top-K configurações PostgreSQL dado um tier e combination.

    Returns:
        Lista de dicts com campos: rank, config (pg_config original),
        pred_geo_tpch_ms, pred_cache_pct, pred_spill_tpcds, score.
    """
    if tier not in TIER_HARDWARE:
        raise ValueError(f"Tier inválido: {tier!r}. Use: {list(TIER_HARDWARE)}")
    if combination not in COMBO_STAGES:
        raise ValueError(f"Combination inválida: {combination!r}. Use: {list(COMBO_STAGES)}")

    if models is None:
        models = _load_specialists()

    result_df = predict_scores(candidates, tier, combination, models)
    top_k     = result_df.head(k)

    if verbose:
        print(f"\n── Top-{k} configs para {tier}/{combination} ──")
        print(f"  {'Rank':>4}  {'geo TPC-H':>10}  {'cache':>7}  {'spill':>6}  {'score':>7}")
        print("  " + "─" * 44)
        for _, row in top_k.iterrows():
            print(f"  #{int(row['candidate_idx'])+1:3d}  "
                  f"{row['pred_geo_tpch_ms']:>10.1f}ms  "
                  f"{row['pred_cache_pct']:>7.1f}%  "
                  f"{row['pred_spill_tpcds']:>6.1f}q  "
                  f"{row['score']:>7.4f}")

    recommendations: list[dict] = []
    for _, row in top_k.iterrows():
        idx = int(row["candidate_idx"])
        recommendations.append({
            "rank":               len(recommendations) + 1,
            "config":             candidates[idx],
            "pred_geo_tpch_ms":   float(row["pred_geo_tpch_ms"]),
            "pred_geo_tpcds_ms":  float(row["pred_geo_tpcds_ms"]),
            "pred_cache_pct":     float(row["pred_cache_pct"]),
            "pred_spill_tpcds":   float(row["pred_spill_tpcds"]),
            "score":              float(row["score"]),
        })
    return recommendations


# ─────────────────────────────────────────────────────────────────────────
# CLI: demonstração com dados reais do dataset
# ─────────────────────────────────────────────────────────────────────────

def _demo_from_csv(tier: str, combination: str, k: int) -> None:
    """Lê configs reais do features.csv e demonstra o ranker."""
    from ml.config import FEATURES_CSV

    df = pd.read_csv(FEATURES_CSV, low_memory=False)
    subset = df[(df["tier"] == tier) & (df["combination"] == combination)]

    if subset.empty:
        print(f"Nenhuma task encontrada para {tier}/{combination}.")
        return

    # Reconstruir pg_config a partir das colunas cfg_*
    cfg_cols = [c for c in df.columns if c.startswith("cfg_")]
    candidates: list[dict] = []
    for _, row in subset.iterrows():
        cfg: dict = {}
        for col in cfg_cols:
            v = row[col]
            if pd.notna(v):
                cfg[col.removeprefix("cfg_")] = v
        candidates.append(cfg)

    print(f"\nDemonstração: {len(candidates)} configs de {tier}/{combination} do dataset")
    models = _load_specialists()
    recommend(tier, combination, candidates, k=k, models=models, verbose=True)

    # Mostrar geo_mean real das top-K para comparação
    result_df = predict_scores(candidates, tier, combination, models)
    top_idx   = result_df.head(k)["candidate_idx"].tolist()
    top_rows  = subset.iloc[top_idx]

    col_geo = TARGETS["geo_mean_tpch"]["column"]
    if col_geo in subset.columns:
        print(f"\n  Geo-mean REAL das top-{k} selecionadas (para validação):")
        for i, (_, row) in enumerate(top_rows.iterrows(), 1):
            v = row.get(col_geo)
            print(f"    #{i}: {v:.1f}ms" if pd.notna(v) else f"    #{i}: N/A")


def main() -> None:
    p = argparse.ArgumentParser(description="Recomenda configurações PostgreSQL.")
    p.add_argument("--tier",   default="high",  help="Tier de hardware (low/medium/high).")
    p.add_argument("--combo",  default="s1_s2", help="Combinação de stages.")
    p.add_argument("--top-k",  type=int, default=5, help="Quantas configs retornar.")
    p.add_argument("--config", type=str, default=None,
                   help="Arquivo JSON com lista de configs candidatas. "
                        "Se omitido, usa configs reais do features.csv.")
    args = p.parse_args()

    if args.config:
        with open(args.config) as f:
            candidates = json.load(f)
        models = _load_specialists()
        recommend(args.tier, args.combo, candidates, k=args.top_k, models=models, verbose=True)
    else:
        _demo_from_csv(args.tier, args.combo, args.top_k)


if __name__ == "__main__":
    main()
