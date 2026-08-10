# Formato dos Resultados

Cada tarefa de benchmark concluída gera um arquivo JSON em:

```
output/benchmark_results/{tier}/{combination}/task_{id}.json
```

**Exemplos de caminho:**
```
output/benchmark_results/low/s1/task_0.json
output/benchmark_results/medium/s1s2/task_45.json
output/benchmark_results/high/s1s2s3/task_629.json
```

## Estrutura completa do arquivo

```json
{
  "task_id": 42,
  "combination": "s1s2",
  "tier": "medium",
  "pg_config": {
    "shared_buffers": "1GB",
    "work_mem": "64MB",
    "max_parallel_workers": 4,
    "max_parallel_workers_per_gather": 2,
    "hash_mem_multiplier": 2.5,
    "enable_hashjoin": 1,
    "enable_nestloop": 0,
    "jit": 1,
    "random_page_cost": 1.5,
    "default_statistics_target": 200,
    "cpu_tuple_cost": 0.01,
    "join_collapse_limit": 10
  },
  "started_at": "2026-04-05T14:23:11.456789",
  "finished_at": "2026-04-05T16:01:34.123456",
  "duration_s": 5902.67,
  "status": "done",
  "tpc_h": { ... },
  "tpc_ds": { ... },
  "hw_metrics": { ... }
}
```

### Campos raiz

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `task_id` | int | ID único da tarefa (corresponde ao ID na fila) |
| `combination` | str | Rótulo da combinação de stages (ex: `"s1"`, `"s1s2s3"`) |
| `tier` | str | Tier de hardware (`"low"`, `"medium"`, `"high"`) |
| `pg_config` | dict | Configuração PostgreSQL exata usada no benchmark |
| `started_at` | str | ISO 8601: início da execução da tarefa |
| `finished_at` | str | ISO 8601: fim da execução da tarefa |
| `duration_s` | float | Duração total em segundos (TPC-H + TPC-DS + overhead) |
| `status` | str | `"done"`, `"failed"`, ou `"abandoned"` |
| `tpc_h` | dict | Resultados do benchmark TPC-H |
| `tpc_ds` | dict | Resultados do benchmark TPC-DS |
| `hw_metrics` | dict | Métricas de hardware coletadas durante a execução |

## Estrutura de `tpc_h` e `tpc_ds`

```json
{
  "queries": [
    {
      "query_id": 1,
      "query_name": "Q1",
      "success": true,
      "exec_ms": 1234.5,
      "failure_reason": "ok",
      "buffers": {
        "shared_hit": 128456,
        "shared_read": 2341,
        "shared_written": 0,
        "temp_read": 0,
        "temp_written": 0
      },
      "plan": {
        "Node Type": "Aggregate",
        "Strategy": "Plain",
        "Partial Mode": "Finalize",
        "Actual Total Time": 1234.5,
        "Plans": [...]
      }
    }
  ],
  "summary": {
    "geo_mean_exec_ms": 856.3,
    "overall_cache_hit_ratio": 0.982,
    "queries_with_spill": 2
  },
  "pg_stats": {
    "buffers_clean": 1234,
    "maxwritten_clean": 0,
    "buffers_backend": 456,
    "buffers_alloc": 89012,
    "buffers_checkpoint": 3456
  },
  "total_ms": 45678.9,
  "n_success": 20,
  "n_failed": 2
}
```

### Campos do resultado de benchmark

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `queries` | list | Lista de resultados individuais por query |
| `summary` | dict | Métricas agregadas do benchmark completo |
| `pg_stats` | dict | Estatísticas do `pg_stat_bgwriter` pós-benchmark |
| `total_ms` | float | Tempo total de execução de todas as queries (soma) |
| `n_success` | int | Número de queries com `success=true` |
| `n_failed` | int | Número de queries com `success=false` |

### Campos de cada query

| Campo | Tipo | Valores |
|-------|------|---------|
| `query_id` | int | Número da query (1–22 para TPC-H, 1–99 para TPC-DS) |
| `query_name` | str | Nome curto (ex: `"Q1"`, `"Q22"`) |
| `success` | bool | `true` se executou sem erro |
| `exec_ms` | float | Tempo de execução em milissegundos |
| `failure_reason` | str | `"ok"`, `"timeout"`, `"oom"`, `"technical"` |
| `buffers.shared_hit` | int | Blocos encontrados no `shared_buffers` |
| `buffers.shared_read` | int | Blocos lidos do disco |
| `buffers.shared_written` | int | Blocos dirty escritos durante a query |
| `buffers.temp_read` | int | Blocos lidos de arquivos temporários (spill) |
| `buffers.temp_written` | int | Blocos escritos em arquivos temporários (spill) |
| `plan` | dict\|null | JSON do plano EXPLAIN ANALYZE BUFFERS |

### Falhas e valores imputados

Quando uma query falha, `exec_ms` recebe um valor imputado para que as métricas agregadas (especialmente `geo_mean_exec_ms`) sejam comparáveis entre configurações:

| `failure_reason` | `exec_ms` imputado | Significado |
|-----------------|-------------------|-------------|
| `"timeout"` | `_IMPUTE_TIMEOUT_MS` | Query ultrapassou 15 minutos |
| `"oom"` | `_IMPUTE_OOM_MS` | Container morto pelo OOM killer |
| `"technical"` | `0` | Erro técnico (não imputa: exclui da média) |

