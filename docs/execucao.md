# Executando o Projeto

Este guia detalha como executar o projeto do zero: desde a geração das configurações até a coleta completa de resultados de benchmark.

## Pré-requisitos

- **Python 3.10+** com os pacotes listados em `requirements.txt`
- **Docker Engine** em execução (`docker ps` deve funcionar sem `sudo`)
- **Espaço em disco**: pelo menos 50 GB livres (imagens TPC-DS SF=4 são grandes)
- **RAM do host**: pelo menos 8 GB (para rodar o tier high com 6 GB alocados)

```bash
# Instalar dependências Python
pip install -r requirements.txt

# Verificar Docker
docker ps
```

## Passo 1 — Interface Web (opcional)

A interface web permite controlar todo o pipeline visualmente, sem usar a CLI diretamente.

```bash
# Iniciar o servidor web (porta 8000)
uvicorn web.app:app --host 0.0.0.0 --port 8000
```

Acesse `http://localhost:8000` no navegador. A interface permite:
- Executar o gerador, prepare e runner com um clique
- Ver os logs em tempo real via SSE
- Inspecionar o estado da fila e resultados
- Verificar o status das imagens Docker

## Passo 2 — Gerar Configurações (`cli/generate.py`)

O gerador cria a fila de tarefas que será executada pelo runner.

```bash
# Gera 7 combinações × 3 tiers × 30 configs = 630 tarefas
python -m cli.generate

# Opções avançadas:
python -m cli.generate --n-configs 10   # 10 configs por combinação (mínimo para testes)
python -m cli.generate --seed 42        # semente aleatória para reprodutibilidade
```

**O que acontece internamente:**

1. Para cada uma das 7 combinações de stages (`[1]`, `[2]`, `[3]`, `[1,2]`, `[1,3]`, `[2,3]`, `[1,2,3]`):
   - Chama `pg_sampler.generate_all_tiers(stages, n_per_tier=10, seed)` que retorna 10 configs para cada um dos 3 tiers
   - Adiciona as configs como tarefas na `ExecutionQueue`
2. Salva a fila em `output/queue.json`
3. Exibe um resumo na tela

**Saída esperada:**

```
output/queue.json   ← 630 tarefas em estado "pending"
output/generate.log ← log completo da geração
```

**Estrutura de uma tarefa na fila:**

```json
{
  "id": 0,
  "combination": "s1",
  "tier": "low",
  "config": {
    "shared_buffers": "512MB",
    "work_mem": "32MB",
    "max_parallel_workers": 2,
    ...
  },
  "status": "pending",
  "result": null,
  "error": null
}
```

## Passo 3 — Preparar Imagens Docker (`cli/prepare.py`)

O prepare constrói as 6 imagens Docker com os dados TPC-H e TPC-DS pré-carregados.

```bash
python -m cli.prepare
```

**Imagens construídas:**

| Imagem | Benchmark | Scale Factor | Tier |
|--------|-----------|--------------|------|
| `tpch-sf1` | TPC-H | SF=1 | low |
| `tpch-sf2` | TPC-H | SF=2 | medium |
| `tpch-sf4` | TPC-H | SF=4 | high |
| `tpcds-sf1` | TPC-DS | SF=1 | low |
| `tpcds-sf2` | TPC-DS | SF=2 | medium |
| `tpcds-sf4` | TPC-DS | SF=4 | high |

**O que acontece por imagem:**

1. `image_builder.build_image(benchmark, scale_factor, image_tag)`:
   - Verifica se a imagem já existe (evita rebuild desnecessário)
   - Inicia um container temporário `{db_name}-build-tmp-sf{N}`
   - Aguarda o script de inicialização TPC completar (pode levar 10–60 minutos por imagem)
   - Para e comita o container como imagem Docker

2. Após construir todas as 6 imagens, executa **smoke tests** em todos:
   - Inicia container `{db_name}_smoketest_{tier}`
   - Executa `SELECT 1` para verificar conectividade
   - Conta as tabelas do banco para verificar que os dados estão presentes
   - Para e remove o container

!!! warning "Tempo de execução"
    A primeira execução do prepare pode levar várias horas. As imagens TPC-DS com SF=4 incluem tabelas de dezenas de gigabytes. Imagens já construídas são detectadas automaticamente e puladas.

