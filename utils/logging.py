"""
Utilitários de logging para o terminal.

Centraliza cores ANSI, formatação de mensagens com timestamp e a classe
TeeWriter usada para duplicar stdout para um arquivo de log (lido pela
interface web via SSE).

Classes
-------
    TeeWriter
        Duplica escritas de stdout para um arquivo de log simultaneamente.

Funções
-------
    log(msg, level, indent): imprime mensagem com timestamp colorido
    banner(title): imprime cabeçalho em caixa dupla
    sep(char, width): imprime linha separadora
"""

from datetime import datetime
from pathlib import Path

_MAX_LOG_BYTES  = 4 * 1024 * 1024  # rotaciona ao atingir 4 MB
_KEEP_LOG_BYTES = 2 * 1024 * 1024  # mantém os últimos 2 MB (≈ 1 hora)

# ---------------------------------------------------------------------------
# Cores ANSI
# ---------------------------------------------------------------------------

RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
CYAN   = "\033[36m"
VIOLET = "\033[35m"

_LEVEL_COLORS = {
    "INFO":  RESET,
    "OK":    GREEN,
    "WARN":  YELLOW,
    "ERROR": RED,
    "HEAD":  CYAN + BOLD,
    "DIM":   DIM,
}


# ---------------------------------------------------------------------------
# TeeWriter: duplica stdout para arquivo de log (lido pelo dashboard via SSE)
# ---------------------------------------------------------------------------

class TeeWriter:
    """Escreve simultaneamente no stream original e em um arquivo de log.

    Substitui ``sys.stdout`` para que toda saída do processo seja
    capturada no arquivo de log sem alterar o comportamento do terminal.

    Args:
        stream: Stream original (normalmente ``sys.stdout``).
        path:   Caminho do arquivo de log onde duplicar a saída.
        mode:   Modo de abertura do arquivo ("w" para sobrescrever,
                "a" para acrescentar). Padrão: "w".

    Example:
        tee = TeeWriter(sys.stdout, Path("output/runner.log"))
        sys.stdout = tee
        # ... processamento ...
        sys.stdout = tee.original
        tee.close()
    """

    def __init__(self, stream, path: Path, mode: str = "w") -> None:
        self.original = stream
        self._path    = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file    = open(path, mode, encoding="utf-8", buffering=1)  # noqa: SIM115
        self._written = 0

    def write(self, data: str) -> int:
        self.original.write(data)
        self._file.write(data)
        self._written += len(data.encode("utf-8"))
        if self._written >= _MAX_LOG_BYTES:
            self._rotate()
        return len(data)

    def _rotate(self) -> None:
        """Descarta a parte mais antiga do log, mantendo os últimos _KEEP_LOG_BYTES."""
        self._file.close()
        size = self._path.stat().st_size
        if size > _KEEP_LOG_BYTES:
            with open(self._path, "rb") as f:
                f.seek(size - _KEEP_LOG_BYTES)
                f.readline()  # descarta linha parcial no ponto de corte
                data = f.read()
            with open(self._path, "wb") as f:
                f.write(data)
        self._file    = open(self._path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        self._written = 0

    def flush(self) -> None:
        self.original.flush()
        self._file.flush()

    def isatty(self) -> bool:
        return self.original.isatty()

    def fileno(self) -> int:
        return self.original.fileno()

    def close(self) -> None:
        self._file.close()

    @property
    def _stream(self):
        """Alias para self.original: compatibilidade com código legado."""
        return self.original


# ---------------------------------------------------------------------------
# Funções de saída
# ---------------------------------------------------------------------------

def log(msg: str, level: str = "INFO", indent: int = 0) -> None:
    """Imprime uma mensagem com timestamp e nível colorido.

    Args:
        msg:    Mensagem a exibir.
        level:  Nível de log: "INFO", "OK", "WARN", "ERROR", "HEAD", "DIM".
        indent: Número de indentações de 2 espaços.
    """
    ts    = datetime.now().strftime("%H:%M:%S")
    pad   = "  " * indent
    color = _LEVEL_COLORS.get(level, RESET)
    print(f"{DIM}[{ts}]{RESET} {pad}{color}{msg}{RESET}", flush=True)


def sep(char: str = "─", width: int = 72) -> None:
    """Imprime uma linha separadora.

    Args:
        char:  Caractere repetido para compor a linha. Padrão: "─".
        width: Largura total da linha. Padrão: 72.
    """
    print(DIM + char * width + RESET, flush=True)


def banner(title: str) -> None:
    """Imprime um banner com título centralizado em caixa dupla.

    Args:
        title: Texto do título.
    """
    sep("═")
    print(f"{CYAN}{BOLD}  {title}{RESET}", flush=True)
    sep("═")
