"""Phase 0-B3: the durable application checkpoint -- schema, versioning, and storage.

A checkpoint is evidence about where a run was, never authority over where it is now
(recovery.reconcile, tested separately in tests/test_recovery_reconciliation.py). These
tests cover checkpoint.py's own (de)serialization/versioning and job_tracker.py's durable
storage of it -- not the reconciliation decision.
"""
import json
import sqlite3

import pytest

import checkpoint
import job_tracker
from job_tracker import JobTracker


# --- checkpoint.py: pure (de)serialization, no browser, no database -------------------------

def test_a_built_checkpoint_round_trips_as_compatible():
    record = checkpoint.build(
        application_key="app1", dedup_key="dedup1", employer="Acme", ats="workday",
        page_url="https://acme.wd1.myworkdayjobs.com/apply/step2",
        page_identity={"host": "acme.wd1.myworkdayjobs.com", "step_indicator": "Step 2 of 5"},
        verified_stage="Step 2 of 5", account_state="SIGNED_IN",
        completed_controls=("resume_attached",), uploaded_documents={"resume": "abc123"},
        last_verified_action="field fill verified", pending_action="",
        uncertain_actions=(), handoff_reason="", submission_effect_state="")
    assert record.checkpoint_id and record.created_at and record.schema_version == checkpoint.SCHEMA_VERSION
    status, parsed = checkpoint.parse(record.to_dict())
    assert status == checkpoint.COMPATIBLE
    assert parsed.application_key == "app1" and parsed.employer == "Acme"
    assert parsed.page_identity == {"host": "acme.wd1.myworkdayjobs.com", "step_indicator": "Step 2 of 5"}
    assert parsed.completed_controls == ("resume_attached",)


def test_no_secret_shaped_field_exists_on_the_checkpoint():
    """A guard-rail, not a content check: the task is explicit that no password, OTP,
    magic-link token, session cookie, or private browser storage ever belongs here."""
    forbidden = ("password", "otp", "token", "cookie", "secret", "credential")
    fields = set(checkpoint.ApplicationCheckpoint.__dataclass_fields__)
    for word in forbidden:
        assert not any(word in field.lower() for field in fields), f"{word!r} looks secret-shaped: {fields}"


@pytest.mark.parametrize("payload", [
    None, "not a dict", 42, [],
    {},                                              # no application_key at all
    {"application_key": "", "schema_version": 1},    # empty application_key
])
def test_an_unusable_payload_is_unsupported_not_a_crash(payload):
    status, parsed = checkpoint.parse(payload)
    assert status == checkpoint.UNSUPPORTED and parsed is None


def test_a_future_schema_version_is_unsupported_not_guessed_at():
    status, parsed = checkpoint.parse({"application_key": "app1", "schema_version": checkpoint.SCHEMA_VERSION + 1})
    assert status == checkpoint.UNSUPPORTED and parsed is None


def test_malformed_field_types_never_raise():
    """completed_controls as a string, uploaded_documents as a list, page_identity as a
    number -- none of it is a reason to crash the workflow; parse() either coerces it or
    reports UNSUPPORTED, but it never propagates an exception."""
    status, parsed = checkpoint.parse({
        "application_key": "app1", "schema_version": 1,
        "completed_controls": 12345, "uploaded_documents": ["not", "a", "dict"],
        "page_identity": "not a dict either",
    })
    assert status in (checkpoint.COMPATIBLE, checkpoint.UNSUPPORTED)


# --- job_tracker.py: durable storage, atomicity, and shared identity with submission effects --

@pytest.fixture
def tracker(tmp_path):
    return JobTracker(tmp_path / "applications.db")


def test_a_checkpoint_round_trips_through_the_tracker(tracker):
    record = checkpoint.build(application_key="app1", dedup_key="dedup1", employer="Acme")
    tracker.write_checkpoint("app1", record.to_dict())
    stored = tracker.read_checkpoint("app1")
    assert stored["application_key"] == "app1" and stored["employer"] == "Acme"


