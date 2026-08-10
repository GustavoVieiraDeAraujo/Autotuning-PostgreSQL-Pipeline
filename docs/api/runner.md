# API Reference: `runner/`

Referência completa dos módulos de orquestração da execução.

## `runner/preflight.py`

### `run_preflight_checks`

```python
def run_preflight_checks(
    tier_configs: dict[str, dict],
    queue_tasks: list[dict],
    results_dir: str,
) -> bool
```

Executa 8 verificações antes de iniciar a execução de benchmarks. Retorna `True` se todas as verificações críticas passaram, `False` se alguma falha crítica impede a execução.

**Parâmetros:**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `tier_configs` | `dict` | Specs dos tiers: `{"low": {cpu, memory_mb, ...}, ...}` |
| `queue_tasks` | `list` | Lista de tarefas da fila (para verificações de integridade) |
| `results_dir` | `str` | Diretório raiz dos resultados (`"output/benchmark_results"`) |

**As 8 verificações:**

| # | Verificação | Crítica? | Ação se falhar |
|---|------------|----------|----------------|
| 1 | Docker daemon em execução (`docker ps`) | Sim | Abort |
| 2 | Espaço em disco ≥ threshold | Sim | Auto-prune, ou Abort se crítico |
| 3 | Sem containers obsoletos de execuções anteriores | Não | Aviso + lista containers |
| 4 | `queue.json` válido e coerente | Sim | Abort |
| 5 | Tabelas TPC-H presentes em cada tier | Não | Aviso |
| 6 | Tabelas TPC-DS presentes em cada tier | Não | Aviso |
| 7 | Conectividade TPC-H: `SELECT 1` funciona | Não | Aviso |
| 8 | Conectividade TPC-DS: `SELECT 1` funciona | Não | Aviso |

**Verificação 3: containers obsoletos:**

Verifica se há containers com nomes que correspondem ao padrão do projeto (`tpch_bench_*`, `tpcds_bench_*`, `*_conntest_*`, `*_smoketest_*`) ainda em execução. Isso pode indicar que uma execução anterior foi encerrada abruptamente sem limpar os containers.

**Verificações 5–8: conectividade:**

Para cada tier, inicia um container temporário `{db_name}_conntest_{tier}` com as imagens construídas pelo prepare, executa `SELECT 1` e conta as tabelas, depois remove o container. Essas verificações validam que as imagens Docker estão funcionais e contêm os dados esperados.

---

## `runner/task_executor.py`

### `run_task`

```python
def run_task(
    task: dict,
    tier_configs: dict[str, dict],
    tpch_callback: Callable,
    tpcds_callback: Callable,
) -> tuple[dict, dict, dict]
```

Executa uma tarefa completa: TPC-H + TPC-DS + coleta de métricas.

**Parâmetros:**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `task` | `dict` | Tarefa da fila: `{id, combination, tier, config, ...}` |
| `tier_configs` | `dict` | Specs dos tiers carregadas do `specs/docker.json` |
| `tpch_callback` | `Callable` | `run_tpch_benchmark` do `benchmarks/tpc_h` |
| `tpcds_callback` | `Callable` | `run_tpcds_benchmark` do `benchmarks/tpc_ds` |

**Retorna:** Tupla `(tpch_result, tpcds_result, hw_metrics)`.

**Lança:**
- `InvalidConfigError`: (re-exportada de `benchmarks.container`) se o PostgreSQL rejeitar a config
- `TaskTimeoutError`: (re-exportada de `benchmarks.query_executor`) se o tempo total exceder o timeout do tier

**Timeout por tier:**

```python
_TASK_TIMEOUT_S = {
    "low":    2 * 3600,   # 2 horas
    "medium": 4 * 3600,   # 4 horas
    "high":   8 * 3600,   # 8 horas
}
```

**Fluxo interno:**

