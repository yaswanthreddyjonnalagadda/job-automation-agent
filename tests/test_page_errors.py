"""A press that does not move the page on is read against what the page says is wrong.

OCC (Workday My Experience, 19 September): the page said "The field Upload a file (5MB max) is required and must
have a value." in an Errors Found panel, and the agent pressed Save and Continue some thirty times without reading
it -- the resume it believed attached was not. tests/fixtures/pages/workday_my_experience_errors.txt is that page,
personal details replaced.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
import page_agent
from page_agent import page_errors

FIXTURES = Path(__file__).parent / "fixtures" / "pages"


def test_the_workday_errors_panel_is_read_in_its_own_words():
    errors = page_errors((FIXTURES / "workday_my_experience_errors.txt").read_text(encoding="utf-8"))
    assert errors == ["The field Upload a file (5MB max) is required and must have a value."]


@pytest.mark.parametrize("snapshot, expected", [
    ('- alert [ref=e1]: Phone number is required', ["Phone number is required"]),
    ('- generic [ref=e1]: Please select a state', ["Please select a state"]),
    ('- alert [ref=e1]: Email address is invalid', ["Email address is invalid"]),
    ('- text: Start date must be before the end date', []),
    ('- generic [ref=e1]: Zip code must be a 5-digit number', ["Zip code must be a 5-digit number"]),
    ('- generic [ref=e1]: "* Indicates a required field"\n- button "Degree Graduate Required" [ref=e2]', []),
    ('- button "Errors Found" [ref=e1]\n- text: Error', []),
    ('- heading "My Experience" [ref=e1]\n- textbox "Job Title" [ref=e2]: Network Engineer', []),
])
def test_what_counts_as_the_page_saying_something_is_wrong(snapshot, expected):
    assert page_errors(snapshot) == expected


def test_the_same_error_twice_is_not_pressed_through():
    snapshot = '- alert [ref=e1]: Phone number is required'
    job = SimpleNamespace(title="t", company="c", url="https://jobs.example.com")
    agent = page_agent.PageAgent(SimpleNamespace(values=None), SimpleNamespace(),
                                 SimpleNamespace(auto_submit=False, ats_email=""),
                                 config.UserProfile(email="jane@example.com"), SimpleNamespace(raw_text="x"), job,
                                 resume_file=None)
    agent.snapshot = lambda page: snapshot
    first = agent.stuck_on_errors(page=None)
    assert first == "the form says: Phone number is required"
    second = agent.stuck_on_errors(page=None)
    assert second.startswith("stop:") and "Phone number is required" in second


def test_an_error_about_a_file_makes_the_agent_attach_it_again():
    job = SimpleNamespace(title="t", company="c", url="https://jobs.example.com")
    agent = page_agent.PageAgent(SimpleNamespace(values=None), SimpleNamespace(),
                                 SimpleNamespace(auto_submit=False, ats_email=""),
                                 config.UserProfile(email="jane@example.com"), SimpleNamespace(raw_text="x"), job,
                                 resume_file=None)
    agent.snapshot = lambda page: (FIXTURES / "workday_my_experience_errors.txt").read_text(encoding="utf-8")
    agent.resume_uploaded, agent._letter_attached = True, True
    agent.stuck_on_errors(page=None)
    assert agent.resume_uploaded is False and agent._letter_attached is False


def test_a_page_with_no_errors_says_nothing():
    job = SimpleNamespace(title="t", company="c", url="u")
    agent = page_agent.PageAgent(SimpleNamespace(values=None), SimpleNamespace(),
                                 SimpleNamespace(auto_submit=False, ats_email=""),
                                 config.UserProfile(email="jane@example.com"), SimpleNamespace(raw_text="x"), job,
                                 resume_file=None)
    agent.snapshot = lambda page: '- heading "My Experience" [ref=e1]'
    assert agent.stuck_on_errors(page=None) == ""


# --- a file box hidden in a drop zone ------------------------------------------------------------

@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
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


@pytest.mark.parametrize("action", ["upload_resume", "upload_cover_letter"])
def test_a_file_goes_into_the_hidden_box_of_a_drop_zone(page, tmp_path, action):
    """Workday's 'Select files' opens no file window; its real box is hidden in the drop zone."""
    resume = tmp_path / "Jane_Doe_Resume.pdf"
    resume.write_bytes(b"%PDF-1.4 x")
    page.set_content('<div class="drop"><h4>Upload a file</h4><button type="button">Select files</button>'
                     '<input type="file" style="display:none"></div>')
    job = SimpleNamespace(title="t", company="c", url="u")
    agent = page_agent.PageAgent(SimpleNamespace(values=None), SimpleNamespace(),
                                 SimpleNamespace(auto_submit=False, ats_email=""),
                                 config.UserProfile(email="jane@example.com"), SimpleNamespace(raw_text="x"), job,
                                 resume_file=resume)
    agent._letter_file = lambda: resume
    agent.settle = lambda *a, **k: None
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    button = next(c for c in controls if c.name == "Select files")
    assert agent.do(page, page_agent.Answer(button.ref, "Upload a file", action, resume.name, "document"), button)
    assert page.evaluate("document.querySelector('input[type=file]').files[0].name") == resume.name
