"""
Coleta de métricas de hardware via hwmon, psutil e Intel RAPL.

Auto-descobre os sensores disponíveis ao importar o módulo:
  - k10temp   → temperatura CPU AMD (Tctl, Tccd1, Tccd2)
  - coretemp  → temperatura CPU Intel (Package id 0, cores individuais)
  - nvme      → temperatura dos SSDs
  - amdgpu    → temperatura GPU (edge, junction, mem)
  - spd5118   → temperatura pentes RAM DDR5
  - Intel RAPL → energia acumulada em µJ (requer leitura de /sys/class/powercap/)

Funções e classes
-----------------
    snapshot() → dict
        Leitura pontual de todas as métricas disponíveis.

    MetricsCollector(interval_s)
        Amostra em background thread e retorna resumo estatístico ao parar.
"""

import os
import statistics
import threading
import time
from pathlib import Path
from typing import Any

import psutil

# ---------------------------------------------------------------------------
# Auto-descoberta de sensores hwmon
# ---------------------------------------------------------------------------

_HWMON_ROOT = Path("/sys/class/hwmon")
_RAPL_ROOT  = Path("/sys/class/powercap/intel-rapl:0")


def _find_hwmon_temp(driver_name: str, label: str | None = None) -> Path | None:
    """Localiza o arquivo temp*_input de um sensor hwmon pelo nome do driver e label."""
    for dev in sorted(_HWMON_ROOT.iterdir()):
        name_file = dev / "name"
        if not name_file.exists() or name_file.read_text().strip() != driver_name:
            continue
        if label is None:
            t = dev / "temp1_input"
            return t if t.exists() else None
        for lf in sorted(dev.glob("temp*_label")):
            if lf.read_text().strip() == label:
                inp = Path(str(lf).replace("_label", "_input"))
                return inp if inp.exists() else None
    return None


def _find_all_hwmon(driver_name: str, label: str | None = None) -> list[Path]:
    """Localiza todos os sensores de um driver (ex: múltiplos nvme ou spd5118)."""
    results: list[Path] = []
    for dev in sorted(_HWMON_ROOT.iterdir()):
        name_file = dev / "name"
        if not name_file.exists() or name_file.read_text().strip() != driver_name:
            continue
        if label is None:
            t = dev / "temp1_input"
            if t.exists():
                results.append(t)
        else:
            for lf in sorted(dev.glob("temp*_label")):
                if lf.read_text().strip() == label:
                    inp = Path(str(lf).replace("_label", "_input"))
                    if inp.exists():
                        results.append(inp)
    return results


# ---------------------------------------------------------------------------
# Descoberta automática de sensores (executada uma única vez ao importar)
# ---------------------------------------------------------------------------

# CPU — suporta AMD k10temp e Intel coretemp
_CPU_TCTL        = _find_hwmon_temp("k10temp", "Tctl")
_CPU_SENSOR_NAME = "k10temp"

if _CPU_TCTL is None:
    _CPU_TCTL        = _find_hwmon_temp("coretemp", "Package id 0")
    _CPU_SENSOR_NAME = "coretemp" if _CPU_TCTL is not None else "k10temp"

# Todos os cores individuais (AMD Tccd* / Intel Core N)
def _discover_cpu_cores() -> list[Path]:
    """Retorna lista de sensores de temperatura por core (em ordem)."""
    # AMD: Tccd1, Tccd2, ...
    cores = _find_all_hwmon("k10temp", "Tccd1") + _find_all_hwmon("k10temp", "Tccd2")
    if cores:
        return cores
    # Intel: Core 0, Core 1, ... (label = "Core N")
    results: list[Path] = []
    for dev in sorted(_HWMON_ROOT.iterdir()):
        name_file = dev / "name"
        if not name_file.exists() or name_file.read_text().strip() != "coretemp":
            continue
        for lf in sorted(dev.glob("temp*_label"), key=lambda p: int(p.stem.removeprefix("temp").removesuffix("_label"))):
            label = lf.read_text().strip()
            if label.startswith("Core "):
                inp = Path(str(lf).replace("_label", "_input"))
                if inp.exists():
                    results.append(inp)
    return results

_CPU_CORES: list[Path] = _discover_cpu_cores()   # Core 0, Core 1, ...

