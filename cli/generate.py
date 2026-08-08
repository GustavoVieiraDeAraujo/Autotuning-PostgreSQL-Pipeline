"""
Gerador de configurações PostgreSQL — ponto de entrada.

Gera todas as 7 combinações de etapas e popula a fila de execução no
Postgres de controle (ver db/schema.sql — variável de ambiente
``DATABASE_URL``, ver utils/db.py).

Uso direto
----------
    python cli/generate.py                         # 51 configs por combinação (padrão)
    python cli/generate.py --n-configs 15          # 15 configs por combinação
    python cli/generate.py --repetitions 3         # cada config executada 3 vezes
    python cli/generate.py --seed 42               # reproduzível

Via interface web
-----------------
    POST /api/generator/start   (iniciado automaticamente pela web)
"""

import argparse
import json
import sys
import time
from pathlib import Path

_ROOT     = Path(__file__).parent.parent
_LOG_PATH = _ROOT / "logs" / "generate.log"

from sampler import generate_configs
from sampler.display import print_docker_table, print_summary_table
from sampler.orchestrator import DOCKER_CONFIG_PATH
from taskqueue import ExecutionQueue
from utils.logging import TeeWriter                                      # noqa: E402


_ALL_COMBINATIONS: list[list[int]] = [
    [1],
    [2],
    [3],
    [1, 2],
    [1, 3],
    [2, 3],
    [1, 2, 3],
]

_DEFAULT_N_CONFIGS = 51   # múltiplo de 3 → 17 configs por tier (low/medium/high)


def main(argv: list[str] | None = None) -> None:
    """Gera configurações para todas as 7 combinações de etapas."""
    parser = argparse.ArgumentParser(
        description="Gera configurações PostgreSQL via LHS e popula a fila."
    )
    parser.add_argument(
        "--n-configs",
        type=int,
        default=_DEFAULT_N_CONFIGS,
        metavar="N",
        help=f"Número de configs por combinação (padrão: {_DEFAULT_N_CONFIGS})",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        metavar="SEED",
        help="Semente para reprodutibilidade do LHS (padrão: não-determinístico)",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=1,
        metavar="N",
        help="Número de vezes que cada config é enfileirada (padrão: 1)",
    )
    args = parser.parse_args(argv)

    tee        = TeeWriter(sys.stdout, _LOG_PATH)
    sys.stdout = tee  # type: ignore[assignment]

    try:
        _generate(args)
    finally:
        sys.stdout = tee._stream
        tee.close()


def _generate(args: argparse.Namespace) -> None:
    queue    = ExecutionQueue()
    stats    = queue.stats()
    blocking = stats["pending"] + stats["running"]
    if blocking > 0:
        print(
            f"ERRO: A fila já contém {blocking} tarefa(s) pendente(s)/em execução.\n"
            f"  Execute o benchmark antes de gerar novas configs.\n"
            f"  Estado atual: {queue}"
        )
        raise SystemExit(1)
    if not queue.is_empty():
        queue.reset()
        print("Fila anterior resetada.")

    with open(DOCKER_CONFIG_PATH, encoding="utf-8") as f:
        docker = json.load(f)

    print_docker_table(docker)

    if args.seed is not None:
        print(f"Seed LHS: {args.seed}")
    if args.repetitions > 1:
        print(f"Repetições por config: {args.repetitions}")

    all_results: dict[str, dict[str, list]] = {}
    t0 = time.perf_counter()

    for stages in _ALL_COMBINATIONS:
        label              = "_".join(f"s{s}" for s in stages)
        all_results[label] = generate_configs(
            stages=stages,
            total=args.n_configs,
            seed=args.seed,
        )

    print_summary_table(
        all_results,
        time.perf_counter() - t0,
        "Postgres (banco de controle — ver DATABASE_URL)",
    )

    queue = ExecutionQueue.from_dict(
        all_results,
        repetitions=args.repetitions,
    )
    print(queue)

    print()
    print("Fila gerada. Execute 'make build-images' para construir as imagens Docker.")


if __name__ == "__main__":
    main()
