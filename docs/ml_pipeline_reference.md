# Referência: Pipeline ML — PostgreSQL Autotuning

> **Última atualização:** 2026-05-10 — resultados reais da Rodada 1 incorporados,
> LightGBM substituído por XGBRanker, Optuna executado, Rodada 2 em andamento.
> Use como referência definitiva ao retomar o desenvolvimento do módulo `ml/`.

---

## 1. Visão geral do pipeline completo

```
specs/spaces/ (JSON)
      │
      ▼
pg_sampler/ — LHS sobre N dimensões → configs → output/queue.json
      │
      ▼
cli/prepare.py — 6 imagens Docker (tpch/tpcds × low/medium/high)
      │
      ▼
cli/run.py → output/benchmark_results/{tier}/{combo}/task_N.json
      │         ⚠ output/ é SOMENTE LEITURA — nunca editar manualmente
      │
      ▼
ml/extract_features.py → output/features.csv
      │
      ├── ml/train.py      → output/models/{m1,m2,m3,m4}.ubj + ranker.ubj
      ├── ml/evaluate.py   → ablação + SHAP + qualidade de ranking
      ├── ml/tune.py       → output/models/best_params.json (Optuna)
      └── ml/recommend.py  → dado tier + combo + candidatos → top-K
```

**Dataset atual:**
- Rodada 1 (concluída): 335 tasks done · 1 abandoned · 3 tiers × 7 combos × 16 configs
- Rodada 2 (em andamento desde 2026-05-10): 357 tasks · 3 tiers × 7 combos × 17 configs
- **Total esperado após Rodada 2:** 692 tasks

---

## 2. Intenção experimental dos estágios (estudo de ablação)

| Grupo | Combinações | Params | Objetivo |
|---|---|---|---|
| Controle | s1, s2, s3 | 12–13 cada | baseline por domínio |
| Pares | s1+s2, s1+s3, s2+s3 | 24–25 | ganho de combinações |
| Completo | s1+s2+s3 | 33 | custo de alta dimensão |

**Resultado (Rodada 1, modelo M1 com todos os dados):**

| Stage | Features | RMSE (ms) | Spearman ρ |
|---|---|---|---|
| S1 only | 13 | 1196 | 0.877 |
| S1+S2 | 25 | 795 | 0.933 |
| S1+S2+S3 | 33 | 733 | **0.962** |

Tendência clara e monótona — mais parâmetros → modelo melhor, com retorno
decrescente. Resultado defensável na banca.

---

## 3. Parâmetros por estágio

### Stage 1 — Memória, paralelismo básico, toggles (13 params)

