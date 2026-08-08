# API Reference — `utils/`

Referência completa dos módulos utilitários compartilhados por todo o projeto.

---

## `utils/logging.py`

Centraliza cores ANSI, formatação de mensagens com timestamp e a classe `TeeWriter`, que duplica `stdout` para um arquivo de log lido pela interface web em tempo real via SSE.

### Constantes de cor ANSI

```python
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
CYAN   = "\033[36m"
VIOLET = "\033[35m"
```

Usadas diretamente em `runner/task_executor.py`, `cli/prepare.py` e outros módulos para colorir a saída do terminal.

---

### `class TeeWriter`

Substitui `sys.stdout` para que toda saída do processo seja simultaneamente exibida no terminal **e** gravada em um arquivo de log. A interface web lê esse arquivo via SSE para exibir o terminal ao vivo.

```python
class TeeWriter:
    def __init__(self, stream, path: Path, mode: str = "w") -> None
```

**Parâmetros:**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `stream` | `IO` | Stream original — normalmente `sys.stdout` |
| `path` | `Path` | Arquivo de log onde duplicar a saída |
| `mode` | `str` | `"w"` sobrescreve, `"a"` acrescenta. Padrão: `"w"` |

**Atributos:**

- `original` — referência ao stream original, usada para restaurar `sys.stdout` no bloco `finally`

**Métodos:**

| Método | Descrição |
|--------|-----------|
| `write(data)` | Escreve em `original` e no arquivo simultaneamente |
| `flush()` | Faz flush em ambos |
| `isatty()` | Delega para `original` — preserva comportamento de TTY |
| `fileno()` | Delega para `original` — compatibilidade com código que inspeciona o fd |
| `close()` | Fecha apenas o arquivo de log; nunca fecha `original` |
| `_stream` (property) | Alias para `self.original` — compatibilidade com código legado |

**Padrão de uso em todos os `cli/`:**

```python
tee        = TeeWriter(sys.stdout, _LOG_PATH)
sys.stdout = tee
try:
    _run(...)
finally:
    sys.stdout = tee._stream   # restaura stdout original
    tee.close()                # fecha o arquivo de log
```

O `mode="a"` é usado em `cli/prepare.py` para acrescentar ao log em vez de sobrescrever, preservando histórico de builds anteriores.

---

### `log(msg, level, indent)`

Imprime uma mensagem no terminal com timestamp colorido no formato `[HH:MM:SS]`.

```python
def log(msg: str, level: str = "INFO", indent: int = 0) -> None
```

**Parâmetros:**

| Parâmetro | Tipo | Padrão | Descrição |
|-----------|------|--------|-----------|
| `msg` | `str` | — | Texto da mensagem |
| `level` | `str` | `"INFO"` | Nível: `"INFO"`, `"OK"`, `"WARN"`, `"ERROR"`, `"HEAD"`, `"DIM"` |
| `indent` | `int` | `0` | Número de indentações de 2 espaços |

**Mapeamento de cores por nível:**

| Nível | Cor | Uso |
|-------|-----|-----|
| `INFO` | Padrão (branco) | Mensagens informativas gerais |
| `OK` | Verde | Sucesso, confirmações |
| `WARN` | Amarelo | Avisos não críticos |
| `ERROR` | Vermelho | Erros, falhas |
| `HEAD` | Ciano + negrito | Cabeçalhos de seção |
| `DIM` | Escurecido | Informações secundárias, progress updates |

**Exemplo de saída:**
```
[14:32:07] Iniciando container...
[14:32:11]   OK Container pronto em 4.2s
[14:32:11]   [TPC-H] Aguardando PostgreSQL... (12s)
```

---

### `sep(char, width)`

Imprime uma linha separadora horizontal.

```python
def sep(char: str = "─", width: int = 72) -> None
```

| Parâmetro | Padrão | Descrição |
|-----------|--------|-----------|
| `char` | `"─"` | Caractere repetido. Uso comum: `"─"` para seções, `"═"` para blocos principais |
| `width` | `72` | Largura total da linha |

---

### `banner(title)`

Imprime um cabeçalho com título centralizado entre duas linhas `═`.

```python
def banner(title: str) -> None
```

**Saída típica:**
```
════════════════════════════════════════════════════════════════════════
  BENCHMARK RUNNER — TPC-H + TPC-DS PostgreSQL Autotuning
════════════════════════════════════════════════════════════════════════
```

---

## `utils/formatting.py`

Utilitários de formatação de tempo e progresso, usados pelo runner para exibir durações e ETAs legíveis.

---

### `fmt_duration(seconds)`

Formata uma duração em segundos para string legível.

```python
def fmt_duration(seconds: float) -> str
```

**Exemplos:**

| Entrada | Saída |
|---------|-------|
| `45.3` | `"45.3s"` |
| `90` | `"1m 30s"` |
| `7500` | `"2h 05m"` |

**Lógica:**
- `< 60s` → `"Xs.Xs"`
- `< 3600s` → `"Xm YYs"`
- `≥ 3600s` → `"Xh YYm"`

---

### `fmt_eta(elapsed_s, done, total)`

