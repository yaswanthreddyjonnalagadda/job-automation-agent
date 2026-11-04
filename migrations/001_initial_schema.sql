-- 001_initial_schema.sql
-- Core 5-Service Autonomous Job Application Infrastructure

CREATE TABLE IF NOT EXISTS company_directory (
    id              SERIAL PRIMARY KEY,
    company_name    TEXT NOT NULL,
    domain          TEXT,
    ats_platform    TEXT NOT NULL,  -- greenhouse, lever, ashby, workday, icims, smartrecruiters
    board_slug      TEXT NOT NULL,  -- the token/slug needed to hit the API
    board_url       TEXT NOT NULL,  -- full resolved career page URL
    api_endpoint    TEXT NOT NULL,  -- exact API URL to poll for jobs
    is_active       BOOLEAN DEFAULT TRUE,
    last_verified   TIMESTAMP,
    last_polled     TIMESTAMP,
    total_jobs_last INT DEFAULT 0,
    discovered_via  TEXT,          -- common_crawl, career_page_crawl, manual, github_list
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE(ats_platform, board_slug)
);

CREATE INDEX IF NOT EXISTS idx_dir_ats ON company_directory(ats_platform);
CREATE INDEX IF NOT EXISTS idx_dir_active ON company_directory(is_active);
CREATE INDEX IF NOT EXISTS idx_dir_last_polled ON company_directory(last_polled);

CREATE TABLE IF NOT EXISTS jobs (
    id              SERIAL PRIMARY KEY,
    external_id     TEXT NOT NULL,          -- ATS-specific job ID
    ats_platform    TEXT NOT NULL,
    company_slug    TEXT NOT NULL,
    company_name    TEXT,
    title           TEXT NOT NULL,
    location        TEXT,
    department      TEXT,
    description     TEXT,                   -- plain text, stripped from HTML
    description_html TEXT,                  -- raw HTML for display
    apply_url       TEXT NOT NULL,
    salary_min      NUMERIC,
    salary_max      NUMERIC,
    salary_currency TEXT DEFAULT 'USD',
    employment_type TEXT,                   -- full-time, contract, intern
    remote          BOOLEAN DEFAULT FALSE,
    posted_at       TIMESTAMP,              -- from ATS
    first_seen_at   TIMESTAMP DEFAULT NOW(), -- when our system first found it
    last_seen_at    TIMESTAMP DEFAULT NOW(), -- last poll where it appeared
    removed_at      TIMESTAMP,              -- when it disappeared from ATS
    status          TEXT DEFAULT 'active',  -- active, removed, applied, skipped
    match_score     NUMERIC,                -- 0-100, set by matcher
    matched_at      TIMESTAMP,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE(ats_platform, external_id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_jobs_match_score ON jobs(match_score) WHERE match_score IS NOT NULL;

CREATE TABLE IF NOT EXISTS applications (
    id                  SERIAL PRIMARY KEY,
    job_id              INT REFERENCES jobs(id),
    match_score         NUMERIC,
    resume_version      TEXT,             -- path to tailored resume file
    cover_letter        TEXT,             -- path to cover letter file
    tailoring_prompt    TEXT,             -- what was sent to Claude
    claude_tokens_used  INT,
    claude_cost_usd     NUMERIC,
    status              TEXT DEFAULT 'pending',
    -- pending -> queued -> in_progress -> submitted -> confirmed
    -- also: failed, skipped_manual, rejected, interview, offer
    submitted_at        TIMESTAMP,
    portal_used         TEXT,             -- which site applied through
    confirmation_id     TEXT,             -- any confirmation number received
    error_log           TEXT,
    follow_up_date      DATE,
    response_received   BOOLEAN DEFAULT FALSE,
    response_type       TEXT,             -- rejection, interview, offer, ghosted
    response_date       TIMESTAMP,
    notes               TEXT,
    created_at          TIMESTAMP DEFAULT NOW(),
    updated_at          TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_app_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_app_submitted ON applications(submitted_at);
