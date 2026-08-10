"""
Formatação e exibição das tabelas de saída no terminal.

Exibe tabelas com bordas Unicode para:
  - Configurações dos containers Docker (tiers)
  - Combinação de etapas sendo gerada
  - Resultados por tier e tempo de geração
  - Resumo final com totais por combinação
  - Relatório de validação estática

Funções
-------
    print_docker_table(docker)
    print_stages_header(n_stages, description)
    print_results_table(results, tier_times)
    print_summary_table(all_results, total_time, queue_path)
    print_validation_table(report)
    fmt_time(seconds)
"""

import re

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

_W   = 54                     # largura interna da caixa
_TOP = "╔" + "═" * _W + "╗"
_BOT = "╚" + "═" * _W + "╝"
_SEP = "╠" + "═" * _W + "╣"

_YELLOW = "\033[93m"
_RED    = "\033[91m"
_GREEN  = "\033[92m"
_CYAN   = "\033[96m"
_RESET  = "\033[0m"

_ANSI = re.compile(r"\033\[[0-9;]*m")


# ---------------------------------------------------------------------------
# Helpers internos de formatação de linhas
# ---------------------------------------------------------------------------

def _row(text: str) -> str:
    """Formata uma linha dentro da caixa, compensando caracteres ANSI invisíveis."""
    visible = len(_ANSI.sub("", text))
    return "║" + text + " " * (_W - visible) + "║"


def _title(text: str) -> str:
    return _row(f"{_YELLOW}{text.center(_W)}{_RESET}")


def _subtitle(text: str) -> str:
    return _row(f"{_RED}{text.center(_W)}{_RESET}")


def _info(text: str) -> str:
    return _row(f"{_CYAN}{text.center(_W)}{_RESET}")


def _wrap(text: str) -> list[str]:
    """Quebra texto em linhas que cabem em _W caracteres, respeitando palavras."""
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = f"{current} {word}" if current else word
        if len(candidate) <= _W:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


# ---------------------------------------------------------------------------
# Formatação de tempo
# ---------------------------------------------------------------------------

def fmt_time(seconds: float) -> str:
    """Formata uma duração para a unidade mais legível.

    Args:
        seconds: Duração em segundos.

    Returns:
        String no formato "Xµs", "X.Xms" ou "X.XXs".
    """
    if seconds < 0.001:
        return f"{seconds * 1_000_000:.0f}µs"
    if seconds < 1.0:
        return f"{seconds * 1_000:.1f}ms"
    return f"{seconds:.2f}s"


# ---------------------------------------------------------------------------
# Tabela: especificações dos containers Docker
# ---------------------------------------------------------------------------

_CT, _CC, _CR, _CS, _CW = 8, 5, 6, 8, 5


def _docker_row(tier: str, cpu: str, ram: str, shm: str, swap: str, color: str = "") -> str:
    line = f"  {tier:<{_CT}}  {cpu:<{_CC}}  {ram:<{_CR}}  {shm:<{_CS}}  {swap:<{_CW}}"
    return _row(f"{color}{line}{_RESET}") if color else _row(line)


def print_docker_table(docker: dict) -> None:
    """Exibe as especificações de recursos dos containers Docker por tier.

    Args:
        docker: Dict ``{tier: {cpu, memory_mb, memory_swap_mb, shm_size_mb}}``.
    """
    print(_TOP)
    print(_title("PostgreSQL Combined Config Generator"))
    print(_SEP)
    print(_subtitle("Docker Container Settings"))
    print(_SEP)
    print(_docker_row("Tier", "CPU", "RAM", "SHM", "Swap", color=_RED))
    print(_SEP)
    for tier, spec in docker.items():
        # swap == memory = sem swap; caso contrário mostra o valor
        swap = "off" if spec["memory_swap_mb"] == spec["memory_mb"] else f"{spec['memory_swap_mb']}MB"
        print(_docker_row(
            tier.upper(),
            f"{spec['cpu']}v",
            f"{spec['memory_mb'] // 1024}GB",
            f"{spec['shm_size_mb']}MB",
            swap,
        ))
    print(_BOT)
    print()


# ---------------------------------------------------------------------------
# Tabela: cabeçalho de etapas sendo geradas
# ---------------------------------------------------------------------------

def print_stages_header(n_stages: int, description: str) -> None:
    """Abre uma nova caixa para a combinação de etapas sendo gerada.

    Args:
        n_stages:    Número de etapas na combinação.
        description: Texto descritivo da combinação (ex: "Etapas 1+2 (30 params...)").
    """
    print(_TOP)
    for line in _wrap(f"Combinação: {description}"):
        print(_info(line))


# ---------------------------------------------------------------------------
# Tabela: resultados de uma combinação
# ---------------------------------------------------------------------------

_RL, _RR = 8, 9


def _result_row(tier: str, configs: str, time: str, color: str = "") -> str:
    left  = f"  {tier:<{_RL - 2}}"
    right = f"{time:>{_RR - 2}}  "
    mid   = configs.center(_W - _RL - _RR)
    line  = left + mid + right
    return _row(f"{color}{line}{_RESET}") if color else _row(line)


