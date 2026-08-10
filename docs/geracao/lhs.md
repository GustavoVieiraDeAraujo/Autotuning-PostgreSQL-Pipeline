# Latin Hypercube Sampling

## O problema: cobrir um espaço de 36 dimensões

O gerador precisa produzir **10 configurações PostgreSQL por tier** que sejam:

1. **Diversificadas**: não podem ser todas parecidas entre si
2. **Representativas**: devem cobrir todo o range de cada parâmetro
3. **Sem correlações artificiais**: a escolha de `shared_buffers` não deve influenciar artificialmente a escolha de `work_mem`

Com 36 parâmetros e apenas 10 configurações por tier, a **amostragem aleatória pura** pode deixar "buracos" no espaço: parâmetros importantes sendo sempre amostrados na mesma região. Com 30 configurações por combinação (padrão), o problema é ainda mais crítico para combinações com 36 dimensões.

## O que é Latin Hypercube Sampling

O **Latin Hypercube Sampling** resolve isso dividindo cada dimensão em `n` estratos de igual probabilidade e garantindo que **exatamente uma amostra** caia em cada estrato.

### Exemplo com 2 dimensões e 5 amostras

```
shared_buffers  ████░░░░░░  5 estratos: [512MB, 768MB, 1GB, 1.5GB, 2GB]
work_mem        ░░░░████░░  cada estrato tem exatamente 1 ponto

  work_mem
  64MB  ┤  ·
  48MB  ┤        ·
  32MB  ┤              ·
  16MB  ┤    ·
   8MB  ┤          ·
        └──────────────── shared_buffers
         512M 768M 1GB 1.5G 2GB
```

Com amostragem aleatória pura, dois pontos poderiam cair no mesmo estrato de `work_mem`, deixando outro completamente vazio.

## Implementação em `lhs_sampler.py`

```python
def lhs_quantiles(n: int, stages: list[int], seed: int | None = None) -> list[dict[str, float]]:
    """
    Gera n conjuntos de quantis LHS para os parâmetros dos stages especificados.

    Retorna uma lista de n dicionários {param_name: quantil ∈ [0, 1]}.
    """
    rng = random.Random(seed)
    params = _params_for_stages(stages)  # lista de nomes de parâmetros amostráveis
    columns = []

    for _ in params:
        # Gera n estratos com jitter interno:
        # estrato k = (k + r) / n  onde r ∈ [0, 1) aleatório
        strata = [(k + rng.random()) / n for k in range(n)]
        rng.shuffle(strata)  # embaralha para eliminar correlações entre dimensões
        columns.append(strata)

    return [
        {params[p]: columns[p][i] for p in range(len(params))}
        for i in range(n)
    ]
```

O resultado é uma lista de `n` dicionários, onde cada dicionário mapeia um parâmetro a um **quantil entre 0 e 1**. Esse quantil é usado pelo `parameter_builder` para selecionar o valor correspondente no espaço de busca.

### Por que jitter (`r` aleatório dentro do estrato)?

Sem jitter, o ponto de cada estrato seria sempre o centro: `(k + 0.5) / n`. Com jitter, o ponto está em uma posição aleatória dentro do estrato: `(k + r) / n`. Isso evita que as amostras formem uma grade perfeitamente regular, que poderia ter viés se os parâmetros tiverem periodicidade.

### Por que embaralhar cada dimensão independentemente?

O embaralhamento garante que não haja correlação artificial entre dimensões. Sem embaralhamento, a primeira amostra teria sempre os menores quantis em todas as dimensões (`q ≈ 0/n` para todos os parâmetros), correlacionando artificialmente os valores mínimos de todos os parâmetros.

## Como o quantil é convertido em valor

O `parameter_builder` usa os quantis para selecionar valores dentro dos ranges definidos em `specs/spaces/`:

### Parâmetros categóricos (memória, enum)

```python
def _pick(choices: list, q: float) -> Any:
    """Seleciona um valor de uma lista usando quantil q."""
    idx = min(int(q * len(choices)), len(choices) - 1)
    return choices[idx]

# Exemplo: work_mem choices = ["16MB", "32MB", "64MB", "128MB"]
# q = 0.0 → idx = 0 → "16MB"
# q = 0.5 → idx = 2 → "64MB"
# q = 0.99 → idx = 3 → "128MB"
```

### Parâmetros inteiros

```python
def _randint(lo: int, hi: int, q: float) -> int:
    """Interpola linearmente entre lo e hi usando quantil q."""
    return round(lo + q * (hi - lo))

# Exemplo: max_parallel_workers, lo=1, hi=4
# q = 0.0  → 1
# q = 0.5  → round(1 + 0.5 × 3) = round(2.5) = 2 ou 3
# q = 1.0  → 4
```

### Parâmetros float

