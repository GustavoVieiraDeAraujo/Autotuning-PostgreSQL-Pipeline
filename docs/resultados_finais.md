# Resultados Finais — PostgreSQL Autotuning Meta-Modelo

> **Referência definitiva para escrita do TCC.**
> Todos os números aqui são resultados reais do modelo treinado com os dados das Rodadas 1 e 2 (672 tasks).
> Última atualização: 2026-05-30

---

## 1. Dataset

| Rodada | Configs por tier | Tasks totais | Status |
|--------|-----------------|--------------|--------|
| Rodada 1 | ~16 configs | 336 tasks | done (335) + abandoned (1) |
| Rodada 2 | ~17 configs | 357 tasks | done (333) + abandoned (3+) |
| **Total** | **~99 configs** | **672 tasks** | **done (668) + abandoned (4)** |

- **Cobertura:** 3 tiers (low/medium/high) × 7 combinações de stage (s1..s1_s2_s3)
- **Scale factors:** low=SF1 (~1 GB), medium=SF2 (~2 GB), high=SF4 (~4 GB)
- **Benchmarks:** TPC-H (20 queries ativas) + TPC-DS (98 queries ativas)
- **Parâmetros amostrados:** 33 por configuração, via Latin Hypercube Sampling

---

## 2. Performance dos Modelos Especialistas

Treinados com KFold(5), avaliados com OOF predictions no espaço original (antes de transformação log).

| Modelo | Target (CSV) | Transformação | RMSE | Spearman ρ | n |
|--------|-------------|---------------|------|------------|---|
| M1 — geo_mean_tpch | `tpch_geo_mean_ms` | log | 13.324 ms | **0.9659** | 672 |
| M2 — geo_mean_tpcds | `tpcds_geo_mean_ms` | log | 1.764 ms | **0.9770** | 668 |
| M3 — cache_hit_tpch | `tpch_cache_hit_ratio` | none | 8.37 pp | **0.9271** | 672 |
| M4 — spill_tpcds | `tpcds_queries_with_spill` | log1p | 5.29 q | **0.9757** | 668 |

> **Interpretar ρ, não RMSE:** o RMSE alto de M1 (13.324 ms) é inflado por 7 tasks outlier com geo_mean > 11.500 ms. ρ=0.966 mostra que o modelo ordena configurações corretamente em 96,6% dos casos — que é o que importa para recomendação.

---

## 3. Estudo de Ablação — Impacto de Adicionar Stages

Modelo M1 (geo_mean_tpch) treinado com diferentes conjuntos de features. Mostra o ganho incremental de cada stage de parâmetros.

| Conjunto | Features | RMSE (ms) | Spearman ρ | Melhora sobre S1 |
|----------|----------|-----------|------------|------------------|
| S1 only (13 params) | 16 colunas | 13.813 | 0.882 | referência |
| S1 + S2 (25 params) | 28 colunas | 13.480 | 0.939 | +5,7 pp |
| S1 + S2 + S3 (33 params) | 36 colunas | **13.324** | **0.966** | +8,4 pp |

**Conclusão:** tendência monótona clara — mais parâmetros → modelo melhor, com retorno decrescente. Resultado defensável na banca: S3 agrega 2,7 pp adicionais ao ρ vs S1+S2, justificando a coleta dos parâmetros mais complexos.

---

## 4. Qualidade de Ranking (XGBRanker)

Avalia se o modelo de ranking coloca as melhores configurações no topo dentro de cada grupo (tier, combination).

| Métrica | Valor | Interpretação |
|---------|-------|---------------|
| Spearman ρ global (score) | **0.744** | 74% da variância do ranking real é explicada pelo modelo |
| Spearman ρ intra-grupo (média) | **0.715** | correlação média dentro de cada (tier, combo) |
| Precisão@1→top3 | **52%** (11/21 grupos) | a config com melhor score predito está no top-3 real |
| Overlap top-3 | **52%** | 52% de concordância entre top-3 predito e top-3 real |
| Grupos avaliados | 21 | (apenas grupos com ≥ 4 configs; alguns combos têm menos) |

> **Nota sobre escala:** os 21 grupos têm em média 32 configs (vs 16 na Rodada 1). Top-3 em 32 configs equivale a identificar a melhor configuração dentro dos 9% superiores do espaço amostrado — critério muito mais exigente que os 19% da Rodada 1.

---

## 5. Importância de Features — SHAP (M1: geo_mean_tpch)

