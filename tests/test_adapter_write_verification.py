"""Phase 0-B4 closure: the class-level audit of remaining unverified adapter writes.

discovery flagged sites/amazon.py's answer_platform_question() (both its textbox and select2
branches) and sites/successfactors.py's upload_attachment()/set_date() as residual, unverified
production mutation paths -- each reported success the instant a Playwright call did not
throw, with no read-back of the actual committed state, despite each adapter already exposing
the deterministic evidence needed (Amazon's own select2-rendered text, already read by
platform_questions(); SuccessFactors' own attachment_is_empty()). Each test here proves both
directions, using only the evidence/markup these adapters already expect -- no new ATS
adapter, no guessed markup.
"""
import pytest

from browser_automation import JobApplicationAssistant
from sites.amazon import AmazonAdapter
from sites.successfactors import SuccessFactorsAdapter


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


def assistant():
    return JobApplicationAssistant.__new__(JobApplicationAssistant)


# --- Amazon: textbox branch -------------------------------------------------------------

def test_amazon_textbox_answer_that_commits_is_verified(page):
    page.set_content('<div data-questionid="q1"><label id="q1-label">Years of experience</label>'
                     '<input type="text"></div>')
    assert AmazonAdapter().answer_platform_question(assistant(), page, "q1", "5") is True
    assert page.locator('[data-questionid="q1"] input').input_value() == "5"


def test_amazon_textbox_answer_that_is_rejected_is_not_verified(page):
    page.set_content(
        '<div data-questionid="q1"><label id="q1-label">Years of experience</label>'
        '<input type="text" oninput="this.value=\'\'"></div>')
    assert AmazonAdapter().answer_platform_question(assistant(), page, "q1", "5") is False
    assert page.locator('[data-questionid="q1"] input').input_value() == ""


# --- Amazon: select2 branch, verified against the same rendered-text evidence
#     platform_questions() already reads --------------------------------------------------

_SELECT2_COMMITS = """
<div data-questionid="q2">
  <label id="q2-label">Are you authorized to work?</label>
  <select style="display:none"><option>Yes</option><option>No</option></select>
  <span class="select2-selection" style="display:inline-block;width:120px;height:20px"
        onclick="document.getElementById('q2-opts').style.display='block'">
    <span class="select2-selection__rendered" id="q2-rendered">Choose...</span>
  </span>
</div>
<ul id="q2-opts" style="display:none">
  <li class="select2-results__option" style="display:block" onclick="document.getElementById('q2-rendered').textContent=this.textContent;
             document.getElementById('q2-opts').style.display='none'">Yes</li>
  <li class="select2-results__option" style="display:block" onclick="document.getElementById('q2-rendered').textContent=this.textContent;
             document.getElementById('q2-opts').style.display='none'">No</li>
</ul>"""

_SELECT2_REJECTS = """
<div data-questionid="q2">
  <label id="q2-label">Are you authorized to work?</label>
  <select style="display:none"><option>Yes</option><option>No</option></select>
  <span class="select2-selection" style="display:inline-block;width:120px;height:20px"
        onclick="document.getElementById('q2-opts').style.display='block'">
    <span class="select2-selection__rendered" id="q2-rendered">Choose...</span>
  </span>
</div>
<ul id="q2-opts" style="display:none">
  <li class="select2-results__option" style="display:block" onclick="document.getElementById('q2-opts').style.display='none'">Yes</li>
  <li class="select2-results__option" style="display:block" onclick="document.getElementById('q2-opts').style.display='none'">No</li>
</ul>"""


def test_amazon_select2_answer_that_commits_is_verified(page):
    page.set_content(_SELECT2_COMMITS)
    assert AmazonAdapter().answer_platform_question(assistant(), page, "q2", "Yes") is True
    assert page.locator("#q2-rendered").inner_text() == "Yes"


def test_amazon_select2_answer_that_is_rejected_is_not_verified(page):
    """The option click closes the dropdown (no exception) but the widget's own rendered
    text never updates -- the old, unverified code reported this as answered."""
    page.set_content(_SELECT2_REJECTS)
    assert AmazonAdapter().answer_platform_question(assistant(), page, "q2", "Yes") is False
    assert page.locator("#q2-rendered").inner_text() == "Choose..."


# --- SuccessFactors: upload_attachment, verified against its own attachment_is_empty() ----

def _attachment_field(reveals_on_upload: bool) -> str:
    onchange = "document.getElementById('x_attachDownloadLabel').style.display='inline'" if reveals_on_upload else ""
    return f"""
        <div class="attachmentField">
          <label>Resume</label>
          <span class="addAttachments" style="display:inline-block;width:20px;height:20px"
                onclick="document.getElementById('x_file').click()">+</span>
          <input type="file" id="x_file" style="display:none" onchange="{onchange}">
          <span id="x_attachDownloadLabel" style="display:none">resume.pdf</span>
        </div>"""


def test_successfactors_upload_attachment_that_shows_is_verified(page, tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4")
    page.set_content(_attachment_field(reveals_on_upload=True))
    assert SuccessFactorsAdapter().upload_attachment(assistant(), page, "resume", resume) is True


def test_successfactors_upload_attachment_that_never_shows_is_not_verified(page, tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4")
    page.set_content(_attachment_field(reveals_on_upload=False))
    assert SuccessFactorsAdapter().upload_attachment(assistant(), page, "resume", resume) is None


# --- SuccessFactors: set_date, verified against the widget's own input value --------------

def test_successfactors_set_date_that_commits_is_verified(page):
    page.set_content('<div data-testid="datePicker" accessible-name="Start Date"><input id="d1"></div>')
    assert SuccessFactorsAdapter().set_date(assistant(), page, "Start Date", "01/15/2025") is True
    assert "2025" in page.locator("#d1").input_value()


def test_successfactors_set_date_that_is_rejected_is_not_verified(page):
    page.set_content(
        '<div data-testid="datePicker" accessible-name="Start Date">'
        '<input id="d1" oninput="this.value=\'\'"></div>')
    assert SuccessFactorsAdapter().set_date(assistant(), page, "Start Date", "01/15/2025") is False
    assert page.locator("#d1").input_value() == ""
