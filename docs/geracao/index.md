# Geração de Configurações PostgreSQL

O pacote `pg_sampler/` é o motor de geração de configurações PostgreSQL. Ele usa **Latin Hypercube Sampling (LHS)** para produzir conjuntos de configurações diversificados que cobrem uniformemente o espaço de busca de 36 parâmetros distribuídos em 3 stages.

## O que é gerado

Por padrão (`cli/generate.py`), o gerador produz **30 configurações por combinação** de stages, distribuídas igualmente entre os 3 tiers:

- 10 configurações para tier `low` (2 vCPU / 2 GB / SF=1)
- 10 configurações para tier `medium` (4 vCPU / 4 GB / SF=2)
- 10 configurações para tier `high` (6 vCPU / 6 GB / SF=4)

Com 7 combinações × 30 configs = **210 tarefas por tier**, **630 tarefas no total**.

Cada configuração é um dicionário `{parâmetro: valor}` que pode ser aplicado diretamente ao PostgreSQL via argumentos de linha de comando.

## Os 3 stages e 36 parâmetros

Os parâmetros são organizados em 3 stages de **12 parâmetros cada**, refletindo diferentes aspectos do sistema PostgreSQL:

### Stage 1 — Memória, Paralelismo, WAL, Planner Básico (12 params)

Os parâmetros de maior impacto em workloads analíticos. Determinam quanto de RAM está disponível para caches e operações, quantos workers paralelos podem ser usados, e como o planner avalia estratégias de acesso.