```python
def _uniform(lo: float, hi: float, q: float) -> float:
    """Interpola linearmente entre lo e hi usando quantil q."""
    return lo + q * (hi - lo)

# Exemplo: random_page_cost, lo=1.0, hi=4.0
# q = 0.0  → 1.0
# q = 0.5  → 2.5
# q = 1.0  → 4.0
```

### Parâmetros booleanos

```python
# Booleans são tratados como categóricos com choices=[0, 1]
_pick([0, 1], q=0.3)  # → 0
_pick([0, 1], q=0.7)  # → 1
```

## Filtros de memória antes da amostragem

Alguns parâmetros têm constraints que dependem de outros parâmetros já gerados. Para evitar rejeições desnecessárias pela validação, o `parameter_builder` filtra as choices **antes** de aplicar o quantil:

### `filter_memory_choices`

```python
def filter_memory_choices(
    choices: list[str],
    min_mb: float | None = None,
    max_mb: float | None = None,
) -> list[str]
```

Filtra uma lista de strings de memória (ex: `["512MB", "1GB", "2GB"]`) para manter apenas valores dentro do range `[min_mb, max_mb]`.

**Exemplo:** Para `effective_cache_size`, o builder precisa garantir `ecs > shared_buffers`:

```python
# shared_buffers já foi gerado como "1GB" = 1024 MB
ecs_choices = filter_memory_choices(
    choices=["1GB", "1.5GB", "2GB", "3GB", "4GB"],
    min_mb=1024 + 1,  # estritamente maior que shared_buffers
)
# → ["1.5GB", "2GB", "3GB", "4GB"]
# O quantil LHS é então aplicado nessa lista filtrada
```

### `filter_delay_choices`

```python
def filter_delay_choices(
    choices: list[str],
    min_ms: float | None = None,
    max_ms: float | None = None,
) -> list[str]
```

Filtra choices de delay (ex: `["50ms", "100ms", "200ms"]`) pelo range em milissegundos. Usado pelo builder para `bgwriter_delay` e outros parâmetros de tempo.

## Comparação LHS vs Random Search

```
Amostragem Aleatória (10 configs, 2 params):    LHS (10 configs, 2 params):

work_mem                                         work_mem
256MB ┤ ● ●                                     256MB ┤              ●
128MB ┤     ●                                   128MB ┤        ●
 64MB ┤ ●   ●                                    64MB ┤   ●
 32MB ┤         ● ●                              32MB ┤         ●
 16MB ┤ ●                                        16MB ┤ ● ●     ●
      └──────────────── shared_buffers                 └──────────────── shared_buffers
       512M 768M 1GB                                    512M 768M 1GB

 Problemas:                                      Garantias:
 - 512MB tem 5 configs (50%)                    - Exatamente 1 config por estrato
 - 1GB tem apenas 1 config                      - Todos os ranges cobertos
 - work_mem 64MB aparece 2×                     - Sem repetição de estratos
```

Para o TCC, a importância do LHS é que os **modelos de ML** subsequentes têm um conjunto de configurações que cobre uniformemente o espaço: sem o LHS, regiões inteiras do espaço de busca poderiam não ser amostradas, levando a modelos com extrapolação pobre.

## Seed para reprodutibilidade

O parâmetro `seed` em `lhs_quantiles()` e em `generate_all_tiers()` permite reproduzir exatamente os mesmos conjuntos de configurações:

```python
# Gera sempre as mesmas 10 configs para stages=[1,2]
result1 = generate_all_tiers(stages=[1, 2], n_per_tier=10, seed=42)
result2 = generate_all_tiers(stages=[1, 2], n_per_tier=10, seed=42)
assert result1 == result2  # True

# Seed diferente → configurações diferentes
result3 = generate_all_tiers(stages=[1, 2], n_per_tier=10, seed=99)
assert result1 != result3  # True
```

O `cli/generate.py` aceita `--seed` como argumento de linha de comando.

## Parâmetros excluídos do LHS

Três parâmetros são **fixos** e excluídos do LHS: não fazem parte das dimensões amostradas:

| Parâmetro | Valor fixo | Motivo |
|-----------|------------|--------|
| `seq_page_cost` | `1.0` | Âncora do sistema de custo: variar seria redundante com `random_page_cost` |
| `max_worker_processes` | `cpu × 2 + 4` | Dependência de múltiplos outros parâmetros; fixar simplifica as constraints |
| `synchronous_commit` | `"off"` | Sem efeito mensurável em workloads SELECT-only como TPC-H/DS |

**Por que `seq_page_cost=1.0` é uma âncora?**

O PostgreSQL avalia todas as estratégias de acesso em termos relativos a `seq_page_cost`. Variar `seq_page_cost=2.0` com `random_page_cost=4.0` produz a mesma decisão do planner que `seq=1.0` com `random=2.0`: a razão é idêntica. Manter `seq=1.0` fixo e variar `random_page_cost` explora toda a relação sem adicionar uma dimensão redundante ao LHS.
