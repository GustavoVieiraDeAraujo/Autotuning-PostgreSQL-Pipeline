# Arquitetura do Projeto

O projeto segue uma **arquitetura em pipeline** onde cada pacote tem responsabilidade única e bem definida. Os dados fluem linearmente da geração de configurações até os resultados de benchmark, com cada etapa sendo independente o suficiente para ser executada e testada separadamente.

## Diagrama de pacotes

```mermaid
graph TD
    subgraph "Entrada"
        SPECS["specs/\ndocker.json\nspaces/stage{1,2,3}/{low,medium,high}.json"]
    end

    subgraph "Geração (pg_sampler/)"
        ORCH["orchestrator.py\ngenerate_all_tiers()"]
        LHS["lhs_sampler.py\nlhs_quantiles()"]
        BUILD["parameter_builder.py\ngenerate_valid_configs()"]
        CONSTR["constraints.py\nvalidate_combined_config()"]
        LOADER["space_loader.py\nload_parameter_space()"]
        TYPES["types.py\nParameterSpace, Config, Environment"]
    end

    subgraph "CLI (cli/)"
        GEN["generate.py\n7 combos × 3 tiers → queue.json"]
        PREP["prepare.py\n6 imagens Docker + smoke tests"]
        RUN["run.py\npreflight → executa fila"]
    end

    subgraph "Fila (task_queue/)"
        QUEUE["execution_queue.py\nExecutionQueue\npending→running→done/failed"]
    end

    subgraph "Runner (runner/)"
        PREFLIGHT["preflight.py\n8 verificações"]
        EXECUTOR["task_executor.py\nrun_task()"]
        WRITER["result_writer.py\nsave JSONs"]
    end

    subgraph "Benchmarks (benchmarks/)"
        IMGBUILD["image_builder.py\nbuild_image() + image_exists()"]
        CONTAINER["container.py\nstart_postgres_container()"]
        QEXEC["query_executor.py\nrun_benchmark()"]
        TPCH["tpc_h/benchmark.py\nDB=tpch, 22 queries"]
        TPCDS["tpc_ds/benchmark.py\nDB=tpcds, 99 queries"]
    end

    subgraph "Monitoramento (monitoring/)"
        METRICS["collector.py\nMetricsCollector\nsnapshot() a cada 2s"]
    end

    subgraph "Utilitários (utils/)"
        LOGGING["logging.py\nTeeWriter, log(), banner()"]
        FMT["formatting.py\nfmt_duration(), fmt_eta()"]
        CLEANUP["docker_cleanup.py\nauto_prune_if_needed()"]
    end

    subgraph "Web (web/)"
        WEBAPP["app.py\nFastAPI + SSE streams"]
        HTML["index.html\nFrontend de controle"]
    end

    subgraph "Saída"
        OUTPUT["output/\nqueue.json\nbenchmark_results/{tier}/{combo}/task_{id}.json"]
    end

    SPECS --> LOADER --> ORCH --> LHS --> BUILD --> CONSTR
    ORCH --> GEN --> QUEUE
    QUEUE --> RUN --> PREFLIGHT
    PREFLIGHT --> EXECUTOR --> CONTAINER --> QEXEC
    QEXEC --> TPCH & TPCDS
    EXECUTOR --> METRICS
    EXECUTOR --> WRITER --> OUTPUT
    PREP --> IMGBUILD
    CLEANUP --> RUN
    LOGGING --> GEN & RUN & PREP
    WEBAPP --> GEN & PREP & RUN
```

## Responsabilidades por pacote

### `pg_sampler/`: Geração de configurações

É o motor de geração de configurações PostgreSQL. Responsável por:

1. Carregar os espaços de parâmetros dos arquivos JSON em `specs/spaces/`
2. Aplicar **Latin Hypercube Sampling** para garantir cobertura uniforme do espaço
3. Construir configurações respeitando restrições semânticas (limites de memória, hierarquias de custo)
4. Validar cada configuração gerada contra todas as restrições conhecidas
5. Salvar o resultado em `output/queue.json` via `cli/generate.py`

