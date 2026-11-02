"""An owner email change must not inherit another account or stale attachments."""
from types import SimpleNamespace

import pytest

from browser_automation import JobApplicationAssistant
from job_tracker import JobTracker


@pytest.mark.parametrize("recorded, expected", [
    ("old@example.com", False), ("NEW@example.com", True),
])
def test_account_expectation_uses_configured_identity(recorded, expected):
    assistant = object.__new__(JobApplicationAssistant)
    assistant._config = SimpleNamespace(ats_email="new@example.com")
    assistant.employer = "Example"
    assistant.tracker = SimpleNamespace(
        get_ats_account=lambda employer: {"email": recorded},
        applied_before=lambda employer: True,
    )
    assert assistant.account_on_record() is expected
    assert assistant.account_expected() is expected


def test_successful_new_identity_is_remembered_without_changing_submissions(tmp_path):
    tracker = JobTracker(tmp_path / "accounts.db")
    tracker.create(dedup_key="submitted", title="Engineer", company="Example")
    tracker.update_status("submitted", "submitted")
    tracker.record_ats_account("Example", "old@example.com", "jobs.example.com", "login")
    tracker.record_ats_account("Example", "new@example.com", "jobs.example.com", "create")
    assert tracker.get_ats_account("Example")["email"] == "new@example.com"
    assert tracker.applied_before("Example")
    assert tracker.get("submitted").status == "submitted"


def test_cached_resume_with_previous_email_is_regenerated(setting, monkeypatch):
    from test_resume_written_once_per_job import prepare
    import apply_flow
    prepare(setting)
    monkeypatch.setattr(apply_flow, "parse_resume", lambda path: SimpleNamespace(
        raw_text="Jane Doe\nEngineer\nold@example.com\nNetwork engineer"))
    prepare(setting)
    assert setting.writer.calls == 2


def test_generic_fallback_updates_email_without_modifying_source(setting, monkeypatch):
    from test_resume_written_once_per_job import prepare
    import apply_flow
    original = setting.folder.parent / "original.pdf"
    original.write_bytes(b"source preserved")
    monkeypatch.setattr(apply_flow.config_module, "RESUME_PATH", str(original))
    monkeypatch.setattr(apply_flow.ai_choice, "writers", lambda cfg: [])
    monkeypatch.setattr(apply_flow, "parse_resume", lambda path: SimpleNamespace(
        raw_text="Jane Doe\nEngineer\nold@example.com\n\nSUMMARY\nNetwork engineer"))
    result = prepare(setting)
    text = result.with_suffix(".txt").read_text(encoding="utf-8")
    assert "jane@example.com" in text and "old@example.com" not in text
    assert original.read_bytes() == b"source preserved"
    assert result != original


from test_resume_written_once_per_job import setting  # noqa: E402,F401
