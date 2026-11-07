"""Tests for Card 7 ("switch the agent over, safely, behind a switch that stays OFF"):

1. `job_source_client.get_open_jobs` reads ONLY the `open_jobs` view, read-only, and
   returns dicts carrying every key `web_ui._suggested_jobs` / `discovered_view.prepare`
   / the job-card template need.
2. `web_ui._suggested_jobs` keeps its exact legacy behaviour when `JOB_SOURCE` is unset
   or `"legacy"`, and only reads from `job_source_client` when `JOB_SOURCE="service"`.

Offline only: every test seeds a SQLite temp file through `JobStorage` (the same schema,
including the `open_jobs` view, that the real job-source Postgres instance uses) and
points `job_source_client` at that same file via `JOB_SOURCE_DSN`, read-only. Never
touches `job-agent-db` (port 5432) or `data/applications.db`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import job_source_client
from services.diff_engine.storage import JobStorage

UTC = timezone.utc


@pytest.fixture
def seeded_storage(tmp_path, monkeypatch):
    db_file = tmp_path / "card7_open_jobs.db"
    storage = JobStorage(dsn=f"sqlite:///{db_file}")
    monkeypatch.setenv("JOB_SOURCE_DSN", f"sqlite:///{db_file}")
    return storage


def _seed(storage, now, ext_id, title, score, description="A real job description.",
          remote=False, ats="greenhouse", board="acme", location="Fairfax, VA"):
    job_id, _label = storage.upsert_posting(ats, board, {
        "external_id": ext_id,
        "title": title,
        "location": location,
        "description": description,
        "apply_url": f"https://boards.greenhouse.io/{board}/jobs/{ext_id}",
        "remote": remote,
    }, now)
    storage.update_match_score(job_id, score)
    return job_id


# ---------------------------------------------------------------------------
# 1. get_open_jobs: only active + described rows, ordered, score-filtered
# ---------------------------------------------------------------------------

def test_get_open_jobs_returns_only_active_described_rows_ordered_by_score_then_newest(seeded_storage):
    storage = seeded_storage
    now = datetime.now(UTC)
    t_old = now - timedelta(hours=2)
    t_new = now - timedelta(hours=1)

    _seed(storage, t_old, "a-1", "Network Engineer", 80)
    _seed(storage, t_new, "a-2", "Network Engineer II", 80)          # same score, seen later -> first
    _seed(storage, t_old, "a-3", "Pending Detail Role", 90, description="")  # no description -> excluded
    _seed(storage, t_old, "a-4", "Low Score Role", 10)                # below min_score -> excluded
    _seed(storage, t_old, "a-5", "Closed Role", 95, board="other")
    storage.close_unseen("greenhouse", "other", older_than=t_new, now=t_new)  # closes a-5

    jobs = job_source_client.get_open_jobs(min_score=60, days=7, limit=40)

    titles = [j["job_title"] for j in jobs]
    assert titles == ["Network Engineer II", "Network Engineer"]


def test_get_open_jobs_excludes_rows_older_than_the_days_window(seeded_storage):
    storage = seeded_storage
    now = datetime.now(UTC)
    _seed(storage, now - timedelta(days=10), "old-1", "Stale Role", 80)
    _seed(storage, now - timedelta(hours=1), "new-1", "Fresh Role", 80)

    jobs = job_source_client.get_open_jobs(min_score=60, days=7, limit=40)

    titles = {j["job_title"] for j in jobs}
    assert titles == {"Fresh Role"}


def test_get_open_jobs_respects_the_limit(seeded_storage):
    storage = seeded_storage
    now = datetime.now(UTC)
    for i in range(5):
        _seed(storage, now, f"lim-{i}", f"Role {i}", 70)

    jobs = job_source_client.get_open_jobs(min_score=60, days=7, limit=2)
    assert len(jobs) == 2


# ---------------------------------------------------------------------------
# 2. returned dicts carry every key _suggested_jobs / discovered_view.prepare need
# ---------------------------------------------------------------------------

def test_get_open_jobs_dicts_have_every_key_suggested_jobs_needs(seeded_storage):
    storage = seeded_storage
    now = datetime.now(UTC)
    _seed(storage, now, "k-1", "Network Engineer", 80, remote=True)

    jobs = job_source_client.get_open_jobs(min_score=60, days=7, limit=40)
    assert len(jobs) == 1
    job = jobs[0]

    # Keys read directly by the job-card template (web_ui.INDEX_HTML) and by
    # discovered_view.drop_handled/_one_per_role, beyond what prepare() derives.
    required_keys = {
        "id", "dedup_key", "company_name", "ats_type", "job_title", "location",
        "is_remote", "url", "match_score", "matched_skills", "status",
        "discovered_at", "resume_score", "resume_matched", "resume_missing",
        "posted_at", "posted_precision", "country_code", "readiness_json",
    }
    assert required_keys.issubset(job.keys())
    assert job["id"] == "js:1"
    assert job["job_title"] == "Network Engineer"
    assert job["is_remote"] is True

    # And it must round-trip through discovered_view.prepare without error, with the
    # expected derived fields present (proves the keys are not just present but correct).
    import discovered_view
    shown = discovered_view.prepare(jobs, applications=[])
    assert len(shown) == 1
    assert shown[0]["resume_score"] is None
    assert shown[0]["resume_matched"] == []
    assert "age" in shown[0]
    assert "place" in shown[0]


# ---------------------------------------------------------------------------
# 3. unreachable / unset DSN -> [] and a warning, never an exception
# ---------------------------------------------------------------------------

def test_get_open_jobs_unset_dsn_returns_empty_list_and_warns(monkeypatch, caplog):
    monkeypatch.delenv("JOB_SOURCE_DSN", raising=False)
    with caplog.at_level("WARNING"):
        jobs = job_source_client.get_open_jobs(min_score=60, days=7, limit=40)
    assert jobs == []
    assert any("JOB_SOURCE_DSN" in rec.message for rec in caplog.records)


def test_get_open_jobs_unreachable_dsn_returns_empty_list_and_warns(tmp_path, monkeypatch, caplog):
    nonexistent = tmp_path / "does_not_exist" / "nope.db"
    monkeypatch.setenv("JOB_SOURCE_DSN", f"sqlite:///{nonexistent}")
    with caplog.at_level("WARNING"):
        jobs = job_source_client.get_open_jobs(min_score=60, days=7, limit=40)
    assert jobs == []
    assert any("job-source" in rec.message.lower() for rec in caplog.records)


def test_get_open_jobs_db_without_the_open_jobs_view_returns_empty_list_and_warns(tmp_path, monkeypatch, caplog):
    empty_db = tmp_path / "no_schema.db"
    import sqlite3
    sqlite3.connect(str(empty_db)).close()  # exists, but has no tables/views at all
    monkeypatch.setenv("JOB_SOURCE_DSN", f"sqlite:///{empty_db}")
    with caplog.at_level("WARNING"):
        jobs = job_source_client.get_open_jobs(min_score=60, days=7, limit=40)
    assert jobs == []
    assert any("job-source" in rec.message.lower() for rec in caplog.records)


# ---------------------------------------------------------------------------
# 4. web_ui._suggested_jobs: JOB_SOURCE unset/"legacy" -> identical to before
# ---------------------------------------------------------------------------

def test_suggested_jobs_legacy_default_never_calls_job_source_client(monkeypatch):
    monkeypatch.delenv("JOB_SOURCE", raising=False)
    import web_ui

    def _boom(*_a, **_kw):
        raise AssertionError("job_source_client.get_open_jobs must not be called in legacy mode")
    monkeypatch.setattr(job_source_client, "get_open_jobs", _boom)

    class _FakeEngine:
        def get_discovered_jobs(self, **_kwargs):
            return [{
                "id": 1, "dedup_key": "gh:1", "company_name": "Acme", "ats_type": "greenhouse",
                "job_title": "Network Engineer", "location": "Remote", "is_remote": True,
                "url": "https://acme.example/jobs/1", "match_score": 80, "matched_skills": [],
                "status": "discovered", "discovered_at": datetime.now(UTC).isoformat(),
                "resume_score": None, "resume_matched": [], "resume_missing": [],
                "posted_at": None, "posted_precision": None, "country_code": "US",
                "readiness_json": None,
            }]

    import job_discovery
    monkeypatch.setattr(job_discovery, "JobDiscoveryEngine", _FakeEngine)

    filters = web_ui._feed_filter_context({})
    jobs, _options = web_ui._suggested_jobs([], filters)
    assert len(jobs) == 1
    assert jobs[0]["company_name"] == "Acme"


# ---------------------------------------------------------------------------
# 5. web_ui._suggested_jobs: JOB_SOURCE="service" -> rows come from the new source
# ---------------------------------------------------------------------------

def test_suggested_jobs_service_mode_reads_from_job_source_client(seeded_storage, monkeypatch):
    storage = seeded_storage
    now = datetime.now(UTC)
    _seed(storage, now, "svc-1", "Senior Network Engineer", 85)

    monkeypatch.setenv("JOB_SOURCE", "service")
    import web_ui
    import job_discovery

    def _boom(*_a, **_kw):
        raise AssertionError("job_discovery.JobDiscoveryEngine must not be used in service mode")
    monkeypatch.setattr(job_discovery, "JobDiscoveryEngine", _boom)

    filters = web_ui._feed_filter_context({})
    jobs, _options = web_ui._suggested_jobs([], filters)

    assert len(jobs) == 1
    assert jobs[0]["id"].startswith("js:")
    assert jobs[0]["job_title"] == "Senior Network Engineer"


def test_suggested_jobs_service_mode_applies_company_and_remote_filters(seeded_storage, monkeypatch):
    storage = seeded_storage
    now = datetime.now(UTC)
    _seed(storage, now, "svc-2", "Remote Role", 85, remote=True, board="acme")
    _seed(storage, now, "svc-3", "Onsite Role", 85, remote=False, board="other")

    monkeypatch.setenv("JOB_SOURCE", "service")
    import web_ui

    filters = web_ui._feed_filter_context({"within": "7d", "workplace": "remote"})
    assert filters["remote_only"] is True
    jobs, _options = web_ui._suggested_jobs([], filters)

    assert len(jobs) == 1
    assert jobs[0]["job_title"] == "Remote Role"


# ---------------------------------------------------------------------------
# 6. matcher scoring loop only picks up open_jobs rows (active + described)
# ---------------------------------------------------------------------------

def test_scoring_fetch_open_candidates_skips_pending_detail_jobs(seeded_storage):
    storage = seeded_storage
    now = datetime.now(UTC)
    _seed(storage, now, "d-1", "Has Description", 0, description="A real description.")
    _seed(storage, now, "d-2", "Pending Description", 0, description="")
    # update_match_score in _seed would make these "matched"; re-clear so they're unmatched.
    storage._conn.execute("UPDATE jobs SET match_score = NULL")
    storage._conn.commit()

    from services.matcher.scoring import _fetch_open_candidates
    candidates = _fetch_open_candidates(storage, limit=10)

    titles = {c["title"] for c in candidates}
    assert titles == {"Has Description"}