Internamente, o pacote usa 3 stages de 12 parâmetros cada (36 total), com cada stage cobrindo uma dimensão diferente do sistema PostgreSQL.

### `benchmarks/`: Execução de benchmarks

Responsável por toda a interação com Docker e PostgreSQL durante a execução:

- `image_builder.py`: Constrói as 6 imagens Docker que contêm os dados TPC-H e TPC-DS pré-carregados. Cada imagem combina um benchmark (tpch/tpcds) com um scale factor (SF=1, SF=2, SF=4) para os três tiers.
- `container.py`: Inicia e para containers PostgreSQL com uma configuração específica. Valida a config antes de iniciar (detecta parâmetros inválidos), aguarda o PostgreSQL estar pronto, e fornece `InvalidConfigError` quando o PG rejeita a configuração.
- `query_executor.py`: Executa todas as queries de um benchmark, coleta `EXPLAIN ANALYZE BUFFERS`, estatísticas do `pg_stat_bgwriter`, e trata timeouts e OOM.

### `runner/`: Orquestração

Liga todos os componentes durante a execução real:

- `preflight.py`: Executa 8 verificações antes de começar (daemon Docker, espaço em disco, containers obsoletos, integridade da fila, tabelas TPC-H/DS por tier, conectividade de banco).
- `task_executor.py`: Para cada tarefa, inicia container TPC-H, executa benchmark, para container, repete para TPC-DS, tudo com timeout por tier (2h/4h/8h). Traduz exceções em ações da fila: `InvalidConfigError` → abandon, `TaskTimeoutError` → abandon, `Exception` → retry até 3×.
- `result_writer.py`: Escreve os resultados de forma incremental durante a execução (não apenas no final), permitindo recuperação em caso de crash.

### `task_queue/`: Fila persistente

`ExecutionQueue` é uma fila de tarefas com estados persistidos em `output/queue.json`. O ciclo de vida de uma tarefa é:

```
pending → running → done
                 ↘ failed → pending (até 3×)
                          ↘ abandoned
```

Na inicialização, tarefas em estado `running` são automaticamente recuperadas para `pending` (tratamento de crash/restart).

### `monitoring/`: Métricas de hardware

`MetricsCollector` coleta amostras de hardware a cada `interval_s=2.0` segundos em uma thread separada. Cada sample inclui:
- CPU: percentual, frequência MHz, temperatura (coretemp/k10temp)
- Memória: GB usados, disponíveis, percentual
- Disco: MB/s de leitura e escrita
- NVMe: temperaturas dos sensores
- GPU AMD: temperaturas edge/junction/memória (amdgpu)
- RAPL: energia em microjoules (Intel)

No final, `stop()` retorna as samples brutas e um summary com média/máximo/mínimo de cada métrica.

### `utils/`: Utilitários transversais

- `logging.py`: `TeeWriter` duplica stdout/stderr para arquivo e terminal simultaneamente. `log()` emite mensagens coloridas com nível (INFO/OK/WARN/ERROR/HEAD). `banner()` e `sep()` formatam seções no terminal.
- `formatting.py`: `fmt_duration()` formata segundos em `1h 23m 45s`. `fmt_eta()` calcula e formata o tempo restante estimado baseado no progresso.
- `docker_cleanup.py`: `auto_prune_if_needed()` limpa imagens/containers Docker quando o disco está abaixo de um threshold. Chamado a cada `PRUNE_EVERY_N_TASKS=5` tarefas pelo runner.

### `web/`: Interface de controle

`app.py` é um servidor FastAPI que expõe:
- Endpoints REST para controlar os subprocessos (generate, prepare, run)
- `GET /api/queue` para visualizar o estado da fila
- `GET /api/results/list` e `GET /api/results/{tier}/{combo}/{filename}` para inspecionar resultados
- `GET /stream/generate`, `/stream/prepare`, `/stream/runner` como streams SSE (Server-Sent Events) que transmitem os arquivos de log em tempo real para o frontend

O frontend `index.html` consome essas APIs e exibe o progresso em tempo real.

### `specs/`: Dados de configuração

Arquivos JSON que definem os parâmetros de entrada do sistema:

