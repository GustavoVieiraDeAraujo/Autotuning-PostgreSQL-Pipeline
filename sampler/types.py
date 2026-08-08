"""
Aliases de tipo usados em todo o pacote config_gen.
"""

from typing import Any

# Espaço de parâmetros carregado de config/spaces/stage{N}/*.json
ParameterSpace = dict[str, Any]

# Uma configuração PostgreSQL gerada: {nome_do_param: valor}
Config = dict[str, Any]

# Especificação do container Docker carregada de config/docker.json
Environment = dict[str, Any]

# Espaços de parâmetros indexados por número de etapa: {1: ParameterSpace, ...}
StageSpaces = dict[int, ParameterSpace]
