# Monitoramento de Hardware

O módulo `monitoring/collector.py` implementa o `MetricsCollector`: uma classe que coleta amostras de métricas de hardware em uma thread separada durante a execução de cada tarefa de benchmark.

## Visão geral

```python
from monitoring.collector import MetricsCollector

collector = MetricsCollector(interval_s=2.0)
collector.start()

# ... executar benchmark TPC-H + TPC-DS ...

hw_metrics = collector.stop()
# hw_metrics = {
#     "samples": [...],         # lista de snapshots brutos
#     "summary": {...}          # médias, máximos, mínimos, energia total
# }
```

O collector usa `threading.Thread` para amostrar métricas sem bloquear a execução do benchmark. A cada `interval_s=2.0` segundos, chama `snapshot()` e acumula os resultados.

## `snapshot()`

```python
def snapshot() -> dict
```

Captura o estado atual do hardware em um único dicionário. Lida graciosamente com sensores ausentes (retorna `None` para métricas indisponíveis, sem lançar exceção).

**Campos retornados:**

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `timestamp_s` | float | Unix timestamp do momento da captura |
| `cpu_percent` | float | Percentual de utilização de CPU (0–100%) |
| `cpu_freq_mhz` | float | Frequência atual da CPU em MHz |
| `cpu_temp_sensor` | str\|None | Nome do sensor de temperatura CPU detectado |
| `cpu_temp_tctl_c` | float\|None | Temperatura Tctl da CPU em °C (AMD k10temp) |
| `cpu_temp_cores_c` | list[float]\|None | Temperatura por core (Intel coretemp) |
| `mem_used_gb` | float | Memória RAM usada em GB |
| `mem_avail_gb` | float | Memória RAM disponível em GB |
| `mem_percent` | float | Percentual de uso de RAM (0–100%) |
| `ram_temps_c` | list[float]\|None | Temperaturas dos módulos de RAM (spd5118) |
| `disk_read_mb_s` | float | Taxa de leitura de disco em MB/s |
| `disk_write_mb_s` | float | Taxa de escrita de disco em MB/s |
| `nvme_temps_c` | list[float]\|None | Temperaturas dos NVMes detectados |
| `gpu_edge_c` | float\|None | Temperatura edge da GPU AMD (amdgpu) |
| `gpu_junction_c` | float\|None | Temperatura junction (hotspot) da GPU AMD |
| `gpu_mem_c` | float\|None | Temperatura da memória da GPU AMD |
| `acpitz_temp_c` | float\|None | Temperatura da zona ACPI |
| `wifi_temp_c` | float\|None | Temperatura do módulo WiFi |
| `rapl_energy_uj` | int\|None | Energia acumulada RAPL em microjoules (Intel) |

## Auto-descoberta de sensores

O collector detecta automaticamente quais sensores estão disponíveis no sistema na primeira chamada:

- **CPU**: verifica se `coretemp` (Intel) ou `k10temp` (AMD) estão disponíveis via `/sys/class/hwmon/`
- **NVMe**: descobre todos os dispositivos NVMe via `nvme list`
- **RAM**: detecta módulos com suporte ao driver `spd5118` (DDR5)
- **GPU**: verifica se o driver `amdgpu` expõe sensores em `/sys/class/drm/`
- **RAPL**: verifica se `/sys/class/powercap/intel-rapl/` está acessível
- **ACPI**: detecta zona de temperatura ACPI via `acpitz`
- **WiFi**: detecta módulos WiFi com sensores de temperatura

A detecção acontece uma vez na criação do `MetricsCollector`. Sensores não encontrados resultam em `None` nos snapshots — sem erros.

## `MetricsCollector`

```python
class MetricsCollector:
    def __init__(self, interval_s: float = 2.0):
        """
        Parâmetros:
            interval_s: Intervalo entre amostras em segundos (padrão: 2.0)
        """
```

### `start()`

```python
def start(self) -> None
```

