# API Reference — `pg_sampler/`

Referência completa de todas as funções e tipos do pacote `pg_sampler`.

## `pg_sampler/__init__.py` — API Pública

O `__init__.py` exporta as funções e tipos principais para uso externo:

```python
from pg_sampler import (
    generate_configs,
    generate_all_tiers,
    stages_label,
    stages_description,
)
```

### `generate_configs`

```python
def generate_configs(
    stages: list[int],
    total: int = 30,
    seed: int | None = None,
) -> dict[str, list[Config]]
```

Gera configurações para um conjunto de stages, distribuídas entre os 3 tiers. Função de alto nível que delega para `generate_all_tiers`.

**Parâmetros:**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `stages` | `list[int]` | Stages a incluir, ex: `[1]`, `[1, 2, 3]` |
| `total` | `int` | Total de configs (deve ser múltiplo de 3). Padrão: 30 |
| `seed` | `int \| None` | Semente para reprodutibilidade |

**Retorna:** `{"low": [...], "medium": [...], "high": [...]}` com `total//3` configs por tier.

### `generate_all_tiers`

```python
def generate_all_tiers(
    stages: list[int],
    n_per_tier: int = 10,
    seed: int | None = None,
) -> dict[str, list[Config]]
```

Gera `n_per_tier` configurações para cada um dos 3 tiers.

**Parâmetros:**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `stages` | `list[int]` | Stages a incluir |
| `n_per_tier` | `int` | Configs por tier. Padrão: 10 |
| `seed` | `int \| None` | Semente para LHS |

**Retorna:** `{"low": [...n_per_tier...], "medium": [...], "high": [...]}`.

**Exemplo:**

```python
from pg_sampler import generate_all_tiers

# 10 configs por tier para stages 1+2
result = generate_all_tiers(stages=[1, 2], n_per_tier=10, seed=42)

print(len(result["low"]))     # 10
print(result["low"][0])       # {"shared_buffers": "512MB", "work_mem": "32MB", ...}
```

### `stages_label`

```python
def stages_label(stages: list[int]) -> str
```

Gera o rótulo curto de uma combinação de stages. Usado como nome de diretório e como `combination` nas tarefas.

```python
stages_label([1])          # → "s1"
stages_label([2])          # → "s2"
stages_label([1, 2])       # → "s1s2"
stages_label([1, 3])       # → "s1s3"
stages_label([1, 2, 3])    # → "s1s2s3"
```

### `stages_description`

```python
def stages_description(stages: list[int]) -> str
```

Gera a descrição longa de uma combinação de stages.

```python
stages_description([1])        # → "Stage 1"
stages_description([1, 2])     # → "Stage 1 + Stage 2"
stages_description([1, 2, 3])  # → "Stage 1 + Stage 2 + Stage 3"
```

---

## `pg_sampler/types.py` — Tipos

```python
from pg_sampler.types import ParameterSpace, Config, Environment, StageSpaces, Stages
```

### Aliases de tipo

| Alias | Tipo base | Descrição |
|-------|-----------|-----------|
| `ParameterSpace` | `dict[str, dict]` | Espaço de busca de um stage: `{param_name: spec}` |
| `Config` | `dict[str, Any]` | Uma configuração PostgreSQL: `{param: valor}` |
| `Environment` | `dict[str, int]` | Recursos de um tier: `{cpu, memory_mb, memory_swap_mb, shm_size_mb}` |
| `StageSpaces` | `dict[int, ParameterSpace]` | Espaços por stage: `{stage_num: ParameterSpace}` |
| `Stages` | `list[int]` | Lista de stages ativos, ex: `[1, 2, 3]` |

---

## `pg_sampler/space_loader.py` — Carregamento de Espaços

### `load_parameter_space`

```python
def load_parameter_space(path: str) -> ParameterSpace
```

Carrega um arquivo JSON de espaço de parâmetros e retorna o dicionário de specs.

```python
space = load_parameter_space("specs/spaces/stage1/medium.json")
# space["shared_buffers"] = {"type": "categorical", "choices": ["512MB", "1GB", "1.5GB"]}
# space["max_parallel_workers"] = {"type": "int", "min": 1, "max": 4}
```

### `load_stage_spaces`

```python
def load_stage_spaces(stages: list[int], tier: str) -> StageSpaces
```

Carrega os espaços de parâmetros para múltiplos stages em um único tier.

```python
spaces = load_stage_spaces([1, 2], "high")
# spaces[1] = {espaço do stage 1 para tier high}
# spaces[2] = {espaço do stage 2 para tier high}
```

