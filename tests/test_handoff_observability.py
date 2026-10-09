"""Phase 0-B4: one precise, structured human-handoff representation, persisted so a restart
does not collapse a specific stop reason into nothing, and rendered where the owner actually
looks.

Discovery (docs/security/phase0-b4-discovery.md sections 11-12) found ~20 distinct
human-facing stop messages, of which only two embedded any site-identifying token (a raw
network host, never the employer's name), and confirmed `application_checkpoints.
handoff_reason` was write-only -- never read back anywhere, including the dashboard. These
tests cover `handoff.py`'s classification/composition, its integration into
`apply_flow.write_recovery_checkpoint`, the new `JobTracker.read_checkpoint_by_dedup_key`
lookup, and that the dashboard now renders it.
"""
from types import SimpleNamespace

import pytest

import checkpoint
import handoff
import job_tracker


# --- classify(): best-effort, from text that already exists ---------------------------------

@pytest.mark.parametrize("outcome_kind, reason_text, expected", [
    ("captcha", "a CAPTCHA is on the page", handoff.CAPTCHA),
    ("owner_needed", "the site wants a second factor it has no authorized way to complete: "
                      "texted you a code. Complete it there, then press Continue", handoff.SMS_MFA),
    ("owner_needed", "the site wants a second factor it has no authorized way to complete: "
                      "authenticator app. Complete it there, then press Continue", handoff.AUTHENTICATOR_MFA),
    ("owner_needed", "the site wants a second factor it has no authorized way to complete: "
                      "security key. Complete it there, then press Continue", handoff.SECURITY_KEY),
    ("owner_needed", "the site wants a second factor it has no authorized way to complete: "
                      "push notification. Complete it there, then press Continue", handoff.PUSH_APPROVAL),
    ("owner_needed", "the site says: account is locked. Unlock or reset it on the site, then press Continue",
     handoff.ACCOUNT_LOCKED),
    ("owner_needed", "an earlier attempt may already have pressed Create Account on acme.com and the run "
                      "stopped before the result was confirmed -- check whether the account now exists",
     handoff.ACCOUNT_CREATION_UNCERTAIN),
    ("owner_needed", "account creation could not be safely recorded; retry after checkpoint storage is available",
     handoff.ACCOUNT_CREATION_UNCERTAIN),
    ("owner_needed", "required field is still blank: Last Name", handoff.FIELD_REQUIRED),
    ("owner_needed", "the form will not go on; it says: Please enter your last name", handoff.VALIDATION_BLOCKER),
    ("owner_needed", "this page keeps asking the same things and is not moving on", handoff.ACTION_OUTCOME_UNKNOWN),
    ("owner_needed", "the page did not move on after several tries", handoff.ACTION_OUTCOME_UNKNOWN),
    ("owner_needed", "something else entirely", handoff.OWNER_REVIEW),
    ("", "", handoff.OTHER),
    ("owner_needed", "reconciliation: DIFFERENT_APPLICATION -- host mismatch", handoff.APPLICATION_RECOVERY),
])
def test_classify_from_existing_text_signals(outcome_kind, reason_text, expected):
    assert handoff.classify(outcome_kind=outcome_kind, reason_text=reason_text) == expected


def test_classify_never_raises_on_empty_input():
    assert handoff.classify() == handoff.OTHER


# --- compose_message(): names the employer/portal, states the reason -----------------------

def test_compose_message_leads_with_employer_and_portal():
    h = handoff.build(employer="Acme", portal="acme.wd1.myworkdayjobs.com",
                      outcome_kind="captcha", reason_text="a CAPTCHA is on the page")
    message = h.compose_message()
    assert message.startswith("Acme / acme.wd1.myworkdayjobs.com: ")
    assert "CAPTCHA" in message


def test_compose_message_without_employer_or_portal_has_no_dangling_prefix():
    h = handoff.build(outcome_kind="owner_needed", reason_text="needs your answer: Last Name")
    assert h.compose_message() == "needs your answer: Last Name"


def test_compose_message_appends_a_required_action_not_already_present():
    h = handoff.build(employer="Acme", outcome_kind="owner_needed", reason_text="the form shows an error",
                      required_action="Fix the Last Name field, then press Continue")
    message = h.compose_message()
    assert "the form shows an error" in message and "Fix the Last Name field" in message


def test_a_known_category_overrides_text_based_classification():
    h = handoff.build(outcome_kind="owner_needed", reason_text="reconciliation: DIFFERENT_APPLICATION",
                      category=handoff.APPLICATION_RECOVERY)
    assert h.category == handoff.APPLICATION_RECOVERY


