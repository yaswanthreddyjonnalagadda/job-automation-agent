"""A cover letter is written and attached wherever the form offers a place for one, required or not (f298).

Fastly (Greenhouse), 2 November 2026: the form had a "Cover Letter" section with an Attach button, no star. The agent
skipped it, no letter was written, and the owner saw the application go out without one. The rule was "only when the
form requires a cover letter", kept in three places; the owner's instruction is to send a cover letter wherever the form
offers one. The letter is still written only when a place for it exists, so no model call is wasted.
"""
import pathlib
import tempfile
from types import SimpleNamespace

import pytest

import apply_flow
import config
import page_agent
import safety
from browser_automation import JobApplicationAssistant

FORM = """<html><body><h1>Apply</h1>
<form onsubmit="localStorage.cover = cover.files.length ? cover.files[0].name : '';
                localStorage.resume = resume.files.length ? resume.files[0].name : '';
                location.href='/done'; return false;">
  <fieldset><legend>Resume/CV*</legend><input type="file" id="resume" aria-label="Attach"></fieldset>
  COVER_SECTION
  <button type="submit">Submit Application</button>
</form></body></html>"""

DONE = "<html><body><h1>Thank you for applying.</h1></body></html>"

SECTIONS = {
    "optional (no star)": '<fieldset><legend>Cover Letter</legend><input type="file" id="cover" aria-label="Attach"></fieldset>',
    "marked optional": '<fieldset><legend>Cover Letter (optional)</legend><input type="file" id="cover" aria-label="Attach"></fieldset>',
    "required": '<fieldset><legend>Cover Letter*</legend><input type="file" id="cover" aria-label="Attach"></fieldset>',
}


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


def ref_of(snapshot, role, name):
    import re
    for line in snapshot.splitlines():
        if re.search(rf'- {role} "{re.escape(name)}', line):
            return re.search(r"\[ref=([\w-]+)\]", line).group(1)
    return ""


def run(page, section, letter_provider):
    body = FORM.replace("COVER_SECTION", section)
    page.route("https://jobs.example.com/**", lambda route: route.fulfill(
        status=200, content_type="text/html", body=DONE if route.request.url.endswith("/done") else body))
    page.goto("https://jobs.example.com/apply/1")
    planner = SimpleNamespace(plan_page=lambda snapshot, facts, feedback="": (
        {"page_kind": "confirmation", "next": {"kind": "none"}} if "Thank you" in snapshot else
        {"page_kind": "application_form", "answers": [],
         "next": {"ref": ref_of(snapshot, "button", "Submit Application"), "label": "Submit Application",
                  "kind": "final_submit"}}))
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    resume = pathlib.Path(tempfile.mkdtemp()) / "Jane_Doe_Resume_Example.pdf"
    resume.write_bytes(b"%PDF-1.4 resume")
    agent = page_agent.PageAgent(assistant, planner, SimpleNamespace(auto_submit=False, ats_email=""),
                                 config.get_user_profile(), SimpleNamespace(raw_text="Network engineer"),
                                 SimpleNamespace(title="Network Engineer", company="Example", url="https://jobs.example.com/apply/1"),
                                 resume_file=resume)
    agent.cover_letter = letter_provider
    agent.run(page)
    return page.evaluate("[...document.querySelectorAll('input[type=file]')].map(i => i.id + ':' + (i.files[0] ? i.files[0].name : ''))")


@pytest.mark.parametrize("name", SECTIONS)
def test_a_cover_letter_is_written_and_attached_where_the_form_offers_a_place_for_one(page, tmp_path, name):
    letter = tmp_path / "Jane_Doe_Cover_Letter_Example.pdf"
    letter.write_bytes(b"%PDF-1.4 letter")
    written = []

    def provider():
        written.append("written")
        return (letter.with_suffix(".txt"), letter)
    files = run(page, SECTIONS[name], provider)
    assert "cover:Jane_Doe_Cover_Letter_Example.pdf" in files, f"{name}: the letter was not attached ({files})"
    assert written == ["written"], f"{name}: the letter was written {len(written)} times"


def test_no_letter_is_written_for_a_form_with_no_place_for_one(page):
    written = []
    files = run(page, "", lambda: written.append("written") or None)
    assert written == [], "a cover letter was written for a form that has no place for one"
    assert all(not f.endswith(":") or f.startswith("resume") for f in files)


def test_a_place_that_already_holds_a_letter_gets_no_second_one(page, tmp_path):
    written = []
    section = '<fieldset><legend>Cover Letter</legend><p>My_Letter.pdf</p><input type="file" id="cover" aria-label="Attach"></fieldset>'
    run(page, section, lambda: written.append("written") or None)
    assert written == []


# -- the switch and the single place that decides ----------------------------------------------------------------

def test_the_letter_is_written_once_and_only_when_the_owner_has_switched_cover_letters_on():
    calls = []

    def write():
        calls.append(1)
        return ("letter.txt", "letter.pdf")
    made = {}
    assert apply_flow.cover_letter_for(SimpleNamespace(generate_cover_letters=False), made, write) is None
    assert calls == [] and made == {}
    on = SimpleNamespace(generate_cover_letters=True)
    first = apply_flow.cover_letter_for(on, made, write)
    second = apply_flow.cover_letter_for(on, made, write)
    assert first == second == ("letter.txt", "letter.pdf") and len(calls) == 1
