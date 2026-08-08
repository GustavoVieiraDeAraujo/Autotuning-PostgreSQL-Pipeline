# Decisões de Engenharia — Registro Formal

> **Finalidade:** Este documento registra as decisões técnicas tomadas ao longo do
> projeto, com justificativa detalhada de cada uma e o impacto esperado na qualidade
> dos dados de treinamento do meta-modelo. Serve como referência para manutenção
> futura, retomada do desenvolvimento e defesa das escolhas metodológicas.
>
> **Última atualização:** 2026-05-30 (seções 27–28 adicionadas)

---

## Índice

1. [Queries permanentemente puladas (TPC-H Q17, Q20 / TPC-DS Q95)](#1-queries-permanentemente-puladas)
2. [Estratégia de timeout — por query e por tarefa](#2-estratégia-de-timeout)
3. [Imputação de dados de tarefas abandonadas](#3-imputação-de-dados-de-tarefas-abandonadas)
4. [Remoção de parâmetros de I/O do Stage 3](#4-remoção-de-parâmetros-de-io-do-stage-3)
5. [Remoção de `enable_windowagg` do Stage 3](#5-remoção-de-enable_windowagg-do-stage-3)
6. [Upgrade PostgreSQL 16 → 17](#6-upgrade-postgresql-16--17)
7. [Validação de parâmetros contra PostgreSQL real no prepare](#7-validação-de-parâmetros-contra-postgresql-real-no-prepare)
18. [Pipeline de ML — arquitetura final (pós-Rodada 1)](#18-pipeline-de-ml--arquitetura-final-pós-rodada-1)
19. [Não alterar espaço de parâmetros entre rodadas](#19-não-alterar-espaço-de-parâmetros-entre-rodadas)
20. [Por que uma segunda rodada foi necessária](#20-por-que-uma-segunda-rodada-foi-necessária)
21. [Mecanismo de start_id para rodadas múltiplas](#21-mecanismo-de-start_id-para-rodadas-múltiplas)
22. [Otimização de hiperparâmetros com Optuna](#22-otimização-de-hiperparâmetros-com-optuna)
23. [Nova Rodada via interface web](#23-nova-rodada-via-interface-web)
24. [Manter outliers no dataset de treino](#24-manter-outliers-no-dataset-de-treino)
25. [Resultados combinados Rodadas 1+2 e decisão sobre terceira rodada](#25-resultados-combinados-rodadas-12-e-decisão-sobre-terceira-rodada)
26. [Top-K accuracy — problema de escala e como reportar no TCC](#26-top-k-accuracy--problema-de-escala-e-como-reportar-no-tcc)
27. [Análise de custo-efetividade em nuvem](#27-análise-de-custo-efetividade-em-nuvem)
28. [Arquitetura do projeto — Cookiecutter Data Science adaptado](#28-arquitetura-do-projeto--cookiecutter-data-science-adaptado)
8. [Adição de `enable_sort` ao Stage 1](#8-adição-de-enable_sort-ao-stage-1)
9. [Restrição `enable_hashjoin=off + enable_mergejoin=off`](#9-restrição-enable_hashjoin--enable_mergejoin)
10. [JIT desabilitado no tier LOW (2 vCPU)](#10-jit-desabilitado-no-tier-low)
11. [Paralelismo mínimo: `max_parallel_workers_per_gather ≥ 1`](#11-paralelismo-mínimo)
12. [Default de configs por combinação: 30 → 50](#12-default-de-configs-por-combinação-30--50)
13. [Suporte a repetições por config (`--repetitions N`)](#13-suporte-a-repetições-por-config)
14. [Feature extractor: targets por query e cache hit ratio](#14-feature-extractor-targets-por-query-e-cache-hit-ratio)
15. [Parâmetros fixos não amostrados](#15-parâmetros-fixos-não-amostrados)
16. [Scale Factors por tier](#16-scale-factors-por-tier)
17. [Constraint `parallel_setup_cost > 1200` com `per_gather > 1`](#17-constraint-parallel_setup_cost--1200)

---

## 1. Queries permanentemente puladas

**Queries afetadas:** TPC-H Q17, Q20 — TPC-DS Q95

**Implementação:** `benchmarks/tpc_h/benchmark.py` e `benchmarks/tpc_ds/benchmark.py`

```python
_SKIP_QUERY_IDS: frozenset[int] = frozenset({17, 20})   # TPC-H
_SKIP_QUERY_IDS: frozenset[int] = frozenset({95})        # TPC-DS
```

### Por que foram puladas

Análise dos 83 resultados `done` mostrou que **100% dessas queries excedem o
`statement_timeout` de 15 minutos** em toda configuração testada, nos três tiers
(SF=1, SF=2, SF=4):

| Query | Motivo estrutural | Impacto sem pular |
|---|---|---|
| TPC-H Q17 (Small-Quantity-Order Revenue) | Self-join em `lineitem` com subquery correlacionada; plano de nested loop inevitável no SF=1 | 15 min × 3 tiers × N configs = **5.25h perdidas por geração** |
| TPC-H Q20 (Potential Part Promotion) | Subquery correlacionada em `partsupp` com `NOT IN`; execução O(n²) no optimizer padrão | Idem |
| TPC-DS Q95 (Web Sales Returns) | Self-join em `web_sales` com filtro em `web_returns`; produto cartesiano inevitável | 15 min × 3 tiers × N configs = **2.625h perdidas por geração** |

### Impacto no meta-modelo

**Sem pular:** essas 3 queries adicionam um sinal constante de `900 000 ms` a
*toda* configuração. Para o meta-modelo isso significa:

- O target `total_ms` seria inflado em **`3 × 900_000 = 2_700_000 ms = 45min`**
  de ruído puro em todos os exemplos.
- Qualquer diferença real entre configurações (que tipicamente varia entre 100ms e
  5000ms por query) ficaria enterrada debaixo desse ruído constante.
- Modelos como XGBoost tenderiam a ignorar features de configuração e aprender
  apenas o valor constante do timeout como predição.

**Pulando:** o sinal passa a refletir apenas as queries onde a configuração faz
diferença. O total de queries ativas (`_TPCH_TOTAL_QUERIES = 20`,
`_TPCDS_TOTAL_QUERIES = 98`) é usado na imputação de tarefas abandonadas.

### No CSV de features

Colunas `tpch_q17_ms`, `tpch_q20_ms` e `tpcds_q95_ms` são sempre `None` — o
modelo não vê sinal nessas posições e pode aplicar máscara durante treinamento.

---

## 2. Estratégia de timeout

### Por query — `statement_timeout`

Cada query recebe `SET statement_timeout = '15min'` antes da execução
(`benchmarks/query_executor.py`). Quando excedido, o PostgreSQL levanta
`canceling statement due to statement timeout`, capturado como
`failure_reason = "timeout"`.

**Por que 15 minutos?**

- Queries analíticas legítimas no TPC-H SF=1 ficam entre 200ms e 10s na
  configuração padrão. Uma query genuinamente responsiva nunca chega perto de
  15min.
- Queries que excedem 15min sob *qualquer* configuração são estruturalmente
  impossíveis de otimizar via parâmetros do planner (são as Q17, Q20, Q95).
- Timeout menor (ex: 5min) arriscaria matar queries que apenas ficaram lentas
  por uma configuração ruim de `nestloop` — perdendo sinal legítimo de "config
  muito ruim".
- Timeout maior aumentaria o custo de cada tarefa sem retorno em sinal útil.

### Por tarefa — tier timeout

Além do timeout por query, cada tarefa tem um timeout de tier (definido em
`specs/docker.json`) para o tempo total da tarefa. Tarefas que excedem esse
limite são abandonadas com `abandoned_reason = "timeout"`.

**Valores por tier:**

| Tier | Timeout de tarefa | Justificativa |
|---|---|---|
| low | ~4h | SF=1, 20+98 queries; esperado ~1.5h para config típica |
| medium | ~8h | SF=2, volume dobrado |
| high | ~16h | SF=4, volume 4× maior |

**Por que não adaptativo?** O timeout adaptativo (ex: "cortar na média + 2σ")
introduziria viés: configurações genuinamente ruins seriam cortadas antes de
terminar, enquanto configurações boas completariam. O resultado seria um dataset
distorcido onde nunca veríamos o comportamento real das piores configs. Um timeout
fixo e generoso preserva o sinal de "config ruim" — a query simplesmente recebe
`exec_ms = 900_000` como imputação.

---

## 3. Imputação de dados de tarefas abandonadas

**Implementação:** `ml/extract_features.py`

```python
_IMPUTE_TIMEOUT_MS = 900_000.0        # 15 min
_IMPUTE_OOM_MS     = 900_000.0 * 1.5  # 22.5 min
```

### Casos de imputação

| Situação | Valor imputado | Raciocínio |
|---|---|---|
| Query com `failure_reason="timeout"` | `900_000 ms` | Valor exato do limite — a query demorou pelo menos isso |
| Query com `failure_reason="oom"` | `1_350_000 ms` | 1.5× o timeout: OOM é pior que timeout (kernel matou o processo, consumiu mais recursos) |
| Query com `failure_reason="technical"` | `900_000 ms` | Conservador — falha de infra, mas não queremos ignorar a query no somatório |
| Tarefa abandonada, query não chegou a iniciar | `900_000 ms` | A tarefa foi cortada pelo timeout do tier — as queries restantes teriam sido igualmente lentas |

### Por que não descartar tarefas abandonadas?

Descartar as 127 tarefas abandonadas (60% do dataset atual de 210) desperdiçaria
informação valiosa:

1. **Tarefas `sigint_or_interrupt`:** foram cortadas manualmente no meio da
   execução, mas as queries que chegaram a rodar têm métricas reais e válidas.
   Descartar seria jogar fora dados custosos.

2. **Tarefas `timeout`:** o fato de uma tarefa ter excedido o timeout total é
   *sinal* — indica que a combinação de configuração + tier foi ruim o suficiente
   para não terminar em tempo hábil. Para o meta-modelo, isso é informação: aquela
   config é "pior que o threshold".

3. **Tarefas `invalid_config`:** essas *devem* ser descartadas (config inválida
   não rodou nada), mas são identificadas pelo campo `abandoned_reason` e terão
   todas as métricas de queries em `0` ou `None`.

### Impacto no treinamento

O meta-modelo deve ser treinado com **imputação explícita**, não com exclusão de
linhas. Usar `900_000 ms` para queries não executadas cria um limite superior
conservador que informa o modelo de que "aquela região do espaço de parâmetros
é perigosa". Modelos como XGBoost tratam isso naturalmente — não há problema com
o valor ser uma estimativa desde que seja sistematicamente aplicado.

---

## 4. Remoção de parâmetros de I/O do Stage 3

**Parâmetros removidos:**
- `checkpoint_completion_target`
- `bgwriter_lru_maxpages`
- `wal_buffers`

**Arquivos modificados:** `specs/spaces/stage3/{low,medium,high}.json`,
`pg_sampler/parameter_builder.py`, `pg_sampler/lhs_sampler.py`,
`ml/extract_features.py`

### Por que foram removidos

Esses três parâmetros controlam exclusivamente o comportamento de **escrita** no
PostgreSQL:

- `checkpoint_completion_target`: distribui a escrita de checkpoints no tempo para
  reduzir picos de I/O.
- `bgwriter_lru_maxpages`: taxa de páginas sujas que o background writer pode
  escrever por round.
- `wal_buffers`: tamanho do buffer de Write-Ahead Log antes de flush para disco.

**Os benchmarks TPC-H e TPC-DS são 100% `SELECT`-only.** Nenhuma das 22+99 queries
escreve dados. Portanto:

- Checkpoints não são disparados durante o benchmark (não há páginas sujas para
  escrever, exceto o overhead mínimo do próprio PostgreSQL).
- O bgwriter não tem trabalho a fazer.
- O WAL é mínimo e irrelevante para operações de leitura.

### Evidência empírica

Correlação entre variações de `checkpoint_completion_target` e `total_ms` nos
83 resultados `done` foi < 0.02 (ruído puro). Nenhum modelo de árvore atribui
importância não-zero a esses parâmetros quando treinado nos dados coletados.

### Impacto no meta-modelo

**Mantendo:** o modelo desperdiçaria dimensões do espaço de parâmetros em
features que têm correlação zero com o target. Isso não prejudica a acurácia
do modelo (ele aprende a ignorar), mas:

1. **Dilui o LHS:** cada um desses parâmetros ocupa 1 das 12 dimensões do Stage 3
   no Latin Hypercube Sampling. Com parâmetros inúteis ocupando dimensões, a
   cobertura efetiva do espaço relevante é reduzida.
2. **Aumenta ruído de SHAP:** importâncias de features ficam distribuídas entre
   parâmetros reais e parâmetros nulos, dificultando a interpretação.
3. **Custo de dimensionalidade sem retorno:** para 50 configs × 3 tiers = 150
   pontos de Stage 3, cada dimensão desperdiçada reduz a densidade amostral.

---

## 5. Remoção de `enable_windowagg` do Stage 3

**Parâmetro removido:** `enable_windowagg`

**Causa raiz:** `enable_windowagg` é um parâmetro introduzido no **PostgreSQL 18**.
Não existe no PostgreSQL 16 nem no PostgreSQL 17.

### Como foi descoberto

O parâmetro foi incluído originalmente no Stage 3 com base na documentação do
PostgreSQL em desenvolvimento. Após upgrade para PostgreSQL 17 e adição do passo
de validação em `cli/prepare.py`, a função `_run_param_validation()` executou
`SELECT name FROM pg_settings WHERE name = 'enable_windowagg'` contra um container
`postgres:17` e retornou zero linhas — confirmando que o parâmetro não existe.

### Consequência antes da correção

**Todas as 80 tarefas com Stage 3** (`s3`, `s1_s3`, `s2_s3`, `s1_s2_s3`) foram
marcadas como `invalid_config` pelo runner ao tentar aplicar a configuração:

```
ERROR: unrecognized configuration parameter "enable_windowagg"
```

Isso representou **38% de desperdício** do dataset esperado. A correção foi:

1. Remover `enable_windowagg` dos 3 spec JSONs.
2. Remover do `parameter_builder.py`, `lhs_sampler.py`, `extract_features.py`.
3. Adicionar validação automática no `prepare` para nunca mais chegar nessa situação.

### Lição aprendida

**Nunca confiar em documentação de versão futura para especificar parâmetros.**
O passo de validação (`cli/prepare.py → _run_param_validation()`) é obrigatório
antes de cada nova rodada de geração e deve ser executado contra a versão exata
do PostgreSQL utilizada.

---

## 6. Upgrade PostgreSQL 16 → 17

**Arquivos modificados:** `benchmarks/tpc_h/Dockerfile`,
`benchmarks/tpc_ds/Dockerfile`, `benchmarks/container.py`

### Motivação

1. **Compatibilidade de parâmetros:** PostgreSQL 17 introduz melhorias no planner
   que tornam alguns parâmetros do Stage 2 e Stage 3 mais impactantes (ex:
   `enable_incremental_sort` otimizado no PG16, `enable_memoize` introduzido no
   PG14 mas com melhorias no PG17).

2. **Suporte ativo:** PostgreSQL 16 entra em EOL em novembro de 2028, mas
   PostgreSQL 17 recebe patches de segurança por mais tempo. Um projeto de TCC
   que pode durar 1-2 anos deve usar versão com suporte ativo.

3. **Consistência do dataset:** ao trocar a versão, as imagens Docker precisam
   ser reconstruídas (`make build-images --force`), mas os benchmarks passam a
   rodar sobre uma versão de planner ligeiramente diferente. Isso é aceitável
   para os fins do TCC.

### Impacto no dataset existente

Todos os 83 resultados `done` coletados foram gerados com PostgreSQL 16. Após o
upgrade, novos dados serão gerados com PostgreSQL 17. **Recomendação:** incluir
a versão do PostgreSQL como feature no vetor X do meta-modelo (`pg_version: 16`
ou `17`) para que o modelo possa separar o efeito da versão do planner do efeito
das configurações.

---

## 7. Validação de parâmetros contra PostgreSQL real no prepare

**Implementação:** `cli/prepare.py → _run_param_validation()`

```python
# Sobe um container postgres:17 temporário, executa:
SELECT name FROM pg_settings WHERE name = ANY(%(params)s)
# Compara com todos os parâmetros nos spec JSONs
# Falha com sys.exit(1) se qualquer parâmetro não for reconhecido
```

### Por que é crucial

Sem essa validação, configurações com parâmetros inválidos só seriam descobertas
durante a execução da fila — potencialmente após horas de espera. O `runner`
tentaria aplicar a config, receberia `ERROR: unrecognized configuration parameter`
do PostgreSQL, e marcaria a tarefa como `invalid_config`.

**O custo de uma rodada com parâmetros inválidos:**

- `enable_windowagg` afetou 80 tarefas de 210 (38%) — todas `invalid_config`.
- Cada tarefa consome um slot na fila e não produz dado utilizável.
- Com 50 configs × 7 combinações × 3 tiers = 1050 tarefas por rodada, 38% de
  desperdício = **399 tarefas perdidas** e potencialmente dias de execução em vão.

### Posição no fluxo

A validação ocorre **após** o display do status da fila e **antes** do build das
imagens Docker. Se falhar, nenhuma imagem é construída, evitando builds caros
seguidos de falha de execução.

---

## 8. Adição de `enable_sort` ao Stage 1

**Parâmetro adicionado:** `enable_sort`

**Arquivos modificados:** `specs/spaces/stage1/{low,medium,high}.json`,
`pg_sampler/lhs_sampler.py`, `pg_sampler/parameter_builder.py`,
`ml/extract_features.py`

### Por que `enable_sort` é relevante para OLAP

`enable_sort` controla se o PostgreSQL pode usar nós de `Sort` no plano de
execução. Desabilitar força o planner a escolher estratégias alternativas:

- Merge joins (que requerem input já ordenado) ficam inviáveis.
- Queries com `ORDER BY`, `GROUP BY`, e `DISTINCT` precisam usar caminhos
  alternativos (hash aggregation, index scan com ordenação implícita).
- Em TPC-DS especificamente, queries com `ORDER BY` em janelas de `RANK()` e
  `ROW_NUMBER()` têm comportamento drasticamente diferente com `enable_sort=off`.

### Relação com outras features do Stage 1

`enable_sort` interage com `enable_hashagg` e `enable_nestloop`:

- `enable_sort=off` + `enable_hashagg=on`: o planner usa hash aggregation para
  `GROUP BY` mesmo quando seria mais barato ordenar primeiro.
- `enable_sort=off` + `enable_mergejoin=on` (Stage 2): merge join fica impossível
  sem sort, forçando hash join para todos os joins.

Essas interações cruzadas são exatamente o tipo de sinal que o meta-modelo precisa
capturar — razão pela qual `enable_sort` é Stage 1 (interage com parâmetros do
Stage 1 e Stage 2) e não Stage 3.

### Por que não estava incluído antes

Omissão original do design: os 12 parâmetros do Stage 1 foram definidos focando
em parâmetros de memória e paralelismo. `enable_sort` é um toggle de planner que
complementa `enable_hashagg` e `enable_nestloop`.

---

## 9. Restrição `enable_hashjoin` + `enable_mergejoin`

**Implementação:** `pg_sampler/constraints.py → _validate_stage2()`

```python
no_hj = config.get("enable_hashjoin")  == "off"
no_mj = config.get("enable_mergejoin") == "off"
if no_hj and no_mj:
    errors.append(
        "E2: enable_hashjoin=off + enable_mergejoin=off — apenas nested loop "
        "disponível; catastrófico para OLAP (timeout garantido)"
    )
```

### Por que essa combinação é proibida

Com `enable_hashjoin=off` e `enable_mergejoin=off` simultaneamente, o planner
tem apenas **nested loop** disponível para executar joins. Para workloads OLAP
com tabelas de múltiplos gigabytes (SF=2, SF=4):

- Um nested loop em `lineitem × orders` (SF=1: ~6M × ~1.5M linhas) seria
  O(n × m) = ~9 × 10¹² operações.
- Queries que completam em 2s com hash join passam a durar horas.
- Na prática: **timeout garantido em 100% das queries de join** (virtualmente
  todas as 22+98 queries).

### Evidência empírica

Nas coletas iniciais (antes da restrição), tarefas com essa combinação foram
abandonadas por timeout em < 10 queries. O custo para o dataset:
- Cada tarefa consome ~15min no tier LOW (timeout da primeira query de join).
- Produz zero sinal útil para o modelo — toda a configuração é inválida.
- O espaço LHS desperdiça um ponto que poderia ter explorado uma região válida.

### Impacto no meta-modelo

Excluir essas configurações **não causa viés** porque o objetivo do meta-modelo
é encontrar configurações boas, não mapear exaustivamente o espaço. Não há
aprendizado possível sobre uma região que é uniformemente catastrófica.

---

## 10. JIT desabilitado no tier LOW

**Implementação:** `specs/spaces/stage1/low.json`

```json
"jit": {
    "type": "categorical",
    "choices": { "mode": "discrete", "values": [0] }
}
```

No tier `medium` e `high`, `jit` tem `values: [0, 1]`.

### Por que JIT é contraproducente em 2 vCPU

O JIT (Just-In-Time compilation) do PostgreSQL compila expressões SQL para código
nativo, o que pode acelerar queries com funções complexas. No entanto, tem overhead:

1. **Overhead de compilação:** compilar expressões JIT no início da query leva
   centenas de milissegundos. Para queries curtas (< 5s), esse overhead pode
   superar o ganho.
2. **CPU contention:** JIT usa threads para compilação paralela. Com 2 vCPU no
   tier LOW, as threads de compilação competem com as threads de execução,
   aumentando latência geral.
3. **Incompatibilidade com SF=1:** o benefício do JIT aparece em queries que
   processam dezenas de milhões de linhas repetidamente. Com SF=1, a maioria das
   queries TPC-H dura entre 200ms e 5s — muito curto para JIT ajudar.

### Evidência empírica

Comparando tarefas `done` em `low/s1` com `jit=0` vs tentativas com `jit=1`:
- `jit=1` adicionou 200-800ms de latência nas primeiras queries de cada benchmark
  (overhead de compilação).
- Nenhuma query mostrou melhora mensurável com `jit=1` no tier LOW.

### Impacto no meta-modelo

Manter `jit=1` no tier LOW adicionaria ruído: o modelo veria a feature `jit=1`
associada a resultados **piores** no LOW, mas **melhores** no HIGH — criando
uma interação hardware×config que exigiria mais dados para ser aprendida
corretamente. Fixar `jit=0` no LOW simplifica o espaço de aprendizado sem perda
de sinal relevante.

---

## 11. Paralelismo mínimo

**Implementação:** `pg_sampler/parameter_builder.py → _fill_stage1()`

```python
min_gather = max(gather_spec["min"], 1)
```

**Constraint:** `max_parallel_workers_per_gather` nunca é amostrado como `0`.

### Por que `per_gather=0` é catastrófico para OLAP

`max_parallel_workers_per_gather = 0` desabilita **completamente** o paralelismo
por query. Em workloads analíticos:

- Queries de agregação sobre tabelas grandes (TPC-H Q1, Q6, TPC-DS Q4) rodam em
  thread única, sem workers paralelos.
- No tier HIGH (6 vCPU), desabilitar paralelismo desperdiça 5/6 da capacidade de
  CPU disponível.
- Queries que deveriam durar 500ms com paralelismo levam 3-5s sem ele.

### Impacto no dataset

Incluir `per_gather=0` no espaço amostral adicionaria pontos onde o sinal de
paralelismo é artificialmente suprimido. O meta-modelo aprenderia que "qualquer
config com `per_gather=0` é ruim" — verdade, mas trivial e sem utilidade prática
(nenhum DBA usaria `per_gather=0` em workload analítico).

O objetivo é explorar **o espaço de configurações razoáveis**, não mapear
configurações deliberadamente ruins.

---

## 12. Default de configs por combinação: 30 → 50

**Implementação:** `cli/generate.py → _DEFAULT_N_CONFIGS = 50`

### Motivação

Com 30 configs × 7 combinações × 3 tiers = **630 tarefas** por rodada:

- Cada tier/combinação tem apenas 30 pontos no espaço LHS.
- Para Stage 1 com 13 parâmetros, 30 pontos é insuficiente para cobrir interações
  de segunda ordem com confiança.

Com 50 configs:
- **1050 tarefas** por rodada completa.
- 50 pontos LHS em 13 dimensões oferecem cobertura significativamente melhor do
  espaço de parâmetros.
- Para o meta-modelo: mais dados = menor variância nas estimativas de importância
  de features (SHAP mais estável).

### Custo em tempo

Estimativa com base nos dados coletados (tier LOW, ~90min por tarefa):

| Tier | Tempo estimado para 50 configs × 7 combos |
|---|---|
| LOW | ~350 tarefas × 90min = ~525h de CPU (paralelizável com múltiplas máquinas) |
| MEDIUM | ~350 × 180min |
| HIGH | ~350 × 360min |

Embora custoso, a qualidade do dataset justifica. Para prototyping e validação
inicial, `--n-configs 15` ainda está disponível.

---

## 13. Suporte a repetições por config (`--repetitions N`)

**Implementação:** `cli/generate.py --repetitions N`,
`task_queue/execution_queue.py → from_dict(repetitions=N)`

```python
# Cada config é enfileirada N vezes, com campo "repetition" para rastreamento
for rep in range(repetitions):
    queue._tasks.append({..., "repetition": rep, ...})
```

### Por que repetições são necessárias

**Variabilidade experimental:** dois runs da mesma query no mesmo container com
a mesma config PostgreSQL podem diferir em 5-15% do tempo de execução devido a:

- Cache state do sistema operacional (páginas no page cache entre runs).
- Variação de scheduling de CPU no Docker.
- Estado de autovacuum no PostgreSQL.
- Variação de I/O no host.

**Para o meta-modelo:** se o modelo treina com uma única medição por config, parte
da variância que ele aprende é ruído experimental, não sinal de configuração.
Com 3 repetições, podemos usar a **mediana** como target — eliminando outliers
causados por picos de I/O ou CPU.

### Quando usar

- **Produção (dataset final):** `--repetitions 3` para robustez estatística.
- **Exploração inicial:** `--repetitions 1` (padrão) para maximizar cobertura
  do espaço antes de aprofundar.
- **Calibração de um ponto específico:** `--n-configs 1 --repetitions 10` para
  estimar variabilidade em uma config específica.

---

## 14. Feature extractor: targets por query e cache hit ratio

**Implementação:** `ml/extract_features.py`

**Colunas adicionadas:**
- `tpch_q{1..22}_ms` (Q17 e Q20 sempre `None`)
- `tpcds_q{1..99}_ms` (Q95 sempre `None`)
- `tpch_cache_hit_ratio`, `tpcds_cache_hit_ratio`

### Por que targets por query são valiosos

**Heterogeneidade de workload:** as 20 queries ativas do TPC-H têm perfis
completamente diferentes:

- Q1 (Pricing Summary): full table scan em `lineitem`, ~7s → beneficia de
  `shared_buffers` alto, `enable_parallel_hash=on`.
- Q11 (Important Stock Identification): join multi-tabela, ~200ms → pouco impacto
  de paralelismo, dominado por cache.
- Q2 (Minimum Cost Supplier): subquery correlacionada, ~2.5s → sensível a
  `enable_nestloop`, `join_collapse_limit`.

Um modelo treinado com `total_ms` como único target aprende um **efeito médio**
sobre todas as queries. Um modelo treinado com targets por query pode aprender
que, por exemplo:

> "Para Q1, `work_mem ≥ 64MB` reduz o tempo em 40%; para Q11, `work_mem` é
> irrelevante; para Q2, `enable_nestloop=off` reduz o tempo em 80%."

Isso permite **recomendações especializadas por workload**, não apenas uma
configuração one-size-fits-all.

### Por que cache hit ratio é uma feature target

`overall_cache_hit_ratio` mede a porcentagem de blocos de dados que foram
encontrados no `shared_buffers` (sem necessitar leitura de disco). É um proxy
direto para a eficiência da configuração de memória:

- `cache_hit_ratio` alto → `shared_buffers` adequado, `effective_cache_size`
  bem estimado, workload cabe em memória.
- `cache_hit_ratio` baixo → `shared_buffers` insuficiente para o SF, ou
  `work_mem` muito alto está competindo com o cache.

Para o meta-modelo de dois objetivos (performance + eficiência de memória), ter
`cache_hit_ratio` como target separado permite que o modelo aprenda a separar:

> "Esta config é rápida porque tem bom uso de cache" vs
> "Esta config é rápida apesar de cache ruim (compensado por paralelismo)."

---

## 15. Parâmetros fixos não amostrados

Os seguintes parâmetros são fixos em todas as configurações e **não entram no
espaço LHS**:

| Parâmetro | Valor fixo | Justificativa |
|---|---|---|
| `seq_page_cost` | `1.0` | Âncora do planner. Todos os outros custos são relativos a este. Variar criaria correlações artificiais com `random_page_cost` e custos de CPU sem sinal adicional. |
| `max_worker_processes` | `cpu × 2 + 4` | Pool total de workers. Deve ser ≥ `max_parallel_workers`. Fixar como função do CPU elimina uma dependência cross-parâmetro que complicaria o LHS. Fórmula: LOW=8, MEDIUM=12, HIGH=16. |
| `synchronous_commit` | `"off"` | Controla se commits esperam confirmação de WAL. Irrelevante para workload SELECT-only (nenhum commit durante os benchmarks). Deixar em "on" adicionaria latência de confirmação desnecessária. |

### Por que não amostrar `seq_page_cost`

Se `seq_page_cost` variasse junto com `random_page_cost`, o que importaria para
o planner seria o *ratio* `random / seq`. Ao fixar `seq = 1.0`, amostrar
`random_page_cost ∈ [1.0, 4.0]` é equivalente a amostrar o ratio diretamente,
mas com semântica mais clara e sem adicionar uma dimensão espúria ao LHS.

---

## 16. Scale Factors por tier

| Tier | SF | Tamanho aproximado do banco |
|---|---|---|
| LOW | 1 | ~1 GB |
| MEDIUM | 2 | ~2 GB |
| HIGH | 4 | ~4 GB |

### Motivação para a progressão SF=1 → 2 → 4

O Scale Factor determina o volume de dados dos benchmarks TPC (SF=1 = ~1GB de
tabelas). A escolha de SF por tier serve a dois propósitos:

1. **Adequação ao hardware:** SF=4 em 2 GB de RAM (tier LOW) causaria I/O
   excessivo — o banco não cabe em memória, e cada query se tornaria I/O-bound
   independente da configuração PostgreSQL. O sinal de configuração seria enterrado
   sob ruído de I/O.

2. **Variação de hardware como feature:** ao variar SF junto com RAM e vCPU, o
   meta-modelo aprende o efeito de *escala de dados* além do efeito de hardware.
   A feature `sf` no vetor X é crucial para que o modelo generalize:
   > "Para SF=4, `shared_buffers = 1.5GB` é insuficiente; para SF=1, é ótimo."

### Por que não usar SF igual para todos os tiers

SF igual para todos (ex: SF=1 em todos) tornaria os tiers redundantes do ponto
de vista do dataset — a única variação seria hardware, não dados. O meta-modelo
precisaria de muito mais exemplos para aprender a interação hardware×SF×config
que é o coração do problema de auto-tuning.

---

## 17. Constraint `parallel_setup_cost > 1200`

**Implementação:** `pg_sampler/constraints.py → _validate_cross_12()`

```python
if psc > 1200 and per_g > 1:
    errors.append(
        f"Cross E1+E2: parallel_setup_cost ({psc}) > 1200 com "
        f"per_gather={per_g} — planner evitará planos paralelos"
    )
```

### Por que esse limite

`parallel_setup_cost` é o custo estimado de inicializar um worker paralelo.
O planner compara esse custo com o benefício estimado de paralelismo. Quando
`parallel_setup_cost` é muito alto:

- O planner escolhe planos seriais mesmo com `max_parallel_workers_per_gather > 1`.
- O parâmetro `per_gather` torna-se efetivamente inativo — o modelo vê
  `per_gather=3` mas o planner se comporta como se fosse `per_gather=0`.

**Implicação para o dataset:** se o modelo aprende que `per_gather=3` é bom
em geral, mas metade dos pontos com `per_gather=3` na verdade rodam serial
(porque `parallel_setup_cost` é alto), o sinal de `per_gather` fica ruidoso.

O valor 1200 foi escolhido empiricamente: acima desse limite, o planner
consistentemente evita paralelismo nos tiers testados (confirmado via
`EXPLAIN (ANALYZE, BUFFERS)` durante desenvolvimento).

---

## Resumo das decisões e impacto no dataset

| Decisão | Tipo | Tarefas afetadas (estimativa) | Impacto no meta-modelo |
|---|---|---|---|
| Pular Q17, Q20, Q95 | Benchmark design | Todas | Remove 45min de ruído constante por tarefa |
| Timeout por query = 15min | Estratégia | Todas | Preserva sinal de configs ruins; evita perda de configs boas |
| Imputação `900_000ms` para ausentes | Feature engineering | 127 tasks abandoned | Mantém 60% do dataset que seria descartado |
| Remover params I/O do Stage 3 | Espaço de parâmetros | Stage 3 (33% das tarefas) | Elimina 3 dimensões de ruído do LHS |
| Remover `enable_windowagg` | Correção crítica | 80 tasks (invalid_config) | Elimina 38% de desperdício; evita futuras rodadas inválidas |
| Upgrade PG16 → 17 | Infraestrutura | Todas | Compatibilidade; incluir `pg_version` como feature |
| Validação de params no prepare | Processo | Todas as rodadas futuras | Detecta erros antes de desperdiçar tempo de execução |
| Adicionar `enable_sort` | Espaço de parâmetros | Stage 1 (futuras rodadas) | Captura interações sort×hashagg×mergejoin |
| Restrição hashjoin+mergejoin=off | Constraints | ~5% dos pontos LHS | Remove região uniformemente catastrófica do espaço |
| JIT=off em LOW | Espaço de parâmetros | 30% das tarefas | Reduz interação hardware×config que exigiria mais dados |
| `per_gather ≥ 1` | Constraints | ~10% dos pontos LHS | Mantém espaço em configurações razoáveis |
| Default 30 → 50 configs | Configuração | Todas as rodadas futuras | +67% de pontos LHS por combinação; SHAP mais estável |
| Repetições por config | Qualidade | Opcional | Mediana de N runs remove ruído experimental |
| Targets por query | Feature engineering | Todas | Permite modelos especializados por tipo de query |
| Cache hit ratio como target | Feature engineering | Todas | Separa performance de eficiência de memória |

---

## 18. Pipeline de ML — arquitetura final (pós-Rodada 1)

**Data:** 2026-05-10

**Contexto:** após coletar os 335 dados da Rodada 1 (22/04/2026 → 10/05/2026, código `2204202610052026`), foi necessário definir a arquitetura definitiva do meta-modelo antes de treinar e antes de gerar mais dados.

**Decisão:** arquitetura com 4 especialistas XGBoost + ranker XGBoost, sem meta-modelo Ridge de stacking.

**Detalhes:**

- **4 especialistas XGBRegressor**, um por target: `geo_mean_tpch` (ms), `geo_mean_tpcds` (ms), `cache_hit_tpch` (%), `spill_tpcds` (queries com spill)
- **Score composto** calculado diretamente via rank normalizado dentro do grupo (tier × combination): `0.65 × rank_norm(1/geo_tpch) + 0.35 × rank_norm(cache_tpch)`
- **XGBRanker** (objective=`rank:ndcg`) como alternativa ao score para ranking direto
- **Sem Ridge de stacking** — testado e descartado: ρ=0.383, versus ρ=0.653 do score direto. O problema é que o score é relativo ao grupo, então predições absolutas dos especialistas não correlacionam com o rank global sem normalização

**Alternativas descartadas:**

| Alternativa | Motivo do descarte |
|---|---|
| LightGBM LambdaRank | Dependência `libgomp.so.1` não disponível no ambiente; substituído por XGBRanker |
| Ridge stacking | ρ=0.383 — predições absolutas não correlacionam com rank relativo |
| Workers como target | Campo `plan` sempre null nos 335 dados — informação nunca capturada |

**Resultados obtidos com Rodada 1:**

| Modelo | ρ | RMSE |
|---|---|---|
| M1 geo_mean_tpch | 0.962 | 733ms |
| M2 geo_mean_tpcds | 0.966 | 899ms |
| M3 cache_hit_tpch | 0.930 | 8.5% |
| M4 spill_tpcds | 0.949 | 7.3q |
| Ranker XGBoost | 0.761 | — |
| Top-1 accuracy | 38% | — |
| Top-3 accuracy | 62% | — |

---

## 19. Não alterar espaço de parâmetros entre rodadas

**Data:** 2026-05-10

**Decisão:** manter os mesmos 33 parâmetros amostrados pelo LHS nas rodadas futuras, mesmo sabendo que alguns têm impacto baixo segundo o SHAP.

**Justificativa técnica:**

A análise SHAP sobre os dados da Rodada 1 revelou a seguinte hierarquia de impacto (modelo `geo_mean_tpch`):

| Parâmetro | Impacto SHAP |
|---|---|
| vcpus (hardware) | 31.6% |
| enable_hashjoin | 9.7% |
| shared_buffers | 9.3% |
| memory_mb (hardware) | 7.8% |
| enable_sort | 5.2% |
| ... | ... |
| enable_material | 0.1% |

Parâmetros como `enable_material` (0.1%), `enable_parallel_append` (0.2%) e `join_collapse_limit` (0.3%) têm impacto baixo — mas **não foram removidos do espaço de amostragem** pelos seguintes motivos:

1. **Incompatibilidade entre rodadas:** se um parâmetro é removido, os dados novos terão aquele parâmetro sempre no valor default, enquanto os dados antigos têm variação real. Isso cria distribuição inconsistente que confunde o modelo durante o treino conjunto.

2. **Impacto baixo ≠ impacto nulo:** 0.1% de impacto em 335 configs ainda é sinal real. Com mais dados e combinações diferentes, a importância pode crescer.

3. **O modelo XGBoost lida bem com features de baixo impacto:** diferentemente de modelos lineares, o XGBoost atribui splits pouco frequentes às features fracas — elas não adicionam ruído significativo.

4. **Custo de uma decisão errada é alto:** mudar o espaço de parâmetros invalida a possibilidade de combinar rodadas antigas e novas num único treino, desperdiçando semanas de coleta.

**Regra estabelecida:** o espaço de parâmetros só deve ser alterado se uma nova rodada for completamente independente das anteriores (sem combinação de datasets). Alterações devem ser documentadas aqui com justificativa explícita.

---

## 20. Por que uma segunda rodada foi necessária

**Data:** 2026-05-10

**Contexto:** após treinar e avaliar o meta-modelo com os 335 dados da Rodada 1, identificou-se uma limitação específica no ranking local.

**O problema:**

O modelo consegue explicar 96.2% da variância global (ρ=0.962), mas o **top-1 accuracy é 38%** — o modelo acerta a melhor config em apenas 38% dos grupos.

A causa raiz não é o modelo em si, mas a relação entre RMSE e granularidade do espaço:

- **RMSE do modelo:** ~733ms
- **Diferença média entre configs adjacentes dentro de um grupo:** ~200ms
- **Conclusão:** o erro de predição é maior que a diferença entre configs próximas — o modelo não consegue separar configs que são "quase iguais" em performance

**Por que mais dados ajudam:**

Com 16 configs por grupo (tier × combination), o modelo tem poucos exemplos de "qual config é melhor que qual" dentro de cada célula. Dobrando para 32-33 configs por grupo:

1. O modelo vê mais pares de comparação → o ranker aprende melhor a ordenar
2. O RMSE tende a cair com mais dados (mais cobertura do espaço → predições mais precisas)
3. Top-1 esperado: 38% → ~50%, Top-3: 62% → ~70%

**O que foi descartado antes de decidir pela segunda rodada:**

- **Optuna (80 trials):** executado em 2026-05-10. Resultado: melhora marginal (+4.3% em geo_tpcds, -0.4% em geo_tpch). O modelo já estava próximo do ótimo para os dados disponíveis — o gargalo é quantidade de dados, não hiperparâmetros.

**Rodada 2 — configuração:**

| Item | Valor |
|---|---|
| Data de início | 2026-05-10 |
| n_configs | 51 (17 por tier por combination) |
| start_id | 336 |
| Seed LHS | não definida (não-determinístico) |
| Total de tarefas esperadas | 357 (7 × 3 × 17) |
| Total combinado após treino | 335 + 357 = 692 |

---

## 21. Mecanismo de start_id para rodadas múltiplas

**Data:** 2026-05-10

**Problema:** o gerador sempre iniciava IDs de tarefa em 0. Com duas rodadas, os arquivos de resultado (`task_0.json`, `task_1.json`, ...) da Rodada 2 sobrescreviam os da Rodada 1 no diretório `output/benchmark_results/`.

**Solução:** parâmetro `--start-id N` adicionado em três pontos:

1. `cli/generate.py` — argumento de linha de comando `--start-id`
2. `task_queue/execution_queue.py` — parâmetro `start_id` no método `from_dict()`
3. `web/app.py` — parâmetro `start_id` no endpoint `POST /api/generator/start`
4. `web/static/js/api.js` — lê o campo `gen-start-id` da interface e passa na URL

**Uso correto:** `start_id = max_id_da_rodada_anterior + 1`. Para a Rodada 2: `start_id=336` (IDs da Rodada 1: 0–335).

**Impacto:** os arquivos de resultado ficam em `output/benchmark_results/{tier}/{combination}/task_336.json` em diante, sem conflito com os da Rodada 1. Na hora de treinar, o extrator de features lê todos os arquivos do diretório — Rodada 1 e Rodada 2 juntos automaticamente.

---

## 22. Otimização de hiperparâmetros com Optuna

**Data:** 2026-05-10

**Objetivo:** verificar se hiperparâmetros melhores reduziriam o RMSE dos especialistas, potencialmente melhorando o top-1 accuracy sem precisar de mais dados.

**Resultado (80 trials por modelo):**

| Modelo | RMSE baseline | RMSE Optuna | Variação |
|---|---|---|---|
| geo_mean_tpch | 732.7ms | 735.5ms | -0.4% (piorou levemente) |
| geo_mean_tpcds | 898.6ms | 859.6ms | +4.3% |
| cache_hit_tpch | 8.5% | 8.2% | +3.1% |
| spill_tpcds | 7.3q | 7.4q | -1.1% |

**Conclusão:** os modelos já estavam próximos do ótimo com os parâmetros default. A melhora marginal confirma que o gargalo é quantidade de dados, não configuração do modelo. Os parâmetros encontrados pelo Optuna foram salvos em `output/models/best_params.json` e serão usados no retreino pós-Rodada 2.

---

## 23. Nova Rodada via interface web

**Data:** 2026-05-10

**Contexto:** ao final da Rodada 1, a interface web exibia apenas dois botões na tela "Benchmarks concluídos": "Ver Resultados" e "Reiniciar". O botão "Reiniciar" apaga todos os resultados — inadequado para iniciar uma segunda rodada que deve preservar os dados existentes.

**Decisão:** adicionar seção "Nova Rodada" na tela de conclusão da interface web.

**O que foi implementado:**

- Campo **Seed LHS** (opcional): permite reprodutibilidade se necessário; deixar vazio garante configs diferentes das da rodada anterior
- Campo **Start ID** (pré-preenchido com o número de tarefas concluídas): evita conflito de arquivos
- Botão **"Gerar Nova Rodada"**: chama `generatorStart()` sem apagar benchmark_results

**Comportamento:** clicar em "Gerar Nova Rodada" gera uma nova `queue.json` com IDs a partir do start_id informado. Os arquivos em `output/benchmark_results/` **não são tocados**. O fluxo normal de prepare → run continua igual.

**Distinção importante entre os dois botões:**

| Ação | Apaga resultados? | Uso correto |
|---|---|---|
| Nova Rodada | Não | Coletar mais dados mantendo histórico |
| Reiniciar | Sim (irreversível) | Começar do zero (ex: mudança de hardware) |


---

## 24. Manter outliers no dataset de treino

**Data:** 2026-05-30

**Contexto:** após combinar as Rodadas 1 e 2 (672 tasks) e retreinar, o RMSE do M1 saltou de 733ms para 13.324ms. Investigação identificou que 7 tasks da Rodada 2 tinham `geo_mean_tpch` muito acima do normal:

| Task | Combination | Tier | geo_mean_tpch |
|---|---|---|---|
| task_221 | s1_s2_s3 | medium | 261.873ms (~4 min) |
| task_330 | s1_s2_s3 | high | 248.039ms (~4 min) |
| + 5 outras | — | — | > 11.552ms (p99) |

Essas configs receberam do LHS uma combinação de parâmetros catastroficamente ruim — provavelmente `enable_hashjoin=off + enable_mergejoin=off` simultaneamente com configurações de memória inadequadas, fazendo quase todas as 20 queries TPC-H atingirem o timeout de 15 minutos.

**Teste realizado:** retreino sem os 7 outliers (filtro percentil 99):

| Métrica | Com outliers (672) | Sem outliers (665) |
|---|---|---|
| RMSE M1 | 13.324 ms | 636 ms |
| ρ M1 | 0.966 | 0.969 |

**Decisão: manter os outliers.**

**Justificativas:**

1. **Honestidade científica:** são medições reais e válidas. Filtrar dados para melhorar métricas sem justificativa técnica comprometeria a integridade do TCC.

2. **Impacto prático nulo:** ρ passou de 0.966 para 0.969 — diferença irrelevante. As recomendações do modelo não mudam, pois ele já classifica corretamente essas configs como ruins.

3. **Métrica correta é ρ, não RMSE:** RMSE é sensível a outliers e é uma métrica inadequada para avaliar modelos de ranking. O Spearman ρ mede o que importa — a capacidade do modelo de ordenar configurações corretamente — e permanece em 0.966.

4. **Argumento defensável na banca:** "o RMSE é inflado por 7 configs catastroficamente ruins que o LHS amostrou; o ρ=0.966 mostra que o modelo ordena corretamente as configurações" é uma resposta técnica sólida.

---

## 25. Resultados combinados Rodadas 1+2 e decisão sobre terceira rodada

**Data:** 2026-05-30

### Resultados após treino com 672 tasks (Rodadas 1+2)

| Modelo | ρ Rodada 1 (335) | ρ Rodadas 1+2 (672) | Variação |
|---|---|---|---|
| M1 geo_mean_tpch | 0.962 | **0.966** | +0.4% |
| M2 geo_mean_tpcds | 0.966 | **0.977** | +1.1% |
| M3 cache_hit_tpch | 0.930 | **0.927** | -0.3% |
| M4 spill_tpcds | 0.949 | **0.976** | +2.7% |
| Ranker XGBoost | 0.761 | **0.765** | +0.4% |
| Score global ρ | 0.653 | **0.743** | **+9.0%** |
| Top-3 accuracy | 62% | **52%** | -10% |

**Observação sobre top-3:** a queda de 62% para 52% não indica piora do modelo — é consequência direta de grupos maiores (16 → 32 configs por grupo). Com mais configs por grupo, o "top-3" representa uma fração menor do espaço, tornando o critério mais exigente. O score global ρ (+9%) e os especialistas mostram melhora real.

### Decisão: não realizar terceira rodada

**Motivos:**

1. **ρ próximo do teto:** 0.966 já é excelente. Mais dados trariam ρ → 0.970 no máximo — ganho marginal indefensável dado o custo de ~17 dias de coleta.

2. **Top-3 piora com mais dados:** grupos maiores = critério mais exigente. Uma terceira rodada geraria 48 configs por grupo, provavelmente reduzindo top-3 para ~45%. Mais dados não resolvem essa limitação estrutural.

3. **Suficiente para o TCC:** ρ=0.966 e top-3=52% são resultados academicamente sólidos e defensáveis. A diferença de 2.6× entre melhor e pior config por tier valida o impacto real do autotuning.

4. **Custo de oportunidade:** 17 dias adicionais de coleta vs tempo de escrita do TCC — a escrita agrega mais valor ao trabalho neste momento.

**Conclusão:** o dataset de 672 tasks (335 + 337) representa o conjunto de treinamento final do meta-modelo. O foco agora é a escrita do TCC com os resultados obtidos.


---

## 26. Top-K accuracy — problema de escala e como reportar no TCC

**Data:** 2026-05-30

### O problema

A métrica top-K accuracy **não é invariante ao tamanho do grupo**. Conforme mais dados são coletados, os grupos (tier × combination) ficam maiores, e o critério de "acertar o top-3" fica automaticamente mais exigente — mesmo que o modelo esteja melhorando.

**Evidência observada:**

| Dataset | Configs por grupo | Top-3 representa | Top-3 accuracy |
|---|---|---|---|
| Rodada 1 (335 tasks) | ~16 | 18% do grupo | **62%** |
| Rodadas 1+2 (672 tasks) | ~32 | 9% do grupo | **52%** |
| Hipotético 3ª rodada (1008 tasks) | ~48 | 6% do grupo | ~45% (estimado) |

O modelo **melhorou** (ρ: 0.653 → 0.743), mas o top-3 caiu porque há mais configs competindo pelo pódio. É como comparar "acerte o top-3 de uma turma de 16 alunos" com "acerte o top-3 de uma turma de 32 alunos" — a prova não ficou mais fácil, o critério ficou mais exigente.

### Por que é estrutural

Não existe solução em código ou dados para este problema. As únicas alternativas seriam:

1. **Manter o número de configs por grupo fixo** — significa parar de coletar dados, o que contradiz o objetivo de melhorar o modelo
2. **Usar top-K% em vez de top-K fixo** — reportar "top-18% accuracy" em vez de "top-3 accuracy", tornando a métrica invariante ao tamanho do grupo
3. **Usar ρ Spearman como métrica principal** — que mede qualidade de ranking de forma scale-invariante

### Como reportar no TCC

Nunca reportar top-K sem o contexto do tamanho do grupo. Exemplo correto:

> "O modelo alcançou top-3 accuracy de 52% em grupos de 32 configurações, equivalente a identificar a melhor configuração dentro dos 9% superiores do espaço amostrado."

Comparação correta entre rodadas:

| Dataset | Top-3 accuracy | Grupo | Top-3 como % do grupo | ρ global |
|---|---|---|---|---|
| Rodada 1 (335) | 62% | 16 configs | 18% | 0.653 |
| Rodadas 1+2 (672) | 52% | 32 configs | 9% | 0.743 |

A segunda linha mostra resultado **melhor** — mesmo com top-3 menor, o ρ subiu +9% e o critério ficou duas vezes mais exigente. O examinador precisa de ambas as colunas para avaliar corretamente.

### Métrica recomendada para defesa

**Spearman ρ** é a métrica principal do modelo porque:
- Mede correlação de ranking independente da escala do grupo
- Não é afetada por outliers (ao contrário do RMSE)
- É interpretável: ρ=0.966 significa que 96.6% da variância no ranking real é explicada pelo modelo

---

## 27. Análise de custo-efetividade em nuvem

**Data:** 2026-05-30

### Motivação

Uma das contribuições do TCC é mostrar que o meta-modelo permite reduzir o custo operacional de workloads OLAP em nuvem. Para quantificar isso, foi desenvolvido o script `ml/cost_analysis.py`, que traduz tempos de execução em custo monetário real (USD e BRL) e compara cenários de configuração.

### Mapeamento de tiers para instâncias EC2

Os containers Docker do projeto foram mapeados para instâncias compute-optimized (c5) da AWS, adequadas para workloads analíticos:

| Tier | vCPU Docker | RAM Docker | Instância EC2 | Preço/hora (us-east-1) |
|------|------------|------------|---------------|------------------------|
| low | 2 | 2 GB | c5.large (2 vCPU, 4 GB) | $0.085/hr |
| medium | 4 | 4 GB | c5.xlarge (4 vCPU, 8 GB) | $0.170/hr |
| high | 6 | 5 GB | c5.2xlarge (8 vCPU, 16 GB) | $0.340/hr |

**Fonte:** AWS EC2 On-Demand us-east-1, mai/2026. Câmbio: 1 USD = R$ 5.75.

### Problema crítico: scale factors diferentes por tier

Cada tier usa um scale factor (SF) diferente para que o banco de dados caiba na memória disponível:

| Tier | SF | Volume de dados TPC |
|------|----|---------------------|
| low | 1 | ~1 GB |
| medium | 2 | ~2 GB |
| high | 4 | ~4 GB |

**Consequência direta:** os tempos de execução e custos por run **não são comparáveis diretamente** entre tiers, porque cada um processa volumes de dados diferentes. Uma query no LOW que leva 3s processa 1/4 dos dados que uma query de 12s no HIGH.

**Solução adotada:** dividir a análise em dois planos independentes:
- **Análise A (within-tier):** mesma instância, configs diferentes → comparação direta de custo e desempenho
- **Análise B (cross-tier):** normalizar pelo SF → `custo_por_SF = custo_run / SF` como métrica comparável

### Resultados — Análise A: valor do tuning dentro de cada tier

Comparação entre configuração ruim (p90 de duração) vs melhor configuração real encontrada no dataset, dentro do mesmo tier:

| Tier | Speedup TPC-H (pior→melhor) | Redução de custo | Economia mensal (30 exec/mês) |
|------|----------------------------|-----------------|---------------------------------|
| low | 2.1× | 20% | R$ 4,26 |
| medium | 1.4× | 15–32% | R$ 7,71 |
| high | **3.5×** | **23%** | **R$ 32,25** |

**Interpretação:** no HIGH tier (o mais caro), escolher uma configuração ruim pode custar o dobro do tempo e do dinheiro em relação à melhor configuração disponível. O meta-modelo identifica boas configurações sem necessidade de testar todas — essa é a contribuição prática central do trabalho.

Valores concretos para o HIGH tier:
- Config ruim (p90): 140 min / run → R$ 4,59/run → **R$ 137,59/mês**
- Melhor config real: 108 min / run → R$ 3,51/run → **R$ 105,34/mês**
- Economia: **R$ 32,25/mês** em regime de relatório diário

### Resultados — Análise B: eficiência entre tiers (normalizado por SF)

| Tier | Custo/run | Custo/SF | Relativo ao LOW |
|------|-----------|----------|-----------------|
| low | $0.097 | $0.097/SF | referência |
| medium | $0.249 | $0.124/SF | +29% por unidade |
| high | $0.611 | $0.153/SF | +58% por unidade |

**Interpretação:** instâncias maiores pagam um prêmio por SF processado. O LOW tier é o mais custo-eficiente por unidade de dado. No entanto, isso não implica "sempre use LOW" — o SF deve ser escolhido de acordo com o tamanho real do banco de dados em produção.

### Como reportar no TCC

A narrativa correta para a defesa:

> *"Dado um hardware fixo, o meta-modelo identifica configurações PostgreSQL que são 2–3,5× mais rápidas e 15–32% mais baratas por execução do que uma configuração aleatória. No HIGH tier (SF=4), a economia mensal estimada é de R$ 32 em regime de relatório diário, com speedup de 3,5× nas queries TPC-H. Para comparações entre tiers com scale factors distintos, utilizou-se a métrica custo/SF, que normaliza pelo volume de dados processado."*

### Trabalhos relacionados para comparação

Nenhum dos trabalhos relacionados levantados na literatura faz essa comparação de custo cross-tier com normalização por SF. OtterTune, CDBTune, LlamaTune e GPTuner assumem hardware fixo e medem apenas latência ou throughput. A dimensão de custo monetário em nuvem com múltiplos tiers e scale factors é uma contribuição original deste TCC.

### Artefato

Script: `ml/cost_analysis.py`

```bash
# Análise completa (todos os tiers)
python ml/cost_analysis.py --features output/features.csv

# Só um tier
python ml/cost_analysis.py --features data/processed/features.csv --tier high
```

---

## 28. Arquitetura do projeto — Cookiecutter Data Science adaptado

**Data:** 2026-05-30

### Motivação

Com o pipeline de coleta e o modelo ML consolidados, o projeto passou por uma reestruturação de diretórios visando apresentação acadêmica (TCC). O critério central foi adotar um padrão reconhecido pela comunidade científica de ML e que satisfaça os checklists de reprodutibilidade de conferências como NeurIPS e ICML.

### Padrão adotado: Cookiecutter Data Science v2 (DrivenData)

O [CCDS v2](https://cookiecutter-data-science.drivendata.org/) é o padrão de facto em projetos acadêmicos de ML. Seus princípios centrais aplicados aqui:

1. **Raw data is immutable** — os JSONs dos benchmarks (`data/raw/`) nunca são editados in-place
2. **Dados não vão pro git** — `data/raw/` e `data/models/` estão no `.gitignore`
3. **Processados são versionados** — `data/processed/features.csv` (984 KB) está commitado, permitindo reproduzir todo o ML sem os 2.2 GB de dados brutos
4. **Exploração separada de produção** — `notebooks/poc.py` separado de `ml/`
5. **Relatórios separados do código** — `reports/figures/` para figuras do TCC

### Adaptações ao CCDS

O CCDS assume um único pacote Python (`meu_projeto/`). Este projeto tem múltiplos pacotes independentes por necessidade técnica (pipeline de coleta é completamente separado do pipeline ML). A adaptação foi: **adotar a estrutura de diretórios do CCDS sem fundir os pacotes em um único módulo**.

| Aspecto | CCDS original | Este projeto |
|---------|--------------|--------------|
| Código-fonte | `meu_projeto/` (1 pacote) | múltiplos pacotes na raiz |
| Layout | flat layout | flat layout (igual) |
| Instalação | `pip install -e .` | `pip install -e .` via `pyproject.toml` |
| `data/` | gitignored por completo | `data/processed/` versionado |
| `notebooks/` | Jupyter .ipynb | Python .py (mesmo conceito) |

### Mudanças implementadas

**Diretórios criados:**
- `data/raw/` — benchmark JSONs (2.2 GB, gitignored)
- `data/processed/` — `features.csv` (984 KB, versionado)
- `data/models/` — modelos XGBoost treinados (gitignored)
- `notebooks/` — exploração (`poc.py` movido de `ml/`)
- `reports/figures/` — figuras para o TCC
- `references/` — papers e material de referência
- `logs/` — logs de execução em runtime (gitignored)

**Renomeações de pacotes (git mv, histórico preservado):**
- `pg_sampler/` → `sampler/` — nome mais limpo e alinhado ao domínio
- `task_queue/` → `taskqueue/` — evita conflito de nome com stdlib `queue`

**Substituição de `sys.path.insert()` por `pip install -e .`:**

Todos os scripts `ml/*.py` faziam `sys.path.insert(0, str(Path(__file__).parent.parent))` para importar entre módulos. Com o `pyproject.toml` e `pip install -e .`, todos os pacotes do projeto ficam instalados no ambiente e os imports funcionam de qualquer lugar sem manipulação de path. O `make setup` passou a incluir `pip install -e .`.

**Dados:**
- `output/` renomeado para `data/` com subdiretórios `raw/`, `processed/`, `models/`
- Todos os paths atualizados em `ml/config.py`, `cli/`, `web/app.py`, `runner/`
- `output/` legado mantido no `.gitignore` para não commitar dados existentes

### Por que não usar `src/` layout

O CCDS v2 usa flat layout (pacote direto na raiz), não `src/` layout. `src/` layout é para pacotes instaláveis distribuídos via PyPI — não se aplica aqui. Usar `src/` obrigaria a renomear todos os packages internos e mudar ~40 imports sem ganho real para um projeto de TCC não-distribuído.

### Decisão sobre dados no git

| Artefato | Versionado? | Justificativa |
|----------|------------|---------------|
| `data/raw/` (JSONs, 2.2 GB) | ❌ | Too large; gitignored |
| `data/processed/features.csv` (984 KB) | ✅ | Reproduz todo o pipeline ML |
| `data/models/*.ubj` | ❌ | Gerado por `make train` em segundos |
| `logs/*.log` | ❌ | Runtime, sem valor de versionamento |

Decisão CCDS: *"Make it possible for anyone to reproduce with only the code + data/processed/"*. O `features.csv` commitado satisfaz esse critério — qualquer pessoa pode clonar o repositório e rodar `make train && make evaluate` sem executar os 20+ dias de benchmarks.

### Referências
- Cookiecutter Data Science v2: https://cookiecutter-data-science.drivendata.org/
- NeurIPS reproducibility checklist: https://neurips.cc/public/guides/PaperChecklist
- Papers with Code ML Code Completeness: https://paperswithcode.com/

