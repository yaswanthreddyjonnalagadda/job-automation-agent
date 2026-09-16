"""Deleting an application, against the real database.

The first version of this deleted application_events by dedup_key, a column
that table does not have, and the dashboard's Delete button answered with an
Internal Server Error. The dashboard tests use a stub tracker, so only a test
that talks to the database itself would have caught it.
"""

import pytest

db = pytest.importorskip("db")


@pytest.fixture
def tracker():
    try:
        t = db.get_tracker()
        t.list_all()
    except Exception as exc:  # no Postgres on this machine
        pytest.skip(f"database not available: {exc}")
    return t


def test_an_application_and_everything_under_it_is_removed(tracker):
    key = "test-delete-me-0001"
    tracker.delete(key)  # from any earlier run of this test
    application_id = tracker.create(
        dedup_key=key, title="Test Role", company="Test Company",
        url="https://example.com/jobs/test-delete-me", notes="created by a test",
    )
    assert application_id
    tracker.record_answer(key, "example.com", "A question asked by a test?", "an answer")
    tracker.record_event(key, "note", "an event written by a test")
    assert tracker.get(key) is not None

    assert tracker.delete(key) is True
    assert tracker.get(key) is None

    with tracker._connect() as conn:
        for table in ("documents", "form_answers", "application_events"):
            left = conn.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE application_id = %s", (application_id,)
            ).fetchone()["n"]
            assert left == 0, f"{table} still holds rows for the deleted application"


def test_deleting_something_that_is_not_there_is_not_an_error(tracker):
    assert tracker.delete("no-such-application-key") is False