# NVMe SSDs
_NVME_TEMPS = _find_all_hwmon("nvme")             # Composite de cada SSD

# GPU AMD
_GPU_EDGE     = _find_hwmon_temp("amdgpu", "edge")
_GPU_JUNCTION = _find_hwmon_temp("amdgpu", "junction")
_GPU_MEM      = _find_hwmon_temp("amdgpu", "mem")

# RAM DDR5 com sensor spd5118 (não presente em DDR4/LPDDR4)
_RAM_TEMPS = _find_all_hwmon("spd5118")

# ACPI thermal zone (chipset / placa-mãe)
_ACPITZ_TEMP = _find_hwmon_temp("acpitz")

# WiFi (iwlwifi ou similar)
_WIFI_TEMP = _find_hwmon_temp("iwlwifi_1") or _find_hwmon_temp("iwlwifi")

# RAPL: verifica se temos permissão de leitura
_RAPL_ENERGY = _RAPL_ROOT / "energy_uj"
_RAPL_MAX    = _RAPL_ROOT / "max_energy_range_uj"
try:
    _RAPL_READABLE = _RAPL_ENERGY.exists() and os.access(_RAPL_ENERGY, os.R_OK)
    _RAPL_MAX_UJ   = int(_RAPL_MAX.read_text()) if _RAPL_READABLE and _RAPL_MAX.exists() else None
except Exception:
    _RAPL_READABLE = False
    _RAPL_MAX_UJ   = None


# ---------------------------------------------------------------------------
# Leituras de baixo nível
# ---------------------------------------------------------------------------

def _read_temp_c(path: Path | None) -> float | None:
    """Lê temperatura em °C de um arquivo hwmon (valor em milli-Celsius)."""
    if path is None:
        return None
    try:
        return round(int(path.read_text()) / 1000.0, 1)
    except (OSError, ValueError):
        return None


def _read_rapl_uj() -> int | None:
    """Lê energia acumulada do RAPL em microjoules (suporta wrap-around)."""
    if not _RAPL_READABLE:
        return None
    try:
        return int(_RAPL_ENERGY.read_text())
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Snapshot público
# ---------------------------------------------------------------------------

def snapshot() -> dict[str, Any]:
    """Captura uma leitura instantânea de todas as métricas disponíveis.

    Todos os campos ausentes (sensor não encontrado ou sem permissão)
    são retornados como ``None``.

    Returns:
        Dict com as chaves:
            timestamp_s        — Unix timestamp da leitura
            cpu_percent        — uso médio de todos os cores (%)
            cpu_freq_mhz       — frequência atual do primeiro core (MHz)
            cpu_temp_sensor    — nome do driver hwmon usado ("coretemp" ou "k10temp")
            cpu_temp_tctl_c    — temperatura Package/Tctl do CPU (°C); Intel ou AMD
            cpu_temp_cores_c   — lista de temperaturas por core (°C); Core 0..N
            mem_used_gb        — RAM utilizada (GB)
            mem_avail_gb       — RAM disponível (GB)
            mem_percent        — uso de RAM (%)
            ram_temps_c        — lista de temperaturas dos pentes DDR5 via spd5118 (°C)
            disk_read_mb_s     — taxa de leitura de disco (MB/s)
            disk_write_mb_s    — taxa de escrita de disco (MB/s)
            nvme_temps_c       — lista de temperaturas compostas dos SSDs NVMe (°C)
            gpu_edge_c         — temperatura edge da GPU AMD (°C)
            gpu_junction_c     — temperatura junction da GPU AMD (°C)
            gpu_mem_c          — temperatura da VRAM da GPU AMD (°C)
            acpitz_temp_c      — temperatura da zona ACPI (chipset/placa-mãe) (°C)
            wifi_temp_c        — temperatura do adaptador Wi-Fi (°C)
            rapl_energy_uj     — energia acumulada via Intel RAPL (µJ); None se indisponível
    """
    ts = time.time()

    # CPU
    cpu_pct  = psutil.cpu_percent(interval=None)
    cpu_freq = psutil.cpu_freq()
    freq_mhz = round(cpu_freq.current, 1) if cpu_freq else None

    # Memória
    mem = psutil.virtual_memory()
    mem_used_gb  = round(mem.used  / 1024**3, 2)
    mem_avail_gb = round(mem.available / 1024**3, 2)

    # Disco (delta desde última chamada — psutil mantém counters cumulativos)
    disk_io = _disk_delta()

    return {
        "timestamp_s":        ts,
        # CPU
        "cpu_percent":        cpu_pct,
        "cpu_freq_mhz":       freq_mhz,
        "cpu_temp_sensor":    _CPU_SENSOR_NAME,
        "cpu_temp_tctl_c":    _read_temp_c(_CPU_TCTL),          # Package / Tctl
        "cpu_temp_cores_c":   [_read_temp_c(p) for p in _CPU_CORES],  # Core 0..N
        # Memória RAM
        "mem_used_gb":        mem_used_gb,
        "mem_avail_gb":       mem_avail_gb,
        "mem_percent":        round(mem.percent, 1),
        "ram_temps_c":        [_read_temp_c(p) for p in _RAM_TEMPS],  # DDR5 spd5118
        # Disco
        "disk_read_mb_s":     disk_io[0],
        "disk_write_mb_s":    disk_io[1],
        "nvme_temps_c":       [_read_temp_c(p) for p in _NVME_TEMPS],
        # GPU AMD
        "gpu_edge_c":         _read_temp_c(_GPU_EDGE),
        "gpu_junction_c":     _read_temp_c(_GPU_JUNCTION),
        "gpu_mem_c":          _read_temp_c(_GPU_MEM),
        # Outros sensores
        "acpitz_temp_c":      _read_temp_c(_ACPITZ_TEMP),       # Placa-mãe / chipset
        "wifi_temp_c":        _read_temp_c(_WIFI_TEMP),          # Adaptador WiFi
        # Energia
        "rapl_energy_uj":     _read_rapl_uj(),
    }


