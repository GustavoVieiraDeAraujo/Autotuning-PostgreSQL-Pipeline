"""
Configuração central do pipeline ML — fonte única da verdade.

Todos os módulos (train, evaluate, recommend) importam daqui.
Para mudar um peso, feature ou hiperparâmetro, editar apenas este arquivo.
"""

from pathlib import Path

# ── Caminhos ──────────────────────────────────────────────────────────────
ROOT         = Path(__file__).parent.parent
RESULTS_DIR  = ROOT / "data" / "raw"                   # somente leitura
FEATURES_CSV = ROOT / "data" / "processed" / "features.csv"
MODELS_DIR   = ROOT / "data" / "models"

# ── Features por stage ────────────────────────────────────────────────────
# NaN para params ausentes no combo — XGBoost roteia NaN nativamente.

S1_PARAMS: list[str] = [
    "cfg_jit",
    "cfg_random_page_cost",
    "cfg_default_statistics_target",
    "cfg_max_parallel_workers",
    "cfg_max_parallel_workers_per_gather",
    "cfg_shared_buffers",
    "cfg_effective_cache_size",
    "cfg_work_mem",
    "cfg_enable_hashagg",
    "cfg_enable_bitmapscan",
    "cfg_enable_nestloop",
    "cfg_enable_parallel_hash",
    "cfg_enable_sort",
]

S2_PARAMS: list[str] = [
    "cfg_cpu_tuple_cost",
    "cfg_cpu_index_tuple_cost",
    "cfg_cpu_operator_cost",
    "cfg_parallel_setup_cost",
    "cfg_parallel_tuple_cost",
    "cfg_min_parallel_table_scan_size",
    "cfg_min_parallel_index_scan_size",
    "cfg_join_collapse_limit",
    "cfg_from_collapse_limit",
    "cfg_hash_mem_multiplier",
    "cfg_enable_mergejoin",
    "cfg_enable_hashjoin",
]

S3_PARAMS: list[str] = [
    "cfg_enable_memoize",
    "cfg_enable_gathermerge",
    "cfg_enable_incremental_sort",
    "cfg_enable_material",
    "cfg_enable_indexscan",
    "cfg_enable_indexonlyscan",
    "cfg_enable_parallel_append",
    "cfg_parallel_leader_participation",
]

HW_COLS: list[str] = ["vcpus", "memory_mb", "sf"]

# Parâmetros fixos por design — variância zero, excluídos do vetor X:
#   cfg_seq_page_cost       → sempre 1.0 (âncora do planner)
#   cfg_synchronous_commit  → sempre off (benchmark SELECT-only)
#   cfg_max_worker_processes→ sempre cpu×2+4 (derivado do hardware)
DROP_PARAMS: list[str] = [
    "cfg_seq_page_cost",
    "cfg_synchronous_commit",
    "cfg_max_worker_processes",
]

ALL_FEATURES: list[str] = S1_PARAMS + S2_PARAMS + S3_PARAMS + HW_COLS  # 36 colunas

# Também removidos por sempre serem constantes nos dados coletados:
#   avg_workers_launched     → 0.0 em 336/336 tasks (plan=null, paralelismo não capturado)
#   queries_with_parallelism → 0   em 336/336 tasks
#   rapl_energy_total_j      → NaN em 336/336 tasks (RAPL sem root)

# ── Targets ───────────────────────────────────────────────────────────────
# Cada entrada define um especialista XGBoost de nível 1.
#
# column       : nome da coluna no features.csv
# transform    : transformação aplicada ao y antes de treinar
#                'log'   → np.log(y.clip(1))   — skew alto (geo_mean)
#                'log1p' → np.log1p(y)         — cauda longa (spill)
#                'none'  → sem transformação   — distribuição ok (cache_hit)
# direction    : 'minimize' ou 'maximize' — para score e ranking
# score_weight : peso no score composto (deve somar 1.0 entre os > 0)
#                0.0 = entra no X_meta mas não no score diretamente
# model_name   : nome do arquivo salvo em MODELS_DIR

TARGETS: dict[str, dict] = {
    "geo_mean_tpch": {
        "column":       "tpch_geo_mean_ms",
        "transform":    "log",
        "direction":    "minimize",
        "score_weight": 0.65,
        "model_name":   "m1_geo_tpch",
    },
    "geo_mean_tpcds": {
        "column":       "tpcds_geo_mean_ms",
        "transform":    "log",
        "direction":    "minimize",
        "score_weight": 0.0,
        "model_name":   "m2_geo_tpcds",
    },
    "cache_hit_tpch": {
        "column":       "tpch_cache_hit_ratio",
        "transform":    "none",
        "direction":    "maximize",
        "score_weight": 0.35,
        "model_name":   "m3_cache_tpch",
    },
    "spill_tpcds": {
        "column":       "tpcds_queries_with_spill",
        "transform":    "log1p",
        "direction":    "minimize",
        "score_weight": 0.0,
        "model_name":   "m4_spill_tpcds",
    },
    # Removidos:
    #   workers → avg_workers_launched = 0 em 336/336 tasks
    #   energy  → rapl_energy_total_j  = NaN em 336/336 tasks
}

