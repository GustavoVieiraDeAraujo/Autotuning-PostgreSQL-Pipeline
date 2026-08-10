"""
Amostragem via Latin Hypercube (LHS) para o espaço de parâmetros PostgreSQL.

O LHS cobre todos os parâmetros das etapas solicitadas, garantindo
cobertura uniforme de até 33 dimensões sem correlações artificiais
entre parâmetros de diferentes dimensões.

Funções principais
------------------
    lhs_quantiles(n, stages, seed) → list[dict]
        Gera n conjuntos de quantis LHS para as etapas especificadas.

Funções auxiliares de amostragem (usadas por parameter_builder)
---------------------------------------------------------------
    _pick(choices, q): seleciona por quantil ou aleatório
    _uniform(lo, hi, q): amostra float no intervalo [lo, hi]
    _randint(lo, hi, q): amostra inteiro no intervalo [lo, hi]

Funções de filtragem de choices
--------------------------------
    filter_memory_choices(choices, min_mb, max_mb)
"""

import random

from .unit_parsers import parse_memory


# ---------------------------------------------------------------------------
# Parâmetros LHS por etapa: Etapa 1 = 13, Etapa 2 = 12, Etapa 3 = 8 (total: 33)
# Parâmetros fixos não entram no LHS: seq_page_cost, max_worker_processes, synchronous_commit
# ---------------------------------------------------------------------------

_LHS_STAGE_1: list[str] = [
    "jit",
    "random_page_cost",
    "default_statistics_target",
    "max_parallel_workers",
    "max_parallel_workers_per_gather",
    "shared_buffers",
    "effective_cache_size",
    "work_mem",
    "enable_hashagg",
    "enable_bitmapscan",
    "enable_nestloop",
    "enable_parallel_hash",
    "enable_sort",
]

_LHS_STAGE_2: list[str] = [
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
    "min_parallel_table_scan_size",
    "min_parallel_index_scan_size",
    "join_collapse_limit",
    "from_collapse_limit",
    "hash_mem_multiplier",
    "enable_mergejoin",
    "enable_hashjoin",
]

_LHS_STAGE_3: list[str] = [
    "enable_memoize",
    "enable_gathermerge",
    "enable_incremental_sort",
    "enable_material",
    "enable_indexscan",
    "enable_indexonlyscan",
    "enable_parallel_append",
    "parallel_leader_participation",
]

_LHS_BY_STAGE: dict[int, list[str]] = {
    1: _LHS_STAGE_1,
    2: _LHS_STAGE_2,
    3: _LHS_STAGE_3,
}


def _lhs_params_for(stages: list[int]) -> list[str]:
    """Retorna a lista unificada de parâmetros LHS para as etapas solicitadas."""
    params: list[str] = []
    for stage in sorted(stages):
        params.extend(_LHS_BY_STAGE[stage])
    return params


# ---------------------------------------------------------------------------
# Latin Hypercube Sampling
# ---------------------------------------------------------------------------

def lhs_quantiles(
    n: int,
    stages: list[int],
    seed: int | None = None,
) -> list[dict[str, float]]:
    """Gera n conjuntos de quantis via Latin Hypercube Sampling.

    Divide o intervalo [0, 1] em n estratos iguais para cada parâmetro
    e amostra um ponto aleatório dentro de cada estrato. Cada dimensão é
    embaralhada independentemente para eliminar correlações artificiais.

    Args:
        n:      Número de configurações a gerar.
        stages: Lista de etapas a incluir, ex: [1], [2], [1, 2, 3].
        seed:   Semente para reprodutibilidade. None = não-determinístico.

    Returns:
        Lista de n dicts ``{nome_do_param: quantil ∈ [0, 1]}``, onde cada
        dict representa uma configuração completa no espaço de quantis.
    """
    rng     = random.Random(seed)
    params  = _lhs_params_for(stages)
    columns = []
    for _ in params:
        strata = [(k + rng.random()) / n for k in range(n)]
        rng.shuffle(strata)
        columns.append(strata)

    return [
        {params[p]: columns[p][i] for p in range(len(params))}
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Helpers de amostragem com suporte a quantil (modo LHS)
# ---------------------------------------------------------------------------

def _pick(choices: list, q: float | None):
    """Seleciona um elemento de uma lista por quantil ou aleatoriamente.

    Args:
        choices: Lista de opções disponíveis.
        q:       Quantil em [0, 1] para modo LHS. None para aleatório.

    Returns:
        Elemento selecionado.
    """
    if q is None:
        return random.choice(choices)
    return choices[min(int(q * len(choices)), len(choices) - 1)]


def _uniform(lo: float, hi: float, q: float | None) -> float:
    """Amostra uniformemente no intervalo [lo, hi].

    Args:
        lo: Limite inferior.
        hi: Limite superior.
        q:  Quantil em [0, 1] para modo LHS. None para aleatório.

    Returns:
        Float no intervalo [lo, hi].
    """
    if q is None:
        return random.uniform(lo, hi)
    return lo + q * (hi - lo)


def _randint(lo: int, hi: int, q: float | None) -> int:
    """Amostra um inteiro no intervalo [lo, hi].

    Args:
        lo: Limite inferior (inclusivo).
        hi: Limite superior (inclusivo).
        q:  Quantil em [0, 1] para modo LHS. None para aleatório.

    Returns:
        Inteiro no intervalo [lo, hi].
    """
    if q is None:
        return random.randint(lo, hi)
    return round(lo + q * (hi - lo))


# ---------------------------------------------------------------------------
# Filtragem de listas de choices
# ---------------------------------------------------------------------------

def filter_memory_choices(
    choices: list[str],
    min_mb: int = 0,
    max_mb: int | None = None,
) -> list[str]:
    """Filtra uma lista de choices de memória por intervalo em MB.

    Args:
        choices: Lista de strings no formato "XMB" ou "XGB".
        min_mb:  Valor mínimo em MB (inclusivo). Padrão: 0.
        max_mb:  Valor máximo em MB (inclusivo). None = sem limite.

    Returns:
        Subconjunto de choices dentro do intervalo especificado.
    """
    result = []
    for choice in choices:
        mb = parse_memory(choice)
        if mb < min_mb:
            continue
        if max_mb is not None and mb > max_mb:
            continue
        result.append(choice)
    return result


