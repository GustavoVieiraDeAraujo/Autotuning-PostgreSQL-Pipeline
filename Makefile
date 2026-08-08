.PHONY: setup generate run build-images docs docs-build clean-results features train evaluate tune recommend cost-analysis

# ----------------------------------------------------------------------------
# Setup — cria ambiente e instala pacotes (inclui pip install -e .)
# ----------------------------------------------------------------------------

setup:
	test -d .venv || python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements.txt
	.venv/bin/pip install -e .
	@echo ""
	@echo "Ambiente pronto."

# ----------------------------------------------------------------------------
# Imagens Docker (executar antes da primeira rodada)
# ----------------------------------------------------------------------------

build-images:
	.venv/bin/python cli/prepare.py

# ----------------------------------------------------------------------------
# Linha de comando
# ----------------------------------------------------------------------------

generate:
	.venv/bin/python cli/generate.py

run:
	.venv/bin/python cli/run.py

run-retry:
	.venv/bin/python cli/run.py --retry-failed

status:
	.venv/bin/python cli/run.py --status

dry-run:
	.venv/bin/python cli/run.py --dry-run

# ----------------------------------------------------------------------------
# Documentação
# ----------------------------------------------------------------------------

docs:
	.venv/bin/mkdocs serve

docs-build:
	.venv/bin/mkdocs build

# ----------------------------------------------------------------------------
# Utilitários
# ----------------------------------------------------------------------------

clean-results:
	@echo "Removendo resultados e fila..."
	rm -rf data/raw data/queue.json data/.runner.lock logs/
	@echo "Feito."

# ----------------------------------------------------------------------------
# ML — pipeline de machine learning
# Pré-requisito: make features (ou ter data/processed/features.csv)
# ----------------------------------------------------------------------------

features:
	.venv/bin/python ml/extract_features.py --summary

train:
	.venv/bin/python ml/train.py

evaluate:
	.venv/bin/python ml/evaluate.py

tune:
	.venv/bin/python ml/tune.py --trials 80

recommend:
	.venv/bin/python ml/recommend.py --tier high --combo s1 --top-k 5

cost-analysis:
	.venv/bin/python ml/cost_analysis.py
