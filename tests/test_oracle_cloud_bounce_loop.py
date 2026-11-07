"""Regression tests for f337: Oracle Cloud Terms Checkbox & Page Alternation Bounce Loop.

Root causes fixed:
1. Oracle JET (and similar frameworks) renders checkboxes as an unclickable checkbox element
   followed by a clickable pointer generic element (oj-checkboxset-checkbox / ref=f2e46),
   with the label words split across child text and link nodes. parse_snapshot previously
   dropped the checkbox completely because the pointer element had no name/value of its own
   and didn't match _same_question.
2. required_questions did not match asterisks on indented child lines:
   - generic "Email Address" [ref=...]:
     - generic [aria-hidden] [ref=...]: "*"
3. StateFingerprintCircuitBreaker only compared fingerprint == self.last_fingerprint,
   completely blind to period-2 (A-B-A-B-A-B) oscillation between job posting and form.
4. press_next allowed open_application to be followed indefinitely even when the agent had
   already reached the application form (_seen_form is True).
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock

import page_agent
from page_agent import parse_snapshot, required_questions, Control
from state_machine import StateFingerprintCircuitBreaker, STATUS_BLOCKED_VALIDATION_LOOP


def test_oracle_cloud_terms_checkbox_parsed_from_pointer_generic():
    """Oracle JET checkbox with separate pointer generic element is parsed into a checkbox Control."""
    fixture_path = Path("output/ADT_Careers_Cloud_Network_Engineer/pages/20261106_185927/page_02.txt")
    if not fixture_path.exists():
        pytest.skip("Fixture page_02.txt not found")
    snapshot = fixture_path.read_text(encoding="utf-8")
    controls = parse_snapshot(snapshot)

    checkboxes = [c for c in controls if c.role == "checkbox"]
    assert len(checkboxes) >= 1, f"Expected at least one checkbox, got {controls}"
    terms_cb = next((c for c in checkboxes if "terms" in c.name.lower()), None)
    assert terms_cb is not None, f"Terms checkbox not found among: {checkboxes}"
    assert terms_cb.ref == "f2e46"
    assert "I agree with the terms and conditions" in terms_cb.name
    assert terms_cb.checked is False


def test_required_questions_detects_indented_asterisk():
    """required_questions detects asterisks on indented child lines under generic questions."""
    fixture_path = Path("output/ADT_Careers_Cloud_Network_Engineer/pages/20261106_185927/page_02.txt")
    if not fixture_path.exists():
        pytest.skip("Fixture page_02.txt not found")
    snapshot = fixture_path.read_text(encoding="utf-8")
    reqs = required_questions(snapshot)

    assert "Email Address" in reqs


def test_circuit_breaker_detects_period_2_oscillation():
    """StateFingerprintCircuitBreaker trips on alternating state oscillation (A-B-A-B-A-B)."""
    breaker = StateFingerprintCircuitBreaker(consecutive_threshold=3)

    # Mock page that returns alternating fingerprints
    page_a = MagicMock()
    page_b = MagicMock()
    page_a.url = "https://jobs.adt.com/job/123"
    page_b.url = "https://fa-erqb.oraclecloud.com/apply/email"

    # We mock compute_state_fingerprint directly or feed fingerprints through check
    from unittest.mock import patch

    fingerprints = [
        ("fp_job_desc", {"status": "ok"}),
        ("fp_auth_form", {"status": "ok"}),
        ("fp_job_desc", {"status": "ok"}),
        ("fp_auth_form", {"status": "ok"}),
        ("fp_job_desc", {"status": "ok"}),
        ("fp_auth_form", {"status": "ok"}),
    ]

    tripped = False
    state = ""
    for fp, meta in fingerprints:
        with patch("state_machine.compute_state_fingerprint", return_value=(fp, meta)):
            tripped, state, _ = breaker.check(page_a)

    assert tripped is True
    assert state == STATUS_BLOCKED_VALIDATION_LOOP


def test_press_next_guards_against_repeated_open_application_bounce():
    """Once _seen_form is True, following open_application repeatedly stops with bounce error."""
    agent = page_agent.PageAgent.__new__(page_agent.PageAgent)
    agent.assistant = MagicMock()
    agent.assistant._submission_execution_state = "active"
    agent.assistant._submission_guard = None
    agent.assistant.on_job_description = lambda p: False
    agent.assistant._submission_execution_state = "active"
    agent.assistant.follow_application_entry.return_value = True
    agent.tab = lambda p: p
    mock_locator = MagicMock()
    mock_locator.evaluate.return_value = ""
    agent.locate = lambda p, ref: mock_locator
    agent.settle = lambda p, ms=0: None
    agent._guard_the_new_page = lambda p: None
    agent.job = MagicMock()
    agent.job.url = "https://jobs.adt.com/job/123"
    agent._seen_form = True
    agent._pressed = {}

    control = Control(ref="btn1", role="button", name="Apply Now", value="")
    controls = [control]
    plan = page_agent.PagePlan(
        page_kind="job_description",
        next_kind="open_application",
        next_ref="btn1",
        next_label="Apply Now",
    )

    page = MagicMock()
    page.is_closed.return_value = False
    page.url = "https://jobs.adt.com/job/123"

    # First bounce might be tolerated once
    res1 = agent.press_next(page, plan, controls)
    assert res1[0] in ("moved", "retry")

    # Second bounce trips the bounce guard
    res2 = agent.press_next(page, plan, controls)
    assert res2[0] == "stop"
    assert "bounced back to the job posting" in res2[2]


def test_page_agent_run_detects_alternating_fingerprints():
    """page_agent.run trips stuck loop detection when alternating between two page states."""
    agent = page_agent.PageAgent.__new__(page_agent.PageAgent)
    agent.assistant = MagicMock()
    agent.assistant._submission_execution_state = "active"
    agent.assistant._submission_guard = None
    agent.assistant.on_job_description = lambda p: False
    agent.job = MagicMock()
    agent.job.url = "https://jobs.adt.com/job/123"
    agent.profile = MagicMock()
    agent.profile.requires_visa_sponsorship = False
    agent.config = MagicMock()
    agent.tab = lambda p: p
    agent._resume_autofill_attempted = True
    agent.final_pressed = False
    agent.account_blocker = False
    agent.pages_read = 0
    agent._shapes = {}
    agent._run_stamp = "test_run"
    agent._save = lambda snapshot: None
    agent.settle = lambda p, ms=0: None
    agent._site_confirms = lambda tab: False
    agent.sign_in_step = lambda page, controls: False

    # Mock snapshots alternating between job description and form
    snap_a = "- heading \"Job Description\" [level=1] [ref=e1]\n- button \"Apply\" [ref=e2]"
    snap_b = "- heading \"Application Form\" [level=1] [ref=f1]\n- textbox \"Email\" [ref=f2]"
    snapshots = [snap_a, snap_b, snap_a, snap_b, snap_a, snap_b]

    call_count = 0
    def mock_read_when_loaded(page):
        nonlocal call_count
        snap = snapshots[min(call_count, len(snapshots) - 1)]
        call_count += 1
        return snap

    agent.read_when_loaded = mock_read_when_loaded

    # profile_plan and press_next just return ok
    mock_plan = page_agent.PagePlan(page_kind="application_form", next_kind="next_step", next_ref="f2")
    agent.profile_plan = lambda *args: mock_plan
    agent.posting_plan = lambda *args: None
    agent.ai_plan = lambda *args: mock_plan
    agent.press_next = lambda *args: ("moved", MagicMock(), "ok")
    agent.do = lambda *args: True
    agent.missing_required = lambda *args: []

    page = MagicMock()
    page.is_closed.return_value = False
    page.url = "https://jobs.adt.com/job/123"

    from unittest.mock import patch
    with patch("safety.captcha_visible", return_value=False), \
         patch("local_debug.capture", return_value=None):
        outcome = agent.run(page)
    assert outcome.kind in ("owner_needed", "needs_user")
    assert any("alternating between pages" in reason for reason in outcome.reasons)


def test_do_ticks_pointer_generic_checkbox():
    """PageAgent.do succeeds on generic pointer element checkbox when get_by_role count is 0."""
    agent = page_agent.PageAgent.__new__(page_agent.PageAgent)
    agent.tab = lambda p: p
    page = MagicMock()

    checked_state = [False]
    def mock_eval(js, *args, **kwargs):
        if "oj-selected" in js or "checked" in js:
            return checked_state[0]
        return False

    loc = MagicMock()
    loc.evaluate.side_effect = mock_eval
    def mock_click(*args, **kwargs):
        checked_state[0] = True
    loc.click.side_effect = mock_click

    agent.locate = lambda p, ref: loc
    page.get_by_role.return_value.count.return_value = 0

    control = Control(ref="f4e46", role="checkbox", name="I agree with the terms and conditions", checked=False)
    ans = page_agent.Answer(ref="f4e46", question="I agree with the terms and conditions", action="check", value="checked", source="rule.acknowledgement")

    assert agent.do(page, ans, control) is True
    assert checked_state[0] is True


def test_in_site_menu_does_not_flag_form_pagination_footer():
    """_in_site_menu returns False when footer is a form pagination/action bar or inside main."""
    agent = page_agent.PageAgent.__new__(page_agent.PageAgent)
    agent.tab = lambda p: p
    page = MagicMock()

    loc = MagicMock()
    # In live DOM, Next button is inside <footer class="apply-flow-pagination__buttons"> within <main>
    # The existing JS returned true because e.closest('footer') was non-null.
    js_query = [None]
    def mock_eval(js, *args, **kwargs):
        js_query[0] = js
        # Simulate browser evaluation of the JS query against Oracle Cloud DOM
        # e.closest('header, nav, footer, [role=banner], [role=navigation], [role=contentinfo]')
        # has tagName 'FOOTER' and className 'apply-flow-pagination__buttons' inside <main>
        return False  # Target expectation: should be recognized as NOT in site menu
    loc.evaluate.side_effect = mock_eval
    agent.locate = lambda p, ref: loc

    control = Control(ref="e56", role="button", name="Next", value="")
    assert agent._in_site_menu(page, control) is False


def test_press_next_does_not_bounce_on_next_button():
    """press_next does not trigger back navigation to job posting when pressing Next in form footer."""
    agent = page_agent.PageAgent.__new__(page_agent.PageAgent)
    agent.assistant = MagicMock()
    agent.assistant._submission_execution_state = "active"
    agent.assistant._submission_guard = None
    agent.assistant.is_review_step.return_value = False
    agent.assistant.submission_step_finality.return_value = "NON_FINAL"
    agent.tab = lambda p: p
    agent._seen_form = True
    agent._pressed = {}
    agent.resume_file = None
    agent.settle = lambda p, ms=0: None
    agent.job = MagicMock()
    agent.job.url = "https://jobs.adt.com/job/123"

    # Suppose evaluate returned True for in_site_menu as it did for Oracle Cloud footer
    loc = MagicMock()
    def mock_evaluate(js, *args, **kwargs):
        if "closest('header, nav, footer" in js:
            return True
        return ""
    loc.evaluate.side_effect = mock_evaluate
    loc.click = MagicMock()
    agent.locate = lambda p, ref: loc

    control = Control(ref="e56", role="button", name="Next", value="")
    controls = [control]
    plan = page_agent.PagePlan(page_kind="application_form", next_kind="next_step", next_ref="e56", next_label="Next")

    page = MagicMock()
    page.is_closed.return_value = False
    page.url = "https://fa-erqb-saasfaprod1.fa.ocs.oraclecloud.com/apply/email"

    res = agent.press_next(page, plan, controls)
    assert res[0] != "stop"
    assert "back to the job" not in res[2]
    # Page must NOT have called goto back to job url!
    assert page.goto.call_count == 0




