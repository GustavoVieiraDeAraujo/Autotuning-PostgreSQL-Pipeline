# Autotuning-PostgreSQL — Pipeline

> **Status: TCC concluído metodologicamente (resultados finais fechados em
> 2026-05-30), projeto arquivado.** O autor está migrando para um novo tema
> de TCC (pré-aquecimento preditivo de infraestrutura de dado serverless em
> pipelines de treino de ML). Este repositório e seus 2 irmãos ficam mantidos
> como referência funcional e ponto de partida de ideias (ver seção
> "Investigação final" abaixo).

## Objetivo do projeto

Meta-modelagem para recomendação de configurações custo-efetivas do
PostgreSQL em workloads analíticos (OLAP). Problema real: achar uma boa
combinação de parâmetros de tuning do Postgres para um hardware/carga
específicos exige rodar benchmarks de verdade — caro e lento, sobretudo em
nuvem. A proposta: treinar um meta-modelo (ML) que aprenda, a partir de
execuções reais passadas, a **prever o desempenho de uma configuração nova
sem precisar rodá-la** — e usar essa previsão para recomendar a melhor
configuração dentre um conjunto de candidatas.

**Método**: Latin Hypercube Sampling sobre 33 parâmetros do PostgreSQL (3
"stages": memória/paralelismo básico, custos de planejamento de join,
toggles avançados do planner), combinados em 7 formas e testados em 3 tiers
de hardware (low/medium/high), cada configuração executada de verdade num
container Docker isolado rodando as suítes completas **TPC-H** (20 queries)
e **TPC-DS** (98 queries). Sobre esses dados reais, treina 4 especialistas
XGBoost (tempo TPC-H, tempo TPC-DS, cache hit, spill em disco) combinados
num score composto ponderado usado pelo `ml/recommend.py` para ranquear
configurações candidatas.

Este repositório é o motor de tudo isso — coleta de benchmark e pipeline de
ML. Contém:

- `sampler/` — amostragem de espaços de configuração (LHS) do PostgreSQL
- `taskqueue/` — fila de tarefas de benchmark
- `runner/` — execução dos benchmarks (Docker + PostgreSQL)
- `benchmarks/` — definições e queries do TPC-H e TPC-DS
- `monitoring/` — coleta de métricas durante a execução
- `utils/` — utilitários compartilhados
- `cli/` — scripts de linha de comando (`generate.py`, `prepare.py`, `run.py`)
- `ml/` — extração de features e pipeline de treinamento/avaliação/tuning
  (XGBoost `XGBRanker`, Optuna, SHAP)
- `specs/` — especificações (JSON) dos espaços de configuração
- `docs/` — documentação (MkDocs) de arquitetura, decisões de engenharia e
  resultados
- `data/processed/features.csv` — dataset de features já extraído, versionado
- `data/raw/rodada{1,2}/` — resultados de benchmark reais já coletados e
  "enxutos" (só os campos que `ml/extract_features.py` de fato lê, ~5,5MB
  no total vs. 2,2GB dos JSONs originais), versionados de propósito para
  reproduzir a extração de features e o treinamento sem precisar rodar
  nenhum benchmark novamente (`make features && make train`)

## Setup

```bash
make setup          # cria .venv, instala requirements.txt e o pacote (pip install -e .)
make build-images   # constrói as imagens Docker com os dados do TPC-H/TPC-DS pré-carregados
make generate       # gera as configurações PostgreSQL e popula a fila de tarefas
make run            # executa o loop de benchmarks
make features && make train   # extrai features e treina o modelo de ML
```

Outros alvos úteis: `make run-retry`, `make status`, `make dry-run`,
`make evaluate`, `make tune`, `make recommend`, `make cost-analysis`,
`make clean-results`, `make docs` / `make docs-build`. Veja o `Makefile` para
a lista completa.

## Resultados finais (dataset completo: 672 tasks, Rodadas 1+2)

Referência completa e detalhada em `docs/resultados_finais.md`. Resumo:

| Modelo | Alvo | Spearman ρ | RMSE |
|---|---|---|---|
| M1 | tempo médio TPC-H (geo mean) | **0,966** | 13.307 ms |
| M2 | tempo médio TPC-DS (geo mean) | **0,977** | 1.775 ms |
| M3 | cache hit ratio TPC-H | **0,924** | 8,52 pp |
| M4 | queries com spill TPC-DS | **0,975** | 5,12 |

