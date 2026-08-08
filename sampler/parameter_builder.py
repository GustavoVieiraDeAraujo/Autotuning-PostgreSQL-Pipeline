"""
Geração de configurações PostgreSQL combinadas (Etapas 1, 2 e/ou 3).

Parâmetros amostrados por etapa (LHS):
  [1]       → 13 params  (memória, paralelismo, toggles de execução básica)
  [2]       → 12 params  (custos CPU, collapse limits, hash memory, toggles planner)
  [3]       →  8 params  (execução avançada)
  [1, 2]    → 25 params
  [1, 2, 3] → 33 params

Parâmetros fixos aplicados em Stage 1 (não amostrados via LHS):

Parâmetros fixos aplicados em todas as configurações (não amostrados):
  - seq_page_cost        = 1.0        (âncora do planner)
  - max_worker_processes = cpu×2 + 4  (pool suficiente, sem dependência cross-param)
  - synchronous_commit   = "off"      (sem efeito em SELECT-only)
"""

import json

from .constraints import (
    _effective_min_cache_ratio,
    _shared_ratio_bounds,
    diagnose_combined,
    validate_combined_config,
)
from .unit_parsers import parse_memory
from .lhs_sampler import (
    _pick,
    _randint,
    _uniform,
    filter_memory_choices,
    lhs_quantiles,
)
from .types import Config, Environment, StageSpaces


def generate_combined_config(
    stage_spaces: StageSpaces,
    env: Environment,
    stages: list[int],
    quantiles: dict[str, float] | None = None,
) -> Config:
    """Gera uma configuração PostgreSQL combinada a partir dos espaços de parâmetros.

    Args:
        stage_spaces: Dict ``{etapa: ParameterSpace}`` para cada etapa incluída.
        env:          Especificação do container Docker (cpu, memory_mb, shm_size_mb).
        stages:       Lista de etapas a incluir, ex: [1], [1, 2], [2], [1, 2, 3].
        quantiles:    Dict ``{param: quantil ∈ [0, 1]}`` para modo LHS.
                      None usa amostragem aleatória pura (fallback após LHS falhar).

    Returns:
        Configuração PostgreSQL como dict ``{nome_do_param: valor}``.
    """
    q: dict[str, float] = quantiles or {}
    config: Config = {}

    if 1 in stages:
        _fill_stage1(config, stage_spaces[1], env, q)
    if 2 in stages:
        _fill_stage2(config, stage_spaces[2], env, q)
    if 3 in stages:
        _fill_stage3(config, stage_spaces[3], env, q)

    return config


# ---------------------------------------------------------------------------
# Etapa 1 — memória, paralelismo, toggles de execução básica (13 params LHS + 3 fixos)
# ---------------------------------------------------------------------------