**Caminho dos arquivos:** `specs/spaces/stage{N}/{tier}.json`

### `load_docker_config`

```python
def load_docker_config(path: str, tier: str) -> Environment
```

Carrega as especificações de hardware de um tier a partir de `specs/docker.json`.

```python
env = load_docker_config("specs/docker.json", "medium")
# env = {"cpu": 4, "memory_mb": 4096, "memory_swap_mb": 4096, "shm_size_mb": 1152}
```

### `save_configs`

```python
def save_configs(configs: dict[str, list[Config]], output_path: str) -> None
```

Salva o dicionário `{tier: [Config]}` em JSON. Cria diretórios intermediários se necessário.

---

## `pg_sampler/lhs_sampler.py` — Latin Hypercube Sampling

### `lhs_quantiles`

```python
def lhs_quantiles(
    n: int,
    stages: list[int],
    seed: int | None = None,
) -> list[dict[str, float]]
```

Gera `n` conjuntos de quantis LHS para os parâmetros amostráveis dos stages especificados.

**Parâmetros:**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `n` | `int` | Número de conjuntos de quantis (= número de configs a gerar) |
| `stages` | `list[int]` | Stages cujos parâmetros serão incluídos |
| `seed` | `int \| None` | Semente para reprodutibilidade |

**Retorna:** Lista de `n` dicionários `{param_name: quantil ∈ [0.0, 1.0]}`.

**Nota:** Apenas parâmetros **amostráveis** são incluídos. Parâmetros fixos (`seq_page_cost`, `max_worker_processes`, `synchronous_commit`) não aparecem nos quantis.

### `_pick`

```python
def _pick(choices: list, q: float) -> Any
```

Seleciona um valor de uma lista usando quantil `q`. `q=0.0` → primeiro item, `q=1.0` → último item.

### `_uniform`

```python
def _uniform(lo: float, hi: float, q: float) -> float
```

Interpola linearmente: `lo + q × (hi - lo)`.

### `_randint`

```python
def _randint(lo: int, hi: int, q: float) -> int
```

Interpola e arredonda: `round(lo + q × (hi - lo))`.

### `filter_memory_choices`

```python
def filter_memory_choices(
    choices: list[str],
    min_mb: float | None = None,
    max_mb: float | None = None,
) -> list[str]
```

Filtra uma lista de strings de memória PostgreSQL (ex: `["512MB", "1GB"]`) para manter apenas valores dentro do range `[min_mb, max_mb]` em MB.

**Parsing de strings:** Entende `"512MB"` → 512, `"1GB"` → 1024, `"256kB"` → 0.25. Se a lista filtrada ficar vazia, retorna a lista original (evita impossibilidade de geração).

### `filter_delay_choices`

```python
def filter_delay_choices(
    choices: list[str],
    min_ms: float | None = None,
    max_ms: float | None = None,
) -> list[str]
```

Filtra strings de delay (ex: `["50ms", "100ms", "200ms"]`) por range em milissegundos.

---

## `pg_sampler/parameter_builder.py` — Construção de Configurações

### `generate_combined_config`

```python
def generate_combined_config(
    stage_spaces: StageSpaces,
    env: Environment,
    stages: list[int],
    quantiles: dict[str, float] | None = None,
) -> Config
```

Gera uma única configuração PostgreSQL. Chama `_fill_stage1/2/3()` na ordem dos stages, acumulando no dict `config`.

**`quantiles=None`** ativa amostragem aleatória pura (para o fallback após rejeição LHS).

### `generate_valid_configs`

```python
def generate_valid_configs(
    stage_spaces: StageSpaces,
    env: Environment,
    stages: list[int],
    n: int,
    max_attempts: int = 100_000,
    seed: int | None = None,
) -> list[Config]
```

Gera `n` configurações válidas e únicas via LHS + fallback.

**Algoritmo:**
1. `diagnose_combined(stage_spaces, stages)` — falha rápido se o espaço é incoerente
2. `lhs_quantiles(n, stages, seed)` — gera quantis
3. Para cada slot: tenta com quantil LHS, depois aleatório até `max_attempts`
4. Garante unicidade (sem configs idênticas)

**Lança `RuntimeError`** se:
- `diagnose_combined` falhar
- Algum slot não puder ser preenchido em `max_attempts` tentativas

### `_fill_stage1`

```python
def _fill_stage1(config: Config, ps: dict, env: Environment, q: dict) -> None
```

Preenche os parâmetros do Stage 1 em `config`. Ordem de preenchimento (com dependências):