- **SHAP**: hardware domina (vcpus 29,8% + memory_mb 7,3% = 37,1%); entre os
  parâmetros de tuning, o mais importante é `enable_hashjoin` (10,7%), à
  frente de `shared_buffers` (8,3%) — resultado não-óbvio, contraria a
  intuição comum de que memória sempre domina.
- **Ablação** (S1 → S1+S2 → S1+S2+S3): melhora monótona de ρ=0,882 →
  0,939 → 0,966, com retorno decrescente — justifica ter coletado os 33
  parâmetros e não só os mais simples.
- **Custo-efetividade** (tier high, hardware mais robusto): escolher a
  configuração errada custa **3,5× mais tempo e 23% mais dinheiro** de nuvem
  frente à melhor configuração real do dataset.
- **Limitações documentadas** (`docs/decisoes-de-engenharia.md`,
  ameaças à validade): amostra pequena por grupo (~32 configs por
  tier×combinação), hardware único virtualizado (Docker, não nuvem real),
  bug de instrumentação zera `avg_workers_launched` em 672/672 tasks, RAPL
  (energia) sempre indisponível por permissão.

## Investigação final (2026-08-09) — sementes pro próximo TCC

Antes de encerrar o projeto, testei se um **foundation model tabular
pré-treinado** (TabICL, in-context learning, sem nenhum tuning de
hiperparâmetro) conseguiria competir com os especialistas XGBoost treinados
especificamente pra este problema — pergunta motivada pela onda de mercado
de 2026 em torno de tabular foundation models (aquisição da Prior Labs pela
SAP, TabPFN/Nature 2025). Ver `ml/tabpfn_eval.py` e
`ml/tabicl_learning_curve.py`.

**Resultado real, mesma metodologia (KFold-5, seed 42) do treino original**:
TabICL **empatou ou superou** o XGBoost especializado em ρ de Spearman em 3
dos 4 alvos — com **zero tuning**, rodando em segundos:

| Alvo | XGBoost ρ | TabICL ρ |
|---|---|---|
| geo_mean_tpch | 0,965 | **0,971** |
| geo_mean_tpcds | **0,978** | 0,975 |
| cache_hit_tpch | 0,924 | **0,935** |
| spill_tpcds | 0,975 | **0,978** |

Numa curva de aprendizado (variando o tamanho do treino de 30 a 537 linhas
via `train_test_split` fixo), o TabICL manteve ρ igual ou melhor que o
XGBoost em **todos** os tamanhos testados, inclusive com só 30 exemplos —
embora o erro absoluto (RMSE) do TabICL seja instável com poucos dados
(≤50 linhas). Como `ml/recommend.py` só usa **ranking relativo** (não valor
absoluto) para recomendar, essa instabilidade importa menos do que parece à
primeira vista. Achado não conclusivo (rodado 1 seed de teste, poucas
repetições) mas promissor o suficiente para registrar como ponto de partida
de investigação futura — não foi aprofundado porque o autor migrou de tema
de TCC.

Também ficou pendente (interrompido por reinício de máquina, não retomado):
uma comparação de execução real entre a **config de fábrica do PostgreSQL
17** e a **config recomendada pelo pgtune** (calculadora heurística de
mercado) contra a recomendação do meta-modelo, nos 3 tiers — ver
`ml/baseline_comparison.py`, `ml/pgtune_baseline.py`,
`scripts/enqueue_baseline_tasks.py`. Achado parcial relevante: o **pgtune só
calcula 7 dos 33 parâmetros** do espaço de busca deste TCC — os outros 26
(toggles do planner, custos de CPU) ficam no default. Um ponto real de
dados foi coletado (config de fábrica, tier low): 339,9ms/53,4% de cache
medidos de verdade, contra 997ms previstos pelo modelo — confirma
extrapolação, já que vários parâmetros de fábrica caem fora do espaço
amostrado no tier low (`jit`, `max_parallel_workers`, `shared_buffers`,
`effective_cache_size`, `work_mem`).

## Escopo deste repositório

Este repositório **não expõe nenhuma interface HTTP própria** — é um pacote
Python instalável (`pip install -e .`) mais um conjunto de scripts de CLI.
Ele foi extraído do monorepo original como parte de uma separação em três
repositórios:

- **pipeline** (este repositório) — geração de benchmarks, execução e
  treinamento de ML, em Python
- **backend** — repositório irmão (`../Autotuning-PostgreSQL-Backend`, Java +
  Spring Boot) que orquestra este pacote (subprocessos + Postgres) e expõe
  a API REST/SSE
- **frontend** — interface web em React + TypeScript, consumidora do backend

Para uso via API/web, veja o repositório `Autotuning-PostgreSQL-Backend`.