def test_an_unknown_category_is_rejected():
    with pytest.raises(ValueError):
        handoff.Handoff(category="NOT_A_REAL_CATEGORY")


# --- resume_condition: how automation knows it can continue ---------------------------------

@pytest.mark.parametrize("category", [
    handoff.CAPTCHA, handoff.SMS_MFA, handoff.AUTHENTICATOR_MFA, handoff.SECURITY_KEY,
    handoff.PUSH_APPROVAL, handoff.ACCOUNT_LOCKED, handoff.ACCOUNT_CREATION_UNCERTAIN,
    handoff.FIELD_REQUIRED, handoff.VALIDATION_BLOCKER, handoff.ACTION_OUTCOME_UNKNOWN,
    handoff.APPLICATION_RECOVERY, handoff.OWNER_REVIEW, handoff.OTHER,
])
def test_every_category_has_its_own_resume_condition(category):
    h = handoff.build(outcome_kind="owner_needed", reason_text="x", category=category)
    assert h.resume_condition and h.resume_condition == handoff.resume_condition_for(category)


# --- to_checkpoint_fields(): what gets persisted ---------------------------------------------

def test_to_checkpoint_fields_carries_employer_and_all_three_new_fields():
    h = handoff.build(employer="Acme", portal="acme.com", outcome_kind="captcha", reason_text="a CAPTCHA is showing")
    fields = h.to_checkpoint_fields()
    assert fields["employer"] == "Acme"
    assert fields["handoff_category"] == handoff.CAPTCHA
    assert fields["handoff_resume_condition"] == handoff.resume_condition_for(handoff.CAPTCHA)
    assert "Acme" in fields["handoff_reason"]


# --- checkpoint.py: the three new fields round-trip through parse()/merge_update() ----------

def test_handoff_fields_round_trip_through_checkpoint_build_and_parse():
    record = checkpoint.build(
        application_key="app1", handoff_category="SMS_MFA",
        handoff_required_action="Complete the SMS code", handoff_resume_condition="MFA clears")
    status, parsed = checkpoint.parse(record.to_dict())
    assert status == checkpoint.COMPATIBLE
    assert parsed.handoff_category == "SMS_MFA"
    assert parsed.handoff_required_action == "Complete the SMS code"
    assert parsed.handoff_resume_condition == "MFA clears"


def test_a_checkpoint_from_before_these_fields_existed_still_parses():
    """An old payload with no handoff_category/required_action/resume_condition keys at all
    must still parse as COMPATIBLE, with safe empty defaults -- not a reason to call it
    unsupported."""
    old_payload = checkpoint.build(application_key="app1").to_dict()
    for key in ("handoff_category", "handoff_required_action", "handoff_resume_condition"):
        del old_payload[key]
    status, parsed = checkpoint.parse(old_payload)
    assert status == checkpoint.COMPATIBLE
    assert parsed.handoff_category == "" and parsed.handoff_required_action == ""


# --- job_tracker.py: the dedup_key lookup the dashboard uses ---------------------------------

@pytest.fixture
def tracker(tmp_path):
    return job_tracker.JobTracker(tmp_path / "applications.db")


def test_read_checkpoint_by_dedup_key_finds_what_write_checkpoint_stored(tracker):
    record = checkpoint.build(application_key="app1", dedup_key="k1", handoff_category="CAPTCHA")
    tracker.write_checkpoint("app1", record.to_dict())
    found = tracker.read_checkpoint_by_dedup_key("k1")
    assert found and found["handoff_category"] == "CAPTCHA"


def test_read_checkpoint_by_dedup_key_is_none_when_nothing_was_ever_written(tracker):
    assert tracker.read_checkpoint_by_dedup_key("never-written") is None


def test_read_checkpoint_by_dedup_key_finds_the_most_recently_updated_one(tracker):
    tracker.write_checkpoint("app1", checkpoint.build(application_key="app1", dedup_key="k1",
                                                       handoff_category="CAPTCHA").to_dict())
    tracker.write_checkpoint("app1", checkpoint.build(application_key="app1", dedup_key="k1",
                                                       handoff_category="SMS_MFA").to_dict())
    assert tracker.read_checkpoint_by_dedup_key("k1")["handoff_category"] == "SMS_MFA"


# --- apply_flow.write_recovery_checkpoint: owner_handoff and uncertain-action union ---------

