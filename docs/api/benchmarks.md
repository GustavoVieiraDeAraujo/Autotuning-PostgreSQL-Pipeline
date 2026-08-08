# API Reference — `benchmarks/`

Referência completa de todos os módulos do pacote `benchmarks`.

## `benchmarks/container.py`

### `start_postgres_container`

```python
def start_postgres_container(
    tier_config: dict,
    pg_config: dict,
    db_name: str,
    image: str,
    container_name: str,
    host_port: int = 5432,
    max_wait_s: float = 60.0,
    log_fn: Callable[[str], None] | None = None,
) -> docker.models.containers.Container
```

Inicia um container PostgreSQL com a configuração especificada e aguarda o servidor estar pronto.

**Parâmetros:**

| Parâmetro | Tipo | Padrão | Descrição |
|-----------|------|--------|-----------|
| `tier_config` | `dict` | — | Specs do tier: `{cpu, memory_mb, memory_swap_mb, shm_size_mb}` |
| `pg_config` | `dict` | — | Parâmetros PostgreSQL: `{param: valor}` |
| `db_name` | `str` | — | Nome do banco (`"tpch"` ou `"tpcds"`) |
| `image` | `str` | — | Tag da imagem Docker (ex: `"tpch-sf2"`) |
| `container_name` | `str` | — | Nome do container (ex: `"tpch_bench_42"`) |
| `host_port` | `int` | `5432` | Porta do host mapeada para a 5432 do container |
| `max_wait_s` | `float` | `60.0` | Timeout de espera do PostgreSQL (segundos) |
| `log_fn` | `Callable \| None` | `None` | Callback de log (recebe string de mensagem) |

**Retorna:** Objeto `Container` do Docker SDK.

**Lança:**
- `InvalidConfigError` — PostgreSQL rejeitou um parâmetro
- `RuntimeError` — Timeout aguardando o PostgreSQL ficar pronto

### `remove_postgres_container`

```python
def remove_postgres_container(
    container: docker.models.containers.Container,
) -> None
```

Para e remove o container. Equivalente a `docker stop <container> && docker rm <container>`. Não lança exceção se o container já não existe.

### `InvalidConfigError`

```python
class InvalidConfigError(Exception):
    pass
```

Lançada quando o PostgreSQL rejeita um parâmetro. O runner captura esta exceção e chama `queue.mark_abandoned()` sem retry.

### `_build_postgres_args`

```python
def _build_postgres_args(pg_config: dict) -> list[str]
```

Converte `{"shared_buffers": "1GB", "work_mem": "64MB"}` em `["-c", "shared_buffers=1GB", "-c", "work_mem=64MB"]`.

### `_is_invalid_pg_config`

```python
def _is_invalid_pg_config(logs: str) -> bool
```

Analisa os logs do container em busca de mensagens de erro do PostgreSQL que indicam configuração inválida. Retorna `True` se detectar padrões como `FATAL: invalid value for parameter`.

### `_wait_postgres_ready`

```python
def _wait_postgres_ready(
    container: Container,
    host_port: int,
    db_name: str,
    max_wait_s: float,
) -> None
```

Aguarda o PostgreSQL aceitar conexões. Tenta a cada 0.5 segundos. Verifica se o container ainda está em execução (pode ter falhado por `InvalidConfigError`).

---

## `benchmarks/image_builder.py`

### Constantes

```python
TIER_IMAGE_TAGS = {
    "tpch":  {"low": "tpch-sf1",  "medium": "tpch-sf2",  "high": "tpch-sf4"},
    "tpcds": {"low": "tpcds-sf1", "medium": "tpcds-sf2", "high": "tpcds-sf4"},
}

TIER_SCALE_FACTORS = {
    "low": 1,
    "medium": 2,
    "high": 4,
}
```

### `build_image`

```python
def build_image(
    benchmark: str,
    scale_factor: int,
    image_tag: str,
) -> None
```

Constrói uma imagem Docker com dados TPC pré-carregados. Se a imagem já existir, retorna imediatamente sem rebuild.

**Processo:**
1. Verifica se `image_exists(image_tag)` → skip se True
2. Inicia container `{benchmark}-build-tmp-sf{scale_factor}` da imagem base
3. Aguarda `_wait_init_complete(container)` (até 1 hora)
4. Para e comita como `image_tag`
5. Remove o container temporário

### `image_exists`

```python
def image_exists(image_tag: str) -> bool
```

Verifica se uma imagem Docker existe localmente.

```python
from benchmarks.image_builder import image_exists, TIER_IMAGE_TAGS

for tier in ["low", "medium", "high"]:
    tag = TIER_IMAGE_TAGS["tpch"][tier]
    print(f"{tag}: {'OK' if image_exists(tag) else 'FALTANDO'}")
```

