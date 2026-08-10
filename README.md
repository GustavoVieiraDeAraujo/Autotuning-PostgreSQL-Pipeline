# Autotuning-PostgreSQL — Pipeline

Motor de geração/execução de benchmarks e treinamento de ML deste projeto de
pesquisa (TCC) sobre autotuning de configurações do PostgreSQL para workloads
analíticos (TPC-H / TPC-DS). Este repositório contém:

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
- `output_backup_*_enxuto/` — resultados de benchmark já coletados e limpos,
  usados para reproduzir a extração de features e o treinamento sem precisar
  rodar os benchmarks novamente

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
