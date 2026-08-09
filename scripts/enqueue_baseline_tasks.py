"""
Insere na fila real (Postgres) as 6 tarefas de baseline pra comparação com o
meta-modelo: config de fábrica do PostgreSQL 17 e config recomendada pelo
pgtune, uma de cada nos 3 tiers (low/medium/high).

Usa `combination` distinto ("baseline_default"/"baseline_pgtune") pra não se
misturar com as combinações reais (s1, s2, ..., s1_s2_s3) usadas no treino —
estas tarefas são só pra avaliação/comparação, não entram no features.csv.

Depois de rodar este script:
    .venv/bin/python cli/prepare.py     # builda as imagens Docker se preciso
    .venv/bin/python cli/run.py         # processa as 6 tarefas da fila

Uso:
    .venv/bin/python scripts/enqueue_baseline_tasks.py
"""

import json

from ml.baseline_comparison import DEFAULT_PG_CONFIG
from ml.pgtune_baseline import pgtune_config
from utils.db import connect

TIERS = ("low", "medium", "high")


def main() -> None:
    rows = []
    for tier in TIERS:
        rows.append(("baseline_default", tier, DEFAULT_PG_CONFIG))
        rows.append(("baseline_pgtune", tier, pgtune_config(tier)))

    with connect() as conn:
        with conn.cursor() as cur:
            for combination, tier, config in rows:
                cur.execute(
                    """
                    INSERT INTO tasks (combination, tier, config, status)
                    VALUES (%s, %s, %s, 'pending')
                    RETURNING id
                    """,
                    (combination, tier, json.dumps(config)),
                )
                task_id = cur.fetchone()["id"]
                print(f"  task {task_id:>4}  {tier:<7} {combination}")

    print(f"\n{len(rows)} tarefas de baseline inseridas na fila.")


if __name__ == "__main__":
    main()
