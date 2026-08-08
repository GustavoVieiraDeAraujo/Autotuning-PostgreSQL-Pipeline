# Execução de Queries

O módulo `benchmarks/query_executor.py` é o coração do benchmark: executa todas as queries de um benchmark, coleta métricas detalhadas de cada query via `EXPLAIN ANALYZE BUFFERS`, coleta estatísticas do PostgreSQL e trata falhas de forma granular.

## Constantes

```python
_QUERY_TIMEOUT_MS = 900_000   # 15 minutos por query
_IMPUTE_TIMEOUT_MS = ...      # exec_ms imputado para queries com timeout
_IMPUTE_OOM_MS = ...          # exec_ms imputado para OOM (container morto)
```

O timeout de **15 minutos por query** é aplicado via `SET statement_timeout = '900000ms'` antes de cada query. Quando excedido, o PostgreSQL cancela a query e lança `QueryCanceled`. O runner captura isso e registra `failure_reason="timeout"`.

## Exceção

### `TaskTimeoutError`

```python
class TaskTimeoutError(Exception):
    pass
```

Lançada quando a tarefa completa (TPC-H + TPC-DS) excede o timeout do tier (2h/4h/8h). Diferente do timeout por query (`_QUERY_TIMEOUT_MS`), esta exceção cobre o tempo total da tarefa. Quando lançada, o runner chama `queue.mark_abandoned()` sem retry.

## Funções

### `run_benchmark`

```python
def run_benchmark(
    container: Container,
    queries: list[str],
    db_name: str,
    log_fn: Callable | None = None,
) -> dict
```

Função principal que executa todas as queries de um benchmark e retorna o resultado completo.

**Retorna um dicionário com:**

```python
{
    "queries": [
        {
            "query_id": 1,
            "query_name": "Q1",
            "success": True,
            "exec_ms": 1234.5,
            "failure_reason": "ok",
            "buffers": {
                "shared_hit": 45231,
                "shared_read": 1024,
                "shared_written": 0,
                "temp_read": 0,
                "temp_written": 0,
            },
            "plan": { ... }  # JSON do EXPLAIN ANALYZE BUFFERS
        },
        # ... uma entrada por query
    ],
    "summary": {
        "geo_mean_exec_ms": 856.3,
        "overall_cache_hit_ratio": 0.978,
        "queries_with_spill": 2,
    },
    "pg_stats": { ... },   # dados do pg_stat_bgwriter
    "total_ms": 45678.9,
    "n_success": 20,
    "n_failed": 2,
}
```

**Algoritmo:**

1. `reset_stats(conn)` — reseta contadores do `pg_stat_bgwriter`
2. Para cada query:
   - `SET statement_timeout = '900000ms'`
   - Executa a query com `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`
   - Captura `exec_ms` do `Execution Time` no plano
   - Captura contadores de buffers do plano
   - Em caso de exceção: registra `failure_reason` e imputa `exec_ms`
3. `collect_pg_stats(conn)` — coleta contadores finais do `pg_stat_bgwriter`
4. `compute_summary(query_results)` — calcula métricas agregadas

### `run_all_queries`

```python
def run_all_queries(
    conn: psycopg2.connection,
    queries: list[tuple[int, str, str]],  # (query_id, query_name, sql)
    log_fn: Callable | None = None,
) -> list[dict]
```

Executa cada query individualmente, capturando exceções por query para que uma falha não interrompa o benchmark.

**Tratamento de falhas por tipo:**

| Exceção Python | `failure_reason` | `exec_ms` |
|----------------|-----------------|-----------|
| `psycopg2.errors.QueryCanceled` | `"timeout"` | `_IMPUTE_TIMEOUT_MS` |
| `MemoryError` ou sinal OOM | `"oom"` | `_IMPUTE_OOM_MS` |
| Qualquer outra exceção | `"technical"` | `0` |

**Como o OOM é detectado:** Se o container PostgreSQL for morto pelo OOM killer do Linux, a conexão `psycopg2` cai com `OperationalError: connection to server was lost`. O runner distingue isso de outros erros de conexão verificando se o container ainda está em execução (`container.status`).

### `run_query`

```python
def run_query(
    conn: psycopg2.connection,
    query_id: int,
    query_name: str,
    sql: str,
) -> dict
```

Executa uma única query e retorna seu resultado. Configura o `statement_timeout` antes de cada execução:

```python
conn.execute("SET statement_timeout = %s", (_QUERY_TIMEOUT_MS,))
result = conn.execute(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}")
plan_json = result.fetchone()[0][0]  # EXPLAIN retorna JSON aninhado

exec_ms = plan_json["Execution Time"]
buffers = _extract_buffers(plan_json["Plan"])
```

### `reset_stats`

