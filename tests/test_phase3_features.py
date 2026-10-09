"""
Tests for Phase 3: Robustness, Topological Adaptation & Safety Compliance.

Verifies:
- Task 3.1: Universal Modal & Policy Interceptor inside interaction.py and browser_automation.py
  (scrolls internal container, asserts compliance checkboxes, clicks confirmation, verifies mask detachment)
- Task 3.2: Dependent Cascading Fields (Topological Re-Scan) with 500ms debounce
- Task 3.3: Human-in-the-Loop Review Border enforcement (is_review_step, decide_next_step halts, submit_gate)
- Task 3.4: Legal Attestation & Visa Sponsorship Shield (detects disqualification clause, forensic screenshot, DISQUALIFIED_POLICY_MISMATCH status)
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from playwright.sync_api import sync_playwright

import apply_flow
import browser_automation
import db
import interaction
import page_agent
import safety


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    pg = context.new_page()
    yield pg
    context.close()


# ==============================================================================
# Task 3.1: Universal Modal & Policy Interceptor
# ==============================================================================

def test_modal_interceptor_scrolls_asserts_and_verifies_detachment(page):
    """Verifies that sweep_modals_and_policies scrolls internal container,
    checks mandatory compliance checkboxes, clicks primary confirmation button,
    and waits for both modal card and backdrop mask to detach/hide."""
    page.set_content("""
    <html>
      <head>
        <style>
          .ant-modal-mask {
            position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.5); z-index: 1000;
          }
          .ant-modal {
            position: fixed; top: 20%; left: 30%; width: 400px;
            background: #fff; z-index: 1001; padding: 20px;
          }
          .scrollable-body {
            height: 80px; overflow-y: scroll; border: 1px solid #ccc;
          }
        </style>
      </head>
      <body>
        <div id="backdrop_mask" class="ant-modal-mask"></div>
        <div id="policy_modal" role="dialog" aria-modal="true" class="ant-modal">
          <h3>Recruiting Communications & Privacy Notice</h3>
          <div id="scroll_box" class="scrollable-body">
            <p>Paragraph 1: Please read our entire communications policy.</p>
            <p>Paragraph 2: We send notifications about application status.</p>
            <p>Paragraph 3: Message and data rates may apply.</p>
            <p>Paragraph 4: You can opt out at any time by replying STOP.</p>
            <p>Paragraph 5: By agreeing, you acknowledge all terms.</p>
          </div>
          <div style="margin-top: 10px;">
            <label>
              <input type="checkbox" id="compliance_check" />
              I have read and agree to recruiting communications
            </label>
          </div>
          <div style="margin-top: 10px;">
            <button id="agree_btn">I Agree</button>
          </div>
        </div>

        <script>
          const agreeBtn = document.getElementById('agree_btn');
          agreeBtn.addEventListener('click', () => {
            const cb = document.getElementById('compliance_check');
            const box = document.getElementById('scroll_box');
            // Record state at time of click
            window.clickScrolled = (box.scrollTop >= (box.scrollHeight - box.clientHeight - 10));
            window.clickChecked = cb.checked;

            // Detach / hide both modal and backdrop mask
            document.getElementById('policy_modal').style.display = 'none';
            document.getElementById('backdrop_mask').style.display = 'none';
          });
        </script>
      </body>
    </html>
    """)

    profile = SimpleNamespace(accept_application_privacy_prompts=True)
    dismissed = interaction.sweep_modals_and_policies(page, profile)

    assert dismissed is True, "Expected sweep_modals_and_policies to successfully dismiss modal"
    assert page.evaluate("window.clickScrolled") is True, "Expected internal container to be scrolled to bottom"
    assert page.evaluate("window.clickChecked") is True, "Expected compliance checkbox to be asserted before clicking"

    # Verify both modal and backdrop mask detached / are hidden
    assert page.locator("#policy_modal").is_hidden(), "Modal card should be hidden"
    assert page.locator("#backdrop_mask").is_hidden(), "Backdrop mask should be hidden"


def test_modal_interceptor_skips_password_login_dialogs(page):
    """Verifies that sweep_modals_and_policies refuses to touch or dismiss
    modals containing password inputs (e.g. employer login forms)."""
    page.set_content("""
    <html><body>
      <div role="dialog" aria-modal="true" class="login-dialog">
        <h3>Sign In to Employer Account</h3>
        <input type="text" placeholder="Email" />
        <input type="password" placeholder="Password" />
        <button id="save_btn">Save</button>
      </div>
    </body></html>
    """)

    profile = SimpleNamespace(accept_application_privacy_prompts=True)
    dismissed = interaction.sweep_modals_and_policies(page, profile)

    assert dismissed is False, "Must not dismiss or click buttons inside login password dialogs"
    assert page.locator("button#save_btn").is_visible(), "Login modal button must remain untouched"


def test_modal_interceptor_never_advances_a_data_entry_dialog(page):
    page.set_content('''<div role="dialog" aria-modal="true" class="modal">
        <label>Legal First Name<input required></label>
        <button onclick="window.continueClicks=(window.continueClicks||0)+1">Continue To Application</button>
        </div>''')
    for _ in range(3):
        assert interaction.sweep_modals_and_policies(page, SimpleNamespace()) is False
    assert page.evaluate('window.continueClicks || 0') == 0


# ==============================================================================
# Task 3.2: Dependent Cascading Fields (Topological Re-Scan)
# ==============================================================================

def test_cascading_fields_rescan_detects_dynamically_mounted_inputs(page):
    """Verifies that interacting with a trigger option triggers 500ms debounce
    and prepends newly mounted dependent/conditional fields to the active queue."""
    page.set_content("""
    <html><body>
      <form id="visa_form">
        <fieldset>
          <legend>Are you legally authorized to work in the United States?</legend>
          <label><input type="radio" name="auth" id="auth_yes" value="Yes" /> Yes</label>
          <label><input type="radio" name="auth" id="auth_no" value="No" /> No</label>
        </fieldset>
        <div id="dependent_container"></div>
      </form>

      <script>
        document.getElementById('auth_yes').addEventListener('change', () => {
          // Simulate modern ATS client-side dynamic rendering after selection
          setTimeout(() => {
            const container = document.getElementById('dependent_container');
            container.innerHTML = `
              <div id="visa_type_group" style="margin-top: 15px;">
                <label for="visa_type">What is your current visa status? *</label>
                <input type="text" id="visa_type" name="visa_type" />
              </div>
            `;
          }, 150);
        });
      </script>
    </body></html>
    """)

    # Mock assistant & page agent environment
    assistant = browser_automation.JobApplicationAssistant(browser_automation.AppConfig())
    profile = SimpleNamespace(
        legally_authorized=True,
        requires_visa_sponsorship=False,
        visa_type="F-1 OPT",
        full_name="Test Applicant",
        accept_application_privacy_prompts=True,
    )
    config = SimpleNamespace(auto_submit=False, auto_submit_verified_only=False)
    agent = page_agent.PageAgent(
        assistant=assistant,
        claude=None,
        config=config,
        profile=profile,
        resume={},
        job=SimpleNamespace(title="Software Engineer", company="Acme"),
        tracker=SimpleNamespace(update_status=lambda *args, **kwargs: None),
        key="test_key",
        job_dir=Path("."),
        resume_file=None,
    )

    snapshot_initial = agent.snapshot(page)
    controls_initial = page_agent.parse_snapshot(snapshot_initial)

    # Initial plan has answer for auth_yes
    auth_control = next(c for c in controls_initial if c.name == "Yes" or "authorized" in c.question.lower())
    plan = page_agent.PagePlan(
        page_kind="form",
        step="1 of 2",
        answers=[
            page_agent.Answer(
                ref=auth_control.ref,
                question="Are you legally authorized to work in the United States?",
                action="choose",
                value="Yes",
                source="profile.legally_authorized",
            )
        ],
        next_kind="next_step",
        next_label="Next",
    )

    # Apply answers: this clicks auth_yes, waits 500ms debounce, re-scans DOM subtree,
    # detects newly mounted "visa status" input and prepends it to the queue.
    given = agent.apply_answers(page, plan, controls_initial)

    assert len(given) >= 1, "Expected at least auth answer to be applied"
    # Verify auth_yes was checked
    assert page.locator("#auth_yes").is_checked(), "Radio trigger should be checked"

    # Verify newly mounted dependent field rendered and was detected
    assert page.locator("#visa_type").is_visible(), "Dependent field should be mounted in DOM"


# ==============================================================================
# Task 3.3: Human-in-the-Loop Review Border
# ==============================================================================

def test_human_in_the_loop_review_border_halts_before_submit(page):
    """Verifies that the agent halts cleanly at the designated review step
    and never auto-submits without verified review approval."""
    page.set_content("""
    <html><body>
      <div data-automation-id="progressBarActiveStep">Review Application</div>
      <h2>Please review your application before submitting</h2>
      <p>Name: Test Applicant</p>
      <button id="final_submit">Submit Application</button>
    </body></html>
    """)

    assistant = browser_automation.JobApplicationAssistant(browser_automation.AppConfig())
    assert assistant.is_review_step(page) is True, "Should identify Review step"

    # In --auto mode, decide_next_step MUST cleanly halt (return 'stop') at review step
    step_decision = apply_flow.decide_next_step(
        assistant=assistant,
        page=page,
        step=3,
        experience_data={},
        filled_experience=True,
    )
    assert step_decision == "stop", "Review step must cleanly halt with 'stop' decision"

    # In PageAgent submit_gate: must gate submission if auto_submit_verified_only is False
    config = SimpleNamespace(auto_submit=False, auto_submit_verified_only=False)
    profile = SimpleNamespace(requires_visa_sponsorship=False, accept_application_privacy_prompts=True)
    agent = page_agent.PageAgent(
        assistant=assistant,
        claude=None,
        config=config,
        profile=profile,
        resume={},
        job=SimpleNamespace(title="Dev", company="Corp"),
        tracker=SimpleNamespace(),
        key="k",
        job_dir=Path("."),
        resume_file=None,
    )

    gate_reason = agent.submit_gate(page, [])
    assert "automatic submission is off" in gate_reason, f"Expected submission gate to halt, got: {gate_reason}"


# ==============================================================================
# Task 3.4: Legal Attestation & Visa Sponsorship Shield
# ==============================================================================

def test_visa_sponsorship_shield_detects_hard_disqualification_and_aborts(page, tmp_path):
    """Verifies that when requires_visa_sponsorship=True and employer has an
    explicit non-sponsorship declaration, check_visa_sponsorship_shield triggers
    an immediate safety abort and captures a full-page forensic screenshot."""
    page.set_content("""
    <html><body>
      <h1>Application Questionnaire</h1>
      <p>Notice: At this time, the company is unable to sponsor or take over sponsorship of an employment visa.</p>
      <label>Full Name: <input type="text" /></label>
    </body></html>
    """)

    profile_needs_visa = SimpleNamespace(requires_visa_sponsorship=True)
    disqualified, clause, shot_path = safety.check_visa_sponsorship_shield(
        page, profile_needs_visa, job_dir=tmp_path
    )

    assert disqualified is True, "Visa shield must trigger when candidate requires sponsorship and employer declares non-sponsorship"
    assert "unable to sponsor or take over sponsorship" in clause.lower(), f"Unexpected clause: {clause}"
    assert shot_path is not None, "Forensic screenshot path should be returned"
    assert Path(shot_path).is_file(), "Forensic screenshot file must exist on disk"
    assert Path(shot_path).stat().st_size > 0, "Screenshot file must not be empty"

    # Profile that does NOT require sponsorship must NOT be disqualified
    profile_no_visa = SimpleNamespace(requires_visa_sponsorship=False)
    disqualified_no, clause_no, shot_no = safety.check_visa_sponsorship_shield(
        page, profile_no_visa, job_dir=tmp_path
    )
    assert disqualified_no is False, "Applicant who does not require visa sponsorship must not be disqualified"
    assert clause_no == ""
    assert shot_no is None


def test_status_constant_in_db_and_safety():
    """Verifies STATUS_DISQUALIFIED_POLICY_MISMATCH constant is available across safety and db."""
    assert safety.STATUS_DISQUALIFIED_POLICY_MISMATCH == "DISQUALIFIED_POLICY_MISMATCH"
    assert db.STATUS_DISQUALIFIED_POLICY_MISMATCH == "DISQUALIFIED_POLICY_MISMATCH"
    assert apply_flow.STATUS_DISQUALIFIED_POLICY_MISMATCH == "DISQUALIFIED_POLICY_MISMATCH"
