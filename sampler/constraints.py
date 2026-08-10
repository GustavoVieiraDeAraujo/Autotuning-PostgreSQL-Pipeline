"""
Regras de validação e diagnóstico do espaço de parâmetros combinado.

Cobre três camadas de restrições para parâmetros relevantes a workloads
analíticos SELECT-only (TPC-H / TPC-DS):

  1. Intra-Etapa 1: memória & paralelismo base (13 params):
       jit, random_page_cost, default_statistics_target,
       max_parallel_workers, max_parallel_workers_per_gather,
       shared_buffers, effective_cache_size, work_mem,
       enable_hashagg, enable_bitmapscan, enable_nestloop, enable_parallel_hash,
       enable_sort.

  2. Intra-Etapa 2: estratégia de join & custos do planner (12 params):
       hash_mem_multiplier, enable_hashjoin, enable_mergejoin,
       parallel_setup_cost, parallel_tuple_cost,
       min_parallel_table_scan_size, min_parallel_index_scan_size,
       join_collapse_limit, from_collapse_limit,
       cpu_tuple_cost, cpu_index_tuple_cost, cpu_operator_cost.

  3. Etapa 3: apenas toggles booleanos (on/off), sem invariantes numéricos
       a validar.

Restrição cruzada mantida (única relevante):

  Cross E1+E2:
    - hash_mem_multiplier (E2) × work_mem (E1) × (per_gather+1) ≤ RAM × 0.85:
      total de memória de hash por query paralela não deve ultrapassar 85% da RAM.
      Com os ranges atuais essa restrição nunca falha, mas é mantida como salvaguarda.

Os toggles booleanos (enable_*) são gerados a partir de listas discretas fixas
("on"/"off") e não precisam de validação de range.
"""

from .unit_parsers import parse_memory
from .types import Config, Environment, ParameterSpace, StageSpaces

# ---------------------------------------------------------------------------
# Constantes compartilhadas
# ---------------------------------------------------------------------------

_PG_SHM_OVERHEAD_MB       = 64     # overhead fixo do PostgreSQL sobre /dev/shm (MB)
_MAX_RAND_SEQ_RATIO        = 4.0   # razão máxima random_page_cost / seq_page_cost
_MAX_PARALLEL_TUPLE_RATIO  = 500   # razão máxima parallel_tuple_cost / cpu_tuple_cost

# Thresholds de RAM para ajuste de limites por tier (MB)
_RAM_SMALL   = 4096   # LOW  (2 vCPU / 2GB)
_RAM_MEDIUM  = 8192   # MEDIUM (4 vCPU / 4GB)

# Limites mínimos de work_mem por tier (MB)
_WORK_MEM_MIN_MEDIUM = 16
_WORK_MEM_MIN_HIGH   = 32

# Estimativa de pico de memória: ratio máximo (shared + parallel work_mem) / RAM
_MEM_PEAK_RATIO_SMALL  = 0.75
_MEM_PEAK_RATIO_MEDIUM = 0.78
_MEM_PEAK_RATIO_LARGE  = 0.82

# effective_cache_size: ratio mínimo sobre a RAM por tier
_CACHE_RATIO_SMALL  = 0.50
_CACHE_RATIO_MEDIUM = 0.45
_CACHE_RATIO_LARGE  = 0.40


# ---------------------------------------------------------------------------
# Diagnóstico dos espaços de parâmetros
# ---------------------------------------------------------------------------

def diagnose_combined(stage_spaces: StageSpaces, stages: list[int]) -> None:
    """Valida invariantes básicos dos espaços de parâmetros antes de gerar configs.

    Deve ser chamado uma vez por combinação de etapas, antes do loop de geração.
    Falha rapidamente se os arquivos JSON de configuração estiverem inconsistentes.

    Args:
        stage_spaces: Dict ``{etapa: ParameterSpace}`` com as etapas carregadas.
        stages:       Lista de etapas solicitadas, ex: [1], [2], [1, 2, 3].

    Raises:
        RuntimeError: Se qualquer invariante crítico for violado.
    """
    if 1 in stages:
        _diagnose_stage1(stage_spaces[1])


def _diagnose_stage1(ps: ParameterSpace) -> None:
    cache_choices  = ps["effective_cache_size"]["choices"]["values"]
    shared_choices = ps["shared_buffers"]["choices"]["values"]
    max_cache  = max(parse_memory(c) for c in cache_choices)
    max_shared = max(parse_memory(c) for c in shared_choices)
    if max_cache <= max_shared:
        raise RuntimeError(
            f"E1: effective_cache_size máximo ({max_cache}MB) deve ser "
            f"> shared_buffers máximo ({max_shared}MB)."
        )


# ---------------------------------------------------------------------------
# Helpers de limites (usados também por parameter_builder)
# ---------------------------------------------------------------------------

def _shared_ratio_bounds() -> tuple[float, float]:
    """Retorna (ratio_mínimo, ratio_máximo) de shared_buffers / RAM."""
    return 0.15, 0.30