```python
def reset_stats(conn: psycopg2.connection) -> None
```

Reseta os contadores do `pg_stat_bgwriter` antes do início do benchmark:

```sql
SELECT pg_stat_reset_shared('bgwriter');
```

Isso garante que as estatísticas coletadas ao final do benchmark (`collect_pg_stats`) reflitam apenas a atividade do benchmark, não de execuções anteriores.

### `collect_pg_stats`

```python
def collect_pg_stats(conn: psycopg2.connection) -> dict
```

Coleta as estatísticas do `pg_stat_bgwriter` ao final do benchmark:

```sql
SELECT buffers_clean, maxwritten_clean, buffers_backend,
       buffers_alloc, buffers_checkpoint
FROM pg_stat_bgwriter;
```

Essas métricas indicam a pressão sobre o buffer pool durante o benchmark:
- `buffers_clean`: páginas escritas pelo bgwriter proativamente
- `buffers_backend`: páginas que backends tiveram que escrever diretamente (pressão alta)
- `buffers_checkpoint`: páginas escritas durante checkpoints

### `compute_summary`

```python
def compute_summary(query_results: list[dict]) -> dict
```

Calcula métricas agregadas a partir dos resultados individuais:

**Média geométrica do tempo de execução (`geo_mean_exec_ms`):**

```python
import math
times = [r["exec_ms"] for r in query_results]
geo_mean = math.exp(sum(math.log(t) for t in times) / len(times))
```

A média geométrica é preferida à aritmética para tempos de execução porque é menos sensível a outliers (uma query muito lenta inflaria a média aritmética).

**Cache hit ratio (`overall_cache_hit_ratio`):**

```python
total_hit = sum(r["buffers"]["shared_hit"] for r in query_results)
total_read = sum(r["buffers"]["shared_read"] for r in query_results)
cache_hit = total_hit / (total_hit + total_read) if (total_hit + total_read) > 0 else 0
```

Mede a proporção de leituras de página satisfeitas pelo cache (`shared_buffers`) vs disco. Valores próximos de 1.0 indicam que o `shared_buffers` está bem dimensionado para o workload.

**Queries com spill (`queries_with_spill`):**

```python
spill_count = sum(
    1 for r in query_results
    if r["buffers"]["temp_read"] > 0 or r["buffers"]["temp_written"] > 0
)
```

Conta quantas queries precisaram usar arquivos temporários em disco (spill) por exceder o `work_mem`. Alto número de spills indica que `work_mem` está subdimensionado.

## Resultado por query — estrutura completa

```json
{
    "query_id": 5,
    "query_name": "Q5",
    "success": true,
    "exec_ms": 2341.7,
    "failure_reason": "ok",
    "buffers": {
        "shared_hit": 128456,
        "shared_read": 2341,
        "shared_written": 0,
        "temp_read": 0,
        "temp_written": 0
    },
    "plan": {
        "Node Type": "Gather",
        "Parallel Aware": true,
        "Actual Total Time": 2341.7,
        "Plans": [...]
    }
}
```

**Campos de `buffers`:**

| Campo | Significado | Interpretação |
|-------|-------------|---------------|
| `shared_hit` | Blocos encontrados no `shared_buffers` | Alto = bom (cache eficiente) |
| `shared_read` | Blocos lidos do disco | Alto = `shared_buffers` insuficiente |
| `shared_written` | Blocos dirty escritos durante a query | Indica pressão no buffer pool |
| `temp_read` | Blocos lidos de arquivos temporários | Alto = `work_mem` insuficiente (spill) |
| `temp_written` | Blocos escritos em arquivos temporários | Alto = spill ocorrendo |

## Wrapper TPC-H (`tpc_h/benchmark.py`)

Thin wrapper que define as constantes específicas do TPC-H e delega para `query_executor`:

```python
DB_NAME = "tpch"
N_QUERIES = 22

def run_tpch_benchmark(container, tier_config, log_fn=None):
    return run_benchmark(container, TPC_H_QUERIES, DB_NAME, log_fn)
```

`TPC_H_QUERIES` é a lista das 22 queries SQL TPC-H, identificadas de Q1 a Q22.

## Wrapper TPC-DS (`tpc_ds/benchmark.py`)

Thin wrapper para TPC-DS:

```python
DB_NAME = "tpcds"
N_QUERIES = 99

def run_tpcds_benchmark(container, tier_config, log_fn=None):
    return run_benchmark(container, TPC_DS_QUERIES, DB_NAME, log_fn)
```

`TPC_DS_QUERIES` é a lista das 99 queries SQL TPC-DS. O TPC-DS é notavelmente mais diverso: algumas queries são triviais (< 1s), outras podem levar vários minutos e exercitam intensamente window functions e CTEs aninhadas.