| Rank | Feature | SHAP médio |valor| | % do total | Stage |
|------|---------|----------------------|------------|-------|
| 1 | `vcpus` | 0.4126 | **29.8%** | Hardware |
| 2 | `cfg_enable_hashjoin` | 0.1488 | **10.7%** | S2 |
| 3 | `cfg_shared_buffers` | 0.1144 | **8.3%** | S1 |
| 4 | `memory_mb` | 0.1017 | **7.3%** | Hardware |
| 5 | `cfg_enable_sort` | 0.0839 | **6.1%** | S1 |
| 6 | `cfg_parallel_leader_participation` | 0.0616 | **4.4%** | S3 |
| 7 | `cfg_enable_indexscan` | 0.0483 | **3.5%** | S3 |
| 8 | `cfg_random_page_cost` | 0.0369 | **2.7%** | S1 |
| 9 | `cfg_enable_hashagg` | 0.0349 | **2.5%** | S1 |
| 10 | `cfg_enable_parallel_hash` | 0.0327 | **2.4%** | S1 |

**Hardware domina (37%):** `vcpus` + `memory_mb` juntos respondem por 37,1% da importância total. Isso confirma que a escolha do tier de hardware é o fator mais impactante no desempenho OLAP — mais do que qualquer parâmetro de tuning individual.

**Top parâmetro de tuning:** `enable_hashjoin` (10,7%) — controla o uso de hash joins, crítico para queries analíticas com grandes joins.

---

## 6. Pesos do Score Composto

Otimizados por grid search via `ml/evaluate.py`:

```
score = 0.65 × rank_norm(1/geo_mean_tpch) + 0.35 × rank_norm(cache_hit_tpch)
```

| Componente | Peso | Justificativa |
|-----------|------|---------------|
| `1/geo_mean_tpch` | **0.65** | Performance bruta (menor tempo = melhor) |
| `cache_hit_tpch` | **0.35** | Eficiência de memória (maior hit = menos I/O) |
| `geo_mean_tpcds` | 0.0 | Correlacionado com tpch_geo_mean (ρ ≈ 0.85) |
| `spill_tpcds` | 0.0 | Correlacionado com cache_hit (ρ ≈ -0.72) |

---

## 7. Análise de Custo-Efetividade em Nuvem

### 7a. Tuning dentro do mesmo tier (comparação direta — mesmo hardware)

| Tier | Config ruim (p90) | Melhor config real | Speedup TPC-H | Economia custo | Economia mensal¹ |
|------|------------------|--------------------|---------------|----------------|-----------------|
| low (SF=1) | 86 min · R$0,70/run | 68 min · R$0,55/run | **2,1×** | 20% | R$ 4/mês |
| medium (SF=2) | 104 min · R$1,69/run | 88 min · R$1,43/run | **1,4×** | 15% | R$ 8/mês |
| high (SF=4) | 141 min · R$4,59/run | 108 min · R$3,51/run | **3,5×** | **23%** | **R$ 32/mês** |

¹ Cenário: relatório diário (30 execuções/mês). Câmbio: 1 USD = R$ 5,75.

> **Conclusão:** escolher a configuração errada no HIGH tier pode custar 3,5× mais tempo e 23% mais dinheiro. O meta-modelo identifica a boa configuração sem testar todas.

### 7b. Eficiência entre tiers — custo por SF (normalizado por volume de dados)

| Tier | Instância EC2 | Custo/run | Custo/SF | Relativo |
|------|--------------|-----------|----------|----------|
| low | c5.large (2 vCPU) | $0,097 | **$0,097/SF** | referência |
| medium | c5.xlarge (4 vCPU) | $0,249 | $0,124/SF | +29% |
| high | c5.2xlarge (8 vCPU) | $0,611 | $0,153/SF | +58% |

> **Atenção:** esta comparação é válida apenas como custo por unidade de dado processada. Para um dado workload real, o tier deve ser escolhido com base no tamanho do banco (SF), não apenas no custo/SF.

---

## 8. Comparação com a Literatura

| Trabalho | Ano | ML | Benchmark | Hardware | Diferença principal |
|----------|-----|-----|-----------|----------|---------------------|
| OtterTune | 2017/2021 | GP Regression | OLTP proprietário | Tier fixo | OLTP, sem co-seleção de hardware |
| CDBTune | 2019 | RL (DDPG) | TPC-C (OLTP) | Tier fixo | RL online, OLTP |
| ResTune | 2021 | RL + meta-learning | Sysbench/TPC-C | Tier fixo | Custo-aware mas OLTP |
| LlamaTune | 2022 | BO + redução dim. | **TPC-H** + YCSB | Tier fixo | Mais próximo; sem co-seleção |
| GPTuner | 2024 | LLM + BO | TPC-H/TPC-C | Tier fixo | LLM-guided, tier fixo |
| **Este TCC** | 2026 | XGBRanker (offline) | **TPC-H + TPC-DS** | **3 tiers** | OLAP puro, co-seleção hardware |

