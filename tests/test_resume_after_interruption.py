"""Phase 0-B3 (closure correction): a process restart must not blindly repeat a
non-idempotent action, and the guard against that must not be erasable by an unrelated
checkpoint write, nor bypassable by a storage failure.

Account creation is the one consequential action discovery found with no durable guard
before this phase (docs/security/phase0-b3-discovery.md section 8). The existing in-memory
`_created_at` guard (account_state.py: "the new-account form is still showing after the
account was created") already protects a *second call on the same PageAgent object* --
tests/test_account_step.py's test_a_new_account_form_creates_the_account_once covers that.
These tests cover what it does not: a second, independent PageAgent instance -- standing in
for a resumed process, sharing nothing in memory with the first -- that only has the durable
tracker in common; a generic, unrelated checkpoint write landing in between; a checkpoint
store that exists but fails; and a live page state that proves nothing either way.
"""
from types import SimpleNamespace

import pytest

import apply_flow
import config
import job_tracker
import page_agent


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


class Assistant:
    def __init__(self, gate=True):
        self._login_paused, self._gate, self.calls = "", gate, []
        self._last_login_rejected = False
        self.submission_key = "subkey-restart-1"

    def adapter(self, tab):
        return SimpleNamespace(candidate_account_state=lambda tab: "")

    def account_on_record(self):
        return False

    def handle_auth_gate(self, tab, email):
        self.calls.append(("gate", email))
        return self._gate


CREATE = """<h2>Create Account</h2><label>Email Address<input type="email"></label>
<label>Password<input type="password"></label><label>Verify New Password<input type="password"></label>
<button>Create Account</button>"""
# Positive, deterministic evidence the create form is behind us: account_state.read_state()
# reads a "current step N of M" indicator with no password box and no account-step wording
# nearby as SIGNED_IN -- "past the account step: the application itself" (account_state.py).
SIGNED_IN_PAGE = "<p>current step 2 of 5</p><h2>My Information</h2><label>First Name<input></label>"
# Ambiguous/transient: account_state.py's own LOADING ("the account step, still drawing
# itself") -- on_account_step wording with no form yet. Proves nothing either way.
LOADING_PAGE = "<p>Create Account / Sign In</p>"


def open_page(browser, body):
    context = browser.new_context()
    pg = context.new_page()
    pg.route("https://jobs.example.com/**", lambda route: route.fulfill(
        status=200, content_type="text/html", body=f"<html><body>{body}</body></html>"))
    pg.goto("https://jobs.example.com/apply")
    return pg


def new_agent(assistant, tracker, tmp_path):
    """A fresh PageAgent with no in-memory history -- standing in for a freshly-started
    process, the same way tests/test_login_guard.py's own restart test ("a new run: a new
    object") stands in for one."""
    job = SimpleNamespace(title="Engineer", company="Acme", url="https://jobs.example.com/apply")
    return page_agent.PageAgent(
        assistant, SimpleNamespace(), SimpleNamespace(auto_submit=False, ats_email="jane@example.com"),
        config.UserProfile(email="jane@example.com"), SimpleNamespace(raw_text="x"), job,
        tracker=tracker, key="k1", resume_file=None, job_dir=tmp_path)


def step(page, agent):
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    return agent.sign_in_step(page, controls)


# --- A: the full production checkpoint boundary, not just a direct sign_in_step() call ------

