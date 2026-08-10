"""
Monitoramento de disco e limpeza automática do Docker/containerd.

O acúmulo de snapshots, camadas e cache do containerd é o principal
responsável pelo crescimento silencioso do disco em workloads que criam
e destroem containers repetidamente (como benchmarks TPC-H/TPC-DS).

Funções públicas
----------------
    disk_free_gb(path)
        Espaço livre no sistema de arquivos que contém `path`.

    docker_used_gb()
        Espaço ocupado reportado por `docker system df` (imagens + containers
        + volumes + build cache).

    prune_docker(aggressive, verbose)
        Executa limpeza do Docker em dois níveis:
            - normal     → remove containers parados + camadas dangling + build cache
            - aggressive → remove também imagens não usadas ativamente (sem container vivo)

    auto_prune_if_needed(free_threshold_gb, critical_threshold_gb, verbose)
        Verifica espaço livre e chama `prune_docker` se necessário:
            - < critical_threshold_gb → prune agressivo
            - < free_threshold_gb     → prune normal
        Retorna True se alguma limpeza foi realizada.

Constante
---------
    PRUNE_EVERY_N_TASKS : int
        Runner chama auto_prune_if_needed após cada múltiplo desse valor.
"""

import shutil
import subprocess
from pathlib import Path

import docker as _docker

from utils.logging import log

# Após quantas tarefas o runner executa checagem periódica de disco
PRUNE_EVERY_N_TASKS: int = 5

# Thresholds padrão (GB)
_DEFAULT_FREE_WARN_GB     = 20   # prune normal se livre < 20 GB
_DEFAULT_FREE_CRITICAL_GB = 10   # prune agressivo se livre < 10 GB


# ---------------------------------------------------------------------------
# Espaço em disco
# ---------------------------------------------------------------------------

def disk_free_gb(path: Path | str = "/") -> float:
    """Retorna espaço livre (GB) no sistema de arquivos que contém `path`."""
    return shutil.disk_usage(str(path)).free / (1024 ** 3)


def docker_used_gb() -> dict[str, float]:
    """Consulta `docker system df` e retorna uso por categoria (GB).

    Returns:
        Dict com chaves: images, containers, volumes, build_cache, total
        Retorna zeros em caso de falha.
    """
    try:
        result = subprocess.run(
            ["docker", "system", "df", "--format", "{{.Type}}\t{{.Size}}"],
            capture_output=True, text=True, timeout=15,
        )
        usage = {"images": 0.0, "containers": 0.0, "volumes": 0.0, "build_cache": 0.0}
        type_map = {
            "Images":      "images",
            "Containers":  "containers",
            "Local Volumes": "volumes",
            "Build Cache": "build_cache",
        }
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) == 2:
                key = type_map.get(parts[0].strip())
                if key:
                    usage[key] = _parse_size_gb(parts[1].strip())
        usage["total"] = sum(usage.values())
        return usage
    except Exception:
        return {"images": 0.0, "containers": 0.0, "volumes": 0.0,
                "build_cache": 0.0, "total": 0.0}


def _parse_size_gb(size_str: str) -> float:
    """Converte string de tamanho Docker (ex: '1.5GB', '500MB') para GB."""
    s = size_str.upper().replace(" ", "")
    try:
        if s.endswith("GB"):
            return float(s[:-2])
        if s.endswith("MB"):
            return float(s[:-2]) / 1024
        if s.endswith("KB"):
            return float(s[:-2]) / (1024 ** 2)
        if s.endswith("TB"):
            return float(s[:-2]) * 1024
        if s.endswith("B"):
            return float(s[:-1]) / (1024 ** 3)
        return float(s) / (1024 ** 3)
    except (ValueError, AttributeError):
        return 0.0


# ---------------------------------------------------------------------------
# Limpeza Docker
# ---------------------------------------------------------------------------

# Prefixos de containers do projeto (nunca removidos em modo agressivo)
_PROJECT_CONTAINER_PREFIXES = (
    "tpch_bench_", "tpch_conntest_",
    "tpcds_bench_", "tpcds_conntest_",
)

# Imagens essenciais do projeto (nunca removidas em modo agressivo)
_PROJECT_IMAGE_PREFIXES = (
    "tpch-postgres",
    "tpcds-postgres",
    "postgres",
    "debian",
)


def prune_docker(aggressive: bool = False, verbose: bool = True) -> dict[str, float]:
    """Executa limpeza do Docker.

    Args:
        aggressive: Se True, remove também imagens sem container ativo
                    (exceto as imagens essenciais do projeto).
        verbose:    Se True, loga progresso via utils.logging.log.

    Returns:
        Dict com espaço recuperado por categoria (GB):
            containers, build_cache, dangling_images, unused_images, total
    """
    reclaimed = {"containers": 0.0, "build_cache": 0.0,
                 "dangling_images": 0.0, "unused_images": 0.0, "total": 0.0}

    try:
        client = _docker.from_env(timeout=30)
    except Exception as exc:
        if verbose:
            log(f"[docker-cleanup] Docker inacessível: {exc}", level="WARN")
        return reclaimed

    try:
        return _prune_with_client(client, aggressive, verbose, reclaimed)
    finally:
        client.close()


