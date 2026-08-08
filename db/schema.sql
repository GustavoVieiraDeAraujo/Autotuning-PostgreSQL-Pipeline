-- Schema do banco de controle da pipeline de autotuning PostgreSQL.
--
-- Substitui data/queue.json (fila) e data/raw/{tier}/{combinacao}/task_{id}.json
-- (resultados) por duas tabelas Postgres. Isso resolve dois problemas:
--
--   1. Persistencia/seguranca: escrita transacional em vez de reescrita de
--      arquivo JSON inteiro a cada mudanca de estado.
--   2. Multiplos workers: varios processos `cli/run.py`, potencialmente em
--      maquinas diferentes, podem reivindicar tarefas da mesma fila com
--      seguranca via `SELECT ... FOR UPDATE SKIP LOCKED` (ver
--      taskqueue/execution_queue.py::next()).
--
-- Aplicado automaticamente na primeira subida do container via
-- docker-entrypoint-initdb.d (ver db/docker-compose.yml).

CREATE TYPE task_status AS ENUM ('pending', 'running', 'done', 'failed', 'abandoned');

CREATE TABLE tasks (
    id                BIGSERIAL PRIMARY KEY,
    combination       TEXT NOT NULL,
    tier              TEXT NOT NULL,
    config            JSONB NOT NULL,
    repetition        INT NOT NULL DEFAULT 0,
    status            task_status NOT NULL DEFAULT 'pending',
    retry_count       INT NOT NULL DEFAULT 0,
    abandoned_reason  TEXT,
    error             TEXT,
    -- Resumo pequeno usado pela fila/API (equivalente ao antigo task["result"]);
    -- o resultado completo do benchmark vai em task_results.
    result_summary    JSONB,
    -- Lease de reivindicacao: quem pegou a tarefa e quando. Usado para
    -- detectar workers mortos (ver next() - reclaim de tasks "running"
    -- cujo lease expirou, baseado no timeout do proprio tier).
    claimed_at        TIMESTAMPTZ,
    claimed_by        TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_tasks_status     ON tasks (status);
CREATE INDEX idx_tasks_tier_combo ON tasks (tier, combination);

CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tasks_set_updated_at
    BEFORE UPDATE ON tasks
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Resultado completo de benchmark por tarefa (equivalente ao antigo
-- data/raw/{tier}/{combinacao}/task_{id}.json). hw_metrics guarda so o
-- "summary" agregado, nunca as amostras brutas (ver decisao do dataset
-- enxuto - o treino do modelo nunca le hw_metrics.samples nem pg_stats).
CREATE TABLE task_results (
    task_id     BIGINT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    started_at  TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    duration_s  DOUBLE PRECISION,
    tpc_h       JSONB,
    tpc_ds      JSONB,
    hw_metrics  JSONB
);
