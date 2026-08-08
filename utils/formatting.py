"""
Utilitários de formatação de tempo e progresso.

Funções
-------
    fmt_duration(seconds) → str
        Formata segundos em string legível ("1m 30s", "2h 05m", etc.).

    fmt_eta(elapsed_s, done, total) → str
        Estima o tempo restante com base no progresso atual.
"""


def fmt_duration(seconds: float) -> str:
    """Formata uma duração em segundos para string legível.

    Args:
        seconds: Duração em segundos.

    Returns:
        String no formato "Xs", "Xm YYs" ou "Xh YYm".

    Examples:
        >>> fmt_duration(45.3)
        '45.3s'
        >>> fmt_duration(90)
        '1m 30s'
        >>> fmt_duration(7500)
        '2h 05m'
    """
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def fmt_eta(elapsed_s: float, done: int, total: int) -> str:
    """Estima o tempo restante com base no progresso atual.

    Usa a média de tempo por tarefa concluída para projetar o tempo
    necessário para completar as tarefas restantes.

    Args:
        elapsed_s: Tempo decorrido desde o início (segundos).
        done:      Número de tarefas concluídas até agora.
        total:     Total de tarefas.

    Returns:
        String formatada com o ETA, ou "calculando..." se done == 0.
    """
    if done == 0:
        return "calculando..."
    avg = elapsed_s / done
    remaining = avg * (total - done)
    return fmt_duration(remaining)
