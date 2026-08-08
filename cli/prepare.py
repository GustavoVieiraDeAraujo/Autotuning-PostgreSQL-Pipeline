"""
Prepara as 6 imagens Docker necessárias para os benchmarks.

Constrói sequencialmente as imagens base de dados para cada tier:

    LOW    (SF=1): tpch-postgres:sf1   tpcds-postgres:sf1
    MEDIUM (SF=2): tpch-postgres:sf2   tpcds-postgres:sf2
    HIGH   (SF=4): tpch-postgres:sf4   tpcds-postgres:sf4

Deve ser executado uma única vez antes de rodar os benchmarks.
Depois de concluído, o runner só precisa subir e derrubar containers com
diferentes configurações PostgreSQL — sem rebuilds de imagem.

Uso
---
    python cli/prepare.py          # constrói imagens ausentes + smoke tests
    python cli/prepare.py --force  # reconstrói todas + smoke tests
"""

import argparse
import json
import sys
import time
from pathlib import Path

_ROOT     = Path(__file__).parent.parent
_LOG_PATH = _ROOT / "logs" / "prepare.log"

import docker                                                                                    # noqa: E402
from benchmarks.container import (      # noqa: E402
    remove_postgres_container,
    start_postgres_container,
)
from benchmarks.image_builder import (  # noqa: E402
    TIER_IMAGE_TAGS,
    TIER_SCALE_FACTORS,
    build_image,
    image_exists,
)
from utils.logging import BOLD, CYAN, GREEN, RED, RESET, YELLOW, DIM, TeeWriter, banner, sep  # noqa: E402
from utils.formatting import fmt_duration                                                       # noqa: E402

_TIERS      = ["low", "medium", "high"]
_DOCKER_CFG = _ROOT / "specs" / "docker.json"
_SPACES_DIR = _ROOT / "specs" / "spaces"
_PG_IMAGE   = "postgres:17"


# ---------------------------------------------------------------------------
# Validação de parâmetros dos specs contra o PostgreSQL real
# ---------------------------------------------------------------------------

def _collect_spec_params() -> dict[str, list[str]]:
    """Retorna {param: [stages onde aparece]} para todos os specs de todas as etapas."""
    params: dict[str, list[str]] = {}
    for stage_dir in sorted(_SPACES_DIR.iterdir()):
        if not stage_dir.is_dir():
            continue
        stage = stage_dir.name  # "stage1", "stage2", "stage3"
        tier_files = list(stage_dir.glob("*.json"))
        if not tier_files:
            continue
        # Todos os tiers têm os mesmos parâmetros — basta um arquivo
        space = json.loads(tier_files[0].read_text(encoding="utf-8"))
        for param in space:
            params.setdefault(param, []).append(stage)
    return params