| Parâmetro | Tipo | Impacto OLAP |
|-----------|------|--------------|
| `jit` | bool | Alto — compilação JIT de expressões complexas |
| `seq_page_cost` | float | **Fixo em 1.0** — âncora do sistema de custo |
| `random_page_cost` | float [1.0, 4.0] | Alto — define preferência por index vs seqscan |
| `default_statistics_target` | int [100, 400] | Médio — qualidade das estimativas de cardinalidade |
| `max_parallel_workers` | int [1, vCPUs] | Alto — limite superior de workers disponíveis |
| `max_parallel_workers_per_gather` | int [1, vCPUs//2] | Alto — workers por nó de Gather |
| `max_worker_processes` | int | **Fixo em cpu×2+4** — pool de processos background |
| `shared_buffers` | memory | Alto — cache de páginas em memória compartilhada |
| `effective_cache_size` | memory | Médio — hint do planner (não aloca RAM real) |
| `work_mem` | memory | **Muito alto** — memória por operação de sort/hash join |
| `enable_hashagg` | bool | Alto — hash aggregation vs sort-based aggregation |
| `enable_bitmapscan` | bool | Médio — bitmap index scans |
| `enable_nestloop` | bool | Alto — nested loop joins (desligar favorece hash/merge) |
| `enable_parallel_hash` | bool | Alto — hash joins em planos paralelos |
| `synchronous_commit` | enum | **Fixo em 'off'** — sem efeito em workloads SELECT-only |

### Stage 2 — Custos de CPU, Paralelismo Fino, Planejamento de Joins (12 params)

Parâmetros de impacto moderado que refinam o comportamento do planner e controlam thresholds de paralelismo.

| Parâmetro | Tipo | Impacto OLAP |
|-----------|------|--------------|
| `cpu_tuple_cost` | float | Baixo — custo de processar uma tupla no resultado |
| `cpu_index_tuple_cost` | float | Baixo — custo de processar uma entrada de índice |
| `cpu_operator_cost` | float | Baixo — custo de avaliar um operador/função |
| `parallel_setup_cost` | int | Alto — overhead de iniciar um plano paralelo |
| `parallel_tuple_cost` | float | Médio — custo por tupla transferida entre workers |
| `min_parallel_table_scan_size` | memory | Alto — tamanho mínimo para habilitar parallel seqscan |
| `min_parallel_index_scan_size` | memory | Médio — tamanho mínimo para parallel index scan |
| `join_collapse_limit` | int [4, 16] | Médio — profundidade da busca exaustiva de ordem de join |
| `from_collapse_limit` | int [4, 16] | Médio — número de subqueries inlineadas |
| `hash_mem_multiplier` | float [1.0, 4.0] | **Muito alto** — multiplica work_mem para hash joins |
| `enable_mergejoin` | bool | Alto — merge join (requer dados ordenados) |
| `enable_hashjoin` | bool | **Muito alto** — hash join (o mais usado em OLAP) |

### Stage 3 — Toggles Avançados do Planner, I/O Background (12 params)

Parâmetros de ajuste fino com impacto sutil mas mensurável em workloads específicos.

| Parâmetro | Tipo | Impacto OLAP |
|-----------|------|--------------|
| `enable_memoize` | bool | Médio — cache de resultados de subplanos internos (PG14+) |
| `enable_gathermerge` | bool | Alto — merge paralelo com ORDER BY |
| `enable_incremental_sort` | bool | Alto — sort incremental em dados parcialmente ordenados (PG13+) |
| `enable_material` | bool | Médio — materialização em loops de join |
| `enable_indexscan` | bool | Médio — index scans B-tree/hash |
| `enable_indexonlyscan` | bool | Médio — index-only scans com covering index |
| `enable_parallel_append` | bool | Médio — append paralelo para UNION ALL e partições (PG11+) |
| `enable_windowagg` | bool | Alto — window functions (RANK, ROW_NUMBER, SUM OVER) |
| `parallel_leader_participation` | bool | Baixo — líder do Gather também processa tuplas |
| `checkpoint_completion_target` | float [0.5, 0.9] | Baixo — spread de I/O de checkpoint |
| `bgwriter_lru_maxpages` | int | Baixo — páginas escritas pelo bgwriter por round |
| `wal_buffers` | memory | Baixo — buffer de WAL em memória compartilhada |

## As 7 combinações de stages

O gerador produz 7 combinações de stages para o estudo de ablação:

| Rótulo | Stages | Dimensões LHS | Objetivo experimental |
|--------|--------|---------------|----------------------|
| `s1` | [1] | 12 | Baseline de memória e paralelismo |
| `s2` | [2] | 12 | Baseline de custos e planning |
| `s3` | [3] | 12 | Baseline de toggles avançados |
| `s1s2` | [1,2] | 24 | Ganho de combinar memória+custo |
| `s1s3` | [1,3] | 24 | Ganho de combinar memória+toggles |
| `s2s3` | [2,3] | 24 | Ganho de combinar custo+toggles |
| `s1s2s3` | [1,2,3] | 36 | Espaço completo — custo de alta dimensão |

A hipótese experimental central: **mais parâmetros → melhor cobertura do espaço, mas menor densidade amostral por dimensão → rendimentos decrescentes a partir de certo ponto.**

## API pública do `pg_sampler`

```python
from pg_sampler import generate_configs, generate_all_tiers, stages_label, stages_description

# Gera 30 configs para uma combinação específica (10 por tier)
result = generate_all_tiers(stages=[1, 2], n_per_tier=10, seed=42)
# result = {"low": [...10 dicts...], "medium": [...10 dicts...], "high": [...10 dicts...]}

# Rótulo curto da combinação (usado como nome de diretório)
stages_label([1, 2, 3])   # → "s1s2s3"
stages_label([1])          # → "s1"

# Descrição longa da combinação
stages_description([1, 2]) # → "Stage 1 + Stage 2"
```

## Estrutura interna do pacote

```
pg_sampler/
├── __init__.py          ← Exporta generate_configs, generate_all_tiers, etc.
├── types.py             ← Aliases: ParameterSpace, Config, Environment, StageSpaces, Stages
├── space_loader.py      ← load_parameter_space(), load_stage_spaces(), load_docker_config()
├── lhs_sampler.py       ← lhs_quantiles(), _pick(), _uniform(), _randint()
├── parameter_builder.py ← generate_valid_configs(), generate_combined_config(), _fill_stage1/2/3()
├── constraints.py       ← validate_combined_config(), diagnose_combined(), _validate_stage1/2/3()
├── orchestrator.py      ← generate_all_tiers(), stages_label(), stages_description(), TIERS
└── display.py           ← print_docker_table(), print_stages_header(), print_results_table()
```

## Páginas desta seção

- [**Latin Hypercube Sampling**](lhs.md) — Como o LHS garante cobertura uniforme do espaço de busca
- [**Stages e Espaços de Parâmetros**](estagios.md) — Detalhamento dos 36 parâmetros por stage
- [**Restrições e Validação**](restricoes.md) — Como configurações inválidas são detectadas e descartadas
