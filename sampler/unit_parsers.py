"""
Funções de parsing para formatos de valor usados nas etapas de geração.

Cobre: memória (MB/GB). Todas as funções são cacheadas via lru_cache —
strings idênticas retornam o resultado já computado sem re-parsear.
"""

from functools import lru_cache


@lru_cache(maxsize=None)
def parse_memory(value: str) -> float:
    """Converte string de memória para megabytes.

    Args:
        value: String no formato "XkB", "XMB" ou "XGB" (case-insensitive).

    Returns:
        Valor em megabytes (float para suportar valores sub-MB como "512kB").

    Raises:
        ValueError: Se o formato não for reconhecido.

    Examples:
        >>> parse_memory("2GB")
        2048.0
        >>> parse_memory("512MB")
        512.0
        >>> parse_memory("512kB")
        0.5
    """
    v = value.strip()
    vu = v.upper()
    if vu.endswith("GB"):
        return float(v[:-2]) * 1024
    if vu.endswith("MB"):
        return float(v[:-2])
    if v.lower().endswith("kb"):
        return float(v[:-2]) / 1024
    raise ValueError(f"Formato de memória inválido: {value!r}")