def test_the_generic_production_checkpoint_write_does_not_erase_an_unresolved_dispatch(browser, tmp_path):
    """Exercises the SAME generic checkpoint writer production uses
    (apply_flow.write_recovery_checkpoint, called once per agent.run() pass) in between the
    dispatch and the restart -- not only a direct sign_in_step() call. This is the exact
    defect: the generic writer used to reconstruct a fresh checkpoint with
    pending_action="" by default, silently erasing the marker on the very next ordinary
    progress write."""
    tracker = job_tracker.JobTracker(tmp_path / "applications.db")
    tracker.create(dedup_key="k1", title="Engineer", company="Acme", location="Remote",
                   url="https://jobs.example.com/apply")
    assistant = Assistant(gate=True)
    job = SimpleNamespace(title="Engineer", company="Acme", url="https://jobs.example.com/apply")

    first_page = open_page(browser, CREATE)
    first = new_agent(assistant, tracker, tmp_path)
    assert step(first_page, first) is True
    assert assistant.calls == [("gate", "jane@example.com")]
    assert tracker.read_checkpoint(assistant.submission_key)["pending_action"] == \
        "account_creation_dispatched@jobs.example.com"

    # The generic, once-per-pass progress checkpoint runs next, exactly as
    # apply_flow.run_page_agent() does after agent.run() returns -- with its own, unrelated
    # progress facts (a new page URL, a handoff reason), nothing about account creation.
    apply_flow.write_recovery_checkpoint(
        tracker, assistant, "k1", first_page, job, handoff_reason="automatic submission is off")
    stored = tracker.read_checkpoint(assistant.submission_key)
    assert stored["pending_action"] == "account_creation_dispatched@jobs.example.com", \
        "the generic checkpoint write erased the unresolved account-creation marker"
    assert stored["handoff_reason"] == "automatic submission is off"      # the update itself still took

    # A fresh, independent PageAgent -- standing in for a resumed process -- reopens the
    # still-showing create form. It must not press Create Account again.
    second_page = open_page(browser, CREATE)
    second = new_agent(assistant, tracker, tmp_path)
    assert step(second_page, second) is False
    assert "earlier attempt" in second.account_blocker and "already" in second.account_blocker
    assert assistant.calls == [("gate", "jane@example.com")]   # not called again


# --- B: a generic checkpoint update never erases sticky lifecycle fields -------------------

def test_a_generic_checkpoint_update_preserves_pending_action_and_uncertain_actions(tmp_path):
    import checkpoint
    tracker = job_tracker.JobTracker(tmp_path / "applications.db")
    seeded = checkpoint.build(
        application_key="app1", pending_action="account_creation_dispatched@jobs.example.com",
        uncertain_actions=("some_other_unresolved_thing",))
    tracker.write_checkpoint("app1", seeded.to_dict())

    job = SimpleNamespace(title="Engineer", company="Acme", url="https://jobs.example.com/apply/step2")
    assistant = SimpleNamespace(submission_key="app1")
    page = SimpleNamespace(url="https://jobs.example.com/apply/step2")
    apply_flow.write_recovery_checkpoint(tracker, assistant, "k1", page, job, handoff_reason="paused")

    stored = tracker.read_checkpoint("app1")
    assert stored["pending_action"] == "account_creation_dispatched@jobs.example.com"
    assert stored["uncertain_actions"] == ["some_other_unresolved_thing"]
    assert stored["handoff_reason"] == "paused"                    # the ordinary update still applied
    assert stored["page_url"] == "https://jobs.example.com/apply/step2"


# --- C: explicit, positive resolution clears it, and a later create is no longer blocked ---

def test_the_marker_clears_only_on_positive_evidence_the_create_form_is_behind_us(browser, tmp_path):
    tracker = job_tracker.JobTracker(tmp_path / "applications.db")
    tracker.create(dedup_key="k1", title="Engineer", company="Acme", location="Remote",
                   url="https://jobs.example.com/apply")
    assistant = Assistant(gate=True)

    first = new_agent(assistant, tracker, tmp_path)
    step(open_page(browser, CREATE), first)
    assert tracker.read_checkpoint(assistant.submission_key)["pending_action"]

    # Positive, deterministic evidence (SIGNED_IN) -- not merely "not CREATE_FORM."
    second = new_agent(assistant, tracker, tmp_path)
    step(open_page(browser, SIGNED_IN_PAGE), second)
    assert tracker.read_checkpoint(assistant.submission_key)["pending_action"] == ""