def _prune_with_client(
    client,
    aggressive: bool,
    verbose: bool,
    reclaimed: dict,
) -> dict:
    """Executa as operações de prune usando um client já aberto."""
    # 1. Remove containers parados não relacionados ao projeto
    try:
        stopped = [
            c for c in client.containers.list(all=True)
            if c.status in ("exited", "dead", "created")
            and not any(c.name.startswith(p) for p in _PROJECT_CONTAINER_PREFIXES)
        ]
        for c in stopped:
            try:
                c.remove(force=True)
            except Exception:
                pass
        if stopped and verbose:
            log(f"[docker-cleanup] {len(stopped)} container(s) parado(s) removido(s)",
                level="DIM")
    except Exception:
        pass

    # 2. Remove camadas dangling (layers sem tag nem container)
    try:
        dangling = client.images.list(filters={"dangling": True})
        removed_bytes = 0
        for img in dangling:
            try:
                size = img.attrs.get("Size", 0)
                client.images.remove(img.id, force=True)
                removed_bytes += size
            except Exception:
                pass
        reclaimed["dangling_images"] = removed_bytes / (1024 ** 3)
        if dangling and verbose:
            log(f"[docker-cleanup] {len(dangling)} imagem(ns) dangling removida(s) "
                f"({reclaimed['dangling_images']:.2f} GB)", level="DIM")
    except Exception:
        pass

    # 3. Prune de build cache
    try:
        result = subprocess.run(
            ["docker", "builder", "prune", "-f"],
            capture_output=True, text=True, timeout=60,
        )
        # Extrai "Total reclaimed space: X.XXX GB" da saída
        for line in result.stdout.splitlines():
            if "reclaimed" in line.lower():
                parts = line.split(":")
                if len(parts) == 2:
                    reclaimed["build_cache"] = _parse_size_gb(parts[1].strip())
        if reclaimed["build_cache"] > 0 and verbose:
            log(f"[docker-cleanup] Build cache: {reclaimed['build_cache']:.2f} GB liberados",
                level="DIM")
    except Exception:
        pass

    # 4. Modo agressivo: remove imagens não essenciais sem container ativo
    if aggressive:
        try:
            active_image_ids = {
                c.image.id for c in client.containers.list(all=False)
            }
            all_images = client.images.list()
            removed_bytes = 0
            removed_count = 0
            for img in all_images:
                if img.id in active_image_ids:
                    continue
                tags = img.tags or []
                if any(
                    any(tag.startswith(pfx) for pfx in _PROJECT_IMAGE_PREFIXES)
                    for tag in tags
                ):
                    continue
                if not tags:
                    # Imagem sem tag não essencial: remove
                    try:
                        size = img.attrs.get("Size", 0)
                        client.images.remove(img.id, force=True)
                        removed_bytes += size
                        removed_count += 1
                    except Exception:
                        pass
            reclaimed["unused_images"] = removed_bytes / (1024 ** 3)
            if removed_count > 0 and verbose:
                log(f"[docker-cleanup] {removed_count} imagem(ns) sem uso removida(s) "
                    f"({reclaimed['unused_images']:.2f} GB)", level="DIM")
        except Exception:
            pass

    reclaimed["total"] = sum(v for k, v in reclaimed.items() if k != "total")
    return reclaimed


# ---------------------------------------------------------------------------
# Verificação e limpeza automática
# ---------------------------------------------------------------------------

def auto_prune_if_needed(
    free_threshold_gb:     float = _DEFAULT_FREE_WARN_GB,
    critical_threshold_gb: float = _DEFAULT_FREE_CRITICAL_GB,
    path:                  Path | str = "/",
    verbose:               bool = True,
) -> bool:
    """Verifica espaço livre e executa limpeza se necessário.

    Args:
        free_threshold_gb:     Prune normal se espaço livre < este valor (GB).
        critical_threshold_gb: Prune agressivo se espaço livre < este valor (GB).
        path:                  Diretório para medir espaço livre.
        verbose:               Se True, loga ações via utils.logging.log.

    Returns:
        True se alguma limpeza foi executada.
    """
    free = disk_free_gb(path)

    if free >= free_threshold_gb:
        return False  # Espaço OK, nada a fazer

    aggressive = free < critical_threshold_gb
    mode       = "AGRESSIVO" if aggressive else "normal"

    if verbose:
        log(
            f"[docker-cleanup] Espaço livre: {free:.1f} GB "
            f"(limiar: {critical_threshold_gb if aggressive else free_threshold_gb:.0f} GB) "
            f"- iniciando prune {mode}...",
            level="WARN",
        )

    before = disk_free_gb(path)
    reclaimed = prune_docker(aggressive=aggressive, verbose=verbose)
    after  = disk_free_gb(path)
    gained = after - before

    if verbose and reclaimed["total"] > 0:
        log(
            f"[docker-cleanup] Prune concluído: "
            f"{gained:.1f} GB liberados | livre agora: {after:.1f} GB",
            level="OK",
        )
    elif verbose:
        log(
            f"[docker-cleanup] Prune sem efeito: livre: {after:.1f} GB",
            level="DIM",
        )

    return True
