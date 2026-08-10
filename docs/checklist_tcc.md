# Checklist do TCC: O que já temos vs O que falta

> Mapeamento de cada seção da dissertação para os artefatos e resultados disponíveis.
> Use este documento para saber o que já pode ser escrito com base no que foi produzido.

---

## Estrutura sugerida do TCC

```
1. Introdução
2. Fundamentação Teórica
3. Trabalhos Relacionados
4. Metodologia
5. Resultados e Discussão
6. Conclusão e Trabalho Futuro
```

---

## 1. Introdução

**O que escrever:** problema (espaço enorme de configuração PostgreSQL), motivação (OLAP em nuvem é caro), objetivo e hipótese.

| Item | Status | Referência |
|------|--------|------------|
| Definição do problema | ✅ Pronto | `docs/escopo_tcc.md` § Objetivo |
| Hipótese do trabalho | ✅ Pronto | `docs/escopo_tcc.md` § Hipótese |
| Motivação (custo em nuvem) | ✅ Pronto | `docs/resultados_finais.md` § 7 |
| Contribuições do trabalho | ✅ Pronto | `docs/resultados_finais.md` § 8 (diferenciais únicos) |

**Números chave para citar na introdução:**
- PostgreSQL expõe centenas de parâmetros; este trabalho avalia 33 com impacto real em OLAP
- Espaço de busca: 33 parâmetros × 7 combinações de stages × 3 tiers de hardware
- Benefício demonstrado: 23% de redução de custo e 3,5× de speedup no HIGH tier

---

## 2. Fundamentação Teórica

**O que escrever:** OLAP vs OLTP, benchmarks TPC-H e TPC-DS, meta-modelagem, Latin Hypercube Sampling, XGBoost.

| Item | Status | Referência |
|------|--------|------------|
| OLAP vs OLTP | ✅ Pronto | `docs/escopo_tcc.md` § Contexto |
| TPC-H e TPC-DS | ✅ Pronto | `docs/escopo_tcc.md` § Benchmarks |
| Espaço de configuração PostgreSQL | ✅ Pronto | `docs/escopo_tcc.md` § Problema |
| Latin Hypercube Sampling | ✅ Pronto | `docs/geracao/lhs.md` |
| Meta-modelagem | ✅ Pronto | `docs/escopo_tcc.md` § Meta-Modelagem |
| XGBoost e XGBRanker | ⚠️ Básico | `docs/ml_pipeline_reference.md` § 3–4 |
| Custo em nuvem (EC2) | ✅ Pronto | `docs/resultados_finais.md` § 7b |

---

## 3. Trabalhos Relacionados

**O que escrever:** OtterTune, CDBTune, ResTune, LlamaTune, GPTuner, e onde este trabalho se diferencia.

| Item | Status | Referência |
|------|--------|------------|
| OtterTune 2017 + 2021 | ✅ Pronto | `docs/escopo_tcc.md` § Trabalhos Relacionados |
| CDBTune / ResTune | ✅ Pronto | `docs/resultados_finais.md` § 8 |
| LlamaTune (mais próximo) | ✅ Pronto | `docs/resultados_finais.md` § 8 |
| GPTuner 2024 | ✅ Pronto | `docs/resultados_finais.md` § 8 |
| Tabela comparativa | ✅ Pronto | `docs/resultados_finais.md` § 8 |
| Posicionamento deste trabalho | ✅ Pronto | `docs/escopo_tcc.md` § Diferenciais |

---

## 4. Metodologia

**O que escrever:** pipeline completo, decisões de design, parâmetros amostrados, critério de score.

| Item | Status | Referência |
|------|--------|------------|
| Visão geral do pipeline | ✅ Pronto | `docs/ml_pipeline_reference.md` § 1 |
| Definição dos stages e parâmetros | ✅ Pronto | `docs/geracao/estagios.md` + `ml/config.py` |
| Critério de exclusão de parâmetros | ✅ Pronto | `docs/decisoes-de-engenharia.md` §§ 4, 5, 14, 15 |
| Estratégia de coleta (LHS, 2 rodadas) | ✅ Pronto | `docs/decisoes-de-engenharia.md` §§ 19, 20, 23 |
| Tratamento de tasks abandonadas | ✅ Pronto | `docs/decisoes-de-engenharia.md` § 3 |
| Arquitetura do meta-modelo (4 especialistas + ranker) | ✅ Pronto | `docs/ml_pipeline_reference.md` §§ 3–5 |
| Fórmula do score composto | ✅ Pronto | `docs/resultados_finais.md` § 6 |
| Validação cruzada (KFold-5) | ✅ Pronto | `docs/ml_pipeline_reference.md` § 3 |
| Justificativa de usar XGBRanker vs LightGBM | ✅ Pronto | `docs/decisoes-de-engenharia.md` § 18 |
| Mapeamento de tiers para EC2 | ✅ Pronto | `docs/resultados_finais.md` § 7b |