- `specs/docker.json`: Recursos de cada tier (cpus, memory_mb, memory_swap_mb, shm_size_mb)
- `specs/spaces/stage{N}/{tier}.json`: Para cada stage (1, 2, 3) e tier (low, medium, high), o espaço de busca de cada parâmetro: tipo (`int`, `float`, `bool`, `categorical`) e range ou valores permitidos

## Fluxo de dados end-to-end

```mermaid
sequenceDiagram
    participant GEN as cli/generate.py
    participant SAMPLER as pg_sampler/
    participant QUEUE as task_queue/
    participant CLI as cli/run.py
    participant RUNNER as runner/
    participant BENCH as benchmarks/
    participant MONITOR as monitoring/
    participant WRITER as result_writer.py
    participant DISK as output/

    GEN->>SAMPLER: generate_all_tiers(stages, n_per_tier, seed)
    SAMPLER-->>GEN: {low: [...], medium: [...], high: [...]}
    GEN->>QUEUE: ExecutionQueue.from_dict(all_results, queue_path)
    QUEUE-->>DISK: queue.json (pending tasks)

    CLI->>RUNNER: run_preflight_checks()
    RUNNER-->>CLI: 8 checks passed

    loop Para cada tarefa pending
        CLI->>QUEUE: queue.next()
        QUEUE-->>CLI: task (status=running)
        CLI->>RUNNER: run_task(task, tier_configs, callbacks)
        RUNNER->>MONITOR: MetricsCollector.start()
        RUNNER->>BENCH: start_postgres_container(tier_config, pg_config, "tpch")
        BENCH-->>RUNNER: container
        RUNNER->>BENCH: run_benchmark(container) → tpch_result
        RUNNER->>BENCH: start_postgres_container(..., "tpcds")
        RUNNER->>BENCH: run_benchmark(container) → tpcds_result
        RUNNER->>MONITOR: MetricsCollector.stop() → hw_metrics
        RUNNER->>WRITER: finalize_task_file(task_id, tpch, tpcds, hw_metrics)
        WRITER-->>DISK: task_{id}.json
        CLI->>QUEUE: queue.mark_done(task)
        QUEUE-->>DISK: queue.json atualizado
    end
```

## Decisões de design

### Por que stages de parâmetros?

A divisão em 3 stages é um **estudo de ablação**: cada stage agrupa parâmetros por tipo de impacto esperado. Isso permite comparar os resultados de modelos treinados com 12 parâmetros (apenas um stage) versus 36 parâmetros (todos os stages), medindo o custo/benefício de aumentar a dimensionalidade do espaço de busca.

### Por que LHS e não random search puro?

Com 36 parâmetros e apenas 30 configs por tier, o random search puro teria alta probabilidade de deixar regiões inteiras do espaço não cobertas. O LHS garante cobertura mínima de todas as dimensões (exatamente 1 amostra por estrato de cada parâmetro) com custo computacional idêntico ao random search.

### Por que fila persistente em JSON?

A execução de 630 tarefas leva dias. O `queue.json` permite:
1. **Retomar** após crashes ou reinicializações do sistema sem perder progresso
2. **Introspecção**: verificar o estado de qualquer tarefa a qualquer momento
3. **Retentativa automática**: tarefas falhas por erros transientes (rede, disco) são recolocadas na fila

### Por que result_writer escreve incrementalmente?

Em vez de escrever o resultado apenas ao final da tarefa, o `result_writer` escreve seções à medida que ficam disponíveis (TPC-H primeiro, depois TPC-DS, depois hw_metrics). Isso garante que, mesmo se o processo for morto durante a segunda metade de uma tarefa, os dados da primeira metade estejam preservados em disco.

### Por que containers por tarefa e não um container global?

Cada tarefa usa um conjunto diferente de parâmetros PostgreSQL (`shared_buffers`, `work_mem`, etc.). Como o PostgreSQL lê a maioria desses parâmetros na inicialização, é necessário reiniciar o servidor para cada configuração. O modelo de container efêmero garante total isolamento entre tarefas e reprodutibilidade dos resultados.