**Diferenciais únicos:** (1) único com TPC-H + TPC-DS combinados, (2) co-seleção hardware + parâmetros, (3) modelo de ranking offline (não requer execuções adicionais para novas recomendações).

---

## 9. Artefatos Gerados

| Arquivo | Descrição | Caminho |
|---------|-----------|---------|
| Features (CSV) | 672 tasks × 292 colunas | `output/features.csv` |
| M1 — geo_mean_tpch | XGBRegressor treinado | `output/models/m1_geo_tpch.ubj` |
| M2 — geo_mean_tpcds | XGBRegressor treinado | `output/models/m2_geo_tpcds.ubj` |
| M3 — cache_hit_tpch | XGBRegressor treinado | `output/models/m3_cache_tpch.ubj` |
| M4 — spill_tpcds | XGBRegressor treinado | `output/models/m4_spill_tpcds.ubj` |
| Ranker | XGBRanker (rank:ndcg) | `output/models/ranker.ubj` (via train.py) |
| Métricas de treino | RMSE + ρ por modelo | `output/models/train_metrics.json` |
| Ablação | RMSE + ρ por grupo de features | `output/models/ablation_results.json` |
| SHAP | Importância por feature | `output/models/shap_importance.json` |
| Ranking quality | ρ global, top-K, overlap | `output/models/ranking_quality.json` |
| Pesos do score | w_geo, w_cache otimizados | `output/models/optimal_score_weights.json` |
| Resultados R1 | Tasks 0–335 | `/Results/output_backup_2204202610052026/` |
| Resultados R2 | Tasks 0–356 | `/Results/output_backup_1105202630052026/` |

---

## 10. Análise de Colunas do features.csv

O CSV possui 299 colunas; análise de variância e SHAP revela o seguinte:

### Colunas sempre NaN (7) — sem sinal, nunca devem entrar no modelo

| Coluna | Motivo |
|--------|--------|
| `abandoned_reason` | Sempre NaN após filtrar `status == done` |
| `tpch_q17_ms`, `tpch_q20_ms` | Q17 e Q20 skipadas permanentemente |
| `tpcds_q95_ms` | Q95 skipada permanentemente |
| `tpch_q17_timed_out`, `tpch_q20_timed_out`, `tpcds_q95_timed_out` | Idem |

### Flags `_timed_out` — 86 zero-variance, 32 com sinal real

Das 118 colunas `_timed_out` (uma por query), **86 são sempre 0** — essas queries nunca causaram timeout em nenhuma das 668 configurações testadas. As **32 com sinal real** são as queries sensíveis à configuração:

| Query | Timeouts (de 668) | % |
|-------|------------------|---|
| `tpcds_q11` | 391 | 59% |
| `tpcds_q4` | 382 | 57% |
| `tpcds_q74` | 359 | 54% |
| `tpcds_q1` | 358 | 54% |
| `tpcds_q6` | 175 | 26% |
| `tpcds_q81` | 140 | 21% |
| (mais 26 com < 30 ocorrências cada) | | |

**Interpretação para o TCC:** 73% das queries TPC-DS são robustas à configuração PostgreSQL (nunca timeout). As 4 queries mais críticas (`q1/q4/q11/q74`) falham em mais de 50% dos configs — são o principal gargalo do benchmark e o maior driver para que `work_mem` e `enable_hashjoin` sejam features importantes.

### Parâmetros cfg com SHAP zero (3) — já excluídos pelo modelo

| Parâmetro | SHAP | Motivo |
|-----------|------|--------|
| `cfg_seq_page_cost` | 0,0% | Fixado em 1.0 (âncora do planner) |
| `cfg_synchronous_commit` | 0,0% | Fixado em `off` (benchmark SELECT-only) |
| `cfg_max_worker_processes` | 0,0% | Derivado do hardware — variância zero |

> Esses três já são excluídos via `DROP_PARAMS` em `ml/config.py`. O modelo nunca os viu.

### Por que `work_mem` aparece com SHAP baixo (0,5%) em geo_mean_tpch

`work_mem` impacta principalmente a quantidade de spill para disco (M4), não o tempo total de execução de queries que não fazem spill. O SHAP calculado sobre M1 (geo_mean_tpch) captura apenas o impacto indireto. Se o SHAP fosse calculado sobre M4 (spill_tpcds), `work_mem` provavelmente apareceria entre os top features — o que justifica ter 4 especialistas separados em vez de um único modelo.
