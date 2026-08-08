"""
Gerenciamento do ciclo de vida de containers PostgreSQL para benchmarks.

Sobe um container com recursos (CPU, RAM, SHM) e parâmetros PostgreSQL
exatos de uma tarefa da fila, aguarda o banco estar pronto e o retorna
para uso pelo executor de benchmark.

Uso
---
    from benchmarks.container import start_postgres_container, remove_postgres_container

    container = start_postgres_container(
        tier_config    = {"cpu": 4, "memory_mb": 4096, "memory_swap_mb": 4096, "shm_size_mb": 1152},
        pg_config      = {"jit": 0, "shared_buffers": "1GB", "work_mem": "32MB"},
        db_name        = "tpch",
        image          = "tpch-postgres:sf2",
        container_name = "bench_task_42",
    )

    # Após o benchmark
    remove_postgres_container(container)
"""

import time

import docker
import docker.errors
from docker.models.containers import Container

POSTGRES_USER = "postgres"
POSTGRES_PASS = "postgres"
POSTGRES_PORT = 5432

# Parâmetros que o PostgreSQL aceita como on/off mas chegam como 0/1
_BOOL_PARAMS = {"jit", "enable_indexscan", "enable_hashjoin"}

# Padrões nos logs do PostgreSQL que indicam rejeição de parâmetros de configuração.
# Quando encontrados, a falha é determinística: repetir não vai ajudar.
_INVALID_CONFIG_PATTERNS = [
    "invalid value for parameter",
    "unrecognized configuration parameter",
    "out of range",
    "invalid configuration",
]


class InvalidConfigError(Exception):
    """PostgreSQL rejeitou um ou mais parâmetros de configuração.

    Falha determinística — nenhum retry vai resolver. A tarefa deve ser
    marcada como ``abandoned`` imediatamente sem consumir mais tentativas.
    """


def _is_invalid_pg_config(logs: str) -> bool:
    """Verifica se os logs do container indicam rejeição de parâmetro pelo PostgreSQL."""
    lower = logs.lower()
    return any(pat in lower for pat in _INVALID_CONFIG_PATTERNS)


def _build_postgres_args(pg_config: dict) -> list[str]:
    """Converte o dict de config PostgreSQL em flags ``-c param=valor``.

    Args:
        pg_config: Dict ``{nome_do_param: valor}`` gerado pelo config_gen.

    Returns:
        Lista de argumentos de linha de comando para o processo postgres.
    """
    args = ["postgres"]
    for key, value in pg_config.items():
        if key in _BOOL_PARAMS and isinstance(value, int):
            value = "on" if value else "off"
        args += ["-c", f"{key}={value}"]
    return args


def start_postgres_container(
    tier_config: dict,
    pg_config: dict,
    db_name: str,
    image: str = "postgres:17",
    container_name: str | None = None,
    host_port: int | None = None,
    max_wait_s: int = 120,
    log_fn=None,
) -> Container:
    """Sobe um container PostgreSQL com os recursos e parâmetros especificados.

    Aplica limites de CPU, memória e SHM conforme a especificação do tier,
    injeta os parâmetros PostgreSQL como flags ``-c`` e aguarda o banco
    estar pronto para aceitar conexões antes de retornar.

    Args:
        tier_config:    Especificação de recursos do container:
                          cpu            — número de vCPUs
                          memory_mb      — RAM em MB
                          memory_swap_mb — swap em MB
                          shm_size_mb    — tamanho de /dev/shm em MB
        pg_config:      Parâmetros PostgreSQL (saída do config_gen).
        db_name:        Nome do banco de dados PostgreSQL ("tpch" ou "tpcds").
        image:          Imagem Docker a usar (deve ter os dados pré-carregados).
        container_name: Nome do container. Gerado automaticamente se None.
        host_port:      Porta do host mapeada para 5432. Sem mapeamento se None.
        max_wait_s:     Tempo máximo de espera pelo PostgreSQL em segundos. Padrão: 120.
        log_fn:         Callable ``(msg: str) -> None`` para log de progresso. Opcional.

    Returns:
        Container Docker em execução com PostgreSQL pronto para receber queries.

    Raises:
        InvalidConfigError:          Se o PostgreSQL rejeitar um parâmetro de configuração.
        TimeoutError:                Se o PostgreSQL não ficar pronto dentro de ``max_wait_s``.
        RuntimeError:                Se o container encerrar inesperadamente.
        docker.errors.ImageNotFound: Se a imagem não estiver disponível localmente.
        docker.errors.APIError:      Se o Docker daemon retornar erro.
    """
    client = docker.from_env()

    memory_mb  = tier_config["memory_mb"]
    swap_mb    = tier_config["memory_swap_mb"]
    shm_mb     = tier_config["shm_size_mb"]
    n_cpus     = tier_config["cpu"]

    ports = {f"{POSTGRES_PORT}/tcp": host_port} if host_port else {}

    try:
        container: Container = client.containers.run(
            image    = image,
            command  = _build_postgres_args(pg_config),
            name     = container_name,
            detach   = True,
            remove   = False,

            # Limites de recursos do tier
            nano_cpus     = int(n_cpus * 1_000_000_000),
            mem_limit     = f"{memory_mb}m",
            memswap_limit = f"{swap_mb}m",
            shm_size      = f"{shm_mb}m",

            ports = ports,

            environment = {
                "POSTGRES_USER":     POSTGRES_USER,
                "POSTGRES_PASSWORD": POSTGRES_PASS,
                "POSTGRES_DB":       db_name,
            },
        )
    finally:
        client.close()

    try:
        _wait_postgres_ready(container, db_name, max_wait_s=max_wait_s, log_fn=log_fn)
    except Exception:
        remove_postgres_container(container)
        raise
    return container