def _effective_min_cache_ratio(ram_mb: int, cache_choices: list[str]) -> float:
    """Calcula o ratio mínimo de effective_cache_size / RAM para o tier."""
    target       = _CACHE_RATIO_SMALL if ram_mb <= _RAM_SMALL else _CACHE_RATIO_MEDIUM if ram_mb <= _RAM_MEDIUM else _CACHE_RATIO_LARGE
    max_cache_mb = max(parse_memory(c) for c in cache_choices)
    return min(target, max_cache_mb / ram_mb)


# ---------------------------------------------------------------------------
# Validação combinada: ponto de entrada principal
# ---------------------------------------------------------------------------

def validate_combined_config(
    config: Config,
    env: Environment,
    stage_spaces: StageSpaces,
    stages: list[int],
) -> list[str]:
    """Valida uma configuração combinada contra o ambiente Docker.

    Args:
        config:       Configuração gerada pelo parameter_builder.
        env:          Especificação do container Docker.
        stage_spaces: Espaços de parâmetros carregados por etapa.
        stages:       Lista de etapas incluídas, ex: [1], [2], [1, 2, 3].

    Returns:
        Lista de strings descrevendo cada erro encontrado. Vazia se válida.
    """
    errors: list[str] = []

    if 1 in stages:
        errors.extend(_validate_stage1(config, env, stage_spaces[1]))
    if 2 in stages:
        errors.extend(_validate_stage2(config, env))
        if 1 in stages:
            errors.extend(_validate_cross_12(config, env))

    return errors


# ---------------------------------------------------------------------------
# Validação intra-Etapa 1: paralelismo, memória, planner básico
# ---------------------------------------------------------------------------

