"""
Gerenciamento incremental dos arquivos de resultado de benchmark.

Cada tarefa da fila produz um arquivo JSON em:
    results/benchmark_results/{tier}/{combination}/task_{id}.json

O arquivo é criado vazio quando a tarefa inicia e preenchido
incrementalmente à medida que queries terminam, permitindo que a
interface web exiba progresso em tempo real mesmo que o processo
seja interrompido.

Funções
-------
    init_task_file(task, started_at, results_dir)
        Cria o arquivo da tarefa com seções vazias para TPC-H e TPC-DS.

    append_query_result(out_path, query_result, benchmark_key)
        Adiciona o resultado de uma query ao benchmark correto.

    finalize_benchmark_section(out_path, benchmark_key, result)
        Preenche summary, pg_stats e total_ms da seção do benchmark.

    finalize_task_file(out_path, finished_at, duration_s)
        Marca a tarefa como done e registra o tempo total.

    task_path(task, results_dir)
        Retorna o Path do arquivo de resultado de uma tarefa.
"""

import json
import os
import stat
from pathlib import Path


def _lock_file(path: Path) -> None:
    """Torna o arquivo somente-leitura para todos (444)."""
    path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def _unlock_file(path: Path) -> None:
    """Restaura permissão de escrita para o dono (644)."""
    path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)


def _atomic_write(path: Path, payload: dict) -> None:
    """Escreve payload JSON em path de forma atômica (tmp + rename).

    Garante que o arquivo nunca fique em estado corrompido/truncado,
    mesmo que o processo seja interrompido no meio da escrita.
    """
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def task_path(task: dict, results_dir: Path) -> Path:
    """Retorna o caminho esperado do arquivo de resultado de uma tarefa.

    Args:
        task:        Dict da tarefa (campos: id, tier, combination).
        results_dir: Diretório raiz de resultados (ex: ``results/benchmark_results``).

    Returns:
        Path absoluto do arquivo JSON da tarefa.
    """
    return results_dir / task["tier"] / task["combination"] / f"task_{task['id']}.json"


def init_task_file(task: dict, started_at: str, results_dir: Path) -> Path:
    """Cria o arquivo da tarefa com seções separadas para TPC-H e TPC-DS.

    As seções de benchmark começam vazias e são preenchidas durante
    a execução via ``append_query_result`` e ``finalize_benchmark_section``.

    Args:
        task:        Dict da tarefa.
        started_at:  Timestamp ISO 8601 de início.
        results_dir: Diretório raiz de resultados.

    Returns:
        Path do arquivo criado.
    """
    out_path = task_path(task, results_dir)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    empty_bench = {
        "queries":   [],
        "summary":   None,
        "pg_stats":  None,
        "total_ms":  None,
        "n_success": 0,
        "n_failed":  0,
    }

    payload = {
        "task_id":     task["id"],
        "combination": task["combination"],
        "tier":        task["tier"],
        "pg_config":   task["config"],
        "started_at":  started_at,
        "finished_at": None,
        "duration_s":  None,
        "status":      "running",
        "tpc_h":       dict(empty_bench),
        "tpc_ds":      dict(empty_bench),
        "hw_metrics":  None,
    }

    _atomic_write(out_path, payload)
    return out_path


def append_query_result(
    out_path: Path,
    query_result: dict,
    benchmark_key: str,
) -> None:
    """Adiciona o resultado de uma query ao benchmark correto.

    Atualiza ``n_success`` / ``n_failed`` atomicamente após cada query.
    Chamado via query_callback durante ``run_all_queries()``.

    Args:
        out_path:      Path do arquivo da tarefa.
        query_result:  Dict retornado por ``run_query()``.
        benchmark_key: ``"tpc_h"`` ou ``"tpc_ds"``.
    """
    with open(out_path, encoding="utf-8") as f:
        payload = json.load(f)

    payload[benchmark_key]["queries"].append(query_result)
    if query_result.get("success"):
        payload[benchmark_key]["n_success"] += 1
    else:
        payload[benchmark_key]["n_failed"] += 1

    _atomic_write(out_path, payload)


def finalize_benchmark_section(
    out_path: Path,
    benchmark_key: str,
    result: dict,
) -> None:
    """Preenche summary, pg_stats e total_ms da seção do benchmark.

    Chamado após ``run_benchmark()`` completar para uma das fases
    (TPC-H ou TPC-DS).

    Args:
        out_path:      Path do arquivo da tarefa.
        benchmark_key: ``"tpc_h"`` ou ``"tpc_ds"``.
        result:        Dict retornado por ``run_benchmark()``.
    """
    with open(out_path, encoding="utf-8") as f:
        payload = json.load(f)

    payload[benchmark_key]["summary"]  = result.get("summary", {})
    payload[benchmark_key]["pg_stats"] = result.get("pg_stats", {})
    payload[benchmark_key]["total_ms"] = round(result["total_ms"], 3)

    _atomic_write(out_path, payload)


def save_hw_metrics(out_path: Path, hw_metrics: dict) -> None:
    """Salva as métricas de hardware coletadas durante a tarefa.

    Args:
        out_path:   Path do arquivo da tarefa.
        hw_metrics: Dict retornado por ``MetricsCollector.stop()``.
    """
    with open(out_path, encoding="utf-8") as f:
        payload = json.load(f)
    payload["hw_metrics"] = hw_metrics
    _atomic_write(out_path, payload)


def finalize_task_file(
    out_path: Path,
    finished_at: str,
    duration_s: float,
) -> None:
    """Marca a tarefa como concluída e registra o tempo total.

    Args:
        out_path:    Path do arquivo da tarefa.
        finished_at: Timestamp ISO 8601 de conclusão.
        duration_s:  Duração total da tarefa em segundos.
    """
    with open(out_path, encoding="utf-8") as f:
        payload = json.load(f)

    payload["finished_at"] = finished_at
    payload["duration_s"]  = round(duration_s, 3)
    payload["status"]      = "done"

    _atomic_write(out_path, payload)
    _lock_file(out_path)


def abandon_task_file(
    out_path: Path,
    finished_at: str,
    duration_s: float,
    reason: str,
    error: str,
) -> None:
    """Marca a tarefa como abandonada com razão e mensagem de erro.

    Garante que o arquivo de resultado reflita o estado real da tarefa
    em vez de ficar preso em "running" para sempre.

    Args:
        out_path:    Path do arquivo da tarefa.
        finished_at: Timestamp ISO 8601 do momento do abandono.
        duration_s:  Tempo decorrido desde o início da tarefa (segundos).
        reason:      Categoria do abandono:
                       ``"timeout"``        — limite de tempo do tier excedido
                       ``"invalid_config"`` — PostgreSQL rejeitou a configuração
                       ``"max_retries"``    — falha de infraestrutura após 3 tentativas
        error:       Mensagem de erro completa (traceback ou descrição).
    """
    with open(out_path, encoding="utf-8") as f:
        payload = json.load(f)

    payload["finished_at"]      = finished_at
    payload["duration_s"]       = round(duration_s, 3)
    payload["status"]           = "abandoned"
    payload["abandoned_reason"] = reason
    payload["error"]            = error

    _atomic_write(out_path, payload)
    _lock_file(out_path)
