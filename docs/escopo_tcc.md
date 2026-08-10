# Escopo e Contexto do TCC

## Título

**Meta-modelagem para Recomendação de Configurações Custo-Efetivas de PostgreSQL em Workloads Analíticos**

---

## Objetivo

Desenvolver um meta-modelo capaz de recomendar configurações custo-efetivas de hardware e parâmetros do PostgreSQL para workloads analíticos, reduzindo o custo computacional sem degradar o desempenho em ambientes de nuvem.

## Hipótese

É possível utilizar meta-modelagem para predizer o desempenho de workloads OLAP em PostgreSQL e identificar combinações de hardware e parâmetros que reduzam o custo computacional em ambientes de nuvem.

---

## Contexto: OLAP vs OLTP

### OLAP (Online Analytical Processing)
- Consultas complexas sobre grandes volumes de dados históricos
- Operações de agregação, joins massivos, scans completos de tabela
- Exemplos: relatórios, dashboards, BI, data warehouses
- Benchmarks padrão: **TPC-H**, **TPC-DS**
- Tuning crítico: `shared_buffers`, `work_mem`, `enable_hashjoin`, paralelismo

### OLTP (Online Transaction Processing)
- Transações curtas e frequentes (INSERT/UPDATE/DELETE pontuais)
- Alta concorrência, latência baixa por transação
- Exemplos: sistemas de e-commerce, bancários, ERP
- Benchmarks padrão: TPC-C, pgbench
- **Fora do escopo deste TCC**

**Decisão:** O foco é exclusivamente OLAP porque o espaço de parâmetros e os trade-offs são fundamentalmente diferentes entre os dois regimes. Misturar os dois domínios produziria um modelo confuso sem especialização real.

---

## Benchmarks Utilizados

### TPC-H
- 8 tabelas, 22 queries, workload de decision support
- Usado neste projeto: **20 queries ativas** (Q15 excluída por criar view)
- Métrica principal: tempo de execução geométrico (geo_mean_tpch)
- Sensível a: paralelismo, hash joins, shared_buffers

### TPC-DS
- Schema mais complexo, 99 queries, simula varejo/e-commerce analítico
- Usado neste projeto: **98 queries ativas** (Q1 excluída por instabilidade)
- Métrica principal: tempo de execução geométrico (geo_mean_tpcds) + spill para disco
- Sensível a: work_mem (spill), paralelismo, particionamento

**Por que ambos?** TPC-H e TPC-DS cobrem padrões de query diferentes. Um modelo treinado só em TPC-H pode não generalizar para workloads com mais joins complexos e subqueries (TPC-DS). Usar os dois aumenta a cobertura e a robustez das recomendações.

---

## Problema: Espaço de Configuração

PostgreSQL expõe centenas de parâmetros de tuning. A busca exaustiva é inviável:
- 33 parâmetros selecionados neste projeto (com impacto real em OLAP)
- 7 combinações de hardware (tiers × stages: s1..s4 × tier1..tier3)
- Espaço contínuo + categórico combinado

**Abordagem adotada:** Latin Hypercube Sampling (LHS) para exploração eficiente do espaço, seguido de meta-modelagem para predição e ranking de configurações sem executar benchmarks reais.

---

## Meta-Modelagem

### O que é
Um modelo que aprende a mapear `(configuração de parâmetros, hardware)` → `(desempenho estimado)` a partir de experimentos já executados, permitindo avaliar novas configurações sem rodar benchmarks.

### Vantagem
Executar um benchmark completo (TPC-H + TPC-DS) leva ~30 minutos por configuração. O meta-modelo faz a predição em milissegundos, viabilizando busca em larga escala.

### Arquitetura adotada
- **XGBRanker** (rank:ndcg): modelo de ranking que ordena configurações dentro de grupos de mesmo hardware/stage
- 4 modelos especialistas: `geo_mean_tpch`, `geo_mean_tpcds`, `cache_hit_tpch`, `spill_tpcds`
- Score final: `0.65 × rank_norm(1/geo_tpch) + 0.35 × rank_norm(cache_tpch)`
- Validação: KFold(5) com OOF predictions

### Métricas de avaliação
| Métrica | O que mede | Por que usar |
|---------|------------|--------------|
| ρ Spearman | Correlação de ranking | Invariante à escala dos outliers |
| RMSE | Erro de predição absoluto | Sensível a outliers: usar com cautela |
| Top-K Accuracy | % vezes que a melhor config real está no top-K predito | Intuitivo, mas escala com tamanho do grupo |

