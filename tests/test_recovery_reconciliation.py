"""Phase 0-B3: resume reconciliation -- comparing a durable checkpoint against live evidence.

Current browser/account/submission evidence always outranks the checkpoint, never the
reverse (task sections 10, 12-16, 38-39). `recovery.reconcile` takes already-computed values
(the same shape as `account_state.read_state` taking a snapshot string rather than a page),
so every case here is a pure function test -- no browser needed.
"""
from types import SimpleNamespace

import pytest

import checkpoint
import recovery


def built(**kw) -> dict:
    defaults = dict(application_key="app1", page_identity={"host": "acme.wd1.myworkdayjobs.com",
                                                            "step_indicator": "Step 2 of 5"})
    defaults.update(kw)
    return checkpoint.build(**defaults).to_dict()


def identity(host="acme.wd1.myworkdayjobs.com", step="Step 2 of 5") -> recovery.PageIdentity:
    return recovery.PageIdentity(host=host, step_indicator=step)


# --- outcome precedence: confirmed submission wins over everything -------------------------

def test_a_confirmed_submission_wins_even_with_no_checkpoint_at_all():
    result = recovery.reconcile(stored_payload=None, live_identity=identity(),
                                live_account_state_kind="SIGNED_IN", live_submission_effect_state="CONFIRMED")
    assert result.outcome == recovery.SUBMITTED


def test_a_confirmed_submission_wins_even_over_a_stale_or_garbage_checkpoint():
    result = recovery.reconcile(stored_payload={"garbage": True}, live_identity=identity(),
                                live_account_state_kind="SIGNED_IN", live_submission_effect_state="CONFIRMED")
    assert result.outcome == recovery.SUBMITTED


# --- no checkpoint / unusable checkpoint -----------------------------------------------------

def test_no_checkpoint_is_its_own_outcome_not_an_error():
    result = recovery.reconcile(stored_payload=None, live_identity=identity(), live_account_state_kind="SIGNED_IN")
    assert result.outcome == recovery.NO_CHECKPOINT


@pytest.mark.parametrize("payload", [{"no": "application_key"}, {"application_key": "a", "schema_version": 999}])
def test_an_unparseable_checkpoint_is_unsupported(payload):
    result = recovery.reconcile(stored_payload=payload, live_identity=identity(), live_account_state_kind="SIGNED_IN")
    assert result.outcome == recovery.UNSUPPORTED


# --- application not found / auth required outrank stage comparison ------------------------

def test_application_not_found_is_checked_before_stage_comparison():
    result = recovery.reconcile(stored_payload=built(), live_identity=identity(step="Step 4 of 5"),
                                live_account_state_kind="SIGNED_IN", application_present=False)
    assert result.outcome == recovery.APPLICATION_NOT_FOUND


@pytest.mark.parametrize("kind", ["MFA_REQUIRED", "CODE_ENTRY", "SIGN_IN_FORM", "LOCKED", "CREATE_FORM"])
def test_any_unfinished_account_step_is_auth_required(kind):
    result = recovery.reconcile(stored_payload=built(account_state="SIGNED_IN"), live_identity=identity(),
                                live_account_state_kind=kind)
    assert result.outcome == recovery.AUTH_REQUIRED


def test_checkpoint_authenticated_never_overrides_a_live_sign_in_required():
    """Task section 38's literal example: checkpoint AUTHENTICATED + live SIGN_IN_REQUIRED
    means SIGN_IN_REQUIRED. (This repo's own account_state kind for that is SIGN_IN_FORM.)"""
    result = recovery.reconcile(stored_payload=built(account_state="SIGNED_IN"), live_identity=identity(),
                                live_account_state_kind="SIGN_IN_FORM")
    assert result.outcome == recovery.AUTH_REQUIRED


def test_auth_required_is_checked_before_a_host_mismatch():
    result = recovery.reconcile(
        stored_payload=built(page_identity={"host": "other.example.com", "step_indicator": "Step 2 of 5"}),
        live_identity=identity(), live_account_state_kind="MFA_REQUIRED")
    assert result.outcome == recovery.AUTH_REQUIRED


