"""
Construção das imagens Docker com dados de benchmark pré-carregados.

O processo de construção de cada imagem é em 4 passos:
  1. docker build  → imagem intermediária com dbgen/dsdgen compilado + init script
  2. docker run    → container inicializa, gera e carrega os dados no PostgreSQL
  3. docker commit → imagem final com dados prontos dentro do PGDATA
  4. docker rm     → remove o container temporário

A imagem final pode ser usada repetidamente: cada container de benchmark
sobe com os dados já carregados, sem precisar gerá-los novamente.

Cada tier usa um scale factor diferente para que o dataset sempre exceda
o shared_buffers típico do tier, tornando o benchmark discriminativo:

    LOW    (2 GB RAM) → SF=1 (~1 GB dados)
    MEDIUM (4 GB RAM) → SF=2 (~2 GB dados)
    HIGH   (6 GB RAM) → SF=4 (~4 GB dados)

Funções
-------
    build_image(benchmark, scale_factor, image_tag)
        Constrói uma imagem para TPC-H ou TPC-DS.

    image_exists(image_tag)
        Verifica se uma imagem já existe localmente.

    TIER_IMAGE_TAGS[benchmark][tier]
        Tags de imagem por benchmark e tier.
"""

import time
from pathlib import Path

import docker
import docker.errors
from docker.models.images import Image

# Diretório raiz de cada benchmark (onde ficam o Dockerfile e os scripts)
_BENCHMARK_DIRS: dict[str, Path] = {
    "tpch":  Path(__file__).parent / "tpc_h",
    "tpcds": Path(__file__).parent / "tpc_ds",
}

POSTGRES_USER = "postgres"
POSTGRES_PASS = "postgres"

# Scale factors por tier (aplicados a ambos os benchmarks)
TIER_SCALE_FACTORS: dict[str, float] = {
    "low":    1,
    "medium": 2,
    "high":   4,
}

# Tags de imagem finais por benchmark e tier
TIER_IMAGE_TAGS: dict[str, dict[str, str]] = {
    "tpch": {
        "low":    "tpch-postgres:sf1",
        "medium": "tpch-postgres:sf2",
        "high":   "tpch-postgres:sf4",
    },
    "tpcds": {
        "low":    "tpcds-postgres:sf1",
        "medium": "tpcds-postgres:sf2",
        "high":   "tpcds-postgres:sf4",
    },
}

# Parâmetro de escala no build arg / env var de cada benchmark
_SCALE_ARG: dict[str, str] = {
    "tpch":  "TPCH_SCALE",
    "tpcds": "TPCDS_SCALE",
}

# Mensagem que o init script escreve no log ao concluir
_INIT_DONE_MSG: dict[str, str] = {
    "tpch":  "tpch] Setup concluído",
    "tpcds": "tpcds] Setup concluído",
}

# Timeouts de init: base (s) + por unidade de scale factor
# TPC-DS gera mais dados que TPC-H no mesmo SF (24 tabelas vs 8)
_TIMEOUT_BASE: dict[str, int]    = {"tpch": 900,  "tpcds": 1200}
_TIMEOUT_PER_SF: dict[str, int]  = {"tpch": 600,  "tpcds": 900}


def build_image(
    benchmark: str,
    scale_factor: float = 1,
    image_tag: str | None = None,
) -> Image:
    """Constrói uma imagem Docker com dados de benchmark pré-carregados.

    Args:
        benchmark:    "tpch" ou "tpcds".
        scale_factor: Fator de escala (1 ≈ 1 GB, 2 ≈ 2 GB, 4 ≈ 4 GB).
        image_tag:    Tag da imagem final. Se None, usa o padrão do tier.

    Returns:
        Objeto Image da imagem construída.

    Raises:
        ValueError: Se benchmark não for "tpch" ou "tpcds".
    """
    if benchmark not in ("tpch", "tpcds"):
        raise ValueError(f"benchmark deve ser 'tpch' ou 'tpcds'; recebido: {benchmark!r}")

    bench_dir  = _BENCHMARK_DIRS[benchmark]
    scale_arg  = _SCALE_ARG[benchmark]
    db_name    = benchmark   # "tpch" ou "tpcds"
    done_msg   = _INIT_DONE_MSG[benchmark]
    client     = docker.from_env(timeout=None)

    if image_tag is None:
        sf_int    = int(scale_factor)
        image_tag = f"{benchmark}-postgres:sf{sf_int}"

    try:
        print(f"[1/3] Construindo imagem base ({benchmark}, scale={scale_factor})...")
        base_tag = f"{benchmark}-builder:sf{scale_factor}"
        client.images.build(
            path      = str(bench_dir),
            tag       = base_tag,
            buildargs = {scale_arg: str(scale_factor)},
            rm        = True,
        )

        print("[2/3] Inicializando dados (pode levar vários minutos)...")
        tmp_name = f"{benchmark}-build-tmp-sf{scale_factor}"
        try:
            stale = client.containers.get(tmp_name)
            print(f"    Container '{tmp_name}' já existe (execução anterior falhou). Removendo...")
            stale.remove(force=True)
        except docker.errors.NotFound:
            pass

        container = client.containers.run(
            image       = base_tag,
            name        = tmp_name,
            detach      = True,
            remove      = False,
            environment = {
                "POSTGRES_USER":     POSTGRES_USER,
                "POSTGRES_PASSWORD": POSTGRES_PASS,
                "POSTGRES_DB":       db_name,
                scale_arg:           str(scale_factor),
            },
        )

        _wait_init_complete(container, scale_factor, benchmark, done_msg)

        print(f"[3/3] Commitando imagem '{image_tag}'...")
        image = container.commit(
            repository = image_tag.split(":")[0],
            tag        = image_tag.split(":")[-1],
        )

        container.stop()
        container.remove()
    finally:
        client.close()

    print(f"Imagem '{image_tag}' pronta. ID: {image.short_id}")
    return image



def image_exists(image_tag: str) -> bool:
    """Verifica se uma imagem já foi construída e existe localmente.

    Args:
        image_tag: Tag da imagem a verificar.

    Returns:
        True se a imagem existir localmente, False caso contrário.
    """
    client = docker.from_env()
    try:
        client.images.get(image_tag)
        return True
    except docker.errors.ImageNotFound:
        return False
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _wait_init_complete(
    container,
    scale_factor: float,
    benchmark: str,
    done_msg: str,
    timeout: int = 0,
) -> None:
    """Aguarda o init script concluir a geração e carga dos dados.

    O timeout é calculado automaticamente com base no benchmark e SF.
    Pode ser sobrescrito passando timeout > 0.

    Args:
        container:    Container em execução.
        scale_factor: Scale factor do benchmark.
        benchmark:    "tpch" ou "tpcds" (determina os timeouts base).
        done_msg:     Substring que aparece no log quando o init conclui.
        timeout:      Timeout em segundos (0 = automático).
    """
    if timeout <= 0:
        timeout = int(_TIMEOUT_BASE[benchmark] + _TIMEOUT_PER_SF[benchmark] * scale_factor)

    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        container.reload()
        if container.status != "running":
            break
        logs = container.logs(tail=500).decode()
        if done_msg in logs:
            return
        time.sleep(3)

    container.reload()
    if container.status == "exited" and container.attrs["State"]["ExitCode"] == 0:
        return

    raise TimeoutError(
        f"Init {benchmark.upper()} não concluiu em {timeout}s (SF={scale_factor}). "
        f"Logs:\n{container.logs(tail=20).decode()}"
    )