```python
def run_task(task, tier_configs, tpch_callback, tpcds_callback):
    tier = task["tier"]
    tier_config = tier_configs[tier]
    pg_config = task["config"]
    task_id = task["id"]
    timeout_s = _TASK_TIMEOUT_S[tier]

    collector = MetricsCollector(interval_s=2.0)
    collector.start()

    start_time = time.time()

    try:
        # TPC-H
        tpch_image = TIER_IMAGE_TAGS["tpch"][tier]
        tpch_container = start_postgres_container(
            tier_config, pg_config, "tpch",
            tpch_image, f"tpch_bench_{task_id}"
        )
        tpch_result = tpch_callback(tpch_container, tier_config)
        remove_postgres_container(tpch_container)

        # Verificar timeout
        if time.time() - start_time > timeout_s:
            raise TaskTimeoutError(f"Tarefa {task_id} excedeu {timeout_s}s")

        # TPC-DS
        tpcds_image = TIER_IMAGE_TAGS["tpcds"][tier]
        tpcds_container = start_postgres_container(
            tier_config, pg_config, "tpcds",
            tpcds_image, f"tpcds_bench_{task_id}"
        )
        tpcds_result = tpcds_callback(tpcds_container, tier_config)
        remove_postgres_container(tpcds_container)

    finally:
        hw_metrics = collector.stop()

    return tpch_result, tpcds_result, hw_metrics
```

### Re-exports

O `task_executor.py` re-exporta para conveniência do caller:

```python
from benchmarks.container import InvalidConfigError
from benchmarks.query_executor import TaskTimeoutError

__all__ = ["run_task", "InvalidConfigError", "TaskTimeoutError"]
```

Isso permite que o `cli/run.py` importe apenas de `runner.task_executor`:

```python
from runner.task_executor import run_task, InvalidConfigError, TaskTimeoutError
```

---

## `runner/result_writer.py`

Módulo responsável pela escrita incremental dos resultados em disco.

### Funções

#### `task_path`

```python
def task_path(
    results_dir: str,
    tier: str,
    combination: str,
    task_id: int,
) -> str
```

Retorna o caminho do arquivo de resultado:
```python
task_path("output/benchmark_results", "medium", "s1s2", 42)
# → "output/benchmark_results/medium/s1s2/task_42.json"
```

#### `init_task_file`

```python
def init_task_file(
    task_id: int,
    combination: str,
    tier: str,
    pg_config: dict,
    started_at: str,
) -> None
```

Cria o arquivo de resultado inicial com os campos básicos. O arquivo é criado imediatamente ao início da tarefa, antes de qualquer benchmark ser executado.

**Arquivo criado:**
```json
{
  "task_id": 42,
  "combination": "s1s2",
  "tier": "medium",
  "pg_config": {...},
  "started_at": "2026-04-05T14:23:11.456789",
  "finished_at": null,
  "duration_s": null,
  "status": "running",
  "tpc_h": null,
  "tpc_ds": null,
  "hw_metrics": null
}
```

#### `append_query_result`

```python
def append_query_result(
    task_id: int,
    benchmark: str,
    query_result: dict,
) -> None
```

Adiciona o resultado de uma query individual ao arquivo. Chamado a cada query durante a execução: permite inspecionar progresso em tempo real.

#### `finalize_benchmark_section`

```python
def finalize_benchmark_section(
    task_id: int,
    benchmark: str,   # "tpc_h" ou "tpc_ds"
    summary: dict,
    pg_stats: dict,
    total_ms: float,
    n_success: int,
    n_failed: int,
) -> None
```

Finaliza a seção de um benchmark no arquivo de resultado, adicionando `summary` e `pg_stats`.

#### `save_hw_metrics`

```python
def save_hw_metrics(task_id: int, hw_metrics: dict) -> None
```

Adiciona a seção `hw_metrics` ao arquivo de resultado.

#### `finalize_task_file`

```python
def finalize_task_file(
    task_id: int,
    finished_at: str,
    duration_s: float,
    status: str,
) -> None
```

Atualiza os campos `finished_at`, `duration_s` e `status` no arquivo de resultado. Chamado como última etapa após todos os benchmarks e métricas terem sido salvos.