def _wait_postgres_ready(
    container: Container,
    db_name: str,
    max_wait_s: int = 120,
    log_fn=None,
) -> None:
    """Aguarda o PostgreSQL aceitar conexões dentro do container.

    Realiza polling com pg_isready até o banco estar pronto, com timeout
    e verificação do status do container a cada iteração.

    Args:
        container:   Container em execução.
        db_name:     Nome do banco de dados a verificar.
        max_wait_s:  Tempo máximo de espera em segundos. Padrão: 120.
        log_fn:      Callable ``(msg: str) -> None`` para log de progresso. Opcional.

    Raises:
        RuntimeError: Se o container sair inesperadamente antes de estar pronto.
        TimeoutError: Se o PostgreSQL não ficar pronto dentro de ``max_wait_s``.
    """
    deadline    = time.monotonic() + max_wait_s
    t0          = time.monotonic()
    last_log_at = t0

    while time.monotonic() < deadline:
        # Verifica se o container ainda está vivo
        try:
            container.reload()
        except docker.errors.NotFound:
            raise RuntimeError(
                f"Container '{container.name}' desapareceu inesperadamente."
            )
        if container.status not in ("running", "created"):
            logs = container.logs(tail=20).decode(errors="replace")
            if _is_invalid_pg_config(logs):
                raise InvalidConfigError(
                    f"Container '{container.name}': PostgreSQL rejeitou parâmetro de configuração "
                    f"(status='{container.status}').\nLog:\n{logs}"
                )
            raise RuntimeError(
                f"Container '{container.name}' terminou com status='{container.status}'.\n"
                f"Últimas linhas do log:\n{logs}"
            )

        # Testa pg_isready
        try:
            result = container.exec_run(
                f"pg_isready -h localhost -U {POSTGRES_USER} -d {db_name}",
                demux=False,
            )
            if result.exit_code == 0:
                return
        except docker.errors.APIError:
            # 409 Conflict: container ainda não chegou ao estado "running"
            pass

        # Log periódico de progresso (a cada 10s)
        now = time.monotonic()
        if log_fn and now - last_log_at >= 10:
            log_fn(f"aguardando PostgreSQL... {now - t0:.0f}s / {max_wait_s}s")
            last_log_at = now

        time.sleep(1)

    logs = container.logs(tail=10).decode(errors="replace")
    raise TimeoutError(
        f"PostgreSQL não ficou pronto em {max_wait_s}s "
        f"(container={container.name}).\n"
        f"Últimas linhas do log:\n{logs}"
    )


def remove_postgres_container(container: Container) -> None:
    """Remove completamente um container PostgreSQL e seus volumes anônimos.

    Força a remoção independente do estado atual (running, parado, pausado).
    Ignora silenciosamente se o container já não existir.

    Args:
        container: Container retornado por ``start_postgres_container()``.
    """
    try:
        container.remove(force=True, v=True)
    except docker.errors.NotFound:
        pass