def print_results_table(results: dict, tier_times: dict) -> None:
    """Exibe a tabela de resultados de uma combinação de etapas.

    Args:
        results:    Dict ``{tier: [Config]}`` com as configs geradas.
        tier_times: Dict ``{tier: segundos}`` com o tempo por tier.
    """
    total      = sum(len(v) for v in results.values())
    total_time = sum(tier_times.values())

    print(_SEP)
    print(_title("Results"))
    print(_SEP)
    print(_result_row("Tier", "Configs", "Time", color=_RED))
    print(_SEP)
    for tier, configs in results.items():
        print(_result_row(tier.upper(), str(len(configs)), fmt_time(tier_times[tier])))
    print(_SEP)
    print(_result_row("Total", str(total), fmt_time(total_time)))
    print(_BOT)
    print()


# ---------------------------------------------------------------------------
# Tabela: resumo final com totais por combinação
# ---------------------------------------------------------------------------

def print_summary_table(
    all_results: dict[str, dict[str, list]],
    total_time: float,
    queue_path: str = "queue.json",
) -> None:
    """Exibe tabela resumo com o total de configurações geradas por combinação.

    Args:
        all_results: Dict ``{label: {tier: [Config]}}`` com todos os resultados.
        total_time:  Tempo total de execução em segundos.
        queue_path:  Caminho relativo da fila gerada (exibido no rodapé).
    """
    tiers = ["low", "medium", "high"]
    _CL, _CTW = 12, 8

    def _sum_row(label: str, per_tier: dict[str, int], grand: int, color: str = "") -> str:
        cells = f"  {label:<{_CL - 2}}"
        for t in tiers:
            cells += f"  {per_tier.get(t, 0):>{_CTW - 2}}"
        cells += f"  {grand:>{_CTW - 2}}"
        return _row(f"{color}{cells}{_RESET}") if color else _row(cells)

    print(_TOP)
    print(_title("Resumo Final: Total de Configurações"))
    print(_SEP)

    header = f"  {'Combinação':<{_CL - 2}}"
    for t in tiers:
        header += f"  {t.upper():>{_CTW - 2}}"
    header += f"  {'TOTAL':>{_CTW - 2}}"
    print(_row(f"{_RED}{header}{_RESET}"))
    print(_SEP)

    grand_total = 0
    grand_per_tier: dict[str, int] = {t: 0 for t in tiers}

    for label, tier_results in all_results.items():
        per_tier = {t: len(tier_results.get(t, [])) for t in tiers}
        total    = sum(per_tier.values())
        grand_total += total
        for t in tiers:
            grand_per_tier[t] += per_tier[t]
        print(_sum_row(label, per_tier, total))

    print(_SEP)
    print(_sum_row("TOTAL", grand_per_tier, grand_total, color=_YELLOW))
    print(_SEP)
    print(_row(f"{_CYAN}  Tempo total: {fmt_time(total_time)}{_RESET}"))
    print(_SEP)
    label = "Output : " + queue_path
    if len(label) > _W:
        label = label[:_W - 1] + "…"
    print(_row(f"{_YELLOW}{label.center(_W)}{_RESET}"))
    print(_BOT)
    print()


# ---------------------------------------------------------------------------
# Tabela: validação estática dos configs gerados
# ---------------------------------------------------------------------------

_VT, _VN, _VV, _VI = 8, 7, 8, 9


def _val_row(
    tier: str,
    total: str,
    valid: str,
    invalid: str,
    note: str = "",
    color: str = "",
) -> str:
    line = (
        f"  {tier:<{_VT - 2}}"
        f"  {total:>{_VN - 2}}"
        f"  {valid:>{_VV - 2}}"
        f"  {invalid:>{_VI - 2}}"
        f"  {note}"
    )
    return _row(f"{color}{line}{_RESET}") if color else _row(line)


def print_validation_table(report: dict) -> None:
    """Exibe o relatório de validação estática após a geração.

    Args:
        report: Dict ``{tier: {total, valid, invalid, sample_error}}``.
    """
    total_all   = sum(v["total"]   for v in report.values())
    total_valid = sum(v["valid"]   for v in report.values())
    total_bad   = sum(v["invalid"] for v in report.values())
    approved    = total_bad == 0

    print(_TOP)
    print(_title("Validação Estática"))
    print(_SEP)
    print(_val_row("Tier", "Total", "Válidas", "Inválidas", color=_RED))
    print(_SEP)

    for tier, stats in report.items():
        n_inv     = stats["invalid"]
        note      = stats["sample_error"] if n_inv else f"{_GREEN}✔{_RESET}"
        inv_color = _RED if n_inv else _GREEN
        line = (
            f"  {tier.upper():<{_VT - 2}}"
            f"  {stats['total']:>{_VN - 2}}"
            f"  {_GREEN}{stats['valid']:>{_VV - 2}}{_RESET}"
            f"  {inv_color}{n_inv:>{_VI - 2}}{_RESET}"
            f"  {note}"
        )
        print(_row(line))

    print(_SEP)
    verdict = f"{_GREEN}✔ APROVADO{_RESET}" if approved else f"{_RED}✘ REPROVADO{_RESET}"
    line = (
        f"  {'Total':<{_VT - 2}}"
        f"  {total_all:>{_VN - 2}}"
        f"  {_GREEN}{total_valid:>{_VV - 2}}{_RESET}"
        f"  {_RED if total_bad else _GREEN}{total_bad:>{_VI - 2}}{_RESET}"
        f"  {verdict}"
    )
    print(_row(line))
    print(_BOT)
    print()