def test_a_third_process_may_create_the_account_once_the_marker_is_resolved(browser, tmp_path):
    """Resolving the marker does not itself forbid a later, legitimate Create Account -- it
    only blocks retrying while the previous attempt's outcome is still unverified."""
    tracker = job_tracker.JobTracker(tmp_path / "applications.db")
    tracker.create(dedup_key="k1", title="Engineer", company="Acme", location="Remote",
                   url="https://jobs.example.com/apply")
    assistant = Assistant(gate=True)

    step(open_page(browser, CREATE), new_agent(assistant, tracker, tmp_path))
    step(open_page(browser, SIGNED_IN_PAGE), new_agent(assistant, tracker, tmp_path))
    assert assistant.calls == [("gate", "jane@example.com")]

    third = new_agent(assistant, tracker, tmp_path)
    assert step(open_page(browser, CREATE), third) is True
    assert assistant.calls == [("gate", "jane@example.com"), ("gate", "jane@example.com")]


# --- D: a transient/ambiguous state must NOT clear it ---------------------------------------

def test_a_loading_or_ambiguous_state_does_not_clear_the_marker(browser, tmp_path):
    tracker = job_tracker.JobTracker(tmp_path / "applications.db")
    tracker.create(dedup_key="k1", title="Engineer", company="Acme", location="Remote",
                   url="https://jobs.example.com/apply")
    assistant = Assistant(gate=True)

    step(open_page(browser, CREATE), new_agent(assistant, tracker, tmp_path))
    assert tracker.read_checkpoint(assistant.submission_key)["pending_action"]

    # The account step is still just drawing itself (account_state.LOADING) -- proves
    # nothing about whether the earlier create attempt succeeded.
    second = new_agent(assistant, tracker, tmp_path)
    step(open_page(browser, LOADING_PAGE), second)
    assert tracker.read_checkpoint(assistant.submission_key)["pending_action"] == \
        "account_creation_dispatched@jobs.example.com"


# --- E / F: a configured tracker that fails is fail-closed, never fail-open -----------------

class RaisingReadTracker:
    def read_checkpoint(self, key):
        raise RuntimeError("checkpoint storage unavailable")

    def write_checkpoint(self, key, payload):
        raise AssertionError("write_checkpoint must not be reached when the read already failed")


class RaisingWriteTracker:
    def read_checkpoint(self, key):
        return None

    def write_checkpoint(self, key, payload):
        raise RuntimeError("checkpoint storage unavailable")


def test_a_checkpoint_read_failure_blocks_account_creation_rather_than_assuming_none_pending(browser, tmp_path):
    assistant = Assistant(gate=True)
    agent = new_agent(assistant, RaisingReadTracker(), tmp_path)
    assert step(open_page(browser, CREATE), agent) is False
    assert "could not be safely recorded" in agent.account_blocker
    assert assistant.calls == []          # handle_auth_gate was never reached


def test_a_checkpoint_write_failure_blocks_account_creation_rather_than_proceeding_unguarded(browser, tmp_path):
    assistant = Assistant(gate=True)
    agent = new_agent(assistant, RaisingWriteTracker(), tmp_path)
    assert step(open_page(browser, CREATE), agent) is False
    assert "could not be safely recorded" in agent.account_blocker
    assert assistant.calls == []          # handle_auth_gate was never reached


# --- G: no tracker configured at all is the project's own, different, preserved case -------

def test_no_tracker_does_not_block_account_creation(browser, tmp_path):
    """A run with no tracker configured (a test double, or tracking genuinely unavailable)
    behaves exactly as before this phase -- the lifecycle is additive, never a new required
    dependency for account creation to proceed at all. This is deliberately NOT the same as
    tests E/F above: "no tracker configured" and "a configured tracker's read/write failed"
    are different situations with different safe behaviors, by design -- see
    PageAgent._pending_account_creation / ._mark_account_creation_dispatched."""
    assistant = Assistant(gate=True)
    agent = new_agent(assistant, None, tmp_path)
    assert step(open_page(browser, CREATE), agent) is True
    assert assistant.calls == [("gate", "jane@example.com")]