**Nota sobre Top-K:** com mais dados (mais grupos maiores), a métrica fica mais difícil estruturalmente. Queda de Top-3 de 62%→52% entre rodadas é explicada pelo aumento de grupos de 24→48 configs, não por piora real do modelo. Usar ρ como métrica primária.

---

## Otimização Multi-Objetivo

### Objetivo
Minimizar simultaneamente:
1. **Custo computacional**: proxy: tipo de instância EC2 (vcpus × memória × preço/hora)
2. **Tempo de execução**: geo_mean das queries TPC-H e TPC-DS

### Fronteira de Pareto
Conjunto de configurações onde não é possível melhorar um objetivo sem piorar o outro. A escolha entre pontos na fronteira é uma decisão do usuário/operador baseada em budget.

**Implementação atual:** ranking por score combinado dentro de cada tier (hardware fixo). A extensão multi-objetivo com Pareto explícito é trabalho futuro.

---

## Baselines e Trabalhos Relacionados

### Baselines para comparação

| Método | Descrição | Limitação |
|--------|-----------|-----------|
| **Random Search** | Configurações aleatórias dentro do espaço | Referência mínima; sem aprendizado |
| **Default PostgreSQL** | Configuração padrão out-of-the-box | Muito conservador, não usa recursos disponíveis |
| **Bayesian Optimization** | Optuna (TPE), SMAC, Hyperopt | Requer execução real a cada iteração; não transfere entre hardware |

### Trabalho relacionado principal

**OtterTune** (CMU, 2017 + 2021):
- Sistema de tuning automático via ML para bancos relacionais
- Versão 2021: usa Gaussian Processes + workload mapping
- Diferenças deste TCC: foco em OLAP puro, espaço de hardware explícito (tiers), uso de benchmarks padrão (TPC-H/DS) em vez de workloads proprietários

---

## Dataset

| Rodada | Configs | Tasks (configs × 7 stages) | Seed LHS |
|--------|---------|----------------------------|----------|
| Rodada 1 | ~48 | 336 | aleatório |
| Rodada 2 | ~51 | 357 (17 configs/tier × 3 tiers) | diferente |
| **Total** | ~99 | **672** |: |

Cada task = 1 configuração rodando TPC-H (20q) + TPC-DS (98q) em 1 stage/tier específico.

---

## Escopo Definitivo

### Dentro do escopo
- Workloads OLAP em PostgreSQL
- Benchmarks TPC-H e TPC-DS
- 3 tiers de hardware (EC2 equivalente: small/medium/large)
- 4 stages de configuração por tier (s1..s4)
- 33 parâmetros PostgreSQL com impacto em OLAP
- Meta-modelo de ranking (XGBRanker)
- Otimização de custo implícita via seleção de tier

### Fora do escopo
- OLTP (TPC-C, pgbench, HTAP)
- Tuning online/adaptativo em produção
- Outros SGBDs (MySQL, MariaDB, etc.)
- Ambientes bare-metal (foco é nuvem)
- Pareto explícito multi-objetivo (trabalho futuro)

---

## Trabalho Futuro

1. **Suporte a OLTP**: TPC-C, pgbench; requer novo espaço de parâmetros
2. **HTAP**: workloads híbridos analítico-transacionais
3. **Otimização Pareto explícita**: fronteira custo × desempenho com seleção interativa
4. **Transfer learning entre hardware**: generalizar modelo para instâncias não vistas
5. **Tuning contínuo**: atualização do modelo com novos dados em produção
6. **Mais benchmarks**: Star Schema Benchmark (SSB), JOB (Join Order Benchmark)

---

## Resumo para Escrita do TCC

**Problema:** Configurar PostgreSQL para OLAP em nuvem é complexo, o espaço é enorme, e busca exaustiva é inviável.

**Solução:** Coletar dados via LHS, treinar meta-modelo de ranking, usar o modelo para recomendar configurações custo-efetivas sem rodar benchmarks novos.

**Resultado:** Meta-modelo com ρ Spearman ~0.97 (R1) e ~0.97 (R1+R2), capaz de ordenar configurações corretamente em ~97% dos casos. Top feature: número de vCPUs (31.6% SHAP), confirmando que hardware domina desempenho OLAP mais do que parâmetros de tuning isolados.

**Contribuição:** Pipeline completo de coleta → extração → treinamento → recomendação para OLAP PostgreSQL, com benchmarks padrão da indústria e análise de importância de features via SHAP.