def test_no_checkpoint_reads_back_as_none(tracker):
    assert tracker.read_checkpoint("never-written") is None


def test_a_second_write_replaces_the_payload_but_keeps_the_original_created_at(tracker):
    first = checkpoint.build(application_key="app1", verified_stage="Step 1 of 3")
    tracker.write_checkpoint("app1", first.to_dict())
    with sqlite3.connect(tracker._db_path) as conn:
        conn.row_factory = sqlite3.Row
        created_before = conn.execute(
            "SELECT created_at FROM application_checkpoints WHERE application_key = 'app1'").fetchone()["created_at"]

    second = checkpoint.build(application_key="app1", verified_stage="Step 2 of 3")
    tracker.write_checkpoint("app1", second.to_dict())
    stored = tracker.read_checkpoint("app1")
    assert stored["verified_stage"] == "Step 2 of 3"
    with sqlite3.connect(tracker._db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT created_at, updated_at FROM application_checkpoints WHERE application_key = 'app1'").fetchone()
        assert row["created_at"] == created_before            # first-written time never changes
        assert row["updated_at"] >= created_before             # last-written time does


def test_a_corrupted_payload_reads_back_as_none_not_an_exception(tracker):
    tracker.write_checkpoint("app1", checkpoint.build(application_key="app1").to_dict())
    with tracker._connect() as conn:
        conn.execute("UPDATE application_checkpoints SET payload = ? WHERE application_key = ?",
                     ("{not valid json", "app1"))
    assert tracker.read_checkpoint("app1") is None    # never raises


def test_write_checkpoint_requires_an_application_key(tracker):
    with pytest.raises(ValueError):
        tracker.write_checkpoint("", checkpoint.build(application_key="app1").to_dict())


def test_checkpoint_identity_is_shared_with_the_submission_effect_identity_group(tracker):
    """Phase 0-B3 task section 3: the checkpoint must be tied to the SAME canonical
    application identity P0-B1's replay protection already uses -- not a second, independent
    identity scheme. Proven here by writing under an alias and reading back under the root
    (and vice versa), through the exact alias graph begin_submission_dispatch uses."""
    tracker.begin_submission_dispatch("root-key")
    tracker.record_submission_identity_alias("root-key", "alias-key")

    tracker.write_checkpoint("alias-key", checkpoint.build(application_key="alias-key", employer="Acme").to_dict())
    assert tracker.read_checkpoint("root-key")["employer"] == "Acme"

    tracker.write_checkpoint("root-key", checkpoint.build(application_key="root-key", employer="Globex").to_dict())
    assert tracker.read_checkpoint("alias-key")["employer"] == "Globex"


def test_postgres_tracker_delegates_checkpoint_storage_to_local_sqlite(monkeypatch, tmp_path):
    """Same residual, already-documented assumption as the pre-existing submission-effect
    delegation (db.py's _local_submission_tracker): checkpoint durability always lives in one
    local SQLite file, never in Postgres, whichever tracker backend is configured."""
    db = pytest.importorskip("db")
    import config
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "applications.db")
    tracker = db.PostgresTracker.__new__(db.PostgresTracker)
    record = checkpoint.build(application_key="app1", employer="Acme")
    tracker.write_checkpoint("app1", record.to_dict())
    assert tracker.read_checkpoint("app1")["employer"] == "Acme"
    assert JobTracker(tmp_path / "applications.db").read_checkpoint("app1")["employer"] == "Acme"


def test_the_sqlite_and_postgres_trackers_still_expose_the_same_checkpoint_methods():
    """Belt-and-braces alongside tests/test_tracker_without_a_server.py's own general parity
    check, which already covers this: both new methods must exist on both classes."""
    db = pytest.importorskip("db")
    for name in ("write_checkpoint", "read_checkpoint"):
        assert hasattr(JobTracker, name) and hasattr(db.PostgresTracker, name)
