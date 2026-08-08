"""
task_queue
==========
Fila de execução persistente para testes sequenciais de configurações PostgreSQL.

    from task_queue import ExecutionQueue

    queue = ExecutionQueue("output/queue.json")
    task  = queue.next()          # next pending task
    queue.mark_done(task["id"], result)
"""

from .execution_queue import ExecutionQueue

__all__ = ["ExecutionQueue"]
