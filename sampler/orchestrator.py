"""
Orquestração da geração de configurações por tier e combinação de etapas.

Carrega os espaços de parâmetros, instancia o ambiente Docker correto
e delega a geração para o parameter_builder, agregando os resultados
e o relatório de validação para todos os tiers.

Funções principais
------------------
    generate_all_tiers(stages, n_per_tier)
        Gera e valida configs para LOW, MEDIUM e HIGH, retornando em memória.

Funções auxiliares
------------------
    stages_description(stages) — texto legível da combinação
"""

import time
from pathlib import Path

from .parameter_builder import generate_valid_configs
from .constraints import validate_combined_config
from .space_loader import load_docker_config, load_stage_spaces
from .types import Config

# config_gen/orchestrator.py → config_gen/ → project root
_ROOT = Path(__file__).resolve().parents[1]

DOCKER_CONFIG_PATH = str(_ROOT / "specs" / "docker.json")

TIERS: list[str] = ["low", "medium", "high"]


_STAGE_PARAM_COUNTS = {1: 13, 2: 12, 3: 8}  # LHS params por etapa


def stages_description(stages: list[int]) -> str:
    """Gera uma descrição legível para uma combinação de etapas.

    Args:
        stages: Lista de números de etapa.

    Returns:
        String descritiva com o número de parâmetros abrangidos.
    """
    n_params = sum(_STAGE_PARAM_COUNTS.get(s, 12) for s in stages)
    _MAP = {
        (1,):      f"Etapa 1 ({n_params} params: paralelismo, memória, planner básico)",
        (2,):      f"Etapa 2 ({n_params} params: custos de CPU, collapse limits, hash memory)",
        (3,):      f"Etapa 3 ({n_params} params: execução avançada, I/O background)",
        (1, 2):    f"Etapas 1+2 ({n_params} params: mem./paralel. + custos de CPU)",
        (1, 3):    f"Etapas 1+3 ({n_params} params: mem./paralel. + execução avançada)",
        (2, 3):    f"Etapas 2+3 ({n_params} params: custos de CPU + execução avançada)",
        (1, 2, 3): f"Etapas 1+2+3 ({n_params} params: todos os parâmetros)",
    }
    key = tuple(sorted(stages))
    return _MAP.get(key, f"Etapas {'+'.join(str(s) for s in sorted(stages))} ({n_params} params)")


def generate_all_tiers(
    stages: list[int],
    n_per_tier: int,
    seed: int | None = None,
) -> tuple[dict[str, list[Config]], dict[str, float], dict[str, dict]]:
    """Gera e valida configurações para todos os tiers, retornando em memória.

    Para cada tier (low, medium, high), carrega o espaço de parâmetros
    correto, gera ``n_per_tier`` configurações via LHS e executa a
    validação estática em cada uma.

    Args:
        stages:     Lista de etapas a incluir, ex: [1], [2, 3], [1, 2, 3].
        n_per_tier: Número de configurações a gerar por tier.
        seed:       Semente base para o LHS. Cada tier deriva um seed único
                    (seed + índice) para garantir diversidade entre tiers e
                    reprodutibilidade global. None = não-determinístico.

    Returns:
        Tupla de três dicts:
          - ``valid_results``: ``{tier: [Config]}`` — configs válidas
          - ``tier_times``:    ``{tier: segundos}`` — tempo de geração
          - ``report``:        ``{tier: {total, valid, invalid, sample_error}}``
    """
    valid_results: dict[str, list[Config]] = {}
    tier_times:    dict[str, float]        = {}
    report:        dict[str, dict]         = {}

    for tier_idx, tier in enumerate(TIERS):
        env          = load_docker_config(DOCKER_CONFIG_PATH, tier)
        stage_spaces = load_stage_spaces(stages, tier)

        # Seed derivado por tier para que cada tier tenha cobertura LHS
        # independente, mas o conjunto inteiro seja reproduzível com a
        # mesma seed base.
        tier_seed = (seed + tier_idx) if seed is not None else None

        t0      = time.perf_counter()
        configs = generate_valid_configs(stage_spaces, env, stages, n_per_tier, seed=tier_seed)
        tier_times[tier] = time.perf_counter() - t0

        valid:        list[Config] = []
        n_invalid:    int          = 0
        sample_error: str          = ""

        for cfg in configs:
            errors = validate_combined_config(cfg, env, stage_spaces, stages)
            if errors:
                n_invalid += 1
                if not sample_error:
                    sample_error = errors[0]
            else:
                valid.append(cfg)

        valid_results[tier] = valid
        report[tier] = {
            "total":        len(configs),
            "valid":        len(valid),
            "invalid":      n_invalid,
            "sample_error": sample_error,
        }

    return valid_results, tier_times, report