---

## 5. Resultados e Discussão

**O que escrever:** ablação, métricas do modelo, SHAP, qualidade de ranking, custo.

### 5.1 Estudo de Ablação
| Item | Status | Números |
|------|--------|---------|
| Tabela S1 / S1+S2 / S1+S2+S3 | ✅ Pronto | `docs/resultados_finais.md` § 3 |
| Interpretação da tendência monótona | ✅ Pronto | `docs/resultados_finais.md` § 3 |

### 5.2 Performance dos Especialistas
| Item | Status | Números |
|------|--------|---------|
| Tabela RMSE + ρ por modelo | ✅ Pronto | `docs/resultados_finais.md` § 2 |
| Explicação do RMSE inflado (outliers) | ✅ Pronto | `docs/decisoes-de-engenharia.md` § 24 |
| Por que reportar ρ como métrica primária | ✅ Pronto | `docs/decisoes-de-engenharia.md` § 26 |

### 5.3 Importância de Features (SHAP)
| Item | Status | Números |
|------|--------|---------|
| Ranking de features | ✅ Pronto | `docs/resultados_finais.md` § 5 |
| Hardware domina (vcpus 29,8% + memory_mb 7,3%) | ✅ Pronto | `docs/resultados_finais.md` § 5 |
| Top parâmetro de tuning: enable_hashjoin (10,7%) | ✅ Pronto | `docs/resultados_finais.md` § 5 |

### 5.4 Qualidade de Ranking
| Item | Status | Números |
|------|--------|---------|
| ρ global (0.744) e intra-grupo (0.715) | ✅ Pronto | `docs/resultados_finais.md` § 4 |
| Precisão@1→top3 (52%) e overlap top-3 (52%) | ✅ Pronto | `docs/resultados_finais.md` § 4 |
| Explicação do problema de escala (Top-K) | ✅ Pronto | `docs/decisoes-de-engenharia.md` § 26 |

### 5.5 Análise de Custo
| Item | Status | Números |
|------|--------|---------|
| Tuning within-tier: speedup e % economia | ✅ Pronto | `docs/resultados_finais.md` § 7a |
| Custo/SF entre tiers | ✅ Pronto | `docs/resultados_finais.md` § 7b |
| Narrativa para defesa (frase pronta) | ✅ Pronto | `docs/decisoes-de-engenharia.md` § 27 |

---

## 6. Conclusão e Trabalho Futuro

| Item | Status | Referência |
|------|--------|------------|
| Contribuições listadas | ✅ Pronto | `docs/resultados_finais.md` § 8 (diferenciais) |
| Limitações (OLTP fora do escopo, SF fixo por tier) | ✅ Pronto | `docs/escopo_tcc.md` § Fora do escopo |
| Trabalho futuro | ✅ Pronto | `docs/escopo_tcc.md` § Trabalho Futuro |

---

## Resumo do que ainda falta

| Item faltante | Prioridade | Observação |
|---------------|-----------|------------|
| Fundamentação teórica sobre XGBoost/XGBRanker | Média | Escrever 1–2 páginas explicando o algoritmo; não temos doc específico |
| Figuras e gráficos | Alta | Os números existem; falta plotar gráficos para o texto |
| Exemplo concreto de recomendação | Média | Mostrar output do `ml/recommend.py` para um caso real |
| Discussão de ameaças à validade | Média | SF diferente por tier; outliers mantidos; 99 configs apenas |

---

## Figuras sugeridas a criar

| Figura | Como gerar | Dados disponíveis |
|--------|-----------|-------------------|
| Pipeline do sistema (diagrama) | Desenhar no draw.io/Mermaid | `docs/ml_pipeline_reference.md` § 1 |
| Ablação: ρ vs número de features | `matplotlib` sobre `ablation_results.json` | ✅ |
| SHAP bar chart top-10 | `shap.plots.bar()` ou `matplotlib` | ✅ `shap_importance.json` |
| Custo × speedup por tier | `matplotlib` scatter | ✅ `output/features.csv` |
| Distribuição de geo_mean por tier | `matplotlib` boxplot | ✅ `output/features.csv` |