def test_write_recovery_checkpoint_with_owner_handoff_populates_the_new_fields(tracker):
    import apply_flow
    tracker.create(dedup_key="k1", title="Engineer", company="Acme", location="Remote",
                   url="https://jobs.example.com/apply")
    assistant = SimpleNamespace(submission_key="app1")
    job = SimpleNamespace(company="Acme", url="https://jobs.example.com/apply")
    page = SimpleNamespace(url="https://jobs.example.com/apply/step2")
    owner_handoff = handoff.build(application_key="app1", employer="Acme", portal="jobs.example.com",
                                  outcome_kind="captcha", reason_text="a CAPTCHA is on the page")
    apply_flow.write_recovery_checkpoint(tracker, assistant, "k1", page, job, owner_handoff=owner_handoff)
    stored = tracker.read_checkpoint("app1")
    assert stored["handoff_category"] == handoff.CAPTCHA
    assert "Acme" in stored["handoff_reason"]


def test_write_recovery_checkpoint_unions_new_uncertain_actions_onto_existing(tracker):
    import apply_flow
    tracker.write_checkpoint("app1", checkpoint.build(
        application_key="app1", uncertain_actions=("entries:Education@acme.com",)).to_dict())
    assistant = SimpleNamespace(submission_key="app1")
    job = SimpleNamespace(company="Acme", url="https://jobs.example.com/apply")
    page = SimpleNamespace(url="https://jobs.example.com/apply")
    apply_flow.write_recovery_checkpoint(
        tracker, assistant, "k1", page, job, new_uncertain_actions=("entries:Work Experience@acme.com",))
    stored = tracker.read_checkpoint("app1")
    assert set(stored["uncertain_actions"]) == {"entries:Education@acme.com", "entries:Work Experience@acme.com"}


def test_write_recovery_checkpoint_with_no_new_uncertain_actions_preserves_existing_ones(tracker):
    import apply_flow
    tracker.write_checkpoint("app1", checkpoint.build(
        application_key="app1", uncertain_actions=("entries:Education@acme.com",)).to_dict())
    assistant = SimpleNamespace(submission_key="app1")
    job = SimpleNamespace(company="Acme", url="https://jobs.example.com/apply")
    page = SimpleNamespace(url="https://jobs.example.com/apply")
    apply_flow.write_recovery_checkpoint(tracker, assistant, "k1", page, job)
    stored = tracker.read_checkpoint("app1")
    assert stored["uncertain_actions"] == ["entries:Education@acme.com"]


# --- the dashboard renders the handoff, closing the confirmed write-only gap ----------------

def test_the_dashboard_detail_page_renders_the_handoff(tmp_path, monkeypatch):
    import web_ui
    tracker = job_tracker.JobTracker(tmp_path / "applications.db")
    tracker.create(dedup_key="k1", title="Engineer", company="Acme", location="Remote",
                   url="https://jobs.example.com/apply")
    record = checkpoint.build(
        application_key="app1", dedup_key="k1", employer="Acme",
        handoff_category="SMS_MFA", handoff_reason="Acme / jobs.example.com: SMS verification is required",
        handoff_required_action="Complete the SMS code on the employer page",
        handoff_resume_condition="the account step no longer asks for an SMS/text code")
    tracker.write_checkpoint("app1", record.to_dict())
    monkeypatch.setattr(web_ui, "get_tracker", lambda: tracker)
    import profile_setup
    monkeypatch.setattr(profile_setup, "needs_setup", lambda: False)
    web_ui.app.config["TESTING"] = True
    client = web_ui.app.test_client()
    app_id = tracker.get("k1").id
    response = client.get(f"/application/{app_id}")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Sms Mfa" in body or "SMS" in body.upper()
    assert "Complete the SMS code on the employer page" in body
    assert "the account step no longer asks for an SMS/text code" in body


def test_the_dashboard_shows_nothing_extra_when_there_is_no_checkpoint(tmp_path, monkeypatch):
    import web_ui
    tracker = job_tracker.JobTracker(tmp_path / "applications.db")
    tracker.create(dedup_key="k1", title="Engineer", company="Acme", location="Remote",
                   url="https://jobs.example.com/apply")
    monkeypatch.setattr(web_ui, "get_tracker", lambda: tracker)
    import profile_setup
    monkeypatch.setattr(profile_setup, "needs_setup", lambda: False)
    web_ui.app.config["TESTING"] = True
    client = web_ui.app.test_client()
    app_id = tracker.get("k1").id
    response = client.get(f"/application/{app_id}")
    assert response.status_code == 200
    assert "What to do" not in response.data.decode("utf-8")
