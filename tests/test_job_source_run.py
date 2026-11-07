"""Offline tests for the job-source scheduled runner (Card 6).

Readers are mocked; storage is a temp-file SQLite JobStorage; clock and sleep
are patched. No live site, no Postgres, no data/applications.db.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from services.diff_engine.storage import JobStorage
from services.poller import run as poller_run
from services.poller.reader_result import ReaderResult

PROVIDER = "greenhouse"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def storage(tmp_path):
    return JobStorage(dsn=f"sqlite:///{tmp_path / 'run_test.db'}")


@pytest.fixture(autouse=True)
def lock_dir_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_SOURCE_DATA_DIR", str(tmp_path / "locks"))


def _register(storage, slug, platform=PROVIDER, status="live", host="boards-api.greenhouse.io"):
    company_id = storage.register_company({
        "company_name": slug.title(),
        "ats_platform": platform,
        "board_slug": slug,
        "board_url": f"https://{host}/{slug}",
        "api_endpoint": f"https://{host}/{slug}/jobs",
    })
    if status != "live":
        storage.set_board_status(company_id, status)
    return company_id


def _rr(postings=(), complete=True, error=None):
    return ReaderResult(postings=list(postings), complete=complete, error=error)


def _posting(ext_id):
    return {
        "external_id": ext_id,
        "title": "Network Engineer",
        "location": "Remote",
        "description": "A real job.",
        "apply_url": f"https://example.com/jobs/{ext_id}",
    }


def _crawl_rows(storage):
    cur = storage._conn.cursor()
    cur.execute("SELECT provider, boards_total, boards_complete, boards_failed FROM crawl_runs")
    return [dict(r) for r in cur.fetchall()]


def test_second_run_while_lock_held_reports_already_running_and_reads_nothing(storage, capsys):
    _register(storage, "acme")
    assert poller_run.acquire_lock(PROVIDER, storage) is True
    try:
        with patch("services.poller.run.poll_single_company") as mock_poll:
            rc = poller_run.main(["--provider", PROVIDER], storage=storage)
        assert rc == 0
        assert "already running" in capsys.readouterr().out
        mock_poll.assert_not_called()
        assert _crawl_rows(storage) == []
    finally:
        poller_run.release_lock(PROVIDER, storage)


def test_lock_older_than_3_hours_is_taken_over(storage):
    _register(storage, "acme")
    path = poller_run.lock_path(PROVIDER)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("stale", encoding="utf-8")
    old = time.time() - 4 * 3600
    os.utime(path, (old, old))
    with patch("services.poller.run.poll_single_company", return_value=_rr([_posting("a1")])):
        rc = poller_run.main(["--provider", PROVIDER], storage=storage)
    assert rc == 0
    assert len(_crawl_rows(storage)) == 1
    assert not path.exists()


def test_lock_is_released_even_when_a_reader_raises(storage):
    _register(storage, "acme")
    with patch("services.poller.run.poll_single_company", side_effect=RuntimeError("boom")):
        rc = poller_run.main(["--provider", PROVIDER], storage=storage)
    assert rc == 0
    assert not poller_run.lock_path(PROVIDER).exists()
    assert _crawl_rows(storage)[0]["boards_failed"] == 1


def test_only_live_boards_are_read(storage):
    _register(storage, "live-one", status="live")
    _register(storage, "pending-one", status="pending")
    _register(storage, "paused-one", status="paused")
    with patch("services.poller.run.poll_single_company", return_value=_rr()) as mock_poll:
        poller_run.main(["--provider", PROVIDER], storage=storage)
    assert [c.args[0]["board_slug"] for c in mock_poll.call_args_list] == ["live-one"]


class _FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.t

    def record_sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds


def test_two_boards_on_the_same_host_are_a_second_apart(storage, monkeypatch):
    _register(storage, "acme", host="boards-api.greenhouse.io")
    _register(storage, "globex", host="boards-api.greenhouse.io")
    clock = _FakeClock()
    monkeypatch.setattr(time, "monotonic", clock.monotonic)
    monkeypatch.setattr(time, "sleep", clock.record_sleep)
    read_at = []

    def fake_poll(company, session=None, storage=None):
        read_at.append(clock.monotonic())
        return _rr([_posting(company["board_slug"])])

    with patch("services.poller.run.poll_single_company", side_effect=fake_poll):
        poller_run.main(["--provider", PROVIDER], storage=storage)
    assert len(read_at) == 2
    assert read_at[1] - read_at[0] >= 1.0
    assert 0.5 in clock.sleeps


def test_icims_is_dispatched(storage):
    company = {"ats_platform": "icims", "board_slug": "example", "board_url": ""}
    mock_res = _rr([_posting("i1")])
    with patch("services.poller.poller_service.read_icims_board", return_value=mock_res) as mock_read:
        from services.poller.poller_service import poll_single_company
        result = poll_single_company(company, storage=storage)
    assert result is mock_res
    mock_read.assert_called_once_with("example", session=None)


def test_workday_gets_known_ids_wired_to_storage(storage):
    storage.upsert_posting("workday", "acme", _posting("known-1"), T0)
    company = {"ats_platform": "workday", "board_url": "https://acme.wd1.myworkdayjobs.com/acme"}
    with patch("services.poller.poller_service.read_workday_board", return_value=_rr()) as mock_read:
        from services.poller.poller_service import poll_single_company
        poll_single_company(company, storage=storage)
    known_ids = mock_read.call_args.kwargs["known_ids"]
    assert callable(known_ids)
    assert known_ids("known-1") is True
    assert known_ids("missing-9") is False


def test_crawl_run_row_carries_the_right_counts(storage, capsys):
    _register(storage, "good")
    _register(storage, "bad")
    results = [_rr([_posting("n1")], complete=True), _rr([], complete=False, error="timeout")]
    with patch("services.poller.run.poll_single_company", side_effect=results):
        rc = poller_run.main(["--provider", PROVIDER], storage=storage)
    assert rc == 0
    rows = _crawl_rows(storage)
    assert len(rows) == 1
    assert (rows[0]["boards_total"], rows[0]["boards_complete"], rows[0]["boards_failed"]) == (2, 1, 1)
    out = capsys.readouterr().out
    assert "total=2" in out and "complete=1" in out and "failed=1" in out
    assert "new=1" in out


def test_all_runs_each_live_provider(storage, capsys):
    _register(storage, "acme", platform="greenhouse")
    _register(storage, "palantir", platform="lever")
    with patch("services.poller.run.poll_single_company", return_value=_rr()) as mock_poll:
        rc = poller_run.main(["--all"], storage=storage)
    assert rc == 0
    assert mock_poll.call_count == 2
    assert len(_crawl_rows(storage)) == 2
    out = capsys.readouterr().out
    assert "greenhouse" in out and "lever" in out


class _FakeCursor:
    def __init__(self, results):
        self.results = list(results)
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.statements.append((sql, params))

    def fetchone(self):
        return self.results.pop(0)


class _FakePGConn:
    def __init__(self, results):
        self.cursor_obj = _FakeCursor(results)

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        pass


class _FakePGStorage:
    is_sqlite = False

    def __init__(self, results):
        self._conn = _FakePGConn(results)


def test_postgres_lock_uses_advisory_lock():
    storage = _FakePGStorage([{"pg_try_advisory_lock": True}])
    assert poller_run.acquire_lock(PROVIDER, storage) is True
    assert "pg_try_advisory_lock" in storage._conn.cursor_obj.statements[0][0]
    poller_run.release_lock(PROVIDER, storage)
    assert any("pg_advisory_unlock" in s for s, _ in storage._conn.cursor_obj.statements)


def test_postgres_busy_lock_is_reported():
    storage = _FakePGStorage([{"pg_try_advisory_lock": False}])
    assert poller_run.acquire_lock(PROVIDER, storage) is False