1. `jit` — `_pick([0, 1], q)`
2. `seq_page_cost` = `1.0` (fixo)
3. `random_page_cost` — `_uniform(1.0, 4.0, q)`, cap em 4.0
4. `default_statistics_target` — `_randint(100, 400, q)`
5. `max_parallel_workers` — `_randint(1, cpu, q)`, cap em vCPUs
6. `max_parallel_workers_per_gather` — `_randint(1, min(mpw, cpu//2), q)`
7. `max_worker_processes` = `cpu × 2 + 4` (fixo)
8. `shared_buffers` — `_pick(filtered_choices, q)`, filtrado por RAM e shm
9. `effective_cache_size` — `_pick(filtered_choices, q)`, filtrado para `> shared_buffers`
10. `work_mem` — `_pick(filtered_choices, q)`, filtrado por floor do tier
11. `enable_hashagg` — `_pick([0, 1], q)`
12. `enable_bitmapscan` — `_pick([0, 1], q)`
13. `enable_nestloop` — `_pick([0, 1], q)`
14. `enable_parallel_hash` — `_pick([0, 1], q)`
15. `synchronous_commit` = `"off"` (fixo)

### `_fill_stage2`

```python
def _fill_stage2(config: Config, ps: dict, env: Environment, q: dict) -> None
```

Preenche os parâmetros do Stage 2. Usa valores do Stage 1 se presentes (`"shared_buffers" in config`):

- `wal_buffers` ← limitado a 5% de `shared_buffers` (ou 1.5% da RAM como proxy)
- Parâmetros dos 12 do Stage 2 em ordem de dependência

### `_fill_stage3`

```python
def _fill_stage3(config: Config, ps: dict, env: Environment, q: dict) -> None
```

Preenche os parâmetros do Stage 3. Usa valores do Stage 1 se presentes (`"max_worker_processes" in config`):

- Parâmetros dos 12 do Stage 3 em ordem de dependência

---

## `pg_sampler/constraints.py` — Validação

### `validate_combined_config`

```python
def validate_combined_config(
    config: Config,
    env: Environment,
    stage_spaces: StageSpaces,
    stages: list[int],
) -> list[str]
```

Executa todas as verificações aplicáveis para os stages especificados.

**Retorna:** Lista de strings de erro. Lista vazia = config válida.

**Funções internas chamadas:**
- `_validate_stage1(config, env)` → se `1 in stages`
- `_validate_stage2(config, env, stage_spaces)` → se `2 in stages`
- `_validate_stage3(config, env)` → se `3 in stages`
- `_validate_cross_12(config, env)` → se `1 in stages and 2 in stages`

### `diagnose_combined`

```python
def diagnose_combined(stage_spaces: StageSpaces, stages: list[int]) -> None
```

Verifica invariantes estruturais dos espaços de parâmetros. Lança `RuntimeError` se qualquer invariante falhar.

Ver [Restrições e Validação](../geracao/restricoes.md) para a lista completa de verificações.

---

## `pg_sampler/orchestrator.py` — Orquestração

### Constantes

```python
TIERS = ["low", "medium", "high"]
DOCKER_CONFIG_PATH = "specs/docker.json"
```

### `generate_all_tiers`

Implementação principal (re-exportada pelo `__init__.py`). Para cada tier em `TIERS`:

1. `load_docker_config(DOCKER_CONFIG_PATH, tier)` → `env`
2. `load_stage_spaces(stages, tier)` → `stage_spaces`
3. `generate_valid_configs(stage_spaces, env, stages, n_per_tier, seed)` → configs

### `stages_label` e `stages_description`

Implementações que geram rótulos e descrições para combinações de stages (re-exportadas pelo `__init__.py`).

---

## `pg_sampler/display.py` — Exibição

Funções de formatação para saída no terminal durante a geração.

### `print_docker_table`

```python
def print_docker_table(tier_configs: dict) -> None
```

Exibe uma tabela com as especificações de hardware de cada tier.

### `print_stages_header`

```python
def print_stages_header(stages: list[int]) -> None
```

Exibe um cabeçalho com a combinação de stages sendo gerada.

### `print_results_table`

```python
def print_results_table(results: dict[str, list[Config]]) -> None
```

Exibe uma tabela com o número de configs geradas por tier.

### `print_summary_table`

```python
def print_summary_table(all_results: dict) -> None
```

Exibe um resumo de todas as combinações geradas.

### `print_validation_table`

```python
def print_validation_table(errors: list[str]) -> None
```

Exibe erros de validação em formato tabular para diagnóstico.

### `fmt_time`

```python
def fmt_time(seconds: float) -> str
```

Formata segundos em string legível: `fmt_time(93.4)` → `"1m 33s"`.
