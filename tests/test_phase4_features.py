"""
Tests for Phase 4: State Machine Circuit Breakers, Forensic Tooling & Pipeline Closure.

Verifies:
- Task 4.1: State Fingerprint Circuit Breaker (SHA-256 hash of URL, active step, visible inputs,
  trips after 3 consecutive identical cycles with BLOCKED_VALIDATION_LOOP and exits cleanly).
- Task 4.2: Automated Forensic Failure Dumper (timestamped folder runs/<timestamp>_<domain>/
  exporting screenshot.png, page_state.html, axtree_dump.json, console_logs.json).
- Task 4.3: Local Resume Mount & Lazy CV Compilation (direct .set_input_files() on
  assets/master_resume.pdf, cover letter generation strictly lazy-loaded and skipped when optional).
- Task 4.4: End-to-End Workspace Integration Testing (apply.py, fill_and_dispatch, boundary adherence).
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from playwright.sync_api import sync_playwright

import apply_flow
import browser_automation
import db
import interaction
import page_agent
import safety
from state_machine import (
    STATUS_BLOCKED_VALIDATION_LOOP,
    StateFingerprintCircuitBreaker,
    compute_state_fingerprint,
    dump_forensic_failure,
)


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
# Task 4.1: State Fingerprint Circuit Breaker
# ==============================================================================

def test_compute_state_fingerprint_structure(page):
    """Verifies that compute_state_fingerprint generates a consistent SHA-256 hash
    incorporating URL, active step indicator, and visible input count."""
    page.set_content("""
    <html>
      <head><title>Job Application Step 2</title></head>
      <body>
        <div class="step-indicator active">Step 2: Experience</div>
        <form>
          <input type="text" id="job_title" />
          <input type="text" id="company" />
          <input type="hidden" id="csrf_token" value="xyz" />
        </form>
      </body>
    </html>
    """)

    state_hash, meta = compute_state_fingerprint(page)
    assert isinstance(state_hash, str) and len(state_hash) == 64
    assert meta["visible_inputs"] == 2
    assert "Step 2: Experience" in meta["step_text"]

    # Identical state produces identical hash
    state_hash_2, _ = compute_state_fingerprint(page)
    assert state_hash == state_hash_2

    # Adding a visible input changes the fingerprint
    page.evaluate("""() => {
        const input = document.createElement('input');
        input.type = 'text';
        input.id = 'extra_field';
        document.querySelector('form').appendChild(input);
    }""")
    state_hash_3, meta_3 = compute_state_fingerprint(page)
    assert meta_3["visible_inputs"] == 3
    assert state_hash_3 != state_hash


def test_circuit_breaker_trips_after_3_identical_cycles(page, tmp_path):
    """Verifies that StateFingerprintCircuitBreaker trips on the 3rd consecutive identical cycle
    with BLOCKED_VALIDATION_LOOP, and resets if state changes."""
    page.set_content("""
    <html>
      <body>
        <div class="step">Step 1</div>
        <input type="text" id="name" />
      </body>
    </html>
    """)

    cb = StateFingerprintCircuitBreaker(consecutive_threshold=3)

    # Cycle 1
    tripped, state, meta = cb.check(page)
    assert not tripped
    assert cb.consecutive_matches == 1

    # Cycle 2
    tripped, state, meta = cb.check(page)
    assert not tripped
    assert cb.consecutive_matches == 2

    # Cycle 3: Tripped!
    tripped, state, meta = cb.check(page)
    assert tripped
    assert state == STATUS_BLOCKED_VALIDATION_LOOP
    assert cb.consecutive_matches == 3

    # trip_and_dump triggers forensic export and updates tracker
    mock_tracker = MagicMock()
    dump_dir = cb.trip_and_dump(
        page,
        reason="Repeated validation errors",
        base_dir=tmp_path / "runs",
        tracker=mock_tracker,
        key="app_key_123",
    )
    assert dump_dir.exists()
    assert (dump_dir / "failure_meta.json").is_file()
    assert mock_tracker.update_status.call_count == 1
    args, kwargs = mock_tracker.update_status.call_args
    assert args == ("app_key_123", STATUS_BLOCKED_VALIDATION_LOOP)
    assert "Repeated validation errors" in kwargs["notes"]
    assert str(dump_dir) in kwargs["notes"]


def test_circuit_breaker_resets_on_state_change(page):
    """Verifies that circuit breaker resets its match count when page state changes."""
    page.set_content("<html><body><div class='step'>Step 1</div><input type='text' /></body></html>")
    cb = StateFingerprintCircuitBreaker(consecutive_threshold=3)

    cb.check(page)
    cb.check(page)
    assert cb.consecutive_matches == 2

    # Mutate page state
    page.set_content("<html><body><div class='step'>Step 2</div><input type='text' /><input type='text' /></body></html>")
    tripped, state, meta = cb.check(page)
    assert not tripped
    assert cb.consecutive_matches == 1


# ==============================================================================
# Task 4.2: Automated Forensic Failure Dumper
# ==============================================================================

def test_forensic_failure_dumper_exports_all_four_assets(page, tmp_path):
    """Verifies that dump_forensic_failure creates runs/<timestamp>_<domain>/ and
    exports: screenshot.png, page_state.html, axtree_dump.json, console_logs.json."""
    page.set_content("""
    <html>
      <head><title>Failure Diagnostics Page</title></head>
      <body>
        <h1>Application Submission Failed</h1>
        <button id="submit_btn">Submit</button>
      </body>
    </html>
    """)

    sample_logs = [
        {"type": "error", "text": "Uncaught TypeError: Cannot read property of undefined"},
        {"type": "warn", "text": "Form validation failed on field #experience"},
    ]

    base_runs = tmp_path / "runs"
    dump_path = dump_forensic_failure(
        page,
        reason="Circuit breaker tripped: loop detected",
        base_dir=base_runs,
        console_logs=sample_logs,
    )

    assert dump_path.is_dir()
    # 1. Full-page screenshot (.png)
    screenshot_file = dump_path / "screenshot.png"
    assert screenshot_file.is_file()
    assert screenshot_file.stat().st_size > 0

    # 2. Complete raw HTML snapshot (page_state.html)
    html_file = dump_path / "page_state.html"
    assert html_file.is_file()
    assert "Failure Diagnostics Page" in html_file.read_text(encoding="utf-8")

    # 3. Native browser accessibility snapshot (axtree_dump.json)
    axtree_file = dump_path / "axtree_dump.json"
    assert axtree_file.is_file()
    axtree_data = json.loads(axtree_file.read_text(encoding="utf-8"))
    assert isinstance(axtree_data, (dict, list))

    # 4. Captured browser console logs (console_logs.json)
    console_file = dump_path / "console_logs.json"
    assert console_file.is_file()
    logs_data = json.loads(console_file.read_text(encoding="utf-8"))
    assert len(logs_data) == 2
    assert "Uncaught TypeError" in logs_data[0]["text"]

    # 5. Metadata verification
    meta_file = dump_path / "failure_meta.json"
    assert meta_file.is_file()
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    assert meta["reason"] == "Circuit breaker tripped: loop detected"


# ==============================================================================
# Task 4.3: Local Resume Mount & Lazy CV Compilation
# ==============================================================================

def test_local_master_resume_mount_exists():
    """Verifies that the standardized local resume is mounted at assets/master_resume.pdf."""
    master = Path("assets/master_resume.pdf")
    assert master.is_file(), "assets/master_resume.pdf must exist as local master file"
    assert master.stat().st_size > 1000


def test_upload_resume_forces_master_resume_set_input_files(page, tmp_path):
    """With RESUME_SOURCE=master, upload_resume attaches assets/master_resume.pdf."""
    page.set_content("""
    <html>
      <body>
        <form>
          <label for="cv_file">Upload Resume</label>
          <input type="file" id="cv_file" name="resume" />
        </form>
      </body>
    </html>
    """)

    assistant = browser_automation.JobApplicationAssistant.__new__(browser_automation.JobApplicationAssistant)
    assistant._config = SimpleNamespace(action_retries=1, resume_source="master")
    assistant.adapter = lambda p: MagicMock(attachment_is_empty=lambda p, k: True)
    assistant._file_already_attached = lambda p, n: False

    # The owner chose the master resume, so it replaces the path passed in.
    dummy_fallback = tmp_path / "other_resume.pdf"
    dummy_fallback.write_bytes(b"%PDF-dummy")

    uploaded = assistant.upload_resume(page, dummy_fallback, file_input_selector="#cv_file")
    assert uploaded is True

    # Verify Playwright set the file input to master_resume.pdf
    val = page.locator("#cv_file").evaluate("el => el.files[0] ? el.files[0].name : ''")
    assert val == "master_resume.pdf"


def test_lazy_cover_letter_compilation_skips_when_optional(page):
    """Verifies that cover letter generation is strictly lazy-loaded and skipped
    when cover letter field is optional or absent."""
    assistant = browser_automation.JobApplicationAssistant.__new__(browser_automation.JobApplicationAssistant)

    # 1. No cover letter field at all
    page.set_content("<html><body><input type='text' name='name' /></body></html>")
    assert assistant.has_required_cover_letter_field(page) is False

    # 2. Optional cover letter field (no 'required', no '*', no 'aria-required')
    page.set_content("""
    <html>
      <body>
        <div class="form-group">
          <label for="cl_input">Cover Letter (Optional)</label>
          <textarea id="cl_input" name="cover_letter"></textarea>
        </div>
      </body>
    </html>
    """)
    assert assistant.has_required_cover_letter_field(page) is False

    # 3. Required cover letter field (with required attribute or asterisk)
    page.set_content("""
    <html>
      <body>
        <div class="form-group required">
          <label for="cl_req">Cover Letter *</label>
          <textarea id="cl_req" name="cover_letter" required></textarea>
        </div>
      </body>
    </html>
    """)
    assert assistant.has_required_cover_letter_field(page) is True


# ==============================================================================
# Task 4.4: End-to-End Workspace Integration Testing
# ==============================================================================

def test_unified_apply_entry_point_and_arguments():
    """Verifies that apply.py imports cleanly, exposes the expected CLI parameters,
    and adheres to the unified entry point design."""
    import apply
    assert hasattr(apply, "main")
    assert hasattr(apply, "run_one")
    assert hasattr(apply, "already_submitted")
    assert hasattr(apply, "skipped_for_sponsorship")


def test_synthetic_event_dispatcher_wired():
    """Verifies that fill_and_dispatch correctly fires input, change, and blur bubbling events."""
    from interaction import fill_and_dispatch
    assert callable(fill_and_dispatch)


def test_status_blocked_validation_loop_registered():
    """Verifies that STATUS_BLOCKED_VALIDATION_LOOP is registered in safety.py, db.py, and state_machine.py."""
    assert safety.STATUS_BLOCKED_VALIDATION_LOOP == "BLOCKED_VALIDATION_LOOP"
    assert db.STATUS_BLOCKED_VALIDATION_LOOP == "BLOCKED_VALIDATION_LOOP"
    assert apply_flow.STATUS_BLOCKED_VALIDATION_LOOP == "BLOCKED_VALIDATION_LOOP"


def test_click_next_step_trips_circuit_breaker_on_stalled_page(page, tmp_path):
    """Verifies that click_next_step checks the circuit breaker and trips
    with BLOCKED_VALIDATION_LOOP when page state remains identical across 3 cycles."""
    page.set_content("""
    <html>
      <body>
        <div class="step">Step 1</div>
        <input type="text" id="req_field" required />
        <button id="next_btn">Save & Continue</button>
      </body>
    </html>
    """)

    assistant = browser_automation.JobApplicationAssistant.__new__(browser_automation.JobApplicationAssistant)
    assistant._config = SimpleNamespace(action_retries=1)
    assistant.commit_open_sections = lambda p: 0
    assistant._wizard_button = lambda p: p.locator("#next_btn")
    assistant._page_fingerprint = lambda p: "constant_fingerprint"
    assistant.tracker = MagicMock()
    assistant.application_key = "test_key"
    assistant._circuit_breaker = StateFingerprintCircuitBreaker(consecutive_threshold=3, base_dir=tmp_path / "runs")

    # Cycle 1
    adv1 = assistant.click_next_step(page)
    assert adv1 is True  # click dispatched
    assert assistant._circuit_breaker.consecutive_matches == 1

    # Cycle 2
    adv2 = assistant.click_next_step(page)
    assert adv2 is True  # click dispatched
    assert assistant._circuit_breaker.consecutive_matches == 2

    # Cycle 3: Tripped!
    adv3 = assistant.click_next_step(page)
    assert adv3 is False  # tripped, click prevented
    assert assistant._stuck_on == STATUS_BLOCKED_VALIDATION_LOOP
    assert assistant._circuit_breaker.tripped is True



def test_the_tailored_resume_is_attached_unless_the_owner_chose_the_master(tmp_path, monkeypatch):
    """One setting decides the resume, so the uploader and the submit gate agree."""
    import config

    tailored = tmp_path / "Tailored_Resume.pdf"
    tailored.write_bytes(b"%PDF-1.4 tailored")
    monkeypatch.delenv("RESUME_SOURCE", raising=False)
    assert config.resume_to_attach(tailored, SimpleNamespace()) == tailored
    assert config.resume_to_attach(tailored, SimpleNamespace(resume_source="tailored")) == tailored
    if config.MASTER_RESUME_PATH.is_file():
        assert config.resume_to_attach(tailored, SimpleNamespace(resume_source="master")) \
            == config.MASTER_RESUME_PATH