# ---------------------------------------------------------------------------
# Delta de I/O de disco (taxa MB/s entre leituras consecutivas)
# ---------------------------------------------------------------------------

_disk_counters_prev: tuple[int, int, float] | None = None
_disk_lock = threading.Lock()


def _disk_delta() -> tuple[float | None, float | None]:
    """Retorna (read_mb_s, write_mb_s) como taxa desde a última chamada."""
    global _disk_counters_prev
    try:
        c = psutil.disk_io_counters()
        if c is None:
            return None, None
        now = time.monotonic()
        with _disk_lock:
            prev = _disk_counters_prev
            _disk_counters_prev = (c.read_bytes, c.write_bytes, now)
        if prev is None:
            return 0.0, 0.0
        dt = now - prev[2]
        if dt <= 0:
            return 0.0, 0.0
        read_mb_s  = round((c.read_bytes  - prev[0]) / dt / 1024**2, 2)
        write_mb_s = round((c.write_bytes - prev[1]) / dt / 1024**2, 2)
        return max(0.0, read_mb_s), max(0.0, write_mb_s)
    except Exception:
        return None, None


# Aquece os contadores de disco para que a primeira leitura real seja válida
psutil.cpu_percent(interval=None)   # descarta leitura inicial (sempre 0.0)
_disk_delta()                        # inicializa contadores de disco


# ---------------------------------------------------------------------------
# MetricsCollector — amostragem em background
# ---------------------------------------------------------------------------

