"""
Conexão com o Postgres de controle (fila + resultados): ver db/schema.sql.

Não confundir com os containers Postgres efêmeros usados pelos benchmarks
TPC-H/TPC-DS (benchmarks/container.py): este é um único banco persistente,
compartilhado por todos os workers da fila, potencialmente em máquinas
diferentes.

    from utils.db import get_dsn, connect
"""

import os

import psycopg
from psycopg.rows import dict_row

_DEFAULT_DSN = "postgresql://autotuning:autotuning@localhost:5433/autotuning_queue"


def get_dsn() -> str:
    """Retorna a connection string do banco de controle.

    Lê de DATABASE_URL; usa o default do docker-compose local (db/) se ausente.
    """
    return os.environ.get("DATABASE_URL", _DEFAULT_DSN)


def connect(dsn: str | None = None) -> psycopg.Connection:
    """Abre uma conexão nova com o banco de controle.

    Autocommit (cada operação da fila é uma única query atômica) e rows
    como dict (compatível com o formato de tarefa usado em todo o projeto).
    Conexões são de curta duração: abertas e fechadas por operação, não
    mantidas vivas entre chamadas, já que uma tarefa pode levar horas entre
    um `next()` e o `mark_done()` correspondente.
    """
    conn = psycopg.connect(dsn or get_dsn(), row_factory=dict_row, autocommit=True)
    return conn