O valor de `_IMPUTE_TIMEOUT_MS` é maior que `_QUERY_TIMEOUT_MS=900_000ms` para que queries com timeout fiquem piores na métrica `geo_mean_exec_ms` do que queries que completaram em 15 minutos. Similarmente, `_IMPUTE_OOM_MS > _IMPUTE_TIMEOUT_MS` pois OOM é pior que timeout.

### Campos do `summary`

| Campo | Tipo | Fórmula | Interpretação |
|-------|------|---------|---------------|
| `geo_mean_exec_ms` | float | `exp(mean(log(exec_ms)))` | **Target principal** do ML: menor é melhor |
| `overall_cache_hit_ratio` | float | `shared_hit / (shared_hit + shared_read)` | Eficiência do `shared_buffers`: maior é melhor |
| `queries_with_spill` | int | `count(temp_read > 0 or temp_written > 0)` | Pressão de `work_mem`: menor é melhor |

**Por que média geométrica?**

A média geométrica é robusta a outliers: uma query que leva 1 hora (timeout) inflaria muito a média aritmética, tornando impossível comparar configurações que diferem apenas nas queries normais. A média geométrica comprime esses outliers, tornando a métrica mais informativa sobre o comportamento geral da configuração.

### Campos do `pg_stats`

| Campo | Interpretação |
|-------|---------------|
| `buffers_clean` | Páginas escritas proativamente pelo bgwriter: alto = bgwriter ativo |
| `maxwritten_clean` | Quantas vezes o bgwriter foi limitado pelo `lru_maxpages`: alto = bgwriter insuficiente |
| `buffers_backend` | Páginas que backends tiveram que escrever diretamente: alto = pressão sobre o buffer pool |
| `buffers_alloc` | Total de páginas alocadas: indicador de volume de dados processados |
| `buffers_checkpoint` | Páginas escritas em checkpoints: alto indica muita atividade de WAL |

## Estrutura de `hw_metrics`

```json
{
  "samples": [
    {
      "timestamp_s": 1704067200.0,
      "cpu_percent": 87.3,
      "cpu_freq_mhz": 3800.0,
      "mem_used_gb": 3.2,
      "mem_avail_gb": 0.8,
      "mem_percent": 80.0,
      "disk_read_mb_s": 12.4,
      "disk_write_mb_s": 45.1,
      "nvme_temps_c": [45.2, 48.1],
      "gpu_edge_c": null,
      "rapl_energy_uj": null
    }
  ],
  "summary": {
    "cpu_percent_avg": 72.1,
    "cpu_percent_max": 99.8,
    "cpu_percent_min": 12.3,
    "mem_percent_avg": 68.4,
    "mem_percent_max": 82.1,
    "disk_write_mb_s_avg": 23.4,
    "rapl_energy_total_j": null,
    "rapl_avg_power_w": null,
    "duration_s": 5902.67,
    "n_samples": 2951
  }
}
```

Ver [Monitoramento](monitoramento.md) para a descrição completa de cada campo.

## Lendo os resultados em Python

```python
import json
import glob
from pathlib import Path

# Carregar todos os resultados de um tier e combinação
results_dir = Path("output/benchmark_results")
files = sorted(results_dir.glob("medium/s1s2/task_*.json"))

results = []
for f in files:
    with open(f) as fp:
        results.append(json.load(fp))

# Extrair métricas principais
for r in results:
    task_id = r["task_id"]
    tpch_geo = r["tpc_h"]["summary"]["geo_mean_exec_ms"]
    tpcds_geo = r["tpc_ds"]["summary"]["geo_mean_exec_ms"]
    cache_hit = r["tpc_h"]["summary"]["overall_cache_hit_ratio"]
    spill = r["tpc_h"]["summary"]["queries_with_spill"]
    shared_buffers = r["pg_config"]["shared_buffers"]

    print(f"Task {task_id}: TPC-H {tpch_geo:.0f}ms, TPC-DS {tpcds_geo:.0f}ms, "
          f"cache_hit={cache_hit:.3f}, spill={spill}, sb={shared_buffers}")
```

## Escrita incremental pelo `result_writer`

O `runner/result_writer.py` não escreve o arquivo apenas ao final: ele escreve seções à medida que ficam disponíveis:

```python
# Fluxo de escrita durante uma tarefa:

init_task_file(task_id, combination, tier, pg_config, started_at)
# → cria output/benchmark_results/{tier}/{combo}/task_{id}.json
#   com campos básicos, sem tpc_h/tpc_ds/hw_metrics

for query_result in tpch_query_results:
    append_query_result(task_id, "tpc_h", query_result)
# → adiciona cada query ao arquivo conforme ela termina

finalize_benchmark_section(task_id, "tpc_h", summary, pg_stats, total_ms, n_success, n_failed)
# → adiciona o summary e pg_stats da seção TPC-H

# Repete para TPC-DS...

save_hw_metrics(task_id, hw_metrics)
# → adiciona a seção hw_metrics

finalize_task_file(task_id, finished_at, duration_s, status)
# → atualiza finished_at, duration_s e status
```

Essa abordagem garante que, mesmo se o processo for morto durante a execução do TPC-DS, os dados do TPC-H já estão salvos em disco e são utilizáveis.

## Verificando resultados no terminal

```bash
# Listar todos os arquivos de resultado
find output/benchmark_results -name "*.json" | sort

# Contar tarefas por status
python -c "
import json, glob
files = glob.glob('output/benchmark_results/**/*.json', recursive=True)
for f in files:
    data = json.load(open(f))
    print(f'{f}: status={data[\"status\"]}, '
          f'tpch_geo={data[\"tpc_h\"][\"summary\"][\"geo_mean_exec_ms\"]:.0f}ms')
"
```