class MetricsCollector:
    """Amostra métricas de hardware em background durante um benchmark.

    Útil para registrar o perfil de temperatura, uso de CPU e consumo
    de energia durante cada configuração testada.

    Args:
        interval_s: Intervalo entre amostras em segundos. Padrão: 2.0.

    Example:
        collector = MetricsCollector(interval_s=2.0)
        collector.start()
        # ... executa benchmark ...
        hw_summary = collector.stop()
        # hw_summary contém avg/max/min de cada métrica
    """

    def __init__(self, interval_s: float = 2.0) -> None:
        self._interval   = interval_s
        self._samples:   list[dict] = []
        self._stop_event = threading.Event()
        self._thread:    threading.Thread | None = None

    @property
    def latest(self) -> dict | None:
        """Retorna o snapshot mais recente, ou None se nenhuma amostra coletada ainda."""
        return self._samples[-1] if self._samples else None

    def start(self) -> None:
        """Inicia a coleta em background."""
        self._stop_event.clear()
        self._samples = []
        self._thread  = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> dict:
        """Para a coleta e retorna as métricas agregadas.

        Returns:
            Dict com:
                samples  — lista de snapshots brutos (timeseries)
                summary  — avg/max/min de cada métrica numérica; inclui
                           rapl_energy_total_j, rapl_avg_power_w, duration_s
                           e n_samples quando disponíveis
        """
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self._interval * 2 + 2)

        return {
            "samples": self._samples,
            "summary": self._aggregate(),
        }

    # ------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._samples.append(snapshot())
            except Exception:
                pass
            self._stop_event.wait(self._interval)

    def _aggregate(self) -> dict[str, Any]:
        """Calcula avg/max/min para cada métrica numérica dos samples."""
        if not self._samples:
            return {}

        # Métricas escalares simples
        scalar_keys = [
            "cpu_percent", "cpu_freq_mhz",
            "cpu_temp_tctl_c",               # Package / Tctl (AMD e Intel)
            "mem_used_gb", "mem_percent",
            "disk_read_mb_s", "disk_write_mb_s",
            "gpu_edge_c", "gpu_junction_c", "gpu_mem_c",
        ]

        agg: dict[str, Any] = {}

        for key in scalar_keys:
            vals = [s[key] for s in self._samples if s.get(key) is not None]
            if vals:
                agg[f"{key}_avg"] = round(statistics.mean(vals), 2)
                agg[f"{key}_max"] = round(max(vals), 2)
                agg[f"{key}_min"] = round(min(vals), 2)

        # CPU cores individuais (Intel Core 0-N / AMD Tccd*)
        n_cores = max((len(s.get("cpu_temp_cores_c") or []) for s in self._samples), default=0)
        for i in range(n_cores):
            vals = [s["cpu_temp_cores_c"][i] for s in self._samples
                    if s.get("cpu_temp_cores_c") and len(s["cpu_temp_cores_c"]) > i
                    and s["cpu_temp_cores_c"][i] is not None]
            if vals:
                agg[f"cpu_core{i}_temp_avg_c"] = round(statistics.mean(vals), 2)
                agg[f"cpu_core{i}_temp_max_c"] = round(max(vals), 2)

        # NVMe temps (lista por dispositivo)
        n_nvme = max((len(s.get("nvme_temps_c") or []) for s in self._samples), default=0)
        for i in range(n_nvme):
            vals = [s["nvme_temps_c"][i] for s in self._samples
                    if s.get("nvme_temps_c") and len(s["nvme_temps_c"]) > i
                    and s["nvme_temps_c"][i] is not None]
            if vals:
                agg[f"nvme{i+1}_temp_avg_c"] = round(statistics.mean(vals), 2)
                agg[f"nvme{i+1}_temp_max_c"] = round(max(vals), 2)

        # RAM temps
        n_ram = max((len(s.get("ram_temps_c") or []) for s in self._samples), default=0)
        for i in range(n_ram):
            vals = [s["ram_temps_c"][i] for s in self._samples
                    if s.get("ram_temps_c") and len(s["ram_temps_c"]) > i
                    and s["ram_temps_c"][i] is not None]
            if vals:
                agg[f"ram{i+1}_temp_avg_c"] = round(statistics.mean(vals), 2)
                agg[f"ram{i+1}_temp_max_c"] = round(max(vals), 2)

        # RAPL energy delta (J)
        rapl_vals = [s["rapl_energy_uj"] for s in self._samples
                     if s.get("rapl_energy_uj") is not None]
        if len(rapl_vals) >= 2 and _RAPL_MAX_UJ:
            first, last = rapl_vals[0], rapl_vals[-1]
            delta_uj = (last - first) if last >= first else (_RAPL_MAX_UJ - first + last)
            t_first  = self._samples[0]["timestamp_s"]
            t_last   = self._samples[-1]["timestamp_s"]
            duration = max(t_last - t_first, 0.001)
            agg["rapl_energy_total_j"] = round(delta_uj / 1e6, 3)
            agg["rapl_avg_power_w"]    = round(delta_uj / duration / 1e6, 2)

        # Duração total
        if len(self._samples) >= 2:
            agg["duration_s"] = round(
                self._samples[-1]["timestamp_s"] - self._samples[0]["timestamp_s"], 1
            )
        agg["n_samples"] = len(self._samples)

        return agg