# --- different application ------------------------------------------------------------------

def test_a_different_host_is_a_different_application():
    result = recovery.reconcile(
        stored_payload=built(page_identity={"host": "other.example.com", "step_indicator": "Step 2 of 5"}),
        live_identity=identity(), live_account_state_kind="SIGNED_IN")
    assert result.outcome == recovery.DIFFERENT_APPLICATION


def test_a_checkpoint_with_no_recorded_host_is_not_treated_as_a_mismatch():
    """An older/thinner checkpoint that never recorded a host is evidence that simply says
    nothing about identity -- it must not be misread as 'different application.'"""
    result = recovery.reconcile(stored_payload=built(page_identity={"step_indicator": "Step 2 of 5"}),
                                live_identity=identity(), live_account_state_kind="SIGNED_IN")
    assert result.outcome == recovery.MATCH


# --- stage comparison: MATCH / AHEAD / BEHIND / UNKNOWN -------------------------------------

def test_identical_step_text_is_a_match():
    result = recovery.reconcile(stored_payload=built(), live_identity=identity(), live_account_state_kind="SIGNED_IN")
    assert result.outcome == recovery.MATCH


def test_a_later_step_number_is_ahead_never_replayed_backward():
    result = recovery.reconcile(stored_payload=built(), live_identity=identity(step="Step 4 of 5"),
                                live_account_state_kind="SIGNED_IN")
    assert result.outcome == recovery.AHEAD


def test_an_earlier_step_number_is_behind():
    result = recovery.reconcile(stored_payload=built(page_identity={"host": "acme.wd1.myworkdayjobs.com",
                                                                     "step_indicator": "Step 4 of 5"}),
                                live_identity=identity(step="Step 2 of 5"), live_account_state_kind="SIGNED_IN")
    assert result.outcome == recovery.BEHIND


def test_step_totals_that_disagree_are_not_comparable():
    """'Step 2 of 5' vs 'Step 2 of 6': the wizard itself seems to disagree about its own
    length -- not a case to guess a direction for."""
    result = recovery.reconcile(stored_payload=built(), live_identity=identity(step="Step 2 of 6"),
                                live_account_state_kind="SIGNED_IN")
    assert result.outcome == recovery.OUTCOME_UNKNOWN


def test_unparseable_step_labels_default_to_unknown_not_a_guess():
    result = recovery.reconcile(
        stored_payload=built(page_identity={"host": "acme.wd1.myworkdayjobs.com", "step_indicator": "Review"}),
        live_identity=identity(step="Payment"), live_account_state_kind="SIGNED_IN")
    assert result.outcome == recovery.OUTCOME_UNKNOWN


# --- parse_step_ordinal ----------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Step 2 of 5", (2, 5)),
    ("2 of 5", (2, 5)),
    ("Step 2/5", (2, 5)),
    ("2/5", (2, 5)),
    ("Review", None),
    ("", None),
    (None, None),
])
def test_parse_step_ordinal(text, expected):
    assert recovery.parse_step_ordinal(text) == expected


# --- compute_page_identity: live evidence only, no secrets, no full-DOM hash ----------------

class FakePage:
    def __init__(self, url, step_text=""):
        self.url = url
        self._step_text = step_text

    def evaluate(self, _js):
        return self._step_text


def test_compute_page_identity_reads_host_and_step_indicator():
    page = FakePage("https://acme.wd1.myworkdayjobs.com/apply/step2", step_text="Step 2 of 5")
    found = recovery.compute_page_identity(page)
    assert found.host == "acme.wd1.myworkdayjobs.com" and found.step_indicator == "Step 2 of 5"


def test_compute_page_identity_never_raises_when_the_page_cannot_be_read():
    class BrokenPage:
        url = "https://acme.wd1.myworkdayjobs.com/apply"

        def evaluate(self, _js):
            raise RuntimeError("page is navigating")

    found = recovery.compute_page_identity(BrokenPage())
    assert found.host == "acme.wd1.myworkdayjobs.com" and found.step_indicator == ""