## Passo 4 — Executar Benchmarks (`cli/run.py`)

O runner executa todas as tarefas da fila, uma por uma.

```bash
# Execução normal
python -m cli.run

# Retentar tarefas que falharam anteriormente
python -m cli.run --retry-failed

# Dry-run: simula execução sem realmente rodar os benchmarks
python -m cli.run --dry-run
```

### Verificações de Preflight

Antes de executar qualquer tarefa, o runner executa 8 verificações:

| # | Verificação | Ação se falhar |
|---|-------------|----------------|
| 1 | Docker daemon em execução | Abort |
| 2 | Espaço em disco (≥ threshold) | Auto-prune se possível, senão abort |
| 3 | Sem containers obsoletos de execuções anteriores | Aviso |
| 4 | Integridade da fila (queue.json válido) | Abort |
| 5 | Tabelas TPC-H presentes em cada tier | Aviso |
| 6 | Tabelas TPC-DS presentes em cada tier | Aviso |
| 7 | Conectividade TPC-H (`SELECT 1`) | Aviso |
| 8 | Conectividade TPC-DS (`SELECT 1`) | Aviso |

### Loop de execução

Para cada tarefa:

1. `queue.next()` → obtém próxima tarefa pending, marca como `running`
2. `runner.run_task(task, tier_configs, callbacks)`:
   - Inicia `tpch_bench_{task_id}` com a config PostgreSQL da tarefa
   - Executa 22 queries TPC-H com `EXPLAIN ANALYZE BUFFERS`
   - Para e remove o container TPC-H
   - Repete para TPC-DS com 99 queries
   - Coleta hw_metrics durante toda a execução
3. Salva resultado em `output/benchmark_results/{tier}/{combo}/task_{id}.json`
4. `queue.mark_done(task)` ou `mark_failed()`/`mark_abandoned()`

### Tratamento de exceções

| Exceção | Significado | Ação |
|---------|-------------|------|
| `InvalidConfigError` | PostgreSQL rejeitou um parâmetro na inicialização | `mark_abandoned` — sem retry |
| `TaskTimeoutError` | Tarefa excedeu o timeout do tier | `mark_abandoned` — sem retry |
| `Exception` (outros) | Erro transiente (Docker, disco, rede) | `mark_failed` + `requeue` (até 3×) → `mark_abandoned` |

### Timeouts por tier

| Tier | Timeout |
|------|---------|
| low | 2 horas |
| medium | 4 horas |
| high | 8 horas |

### Limpeza automática de disco

A cada `PRUNE_EVERY_N_TASKS=5` tarefas, o runner chama `auto_prune_if_needed()` que:
- Verifica o espaço livre em disco
- Se abaixo do threshold, executa `docker system prune` (conservador)
- Se abaixo do threshold crítico, executa `docker system prune -a` (agressivo)

### Acompanhando o progresso

```bash
# Ver estado atual da fila
python -m cli.run --status

# Ou via interface web em http://localhost:8000

# Saída típica:
# ┌─────────────────────────────────────┐
# │  Queue: 420/630 done (66.7%)        │
# │  Pending: 150  Running: 1           │
# │  Failed: 3     Abandoned: 56        │
# │  ETA: ~2h 34m                       │
# └─────────────────────────────────────┘
```

## Saída dos Resultados

Cada tarefa concluída gera um arquivo JSON em:
```
output/benchmark_results/{tier}/{combination}/task_{id}.json
```

Ver [Formato dos Resultados](resultado.md) para a estrutura completa.

## Fluxo de Recuperação

Se o runner for interrompido (SIGINT, SIGTERM, ou crash):

1. Tarefas com status `running` no `queue.json` são automaticamente revertidas para `pending` na próxima inicialização
2. Arquivos de resultado escritos parcialmente pelo `result_writer` contêm os dados disponíveis até o momento do crash
3. Simplesmente execute `python -m cli.run` novamente para retomar de onde parou

## Logs

| Arquivo | Conteúdo |
|---------|----------|
| `output/generate.log` | Log completo da geração de configs |
| `output/runner.log` | Log completo da execução de benchmarks |

O `TeeWriter` em `utils/logging.py` garante que os logs sejam escritos simultaneamente no arquivo e no terminal, com formatação colorida no terminal e texto plano no arquivo.