Inicia a thread de coleta em background. A thread coleta uma amostra a cada `interval_s` segundos até que `stop()` seja chamado.

### `stop()`

```python
def stop(self) -> dict
```

Para a thread de coleta e retorna o dicionário completo de métricas:

```python
{
    "samples": [
        {
            "timestamp_s": 1704067200.0,
            "cpu_percent": 87.3,
            "cpu_freq_mhz": 3800.0,
            "cpu_temp_tctl_c": 72.5,
            "mem_used_gb": 3.2,
            "mem_avail_gb": 0.8,
            "mem_percent": 80.0,
            "disk_read_mb_s": 12.4,
            "disk_write_mb_s": 45.1,
            "gpu_edge_c": null,
            "rapl_energy_uj": null,
            # ... outros campos
        },
        # ... um dict por amostra (a cada 2s)
    ],
    "summary": {
        # Média, máximo e mínimo de cada métrica numérica
        "cpu_percent_avg": 72.1,
        "cpu_percent_max": 99.8,
        "cpu_percent_min": 12.3,
        "mem_used_gb_avg": 2.8,
        "mem_used_gb_max": 3.5,
        "mem_used_gb_min": 0.4,
        # ... idem para todas as métricas numéricas

        # Métricas especiais calculadas
        "rapl_energy_total_j": null,  # energia total em joules (null se RAPL indisponível)
        "rapl_avg_power_w": null,     # potência média em watts
        "duration_s": 1847.3,         # duração total da coleta em segundos
        "n_samples": 923,             # número de amostras coletadas
    }
}
```

## Uso no contexto do runner

O `runner/task_executor.py` inicia o collector antes de iniciar os containers e para após todos os benchmarks:

```python
from monitoring.collector import MetricsCollector
from monitoring import MetricsCollector as MC  # re-exported from __init__

async def run_task(task, tier_configs, tpch_callback, tpcds_callback):
    collector = MetricsCollector(interval_s=2.0)
    collector.start()

    try:
        # Executa TPC-H
        tpch_container = start_postgres_container(...)
        tpch_result = run_tpch_benchmark(tpch_container, ...)
        remove_postgres_container(tpch_container)

        # Executa TPC-DS
        tpcds_container = start_postgres_container(...)
        tpcds_result = run_tpcds_benchmark(tpcds_container, ...)
        remove_postgres_container(tpcds_container)

    finally:
        hw_metrics = collector.stop()  # sempre para, mesmo em caso de erro

    return tpch_result, tpcds_result, hw_metrics
```

O `finally` garante que a thread de coleta sempre para, evitando threads orfãs.

## Interpretação das métricas para o ML

As métricas de hardware servem como **features adicionais** e **variáveis de controle** para os modelos ML:

### `cpu_percent_max`
Pico de utilização de CPU durante o benchmark. Configurações com `max_parallel_workers` alto devem mostrar picos mais altos. Se o pico for sempre < 50%, os workers não estão sendo aproveitados.

### `mem_percent_max`
Pico de uso de RAM. Valores próximos de 100% indicam pressão de memória — podem correlacionar com `work_mem` alto e spill para disco.

### `disk_write_mb_s_avg`
Taxa de escrita em disco durante o benchmark. Para workloads SELECT-only como TPC-H/DS, escrita alta indica spill de hash joins (`temp_written` alto) ou atividade de checkpoint/bgwriter.

### `rapl_energy_total_j`
Energia total consumida em joules, medida via Intel RAPL (Running Average Power Limit). Permite comparar a eficiência energética de diferentes configurações.

!!! warning "RAPL no servidor do TCC"
    O servidor usado neste TCC não permite acesso ao RAPL sem `root`. Todas as amostras de energia serão `null`. A métrica está implementada e funcionará em servidores com permissão adequada.

### `gpu_*_c`
Temperaturas da GPU AMD (se presente). As temperaturas da GPU são coletadas mesmo que a GPU não seja usada pelo PostgreSQL — servem como indicador de carga térmica do sistema.
