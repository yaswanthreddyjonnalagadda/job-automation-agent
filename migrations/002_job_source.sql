-- 002_job_source.sql
-- Card 1: store jobs and close them safely.
-- Adds detail/liveness tracking to jobs, health tracking to company_directory,
-- and crawl/board run history so a failed or incomplete read never silently
-- closes jobs that are actually still open.

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS detail_status TEXT DEFAULT 'done';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS liveness_strikes INTEGER DEFAULT 0;

ALTER TABLE company_directory ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'live';
ALTER TABLE company_directory ADD COLUMN IF NOT EXISTS last_crawl_at TIMESTAMP;
ALTER TABLE company_directory ADD COLUMN IF NOT EXISTS last_error TEXT;
ALTER TABLE company_directory ADD COLUMN IF NOT EXISTS failures_in_row INTEGER DEFAULT 0;

CREATE TABLE IF NOT EXISTS crawl_runs (
    id              SERIAL PRIMARY KEY,
    provider        TEXT NOT NULL,
    started_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMP,
    boards_total    INTEGER DEFAULT 0,
    boards_complete INTEGER DEFAULT 0,
    boards_failed   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS board_runs (
    id              SERIAL PRIMARY KEY,
    run_id          INTEGER NOT NULL REFERENCES crawl_runs(id),
    ats_platform    TEXT NOT NULL,
    board           TEXT NOT NULL,
    complete        BOOLEAN NOT NULL DEFAULT FALSE,
    postings_seen   INTEGER DEFAULT 0,
    new_count       INTEGER DEFAULT 0,
    reopened_count  INTEGER DEFAULT 0,
    closed_count    INTEGER DEFAULT 0,
    error           TEXT,
    finished_at     TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_board_runs_run_id ON board_runs(run_id);
CREATE INDEX IF NOT EXISTS idx_board_runs_board ON board_runs(ats_platform, board);

DROP VIEW IF EXISTS open_jobs;
CREATE VIEW open_jobs AS
SELECT * FROM jobs
WHERE status = 'active' AND detail_status = 'done';
