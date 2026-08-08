"""
pg_sampler
==========
Pacote para geração automática de configurações PostgreSQL via
Latin Hypercube Sampling, cobrindo até 36 parâmetros distribuídos
em 3 etapas de impacto crescente.

Uso rápido
----------
    from pg_sampler import generate_configs

    # Todas as etapas (33 params LHS)
    configs = generate_configs(stages=[1, 2, 3])

    # Combinação arbitrária (21 params LHS)
    configs = generate_configs(stages=[1, 3])

    # Etapa isolada (12 params LHS)
    configs = generate_configs(stages=[2])

    # Reproduzível
    configs = generate_configs(stages=[1, 2, 3], seed=42)

Estrutura de etapas (parâmetros amostrados via LHS)
----------------------------------------------------
    Etapa 1 (13 params): paralelismo, memória, planner básico
    Etapa 2 (12 params): custos de CPU, collapse limits, hash memory
    Etapa 3 ( 8 params): execução avançada

Tiers de hardware
-----------------
    low    : 2 vCPU / 2 GB RAM / SF=1
    medium : 4 vCPU / 4 GB RAM / SF=2
    high   : 6 vCPU / 5 GB RAM / SF=4
"""

from .orchestrator import generate_all_tiers, stages_description
from .display import (
    print_results_table,
    print_stages_header,
    print_validation_table,
)

_VALID_STAGES = {1, 2, 3}


def generate_configs(
    stages: list[int],
    total: int = 99,
    seed: int | None = None,
) -> dict[str, list]:
    """Gera configurações PostgreSQL para uma combinação arbitrária de etapas.

    Gera ``total`` configs via LHS (``total // 3`` por tier), valida cada
    uma e retorna em memória, sem escrita em disco.

    Args:
        stages: Lista de etapas a incluir, ex: [1], [2, 3], [1, 2, 3].
                Cada etapa cobre 12 parâmetros:
                  1 → paralelismo, memória, planner básico
                  2 → custos de CPU, collapse limits, hash memory
                  3 → execução avançada, I/O background
        total:  Total de configurações a gerar. Deve ser múltiplo de 3
                (distribuídas igualmente entre low, medium e high).
                Padrão: 99 (33 por tier).
        seed:   Semente para reprodutibilidade do LHS. None = não-determinístico.

    Returns:
        Dict ``{tier: [Config]}`` com as configurações válidas geradas.

    Raises:
        ValueError:   Se stages for inválido ou total não for múltiplo de 3.
        RuntimeError: Se algum slot LHS não puder ser preenchido.
    """
    if not stages or not all(s in _VALID_STAGES for s in stages):
        raise ValueError(
            f"stages deve ser uma lista não-vazia com valores em {{1, 2, 3}}; "
            f"recebido: {stages!r}"
        )
    if len(stages) != len(set(stages)):
        raise ValueError(f"stages não pode ter valores duplicados; recebido: {stages!r}")
    if total < 3 or total % 3 != 0:
        raise ValueError(
            f"total deve ser múltiplo de 3 e >= 3; recebido: {total!r}"
        )

    stages     = sorted(stages)
    n_per_tier = total // 3

    print_stages_header(len(stages), stages_description(stages))

    results, tier_times, report = generate_all_tiers(stages, n_per_tier, seed=seed)
    print_results_table(results, tier_times)
    print_validation_table(report)

    return results