Estima o tempo restante baseado na velocidade média de conclusão de tarefas.

```python
def fmt_eta(elapsed_s: float, done: int, total: int) -> str
```

**Parâmetros:**

| Parâmetro | Descrição |
|-----------|-----------|
| `elapsed_s` | Tempo decorrido desde o início (segundos) |
| `done` | Número de tarefas já concluídas |
| `total` | Total de tarefas na fila |

**Retorna:** String formatada com `fmt_duration` ou `"calculando..."` se `done == 0`.

**Fórmula:** `ETA = (elapsed_s / done) × (total - done)`

---

## `utils/docker_cleanup.py`

Monitora espaço em disco e executa limpeza automática de cache Docker/containerd. O acúmulo de snapshots, camadas e cache é o principal causador de crescimento silencioso do disco em workloads que criam e destroem containers repetidamente.

### Constantes

```python
PRUNE_EVERY_N_TASKS: int = 5
_DEFAULT_FREE_WARN_GB     = 20   # prune normal se livre < 20 GB
_DEFAULT_FREE_CRITICAL_GB = 10   # prune agressivo se livre < 10 GB
```

`PRUNE_EVERY_N_TASKS` é importado por `cli/run.py`: a cada 5 tarefas concluídas (ou abandonadas), o runner chama `auto_prune_if_needed` automaticamente.

---

### `disk_free_gb(path)`

```python
def disk_free_gb(path: Path | str = "/") -> float
```

Retorna o espaço livre em GB no sistema de arquivos que contém `path`. Usa `shutil.disk_usage` internamente.

---

### `disk_total_gb(path)`

```python
def disk_total_gb(path: Path | str = "/") -> float
```

Retorna o espaço total em GB. Útil para calcular percentual de uso.

---

### `docker_used_gb()`

```python
def docker_used_gb() -> dict[str, float]
```

Consulta `docker system df` e retorna o espaço ocupado por categoria.

**Retorna:**
```python
{
    "images":      float,   # GB usados por imagens
    "containers":  float,   # GB usados por containers parados
    "volumes":     float,   # GB usados por volumes
    "build_cache": float,   # GB usados por cache de build
    "total":       float,   # soma de todos
}
```

Retorna zeros em caso de falha (Docker inacessível, timeout).

---

### `prune_docker(aggressive, verbose)`

```python
def prune_docker(aggressive: bool = False, verbose: bool = True) -> dict[str, float]
```

Executa limpeza do Docker em dois níveis.

**Parâmetros:**

| Parâmetro | Padrão | Descrição |
|-----------|--------|-----------|
| `aggressive` | `False` | Se `True`, remove também imagens sem container ativo |
| `verbose` | `True` | Loga ações via `utils.logging.log` |

**Sequência de limpeza:**

1. **Containers parados** — remove containers com status `exited`, `dead` ou `created` que não sejam do projeto (prefixos `tpch_bench_`, `tpcds_bench_`, `tpch_conntest_`, `tpcds_conntest_`)
2. **Imagens dangling** — camadas sem tag e sem container referenciando
3. **Build cache** — via `docker builder prune -f`
4. **Imagens sem uso** *(apenas modo agressivo)* — imagens sem container ativo, exceto as essenciais do projeto (`tpch-postgres`, `tpcds-postgres`, `postgres`, `debian`)

**Retorna:**
```python
{
    "containers":     float,   # GB recuperados de containers
    "dangling_images": float,  # GB recuperados de imagens dangling
    "build_cache":    float,   # GB recuperados de build cache
    "unused_images":  float,   # GB recuperados de imagens sem uso (modo agressivo)
    "total":          float,   # total recuperado
}
```

---

### `auto_prune_if_needed(free_threshold_gb, critical_threshold_gb, path, verbose)`

```python
def auto_prune_if_needed(
    free_threshold_gb:     float = 20,
    critical_threshold_gb: float = 10,
    path:                  Path | str = "/",
    verbose:               bool = True,
) -> bool
```

Ponto de entrada principal chamado periodicamente pelo runner. Verifica o espaço livre e decide se e como limpar.

**Lógica de decisão:**

```
livre ≥ free_threshold_gb   → não faz nada, retorna False
livre < free_threshold_gb   → prune normal (sem remover imagens essenciais)
livre < critical_threshold_gb → prune agressivo (remove imagens não essenciais)
```

**Retorna:** `True` se alguma limpeza foi executada, `False` se o espaço estava OK.

**Uso no runner (`cli/run.py`):**
```python
done_so_far = tasks_done + tasks_abandoned
if done_so_far > 0 and done_so_far % PRUNE_EVERY_N_TASKS == 0:
    auto_prune_if_needed(path=_RESULTS_DIR.parent)
```

**Uso no preflight (`runner/preflight.py`):**
```python
if free_gb < _WARN_FREE_GB:   # 20 GB
    auto_prune_if_needed(
        free_threshold_gb=_WARN_FREE_GB,
        critical_threshold_gb=_MIN_FREE_GB,   # 10 GB
        path=results_dir.parent,
        verbose=True,
    )
```
