"""
taskqueue
=========
Fila de execução persistente (Postgres) para testes sequenciais de
configurações PostgreSQL. Suporta múltiplos workers concorrentes,
inclusive em máquinas diferentes (ver db/schema.sql).

    from taskqueue import ExecutionQueue

    queue = ExecutionQueue()      # usa DATABASE_URL (utils/db.py)
    task  = queue.next()          # next pending task
    queue.mark_done(task["id"], result)
"""

from .execution_queue import ExecutionQueue

__all__ = ["ExecutionQueue"]