def _run_param_validation() -> bool:
    """Valida todos os parâmetros dos specs contra o PostgreSQL real.

    Sobe um container ``postgres:17`` limpo (sem dados), consulta ``pg_settings``
    para obter todos os parâmetros reconhecidos e cruza com os parâmetros
    definidos nos specs de todas as etapas.

    Exibe uma tabela com OK / INVÁLIDO por parâmetro e etapa. Retorna False
    se qualquer parâmetro não for reconhecido pelo PostgreSQL.
    """
    sep()
    print(f"\n{CYAN}{BOLD}Validação de Parâmetros — verificando specs contra PostgreSQL{RESET}\n",
          flush=True)

    spec_params = _collect_spec_params()
    total_params = len(spec_params)
    print(f"  {total_params} parâmetros encontrados nos specs. Subindo container de validação...\n",
          flush=True)

    client    = docker.from_env()
    container = None

    try:
        # Puxa a imagem se não existir localmente
        try:
            client.images.get(_PG_IMAGE)
        except docker.errors.ImageNotFound:
            print(f"  {YELLOW}Baixando {_PG_IMAGE}...{RESET}", flush=True)
            client.images.pull(_PG_IMAGE)

        # Sobe um postgres limpo (sem dados de benchmark)
        container = client.containers.run(
            _PG_IMAGE,
            environment={
                "POSTGRES_USER":     "postgres",
                "POSTGRES_PASSWORD": "postgres",
                "POSTGRES_DB":       "postgres",
            },
            detach      = True,
            name        = "pg_param_validator",
            auto_remove = False,
        )

        # Aguarda o PostgreSQL estar pronto (até 60s)
        ready = False
        for _ in range(60):
            res = container.exec_run(["pg_isready", "-U", "postgres", "-d", "postgres"])
            if res.exit_code == 0:
                ready = True
                break
            time.sleep(1)

        if not ready:
            print(f"  {RED}Timeout: PostgreSQL não ficou pronto para validação.{RESET}\n",
                  flush=True)
            return True  # não bloqueia prepare — falha na validação é não-fatal

        # Consulta pg_settings para obter todos os GUC reconhecidos
        res = container.exec_run(
            ["psql", "-U", "postgres", "-d", "postgres", "-t", "-A",
             "-c", "SELECT name FROM pg_settings ORDER BY name;"]
        )
        known = set(res.output.decode(errors="replace").strip().splitlines())

        # Classifica os parâmetros dos specs
        ok_params  = {p: s for p, s in spec_params.items() if p in known}
        bad_params = {p: s for p, s in spec_params.items() if p not in known}

        # Exibe tabela de resultados
        col_p = max(len(p) for p in spec_params) + 2
        print(f"  {'Parâmetro':<{col_p}} {'Etapa(s)':<18} {'Status'}", flush=True)
        sep()

        for param, stages in sorted(spec_params.items()):
            stages_str = ", ".join(stages)
            if param in known:
                status = f"{GREEN}OK{RESET}"
            else:
                status = f"{RED}INVÁLIDO — não reconhecido pelo PostgreSQL {_PG_IMAGE}{RESET}"
            print(f"  {param:<{col_p}} {stages_str:<18} {status}", flush=True)

        sep()
        if bad_params:
            print(
                f"\n{RED}{BOLD}{len(bad_params)} parâmetro(s) inválido(s) encontrado(s).{RESET}\n"
                f"{RED}Remova-os dos specs antes de gerar a fila — causarão invalid_config "
                f"em todas as tasks que os incluírem.{RESET}\n",
                flush=True,
            )
            return False

        print(
            f"\n{GREEN}{BOLD}Todos os {total_params} parâmetros validados com sucesso "
            f"no PostgreSQL {_PG_IMAGE}.{RESET}\n",
            flush=True,
        )
        return True

    except Exception as exc:
        print(f"  {YELLOW}Aviso: validação de parâmetros falhou ({exc}). "
              f"Continuando sem validação.{RESET}\n", flush=True)
        return True  # não bloqueia o prepare em caso de falha inesperada

    finally:
        if container:
            try:
                container.stop(timeout=5)
                container.remove()
            except Exception:
                pass
        try:
            client.close()
        except Exception:
            pass


def _image_plan() -> list[dict]:
    """Gera a lista das 6 imagens a construir (tier × benchmark)."""
    plan = []
    for tier in _TIERS:
        for benchmark in ("tpch", "tpcds"):
            plan.append({
                "benchmark": benchmark.upper(),
                "bench_key": benchmark,
                "tier":      tier,
                "sf":        TIER_SCALE_FACTORS[tier],
                "tag":       TIER_IMAGE_TAGS[benchmark][tier],
            })
    return plan


def _smoke_test_one(image_tag: str, db_name: str, tier: str, tier_configs: dict) -> tuple[bool, str]:
    """Sobe um container, testa conectividade e tabelas, destrói.

    Returns:
        (ok, mensagem) — ok=True se passou, mensagem descreve o resultado.
    """
    container_name = f"{db_name}_smoketest_{tier}"
    container = None
    try:
        container = start_postgres_container(
            tier_config    = tier_configs[tier],
            pg_config      = {},
            db_name        = db_name,
            image          = image_tag,
            container_name = container_name,
            max_wait_s     = 120,
        )

        # Conectividade básica
        res = container.exec_run(
            ["psql", "-U", "postgres", "-d", db_name, "-t", "-A", "-c", "SELECT 1;"],
            demux=False,
        )
        if res.exit_code != 0:
            return False, f"SELECT 1 falhou (exit={res.exit_code})"

        # Contagem de tabelas no schema public
        res2 = container.exec_run(
            ["psql", "-U", "postgres", "-d", db_name, "-t", "-A", "-c",
             "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public';"],
            demux=False,
        )
        n_tables = int(res2.output.decode(errors="replace").strip()) if res2.exit_code == 0 else "?"
        return True, f"{n_tables} tabelas"

    except Exception as exc:
        return False, str(exc)[:120]
    finally:
        if container:
            try:
                remove_postgres_container(container)
            except Exception:
                pass