# Score composto — aplica-se DENTRO de cada grupo (tier, combination).
# Targets com score_weight > 0 e seus pesos (devem somar 1.0).
SCORE_WEIGHTS: dict[str, float] = {
    name: cfg["score_weight"]
    for name, cfg in TARGETS.items()
    if cfg["score_weight"] > 0
}
# → {"geo_mean_tpch": 0.65, "cache_hit_tpch": 0.35}

# ── Grupos de ablação ─────────────────────────────────────────────────────
# Resultado central do TCC: comparar qualidade do modelo com diferentes
# conjuntos de features. Treinar M1 (geo_mean_tpch) em cada grupo e comparar
# RMSE e Spearman ρ.

ABLATION_GROUPS: dict[str, list[str]] = {
    "S1_only  (13 params)": S1_PARAMS + HW_COLS,
    "S1+S2    (25 params)": S1_PARAMS + S2_PARAMS + HW_COLS,
    "S1+S2+S3 (33 params)": ALL_FEATURES,
}

# ── Hiperparâmetros base XGBoost ──────────────────────────────────────────
# Conservadores para 336 amostras — max_depth=4 e min_child_weight=5
# previnem overfitting. Ajustar via Optuna se quiser extrair mais performance.

XGB_PARAMS: dict = dict(
    n_estimators=400,
    max_depth=4,
    learning_rate=0.04,
    subsample=0.8,
    colsample_bytree=0.8,
    min_child_weight=5,
    reg_lambda=2.0,
    tree_method="hist",
    random_state=42,
    verbosity=0,
)

# ── Hiperparâmetros XGBoost Ranker ───────────────────────────────────────
# Aprende a ordenar configs dentro de cada (tier, combo) via rank:ndcg.
# Não depende de libgomp1 — funciona com a instalação padrão do XGBoost.
# Alternativa futura: LightGBM LambdaRank (requer sudo apt install libgomp1)

XGB_RANKER_PARAMS: dict = dict(
    objective="rank:ndcg",
    n_estimators=300,
    max_depth=4,
    learning_rate=0.05,
    subsample=0.8,
    colsample_bytree=0.8,
    min_child_weight=3,
    random_state=42,
    verbosity=0,
)

# ── Validação cruzada ─────────────────────────────────────────────────────
CV_FOLDS = 5
CV_SEED  = 42

# ── Hardware por tier (espelha specs/docker.json) ─────────────────────────
# Usado por recommend.py e extract_features.py.
TIER_HARDWARE: dict[str, dict] = {
    "low":    {"vcpus": 2, "memory_mb": 2048, "sf": 1},
    "medium": {"vcpus": 4, "memory_mb": 4096, "sf": 2},
    "high":   {"vcpus": 6, "memory_mb": 5120, "sf": 4},
}

# ── Codificação de parâmetros PostgreSQL ──────────────────────────────────
# Compartilhado por extract_features.py e recommend.py — fonte única.
BOOL_PARAMS: frozenset[str] = frozenset({
    "jit",
    "enable_hashagg", "enable_bitmapscan", "enable_nestloop",
    "enable_parallel_hash", "enable_sort",
    "enable_mergejoin", "enable_hashjoin",
    "enable_memoize", "enable_gathermerge", "enable_incremental_sort",
    "enable_material", "enable_indexscan", "enable_indexonlyscan",
    "enable_parallel_append", "parallel_leader_participation",
    "synchronous_commit",
})

MEMORY_PARAMS: frozenset[str] = frozenset({
    "shared_buffers", "effective_cache_size", "work_mem",
    "min_parallel_table_scan_size", "min_parallel_index_scan_size",
    "wal_buffers",
})

# ── Combinações válidas e seus stages ────────────────────────────────────
COMBO_STAGES: dict[str, list[int]] = {
    "s1":       [1],
    "s2":       [2],
    "s3":       [3],
    "s1_s2":    [1, 2],
    "s1_s3":    [1, 3],
    "s2_s3":    [2, 3],
    "s1_s2_s3": [1, 2, 3],
}

def features_for_combo(combination: str) -> list[str]:
    """Retorna as colunas de feature relevantes para uma combinação de stages."""
    stages = COMBO_STAGES.get(combination, [])
    cols: list[str] = []
    if 1 in stages:
        cols += S1_PARAMS
    if 2 in stages:
        cols += S2_PARAMS
    if 3 in stages:
        cols += S3_PARAMS
    cols += HW_COLS
    return cols