def _fill_stage1(
    config: Config,
    ps: dict,
    env: Environment,
    q: dict[str, float],
) -> None:
    """Preenche os 13 parâmetros amostrados da Etapa 1 + 3 fixos."""
    cpu    = env["cpu"]
    ram_mb = env["memory_mb"]
    shm_mb = env.get("shm_size_mb", 64)

    # 1. JIT — desabilitado em LOW (2 vCPU): overhead de compilação supera ganho
    config["jit"] = _pick(ps["jit"]["choices"]["values"], q.get("jit"))

    # 2. seq_page_cost — fixo em 1.0 (âncora do planner)
    config["seq_page_cost"] = 1.0

    # 3. random_page_cost — limitado a 4.0 (irrealista acima disso em SSD overlay2)
    rand = ps["random_page_cost"]["choices"]["values"]
    config["random_page_cost"] = round(
        _uniform(rand["min"], min(rand["max"], 4.0), q.get("random_page_cost")), 3
    )

    # 4. default_statistics_target — valores altos melhoram estimativas de join em OLAP
    stats = ps["default_statistics_target"]["choices"]["values"]
    config["default_statistics_target"] = _randint(
        stats["min"], stats["max"], q.get("default_statistics_target")
    )

    # 5. max_parallel_workers — limitado às vCPUs disponíveis
    workers_spec = ps["max_parallel_workers"]["choices"]["values"]
    max_workers  = min(cpu, workers_spec["max"])
    min_workers  = min(workers_spec["min"], max_workers)
    config["max_parallel_workers"] = _randint(
        min_workers, max_workers, q.get("max_parallel_workers")
    )

    # 6. max_parallel_workers_per_gather — mínimo 1 (nunca desabilitar paralelismo
    #    por query em OLAP; per_gather=0 é catastrófico para TPC-H/TPC-DS)
    gather_spec = ps["max_parallel_workers_per_gather"]["choices"]["values"]
    max_gather  = min(config["max_parallel_workers"], max(1, cpu // 2), gather_spec["max"])
    min_gather  = max(gather_spec["min"], 1)
    min_gather  = min(min_gather, max_gather)
    config["max_parallel_workers_per_gather"] = _randint(
        min_gather, max_gather, q.get("max_parallel_workers_per_gather")
    )

    # 7. max_worker_processes — fixo derivado do CPU; remove dependência cross-param
    #    Fórmula: cpu×2 + 4  →  LOW=8, MEDIUM=12, HIGH=16
    config["max_worker_processes"] = cpu * 2 + 4

    # 8. shared_buffers — limitado pelo teto de /dev/shm e pela razão de RAM
    shared_choices = ps["shared_buffers"]["choices"]["values"]
    min_r, max_r   = _shared_ratio_bounds()
    min_shared     = int(ram_mb * min_r)
    max_shared     = min(int(ram_mb * max_r), shm_mb - 64)
    filtered = filter_memory_choices(shared_choices, min_shared, max_shared)
    if not filtered:
        filtered = filter_memory_choices(shared_choices, int(ram_mb * 0.10), shm_mb)
    config["shared_buffers"] = _pick(filtered or shared_choices, q.get("shared_buffers"))

    # 9. effective_cache_size — hint do planner; deve exceder shared_buffers
    shared_mb       = parse_memory(config["shared_buffers"])
    cache_choices   = ps["effective_cache_size"]["choices"]["values"]
    min_cache_ratio = _effective_min_cache_ratio(ram_mb, cache_choices)
    cache_min = max(int(ram_mb * min_cache_ratio), shared_mb + 1)
    filtered  = filter_memory_choices(cache_choices, cache_min, int(ram_mb * 0.80))
    if not filtered:
        filtered = filter_memory_choices(cache_choices, shared_mb + 1, ram_mb)
    config["effective_cache_size"] = _pick(
        filtered or cache_choices, q.get("effective_cache_size")
    )

    # 10. work_mem — floor por tier evita spill constante em TPC-H/TPC-DS;
    #     cap quando paralelismo alto evita OOM.
    work_choices = ps["work_mem"]["choices"]["values"]
    work_min_mb  = 32 if ram_mb >= 6144 else 16 if ram_mb >= 4096 else 8
    filtered     = filter_memory_choices(work_choices, work_min_mb, int(ram_mb * 0.10))
    if config["max_parallel_workers_per_gather"] >= 2:
        work_cap_mb = 128 if ram_mb >= 6144 else 64
        capped = [w for w in filtered if parse_memory(w) <= work_cap_mb]
        if capped:
            filtered = capped
    config["work_mem"] = _pick(filtered or work_choices, q.get("work_mem"))

    # 11–13 (toggles). Toggles de execução básica — sem dependências entre si ou com outros params
    for key in [
        "enable_hashagg",
        "enable_bitmapscan",
        "enable_nestloop",
        "enable_parallel_hash",
        "enable_sort",
    ]:
        config[key] = _pick(ps[key]["choices"]["values"], q.get(key))

    # Parâmetros fixos — irrelevantes para workloads SELECT-only
    config["synchronous_commit"] = "off"


# ---------------------------------------------------------------------------
# Etapa 2 — estratégia de join & custos do planner (12 params LHS)
# ---------------------------------------------------------------------------

def _fill_stage2(
    config: Config,
    ps: dict,
    env: Environment,
    q: dict[str, float],
) -> None:
    """Preenche os 12 parâmetros LHS da Etapa 2."""

    # 1. cpu_tuple_cost — teto da hierarquia de custos CPU
    tup_spec = ps["cpu_tuple_cost"]["choices"]["values"]
    config["cpu_tuple_cost"] = round(
        _uniform(tup_spec["min"], tup_spec["max"], q.get("cpu_tuple_cost")), 4
    )

    # 2. cpu_index_tuple_cost ≤ cpu_tuple_cost
    idx_spec = ps["cpu_index_tuple_cost"]["choices"]["values"]
    idx_max  = min(idx_spec["max"], config["cpu_tuple_cost"])
    idx_min  = min(idx_spec["min"], idx_max)
    config["cpu_index_tuple_cost"] = round(
        _uniform(idx_min, idx_max, q.get("cpu_index_tuple_cost")), 5
    )

    # 3. cpu_operator_cost ≤ cpu_index_tuple_cost
    op_spec = ps["cpu_operator_cost"]["choices"]["values"]
    op_max  = min(op_spec["max"], config["cpu_index_tuple_cost"])
    op_min  = min(op_spec["min"], op_max)
    config["cpu_operator_cost"] = round(
        _uniform(op_min, op_max, q.get("cpu_operator_cost")), 5
    )

    # 4. Custos de overhead de paralelismo
    psc_spec = ps["parallel_setup_cost"]["choices"]["values"]
    config["parallel_setup_cost"] = _randint(
        psc_spec["min"], psc_spec["max"], q.get("parallel_setup_cost")
    )

    ptc_spec = ps["parallel_tuple_cost"]["choices"]["values"]
    config["parallel_tuple_cost"] = round(
        _uniform(ptc_spec["min"], ptc_spec["max"], q.get("parallel_tuple_cost")), 3
    )

    # 5. Tamanhos mínimos para scans paralelos
    tbl_choices = ps["min_parallel_table_scan_size"]["choices"]["values"]
    config["min_parallel_table_scan_size"] = _pick(
        tbl_choices, q.get("min_parallel_table_scan_size")
    )

    idx_choices = ps["min_parallel_index_scan_size"]["choices"]["values"]
    config["min_parallel_index_scan_size"] = _pick(
        idx_choices, q.get("min_parallel_index_scan_size")
    )

    # 6. Collapse limits — controlam profundidade da busca exaustiva de ordem de join
    jcl_spec = ps["join_collapse_limit"]["choices"]["values"]
    config["join_collapse_limit"] = _randint(
        jcl_spec["min"], jcl_spec["max"], q.get("join_collapse_limit")
    )

    fcl_spec = ps["from_collapse_limit"]["choices"]["values"]
    config["from_collapse_limit"] = _randint(
        fcl_spec["min"], fcl_spec["max"], q.get("from_collapse_limit")
    )

    # 7. Hash memory multiplier — multiplica work_mem para hash joins/aggregations
    hmm_spec = ps["hash_mem_multiplier"]["choices"]["values"]
    config["hash_mem_multiplier"] = round(
        _uniform(hmm_spec["min"], hmm_spec["max"], q.get("hash_mem_multiplier")), 1
    )

    # 8–9. Toggles de métodos de join
    config["enable_mergejoin"] = _pick(
        ps["enable_mergejoin"]["choices"]["values"], q.get("enable_mergejoin")
    )
    config["enable_hashjoin"] = _pick(
        ps["enable_hashjoin"]["choices"]["values"], q.get("enable_hashjoin")
    )


# ---------------------------------------------------------------------------
# Etapa 3 — execução avançada (8 params LHS)
# ---------------------------------------------------------------------------

def _fill_stage3(
    config: Config,
    ps: dict,
    env: Environment,
    q: dict[str, float],
) -> None:
    """Preenche os 8 parâmetros LHS da Etapa 3."""

    # 1–8. Toggles do planner — sem dependências entre si
    for key in [
        "enable_memoize",          # caching de resultados de inner loop (PG14+)
        "enable_gathermerge",      # gather merge em queries paralelas com ORDER BY
        "enable_incremental_sort", # sort incremental aproveitando ordenação parcial (PG13+)
        "enable_material",         # nós de materialização em joins/subqueries
        "enable_indexscan",        # index scans (acesso por B-tree/hash index)
        "enable_indexonlyscan",    # index-only scans quando covering index disponível
        "enable_parallel_append",  # append paralelo para UNION ALL e partições (PG11+)
    ]:
        config[key] = _pick(ps[key]["choices"]["values"], q.get(key))

    # 9. parallel_leader_participation — líder do gather participa como worker
    config["parallel_leader_participation"] = _pick(
        ps["parallel_leader_participation"]["choices"]["values"],
        q.get("parallel_leader_participation"),
    )

    # checkpoint_completion_target, bgwriter_lru_maxpages e wal_buffers foram
    # removidos: são parâmetros de I/O de escrita sem efeito mensurável em
    # workloads SELECT-only (TPC-H / TPC-DS). Geram ruído no dataset ML.


# ---------------------------------------------------------------------------
# Geração em lote com LHS
# ---------------------------------------------------------------------------

def generate_valid_configs(
    stage_spaces: StageSpaces,
    env: Environment,
    stages: list[int],
    n: int,
    max_attempts: int = 100_000,
    seed: int | None = None,
) -> list[Config]:
    """Gera n configurações combinadas válidas e únicas via LHS.

    Usa LHS para o primeiro candidato de cada slot; se a config gerada
    for inválida ou duplicada, recai em amostragem aleatória pura até
    atingir max_attempts.

    Args:
        stage_spaces: Espaços de parâmetros carregados por etapa.
        env:          Especificação do container Docker.
        stages:       Lista de etapas a incluir.
        n:            Número de configurações a gerar.
        max_attempts: Máximo de tentativas por slot antes de falhar.
        seed:         Semente para o LHS. None = não-determinístico.

    Returns:
        Lista de n configurações válidas e únicas.

    Raises:
        RuntimeError: Se o diagnóstico falhar ou algum slot não puder
            ser preenchido dentro de max_attempts.
    """
    configs: list[Config] = []
    seen:    set[str]     = set()

    diagnose_combined(stage_spaces, stages)
    quantile_sets = lhs_quantiles(n, stages, seed=seed)

    for quantiles in quantile_sets:
        filled = False
        for attempt in range(max_attempts):
            q   = quantiles if attempt == 0 else None
            cfg = generate_combined_config(stage_spaces, env, stages, q)

            errors = validate_combined_config(cfg, env, stage_spaces, stages)
            key    = json.dumps(cfg, sort_keys=True)

            if not errors and key not in seen:
                configs.append(cfg)
                seen.add(key)
                filled = True
                break

        if not filled:
            raise RuntimeError(
                f"Slot LHS não preenchido após {max_attempts} tentativas "
                f"(stages={stages}, env={env})."
            )

    return configs
