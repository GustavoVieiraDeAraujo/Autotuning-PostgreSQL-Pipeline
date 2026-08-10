"""
monitoring
==========
Coleta de métricas de hardware do servidor em tempo real.

Auto-descobre os sensores disponíveis ao importar:
  - CPU: uso (%), frequência (MHz), temperatura Package/Tctl e por core
               (suporta Intel coretemp e AMD k10temp)
  - Memória: usada (GB), disponível (GB), uso (%)
  - Disco: taxa de leitura/escrita (MB/s)
  - NVMe: temperatura composta dos SSDs (°C)
  - GPU: temperatura edge/junction/mem da GPU AMD (°C)
  - RAM: temperatura dos pentes DDR5 via spd5118 (°C)
  - Energia: consumo acumulado (J) e potência (W) via Intel RAPL
               (requer permissão em /sys/class/powercap/)

Uso rápido
----------
    from monitoring import snapshot, MetricsCollector

    # Leitura única
    metrics = snapshot()

    # Coleta contínua durante um benchmark
    collector = MetricsCollector(interval_s=2.0)
    collector.start()
    # ... benchmark ...
    summary = collector.stop()   # dict com avg/max/min por métrica
"""

from .collector import MetricsCollector, snapshot

__all__ = ["MetricsCollector", "snapshot"]
