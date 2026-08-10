"""
Config recomendada pelo pgtune (calculadora heurística, não-ML) para cada tier.

Fórmulas transcritas fielmente do código-fonte real do pgtune
(github.com/le0pard/pgtune, src/features/configuration/configurationSlice.js,
lido via `gh api` em 2026-08-09), assumindo:
    db_type = "Data Warehouse" (o mais próximo do workload OLAP TPC-H/TPC-DS)
    hd_type = "SSD"            (mesma suposição do resto do projeto: Docker
                                 sobre NVMe local, não HDD giratório)
    db_size = "mid_ram"        (opção padrão do próprio pgtune; não medimos o
                                 tamanho real em disco de cada base TPC)

Achado relevante para o TCC: pgtune SÓ calcula shared_buffers,
effective_cache_size, default_statistics_target, random_page_cost, work_mem e
os 2 parâmetros de paralelismo (quando cpuNum>=4): nenhum dos 26 outros
parâmetros do nosso espaço de busca (toggles enable_*, custos de CPU,
collapse_limit, hash_mem_multiplier) é tocado pelo pgtune. Isso por si só é
um dado comparativo: uma calculadora heurística de mercado cobre ~7/33 (21%)
dos parâmetros que o meta-modelo deste TCC de fato varia.
"""

import math

from ml.baseline_comparison import DEFAULT_PG_CONFIG
from ml.config import TIER_HARDWARE

DB_TYPE_MAX_CONNECTIONS = 40  # Data Warehouse, per pgtune selectMaxConnections


def pgtune_config(tier: str) -> dict:
    """Config pgtune (Data Warehouse / SSD / mid_ram) pro hardware do tier."""
    hw = TIER_HARDWARE[tier]
    cpu_num = hw["vcpus"]
    total_kb = hw["memory_mb"] * 1024

    cfg = dict(DEFAULT_PG_CONFIG)  # pgtune não toca no resto: herda o default

    shared_buffers_kb = math.floor(total_kb / 4)
    cfg["shared_buffers"] = f"{shared_buffers_kb // 1024}MB"

    effective_cache_kb = math.floor(total_kb * 3 / 4)
    cfg["effective_cache_size"] = f"{effective_cache_kb // 1024}MB"

    cfg["default_statistics_target"] = 500  # DW

    cfg["random_page_cost"] = 4.0  # DW em storage não-HDD: pgtune mantém o default (evita index scan ruim)

    if cpu_num >= 4:
        workers_per_gather = math.ceil(cpu_num / 2)  # sem cap de 4 pra DW
        cfg["max_parallel_workers"] = cpu_num
        cfg["max_parallel_workers_per_gather"] = workers_per_gather
        parallel_for_work_mem = cpu_num
    else:
        # pgtune não define parâmetros de paralelismo com <4 vCPUs: usa o
        # default interno max_worker_processes=8 só pro cálculo de work_mem.
        parallel_for_work_mem = 8

    work_mem_value = (total_kb - shared_buffers_kb) / (
        (DB_TYPE_MAX_CONNECTIONS + parallel_for_work_mem) * 3
    )
    work_mem_kb = math.floor(work_mem_value / 2)  # DW divide por 2
    work_mem_kb = max(work_mem_kb, 4096)  # piso de 4MB
    cfg["work_mem"] = f"{work_mem_kb}kB"

    return cfg


if __name__ == "__main__":
    import json
    for tier in ("low", "medium", "high"):
        print(f"\n── {tier} ──")
        print(json.dumps(pgtune_config(tier), indent=2))
