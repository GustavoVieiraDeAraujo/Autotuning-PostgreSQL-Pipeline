"""
Carregamento dos espaços de parâmetros e configurações Docker a partir de JSON.

Os espaços de parâmetros ficam em ``specs/spaces/stage{N}/{tier}.json``
e definem os intervalos e valores válidos para cada parâmetro PostgreSQL.
"""

import json
from pathlib import Path

from .types import Environment, ParameterSpace, StageSpaces

# config_gen/space_loader.py → config_gen/ → project root
_ROOT = Path(__file__).resolve().parents[1]

_STAGE_DIRS: dict[int, Path] = {
    1: _ROOT / "specs" / "spaces" / "stage1",
    2: _ROOT / "specs" / "spaces" / "stage2",
    3: _ROOT / "specs" / "spaces" / "stage3",
}


def load_parameter_space(path: Path) -> ParameterSpace:
    """Carrega um espaço de parâmetros de um arquivo JSON.

    Remove a chave ``_meta`` se presente (usada apenas para documentação
    interna dos arquivos de configuração).

    Args:
        path: Caminho para o arquivo JSON.

    Returns:
        Dict com os parâmetros e seus espaços de busca.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data.pop("_meta", None)
    return data


def load_stage_spaces(stages: list[int], tier: str) -> StageSpaces:
    """Carrega os espaços de parâmetros para as etapas e tier especificados.

    Args:
        stages: Lista de etapas a incluir, ex: [1], [2], [1, 2, 3].
        tier:   Tier Docker: "low", "medium" ou "high".

    Returns:
        Dict ``{etapa: ParameterSpace}`` para cada etapa listada.
    """
    return {
        stage: load_parameter_space(_STAGE_DIRS[stage] / f"{tier}.json")
        for stage in stages
    }


def load_docker_config(path: str | Path, tier: str) -> Environment:
    """Carrega a especificação de recursos Docker para um tier específico.

    Args:
        path: Caminho para ``config/docker.json``.
        tier: Nome do tier: "low", "medium" ou "high".

    Returns:
        Dict com ``cpu``, ``memory_mb``, ``memory_swap_mb``, ``shm_size_mb``.
    """
    with open(path, encoding="utf-8") as f:
        return json.load(f)[tier]