| Parâmetro | Tipo | Impacto OLAP |
|---|---|---|
| `jit` | bool | Alto — on/off de compilação JIT |
| `random_page_cost` | float [1.0, 4.0] | Alto — index vs seqscan |
| `default_statistics_target` | int [100, 400] | Médio — qualidade de estimativas |
| `max_parallel_workers` | int [1, vcpus] | Alto — teto de workers |
| `max_parallel_workers_per_gather` | int [1, vcpus//2] | Alto — workers por Gather |
| `shared_buffers` | memory | **Alto** — SHAP #3 (11.7%) |
| `effective_cache_size` | memory | Médio — hint do planner |
| `work_mem` | memory | **Muito alto** — governa hash joins e sorts |
| `enable_hashagg` | bool | Alto |
| `enable_bitmapscan` | bool | Médio |
| `enable_nestloop` | bool | Alto |
| `enable_parallel_hash` | bool | Alto |
| `enable_sort` | bool | **Alto** — SHAP #5 (6.6%) |

**Fixos (não amostrados, excluídos do vetor X):**
- `seq_page_cost = 1.0` — âncora do planner
- `max_worker_processes = cpu×2+4` — derivado do hardware
- `synchronous_commit = "off"` — SELECT-only, sem efeito

---

### Stage 2 — Custos de CPU, paralelismo fino, join planning (12 params)

| Parâmetro | Tipo | Impacto OLAP |
|---|---|---|
| `cpu_tuple_cost` | float | Baixo |
| `cpu_index_tuple_cost` | float | Baixo |
| `cpu_operator_cost` | float | Baixo |
| `parallel_setup_cost` | float | Alto — overhead de setup |
| `parallel_tuple_cost` | float | Médio |
| `min_parallel_table_scan_size` | memory | Alto — threshold seqscan paralelo |
| `min_parallel_index_scan_size` | memory | Médio |
| `join_collapse_limit` | int [4, 16] | Médio |
| `from_collapse_limit` | int [4, 16] | Médio |
| `hash_mem_multiplier` | float [1.0, 4.0] | **Muito alto** — multiplica work_mem |
| `enable_mergejoin` | bool | Alto |
| `enable_hashjoin` | bool | **Muito alto** — SHAP #2 (12.2%) |

---

### Stage 3 — Toggles avançados do planner (8 params)

Parâmetros removidos do Stage 3 original (decisão de engenharia #4 e #5):
- ~~`checkpoint_completion_target`~~ — correlação ~0 com targets (SELECT-only)
- ~~`bgwriter_lru_maxpages`~~ — idem
- ~~`wal_buffers`~~ — idem
- ~~`enable_windowagg`~~ — não existe no PostgreSQL 17

| Parâmetro | Tipo | Impacto OLAP |
|---|---|---|
| `enable_memoize` | bool | Médio |
| `enable_gathermerge` | bool | Alto |
| `enable_incremental_sort` | bool | Alto |
| `enable_material` | bool | Médio |
| `enable_indexscan` | bool | Médio |
| `enable_indexonlyscan` | bool | Médio |
| `enable_parallel_append` | bool | Médio |
| `parallel_leader_participation` | bool | **Médio** — SHAP #6 (4.9%) |

---

## 4. Tiers de hardware

| Tier | vCPUs | RAM | Scale Factor | shm |
|---|---|---|---|---|
| low | 2 | 2 GB | SF=1 | 576 MB |
| medium | 4 | 4 GB | SF=2 | 1152 MB |
| high | 6 | 5 GB | SF=4 | 1536 MB |

**Encoding:** `vcpus`, `memory_mb`, `sf` como features numéricas contínuas
(não one-hot). Importância SHAP: HW=41% do sinal total — `vcpus` é a feature
mais importante de todas (39.7% isolada).

---

## 5. Queries — benchmarks ativos

**TPC-H:** 20 queries ativas (Q17 e Q20 excluídas permanentemente — sempre
timeout em qualquer configuração, adicionariam 45min de ruído por task).

**TPC-DS:** 98 queries ativas (Q95 excluída — mesmo motivo).

**Timeout por query:** 15 minutos (`statement_timeout`).
**Imputação:** timeout → 900 000 ms · OOM → 1 350 000 ms (1.5×).

---

## 6. Targets — o que o modelo aprende

### Targets ativos (com sinal real)

| Target | Coluna no CSV | Transform | Score weight | ρ Rodada 1 |
|---|---|---|---|---|
| `geo_mean_tpch` | `tpch_geo_mean_ms` | `log` | **0.65** | 0.962 |
| `geo_mean_tpcds` | `tpcds_geo_mean_ms` | `log` | 0.0 | 0.966 |
| `cache_hit_tpch` | `tpch_cache_hit_ratio` | none | **0.35** | 0.930 |
| `spill_tpcds` | `tpcds_queries_with_spill` | `log1p` | 0.0 | 0.949 |

`geo_mean_tpcds` e `spill_tpcds` entram no diagnóstico mas não no score
composto. Specialists com `score_weight=0` são treinados e salvos para
análise SHAP e uso futuro.

### Targets removidos (sem sinal nos dados coletados)

| Target | Motivo |
|---|---|
| `avg_workers_launched` | Sempre 0.0 — `plan=null` em 336/336 tasks, paralelismo não capturado |
| `queries_with_parallelism` | Sempre 0 — mesma causa |
| `rapl_energy_total_j` | Sempre NaN — Intel RAPL inacessível sem root |

### Nota sobre spill (mudança de design)

O design original previa `XGBClassifier` com target `queries_with_spill > 0`.
Nos dados coletados, **100% das tasks têm spill** (min=4, max=89 queries no
TPC-DS). Sem classe negativa, o classificador aprende apenas o prior.

**Solução:** `XGBRegressor` com `log1p(queries_with_spill_tpcds)` — aprende
*quanto* de pressão de memória, não *se* há pressão. Spill TPC-DS tem CV=0.64
e range 4–89, sinal suficiente para regressão.

---

## 7. Arquitetura dos modelos

### Nível 1 — Especialistas XGBoost (4 modelos)

Treinados com `KFold(5, shuffle=True, seed=42)`. OOF predictions salvas em
`output/models/oof_predictions.csv`. Formato de arquivo: `.ubj` (binário XGBoost).

```python
# ml/config.py — hiperparâmetros base (pós-Optuna 2026-05-10)
XGB_PARAMS = dict(
    n_estimators=400, max_depth=4, learning_rate=0.04,
    subsample=0.8, colsample_bytree=0.8, min_child_weight=5,
    reg_lambda=2.0, tree_method="hist", random_state=42,
)
# Parâmetros otimizados por modelo salvos em output/models/best_params.json
# (Optuna 80 trials — melhora marginal; baseline já estava próximo do ótimo)
```

**Resultados por rodada (KFold-5 OOF):**

| Modelo | Rodada 1 ρ (335 tasks) | Rodadas 1+2 ρ (672 tasks) |
|---|---|---|
| M1 geo_mean_tpch | 0.962 | **0.966** |
| M2 geo_mean_tpcds | 0.966 | **0.977** |
| M3 cache_hit_tpch | 0.930 | **0.927** |
| M4 spill_tpcds | 0.949 | **0.976** |

**Nota sobre RMSE:** o RMSE do M1 subiu de 733ms (Rodada 1) para 13.324ms (672 tasks) devido a 7 configs catastroficamente ruins amostradas pelo LHS na Rodada 2 (geo_mean > 11.5s). Sem esses outliers, RMSE = 636ms. Decisão: manter os outliers (ver decisão de engenharia #24). Para avaliação, usar ρ como métrica principal.

### Nível 2 — Ranker XGBoost

```python
# XGBRanker(objective="rank:ndcg") — substituiu LightGBM em 2026-05-10
# Motivo: LightGBM exige libgomp.so.1 (não disponível no ambiente)
# Grupos: (tier, combination) — ~16 configs por grupo
# Relevância: score composto quantizado 0–9
```

**Resultado Rodada 1:** ρ=0.761 (ranking global)

**Histórico da decisão:** LightGBM `LGBMRanker` foi a escolha original, mas gerou
`OSError: libgomp.so.1` por falta de dependência de sistema. XGBRanker com
`objective="rank:ndcg"` é equivalente e não depende de libs externas.

### Score composto (sem meta-modelo Ridge)

O Ridge de stacking foi testado e descartado: ρ=0.383 contra ρ=0.653 do score direto.
O problema: o score é relativo ao grupo — predições absolutas dos especialistas não
correlacionam com o rank sem normalização dentro do grupo.

**Score aplicado diretamente nas predições:**
```python
# Dentro de cada grupo de candidatos (mesmo tier/combo):
score = (
    0.65 × rank_norm(1 / ŷ_geo_mean_tpch) +   # minimizar latência TPC-H
    0.35 × rank_norm(ŷ_cache_hit_tpch)          # maximizar cache hit
)
# Pesos otimizados via grid search — 0.65/0.35 confirmado como ótimo
# Resultado: ρ=0.653 global (vs 0.383 do Ridge)
```

### Validação das recomendações (2026-05-10)

Rodado `ml/recommend.py` para os 3 tiers com combination `s1` usando os dados reais
da Rodada 1. Resultado:

| Tier | Geo-mean previsto | Geo-mean real | Erro |
|---|---|---|---|
| low | 731.5 ms | 719.6 ms | 11.9 ms |
| medium | 1564.4 ms | 1413.0 ms | 151 ms |
| high | 3699.5 ms | 3532.9 ms | 166 ms |

**Config recomendada pelo modelo (parâmetros principais S1):**

| Parâmetro | LOW | MEDIUM | HIGH |
|---|---|---|---|
| shared_buffers | 512 MB | 1 GB | 1.25 GB |
| work_mem | 32 MB | 32 MB | 32 MB |
| effective_cache_size | 1.5 GB | 2.5 GB | 4 GB |
| jit | OFF | OFF | OFF |
| enable_nestloop | ON | ON | ON |
| max_parallel_workers | 2 | 3 | 4 |
| random_page_cost | 3.93 | 2.51 | 3.14 |

**Análise de sanidade (faz sentido técnico?):**
- `shared_buffers` cresce com o hardware ✅ — cache principal, mais RAM = melhor
- `work_mem` = 32 MB nos três ✅ — workloads paralelos: valor alto desperdiça RAM total
- `jit = OFF` nos três ✅ — TPC-H/TPC-DS têm queries médias; JIT tem overhead de compilação
- Paralelismo cresce com vCPUs ✅ — lógico
- `random_page_cost ~3` ✅ — NVMe é mais rápido que HDD (default=4), mas tem latência real

**Diferença best vs worst por tier (sinal real do modelo):**

| Tier | Melhor config real | Pior config real | Razão |
|---|---|---|---|
| low | 708 ms | 1780 ms | **2.5×** |
| medium | 1413 ms | 3555 ms | **2.5×** |
| high | 3533 ms | 9063 ms | **2.6×** |

A diferença de até 2.6× entre a melhor e pior configuração valida que a escolha
de parâmetros PostgreSQL tem impacto real e mensurável, justificando o projeto.

### Qualidade de ranking — comparação entre rodadas

| Métrica | Rodada 1 (335 tasks) | Rodadas 1+2 (672 tasks) |
|---|---|---|
| Configs por grupo | ~16 | ~32 |
| Top-3 accuracy | 62% | 52% |
| Top-3 como % do grupo | 18% | 9% |
| Score global ρ | 0.653 | **0.743** |
| Ranker ρ | 0.761 | 0.765 |

**Nota sobre top-3:** a queda de 62% → 52% não indica piora — é consequência de grupos maiores (critério mais exigente). O ρ global subiu +9%, mostrando melhora real. Ver decisão de engenharia #26 para explicação completa e como reportar no TCC.

**Limitação estrutural do top-K:** top-K accuracy não é invariante ao tamanho do grupo. Com mais dados, o critério fica automaticamente mais exigente. A métrica correta para avaliar o modelo é ρ Spearman, que é scale-invariante.

---

## 8. Módulo ml/ — estado atual

```
ml/
├── config.py           ✅ fonte única da verdade (features, targets, pesos, paths)
├── extract_features.py ✅ lê JSONs → features.csv (inclui geo_mean e spill)
├── train.py            ✅ treina M1–M4 + ranker · salva output/models/
├── evaluate.py         ✅ ablação · SHAP · qualidade de ranking
├── tune.py             ✅ Optuna — busca hiperparâmetros XGBoost (80 trials)
├── recommend.py        ✅ top-K configs dado tier + combo + candidatos
└── poc.py              ✅ script de prova de conceito (resultados arquivados)
```

**Artefatos gerados em `output/models/`:**
```
m1_geo_tpch.ubj          ← especialista latência TPC-H
m2_geo_tpcds.ubj         ← especialista latência TPC-DS
m3_cache_tpch.ubj        ← especialista cache hit
m4_spill_tpcds.ubj       ← especialista spill TPC-DS
ranker.ubj               ← XGBRanker (rank:ndcg)
oof_predictions.csv      ← predições OOF dos 4 especialistas
best_params.json         ← hiperparâmetros Optuna por modelo
optimal_score_weights.json
ablation_results.json
shap_importance.json
ranking_quality.json
train_metrics.json
```

**Dependências ML:**
```
numpy · pandas · scikit-learn · xgboost · shap · optuna    ✅ instalados no .venv
```

**Fluxo de execução (em ordem):**
```bash
# 1. Extrair features (somente leitura em output/benchmark_results/)
.venv/bin/python ml/extract_features.py

# 2. [Opcional] Otimizar hiperparâmetros
.venv/bin/python ml/tune.py --trials 80

# 3. Treinar modelos → output/models/
.venv/bin/python ml/train.py

# 4. Avaliar (ablação + SHAP + ranking)
.venv/bin/python ml/evaluate.py

# 5. Recomendar configs
.venv/bin/python ml/recommend.py --tier high --combo s1_s2 --top-k 3
```

---

## 9. SHAP — importância de features (resultado real Rodada 1)

Calculado com `shap.TreeExplainer` sobre o modelo M1 (geo_mean_tpch) treinado
com todos os 335 dados. Salvo em `output/models/shap_importance.json`.

**Top features por importância SHAP:**

| Rank | Feature | Stage | SHAP absoluto | % do total |
|---|---|---|---|---|
| 1 | vcpus | HW | 0.397 | **31.6%** |
| 2 | enable_hashjoin | S2 | 0.122 | 9.7% |
| 3 | shared_buffers | S1 | 0.117 | 9.3% |
| 4 | memory_mb | HW | 0.098 | 7.8% |
| 5 | enable_sort | S1 | 0.066 | 5.2% |
| 6 | parallel_leader_participation | S3 | 0.049 | 3.9% |
| 7 | random_page_cost | S1 | 0.040 | 3.2% |
| 8 | enable_hashagg | S1 | 0.037 | 3.0% |
| 9 | enable_indexscan | S3 | 0.028 | 2.3% |
| 10 | effective_cache_size | S1 | 0.028 | 2.3% |
| ... | work_mem | S1 | 0.008 | 0.6% |
| ... | jit | S1 | 0.007 | 0.6% |
| ... | enable_material | S3 | 0.002 | 0.1% |

**Por stage:** HW=**39.4%** · S1=**32.9%** · S2=**17.6%** · S3=**8.5%**

**Descobertas relevantes para o TCC:**
- Hardware (vcpus) é o fator mais impactante — 31.6% isolado. Valida a decisão de separar por tier.
- `enable_hashjoin` (9.7%) supera `shared_buffers` (9.3%) — inesperado: um toggle booleano impacta mais que o principal parâmetro de memória do PostgreSQL. Isso ocorre porque TPC-H/TPC-DS são workloads com muitos joins e hash join é o algoritmo preferido para grandes volumes.
- `work_mem` (0.6%) e `jit` (0.6%) têm impacto baixo — não foram removidos para manter compatibilidade entre rodadas (ver decisão de engenharia #19).
- S3 contribui com 8.5% — não é ruído, tem sinal real. `parallel_leader_participation` em #6 é a descoberta mais surpreendente.

---

## 10. Histórico de rodadas de coleta

### Rodada 1 — concluída

| Item | Valor |
|---|---|
| Código identificador | `2204202610052026` |
| Data de início | 2026-04-22 |
| Data de conclusão | 2026-05-10 |
| Duração total | ~17.7 dias wall-clock |
| n_configs por combination | 48 (16 por tier) |
| Total de tarefas | 336 (IDs 0–335) |
| Done / Abandoned | 335 / 1 |
| Backup | `/home/araujo/Results/output_backup_2204202610052026/` |
| Resultados em | `output/benchmark_results/` (IDs 0–335) |

### Rodada 2 — concluída

| Item | Valor |
|---|---|
| Data de início | 2026-05-11 |
| Data de conclusão | 2026-05-30 |
| Duração total | ~19 dias wall-clock |
| n_configs por combination | 51 (17 por tier) |
| Total de tarefas | 336 (IDs 0–335) |
| Seed LHS | não definida (não-determinístico) |
| Backup | `/home/araujo/Results/output_backup_1105202630052026/` |

**Fluxo adotado entre rodadas:** cada rodada gera IDs a partir de 0, faz backup ao terminar e reseta o output/ para execução limpa. Na hora de treinar, `extract_features.py` combina múltiplos diretórios via `--results-dirs`.

```bash
.venv/bin/python ml/extract_features.py \
  --results-dirs /home/araujo/Results/output_backup_2204202610052026/benchmark_results \
                 /home/araujo/Results/output_backup_1105202630052026/benchmark_results
```

### Decisão: dataset final = 672 tasks (Rodadas 1+2)

Terceira rodada descartada. Ver decisão de engenharia #25 para justificativa completa.

---

## 11. O que foi feito × o que ainda falta

### Feito (2026-05-10)

| Item | Status | Resultado |
|---|---|---|
| 4 especialistas XGBoost | ✅ | ρ=0.962/0.966/0.930/0.949 |
| XGBRanker (substituiu LightGBM) | ✅ | ρ=0.761 |
| Score composto otimizado | ✅ | pesos 0.65/0.35 confirmados |
| Ablação por stage | ✅ | S1→S1+S2→S1+S2+S3 monótono |
| SHAP por feature e stage | ✅ | HW>S1>S2>S3 |
| Optuna 80 trials | ✅ | melhora marginal; baseline já bom |
| recommend.py validado | ✅ | recomendações fazem sentido técnico |
| Rodada 2 iniciada | ✅ | 357 tasks em execução |

### Falta (pós-Rodada 2)

| Item | Impacto esperado |
|---|---|
| Retreino com 692 tasks | Top-1: 38%→~50%, Ranker: 0.761→~0.85 |
| Atualizar SHAP com novos dados | Confirmar ou revisar hierarquia de features |
| Atualizar seção 7 e 9 deste doc | Números definitivos para o TCC |

**Não fazer (decisões irreversíveis documentadas):**
- Alterar os 33 parâmetros amostrados — invalida combinação de rodadas (ver decisão #19)
- Alterar schema dos JSONs em output/ — arquivo histórico, somente leitura
- Ridge de stacking — testado, ρ=0.383 vs 0.653 do score direto

---

## 12. Referências rápidas

| Conceito | Biblioteca | Nota |
|---|---|---|
| XGBoost Regressor | `xgboost.XGBRegressor` | NaN nativo para params ausentes |
| XGBoost Ranker | `xgboost.XGBRanker` | `objective="rank:ndcg"` |
| SHAP | `shap.TreeExplainer` | para XGBoost |
| Optuna | `optuna` | TPE sampler, 80 trials por modelo |
| Correlação de ranking | `scipy.stats.spearmanr` | métrica principal de avaliação |
| Validação cruzada OOF | `sklearn.model_selection.KFold` | n_splits=5, shuffle=True |