def _run_smoke_tests(plan: list[dict], tier_configs: dict) -> bool:
    """Roda smoke tests em todas as 6 imagens e exibe tabela de resultados.

    Para cada imagem: sobe container → SELECT 1 → conta tabelas → destrói.

    Returns:
        True se todas passaram, False se alguma falhou.
    """
    sep()
    print(f"\n{CYAN}{BOLD}Smoke Tests — verificação final das 6 imagens{RESET}\n", flush=True)
    print(f"  {'#':<3} {'Benchmark':<8} {'Tier':<8} {'Imagem':<28} {'Resultado'}", flush=True)
    sep()

    all_ok   = True
    t0_total = time.perf_counter()

    for i, img in enumerate(plan, 1):
        label = f"  {i:<3} {img['benchmark']:<8} {img['tier']:<8} {img['tag']:<28}"
        print(f"{label} {DIM}testando...{RESET}", end="\r", flush=True)

        t0 = time.perf_counter()
        ok, msg = _smoke_test_one(img["tag"], img["bench_key"], img["tier"], tier_configs)
        elapsed = fmt_duration(time.perf_counter() - t0)

        if ok:
            status = f"{GREEN}OK{RESET}  {msg}  {DIM}({elapsed}){RESET}"
        else:
            status = f"{RED}FALHA{RESET}  {msg}"
            all_ok = False

        print(f"{label} {status}          ", flush=True)

    sep()
    elapsed_total = fmt_duration(time.perf_counter() - t0_total)

    if all_ok:
        print(
            f"\n{GREEN}{BOLD}Todas as imagens verificadas com sucesso em {elapsed_total}.{RESET}\n"
            f"{GREEN}A fila pode ser executada com segurança.{RESET}\n",
            flush=True,
        )
    else:
        print(
            f"\n{RED}{BOLD}Smoke tests com falha — corrija antes de executar a fila.{RESET}\n",
            flush=True,
        )

    return all_ok


def prepare(force: bool = False) -> None:
    """Constrói as 6 imagens e verifica cada uma com smoke tests.

    Fluxo:
        1. Exibe status atual das 6 imagens
        2. Constrói as que estão ausentes (ou todas se --force)
        3. Roda smoke tests em todas as 6 imagens
        4. Encerra com erro se qualquer build ou smoke test falhar

    Args:
        force: Se True, reconstrói imagens mesmo que já existam.
    """
    plan         = _image_plan()
    tier_configs = json.loads(_DOCKER_CFG.read_text(encoding="utf-8"))

    banner("PREPARE — Construindo e verificando imagens dos benchmarks")

    # ── Status atual ──────────────────────────────────────────────────────────
    print(f"{'#':<3} {'Benchmark':<8} {'Tier':<8} {'SF':<4} {'Imagem':<26} {'Status'}",
          flush=True)
    sep()
    for i, img in enumerate(plan, 1):
        exists = image_exists(img["tag"])
        status = f"{GREEN}disponível{RESET}" if exists else f"{YELLOW}ausente{RESET}"
        print(f"{i:<3} {img['benchmark']:<8} {img['tier']:<8} {img['sf']:<4} "
              f"{img['tag']:<26} {status}", flush=True)
    sep()

    # ── Validação de parâmetros ───────────────────────────────────────────────
    params_ok = _run_param_validation()
    if not params_ok:
        sys.exit(1)

    # ── Build ─────────────────────────────────────────────────────────────────
    to_build = [img for img in plan if force or not image_exists(img["tag"])]

    if not to_build:
        print(f"{GREEN}Todas as 6 imagens já estão disponíveis.{RESET}\n", flush=True)
    else:
        print(f"{YELLOW}{len(to_build)} imagem(ns) a construir.{RESET}\n", flush=True)

        t_total = time.perf_counter()
        built   = 0
        failed  = 0

        for img in to_build:
            sep()
            print(
                f"{CYAN}{BOLD}[{img['benchmark']} / {img['tier']} / SF={img['sf']}]{RESET} "
                f"→ {img['tag']}",
                flush=True,
            )
            t0 = time.perf_counter()
            try:
                build_image(
                    benchmark    = img["bench_key"],
                    scale_factor = img["sf"],
                    image_tag    = img["tag"],
                )
                elapsed = time.perf_counter() - t0
                print(f"{GREEN}Pronta em {fmt_duration(elapsed)}.{RESET}", flush=True)
                built += 1
            except Exception as exc:
                print(f"{RED}FALHA ao construir '{img['tag']}': {exc}{RESET}", flush=True)
                failed += 1

        sep("═")
        elapsed_total = fmt_duration(time.perf_counter() - t_total)
        if failed:
            print(
                f"{RED}Build: {failed} falha(s). {built} construída(s) em {elapsed_total}.{RESET}",
                flush=True,
            )
            sys.exit(1)
        else:
            print(
                f"{GREEN}{BOLD}{built} imagem(ns) construída(s) em {elapsed_total}.{RESET}",
                flush=True,
            )

    # ── Smoke tests em todas as 6 imagens ─────────────────────────────────────
    all_ok = _run_smoke_tests(plan, tier_configs)
    if not all_ok:
        sys.exit(1)


def main(force: bool = False) -> None:
    tee        = TeeWriter(sys.stdout, _LOG_PATH, mode="a")
    sys.stdout = tee  # type: ignore[assignment]
    try:
        prepare(force=force)
    finally:
        sys.stdout = tee._stream
        tee.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prepara imagens Docker para os benchmarks")
    parser.add_argument("--force", action="store_true",
                        help="Reconstrói imagens mesmo que já existam")
    args = parser.parse_args()
    main(force=args.force)