### `_wait_init_complete`

```python
def _wait_init_complete(container, timeout_s: float = 3600.0) -> None
```

Aguarda o script de inicialização TPC completar verificando a presença de um arquivo sentinel a cada 10 segundos.

---

## `benchmarks/query_executor.py`

### Constantes

```python
_QUERY_TIMEOUT_MS = 900_000   # 15 minutos por query
```

### `TaskTimeoutError`

```python
class TaskTimeoutError(Exception):
    pass
```

Lançada quando a tarefa completa (TPC-H + TPC-DS juntos) excede o timeout do tier (2h/4h/8h). O runner captura e chama `queue.mark_abandoned()`.

### `run_benchmark`

```python
def run_benchmark(
    container: Container,
    queries: list[tuple[int, str, str]],  # (id, name, sql)
    db_name: str,
    log_fn: Callable | None = None,
) -> dict
```

Executa todas as queries de um benchmark. Retorna o resultado completo com queries, summary e pg_stats.

**Fluxo interno:**
1. Conecta ao PostgreSQL no container
2. `reset_stats(conn)`
3. `run_all_queries(conn, queries, log_fn)`
4. `collect_pg_stats(conn)`
5. `compute_summary(query_results)`
6. Retorna dicionário completo

### `run_all_queries`

```python
def run_all_queries(
    conn: psycopg2.connection,
    queries: list[tuple[int, str, str]],
    log_fn: Callable | None = None,
) -> list[dict]
```

Executa cada query individualmente, capturando exceções por query. Uma falha em uma query não interrompe as demais.

**Tratamento de exceções:**
- `psycopg2.errors.QueryCanceled` → `failure_reason="timeout"`, `exec_ms=_IMPUTE_TIMEOUT_MS`
- `MemoryError` / OOM detectado → `failure_reason="oom"`, `exec_ms=_IMPUTE_OOM_MS`
- Outros → `failure_reason="technical"`, `exec_ms=0`

### `run_query`

```python
def run_query(
    conn: psycopg2.connection,
    query_id: int,
    query_name: str,
    sql: str,
) -> dict
```

Executa uma única query com `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`. Retorna dicionário com `exec_ms`, `buffers`, `plan`, `success`, `failure_reason`.

**Configuração por query:**
```sql
SET statement_timeout = '900000ms';
EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) <sql>;
```

### `reset_stats`

```python
def reset_stats(conn: psycopg2.connection) -> None
```

Reseta contadores do `pg_stat_bgwriter`:
```sql
SELECT pg_stat_reset_shared('bgwriter');
```

### `collect_pg_stats`

```python
def collect_pg_stats(conn: psycopg2.connection) -> dict
```

Coleta contadores do `pg_stat_bgwriter` após o benchmark:
```sql
SELECT buffers_clean, maxwritten_clean, buffers_backend,
       buffers_alloc, buffers_checkpoint
FROM pg_stat_bgwriter;
```

### `compute_summary`

```python
def compute_summary(query_results: list[dict]) -> dict
```

Calcula métricas agregadas:

- `geo_mean_exec_ms`: média geométrica do `exec_ms` (exclui `failure_reason="technical"`)
- `overall_cache_hit_ratio`: `sum(shared_hit) / (sum(shared_hit) + sum(shared_read))`
- `queries_with_spill`: contagem de queries com `temp_read > 0 or temp_written > 0`

---

## `benchmarks/tpc_h/benchmark.py`

```python
DB_NAME = "tpch"
N_QUERIES = 22

def run_tpch_benchmark(
    container: Container,
    tier_config: dict,
    log_fn: Callable | None = None,
) -> dict
```

Wrapper fino que chama `run_benchmark(container, TPC_H_QUERIES, DB_NAME, log_fn)`.

`TPC_H_QUERIES`: lista de 22 tuplas `(query_id, query_name, sql)` com as queries padrão TPC-H (Q1–Q22).

---

## `benchmarks/tpc_ds/benchmark.py`

```python
DB_NAME = "tpcds"
N_QUERIES = 99

def run_tpcds_benchmark(
    container: Container,
    tier_config: dict,
    log_fn: Callable | None = None,
) -> dict
```

Wrapper fino que chama `run_benchmark(container, TPC_DS_QUERIES, DB_NAME, log_fn)`.

`TPC_DS_QUERIES`: lista de 99 tuplas com as queries TPC-DS (Q1–Q99). O TPC-DS tem queries significativamente mais complexas que o TPC-H, especialmente em window functions e CTEs aninhadas.