def _validate_stage1(
    config: Config,
    env: Environment,
    ps: ParameterSpace,
) -> list[str]:
    errors: list[str] = []

    cpu    = env["cpu"]
    ram_mb = env["memory_mb"]
    shm_mb = env.get("shm_size_mb", 64)

    shared     = parse_memory(config["shared_buffers"])
    work       = parse_memory(config["work_mem"])
    cache      = parse_memory(config["effective_cache_size"])
    mpw        = config["max_parallel_workers"]
    per_gather = config["max_parallel_workers_per_gather"]
    mwp        = config["max_worker_processes"]

    cache_choices   = ps["effective_cache_size"]["choices"]["values"]
    min_cache_ratio = _effective_min_cache_ratio(ram_mb, cache_choices)
    min_shared_r, max_shared_r = _shared_ratio_bounds()

    # Paralelismo
    if per_gather < 1:
        errors.append("E1: max_parallel_workers_per_gather < 1: paralelismo desabilitado")
    if per_gather > mpw:
        errors.append("E1: max_parallel_workers_per_gather > max_parallel_workers")
    if mpw > cpu:
        errors.append("E1: max_parallel_workers acima das CPUs do container")
    if per_gather > max(1, cpu // 2):
        errors.append("E1: max_parallel_workers_per_gather muito alto para a CPU")

    # Custos do planner
    if config["random_page_cost"] < config["seq_page_cost"]:
        errors.append("E1: random_page_cost < seq_page_cost")
    ratio = config["random_page_cost"] / config["seq_page_cost"]
    if ratio > _MAX_RAND_SEQ_RATIO:
        errors.append(
            f"E1: random/seq ratio {ratio:.1f}× > {_MAX_RAND_SEQ_RATIO}×: "
            f"planner evitará index scans"
        )

    # shared_buffers vs /dev/shm
    if shared > shm_mb - _PG_SHM_OVERHEAD_MB:
        errors.append(
            f"E1: shared_buffers ({shared}MB) + overhead ({_PG_SHM_OVERHEAD_MB}MB) "
            f"> shm_size ({shm_mb}MB)"
        )

    # effective_cache_size
    if cache <= shared:
        errors.append("E1: effective_cache_size <= shared_buffers")
    if cache < ram_mb * min_cache_ratio:
        errors.append("E1: effective_cache_size muito baixo para o tier")

    # Ratio de shared_buffers
    shared_ratio = shared / ram_mb
    if shared_ratio < min_shared_r:
        errors.append("E1: shared_buffers muito baixo")
    if shared_ratio > max_shared_r:
        errors.append("E1: shared_buffers muito alto")

    # Estimativa de pico de memória (shared + parallel work_mem)
    estimated      = shared + (work * max(1, per_gather) * 3)
    max_ratio      = _MEM_PEAK_RATIO_SMALL if ram_mb <= _RAM_SMALL else _MEM_PEAK_RATIO_MEDIUM if ram_mb <= _RAM_MEDIUM else _MEM_PEAK_RATIO_LARGE
    if estimated > ram_mb * max_ratio:
        errors.append("E1: estimativa de uso de memória excede o limite do container")

    # Mínimo de work_mem por tier
    if ram_mb >= _RAM_MEDIUM and work < _WORK_MEM_MIN_HIGH:
        errors.append(f"E1: work_mem ({work}MB) insuficiente para HIGH (mínimo {_WORK_MEM_MIN_HIGH}MB)")
    elif ram_mb >= _RAM_SMALL and work < _WORK_MEM_MIN_MEDIUM:
        errors.append(f"E1: work_mem ({work}MB) insuficiente para MEDIUM (mínimo {_WORK_MEM_MIN_MEDIUM}MB)")

    # JIT tem overhead maior que benefício no tier LOW (2 vCPU)
    if cpu <= 2 and config.get("jit") == 1:
        errors.append("E1: jit=1 não recomendado para tier LOW (2 vCPU)")

    return errors


# ---------------------------------------------------------------------------
# Validação intra-Etapa 2: custos CPU, collapse limits, hash memory
# ---------------------------------------------------------------------------

def _validate_stage2(config: Config, env: Environment) -> list[str]:
    errors: list[str] = []

    hmm     = config["hash_mem_multiplier"]
    cpu_tup = config["cpu_tuple_cost"]
    cpu_idx = config["cpu_index_tuple_cost"]
    cpu_op  = config["cpu_operator_cost"]
    ptc     = config["parallel_tuple_cost"]

    # Hierarquia semântica de custos: operador ≤ índice ≤ tupla
    if cpu_idx > cpu_tup:
        errors.append(f"E2: cpu_index_tuple_cost ({cpu_idx}) > cpu_tuple_cost ({cpu_tup})")
    if cpu_op > cpu_idx:
        errors.append(f"E2: cpu_operator_cost ({cpu_op}) > cpu_index_tuple_cost ({cpu_idx})")
    if cpu_op > cpu_tup:
        errors.append(f"E2: cpu_operator_cost ({cpu_op}) > cpu_tuple_cost ({cpu_tup})")

    # Custo de transferência paralela não pode exceder muito o custo de tupla
    if cpu_tup > 0 and ptc / cpu_tup > _MAX_PARALLEL_TUPLE_RATIO:
        errors.append(
            f"E2: parallel_tuple_cost ({ptc}) / cpu_tuple_cost ({cpu_tup}) = "
            f"{ptc / cpu_tup:.0f}× > {_MAX_PARALLEL_TUPLE_RATIO}×"
        )

    if hmm < 1.0:
        errors.append(f"E2: hash_mem_multiplier ({hmm}) < 1.0: inválido")

    # Com hashjoin e mergejoin desabilitados, o planner usa apenas nested loop.
    # Para workloads OLAP (TPC-H / TPC-DS), nested loop em tabelas grandes é
    # catastrófico: queries que demoram 30s passam a demorar horas. Esta combinação
    # já causou abandono por timeout em múltiplas tasks e não acrescenta valor ao
    # dataset ML além dos poucos exemplos já coletados.
    no_hj = config.get("enable_hashjoin")  == "off"
    no_mj = config.get("enable_mergejoin") == "off"
    if no_hj and no_mj:
        errors.append(
            "E2: enable_hashjoin=off + enable_mergejoin=off: apenas nested loop "
            "disponível; catastrófico para OLAP (timeout garantido)"
        )

    return errors


# ---------------------------------------------------------------------------
# Validação cruzada E1+E2
# ---------------------------------------------------------------------------

def _validate_cross_12(config: Config, env: Environment) -> list[str]:
    """Restrições verificáveis apenas quando Etapa 1 e Etapa 2 estão juntas."""
    errors: list[str] = []

    ram_mb  = env["memory_mb"]
    work    = parse_memory(config["work_mem"])           # E1
    per_g   = config["max_parallel_workers_per_gather"]  # E1
    hmm     = config["hash_mem_multiplier"]              # E2
    psc     = config["parallel_setup_cost"]              # E2

    # Pico de memória de hash por query paralela não pode exceder 85% da RAM
    hash_total = hmm * work * (per_g + 1)
    hash_limit = ram_mb * 0.85
    if hash_total > hash_limit:
        errors.append(
            f"Cross E1+E2: hash_mem_multiplier ({hmm}) × work_mem ({work}MB) × "
            f"(per_gather+1={per_g+1}) = {hash_total:.0f}MB > RAM×0.85 ({hash_limit:.0f}MB)"
        )

    # parallel_setup_cost alto com per_gather > 1: o planner escolherá planos seriais
    # mesmo com workers disponíveis: per_gather torna-se um parâmetro fantasma
    if psc > 1200 and per_g > 1:
        errors.append(
            f"Cross E1+E2: parallel_setup_cost ({psc}) > 1200 com "
            f"per_gather={per_g}: planner evitará planos paralelos"
        )

    # Sem nenhum método de join disponível: deadlock de planner
    no_nl = config.get("enable_nestloop") == "off"   # E1
    no_mj = config.get("enable_mergejoin") == "off"  # E2
    no_hj = config.get("enable_hashjoin") == "off"   # E2
    if no_nl and no_mj and no_hj:
        errors.append(
            "Cross E1+E2: enable_nestloop=off + enable_mergejoin=off + "
            "enable_hashjoin=off: nenhum método de join disponível"
        )

    return errors


