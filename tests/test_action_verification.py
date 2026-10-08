"""Phase 0-B4: an important action is not successful merely because it was attempted.

Covers the field-write/upload/repeated-entry verification gaps discovery found (
docs/security/phase0-b4-discovery.md sections 2-8): PageAgent.do()'s generic textbox fill,
the radio/checkbox "choose" dispatch, native-select, resume upload, and the bulk
repeated-entry filler all used to report success the instant a Playwright call did not
throw, with no check of the resulting committed browser state. Each test here proves BOTH
directions -- a genuine write still succeeds, and a rejected/uncommitted one is now reported
honestly rather than recorded as answered.
"""
from types import SimpleNamespace

import pytest

import action_result
import config
import job_tracker
import page_agent
from browser_automation import JobApplicationAssistant


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


@pytest.fixture
def resume_file(tmp_path):
    path = tmp_path / "Yaswanth_Jonnalagadda_Resume_Example.pdf"
    path.write_bytes(b"%PDF-1.4 test resume")
    return path


def agent(resume_file=None, tmp_path=None, tracker=None, key=""):
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    job = SimpleNamespace(title="Engineer", company="Acme", url="https://jobs.example.com/apply")
    return page_agent.PageAgent(
        assistant, SimpleNamespace(), SimpleNamespace(auto_submit=False, ats_email=""),
        config.UserProfile(), SimpleNamespace(raw_text="x"), job,
        tracker=tracker, key=key, resume_file=resume_file, job_dir=tmp_path)


def control_for(a, page, role, name):
    controls = page_agent.parse_snapshot(a.snapshot(page))
    return next(c for c in controls if c.role == role and (c.name or c.question) == name)


# --- generic textbox fill: a real commit still works, a rejected one is reported honestly ---

def test_an_ordinary_fill_is_verified_and_reported_as_written(page, tmp_path):
    page.set_content('<label>First Name<input id="fn"></label>')
    a = agent(tmp_path=tmp_path)
    box = control_for(a, page, "textbox", "First Name")
    assert a.do(page, page_agent.Answer(box.ref, "First Name", "fill", "Yaswanth"), box) is True
    assert page.locator("#fn").input_value() == "Yaswanth"


def test_a_controlled_input_that_rejects_the_value_is_not_reported_as_written(page, tmp_path):
    """A React/Vue-style controlled input that snaps back to its old value on every input
    event -- the Playwright fill() call itself never throws, so the old, unverified do()
    reported this as a successful write every time."""
    page.set_content(
        '<label>First Name<input id="fn" value="" '
        'oninput="this.value=\'\'"></label>')
    a = agent(tmp_path=tmp_path)
    box = control_for(a, page, "textbox", "First Name")
    assert a.do(page, page_agent.Answer(box.ref, "First Name", "fill", "Yaswanth"), box) is False
    assert page.locator("#fn").input_value() == ""


# --- radio/checkbox "choose" dispatch: verified both ways -----------------------------------

def test_a_radio_choice_is_verified_as_checked(page, tmp_path):
    page.set_content(
        '<fieldset><legend>Are you at least 18 years old? *</legend>'
        '<label><input type="radio" name="adult" value="Yes"> Yes</label>'
        '<label><input type="radio" name="adult" value="No"> No</label></fieldset>')
    a = agent(tmp_path=tmp_path)
    box = control_for(a, page, "radio", "Yes")
    assert a.do(page, page_agent.Answer(box.ref, "Are you at least 18 years old?", "choose", "Yes"), box) is True
    assert page.locator("input[value='Yes']").is_checked()


def test_a_radio_click_that_does_not_actually_check_it_is_not_reported_as_chosen(page, tmp_path):
    """A radio whose own handler immediately unchecks itself again -- the click() call does
    not throw, so the old, unverified "choose" dispatch reported this as answered."""
    page.set_content(
        '<fieldset><legend>Are you at least 18 years old? *</legend>'
        '<label><input type="radio" name="adult" value="Yes" '
        'onclick="this.checked=false"> Yes</label>'
        '<label><input type="radio" name="adult" value="No"> No</label></fieldset>')
    a = agent(tmp_path=tmp_path)
    box = control_for(a, page, "radio", "Yes")
    assert a.do(page, page_agent.Answer(box.ref, "Are you at least 18 years old?", "choose", "Yes"), box) is False
    assert not page.locator("input[value='Yes']").is_checked()


# --- native <select>: verified both ways -----------------------------------------------------

def test_a_native_select_choice_is_verified_as_committed(page, tmp_path):
    page.set_content('<label>Country<select id="c"><option value="">-</option>'
                     '<option>India</option><option>United States</option></select></label>')
    a = agent(tmp_path=tmp_path)
    box = control_for(a, page, "combobox", "Country")
    assert a.choose(page, box, "United States", [box]) is True
    assert page.locator("#c").input_value() == "United States"


def test_a_native_select_that_snaps_back_is_not_reported_as_chosen(page, tmp_path):
    """A managed <select> whose change handler resets the selection -- select_option() itself
    never throws, so the old, unverified native-select branch reported this as chosen."""
    page.set_content(
        '<label>Country<select id="c" onchange="this.selectedIndex=0"><option value="">-</option>'
        '<option>India</option><option>United States</option></select></label>')
    a = agent(tmp_path=tmp_path)
    box = control_for(a, page, "combobox", "Country")
    assert a.choose(page, box, "United States", [box]) is False
    assert page.locator("#c").input_value() == ""


# --- resume upload: verified both ways -------------------------------------------------------

UPLOAD_FORM = (
    '<label for="r">Resume *</label>'
    '<input type="file" id="r" aria-label="Resume *" '
    'onchange="document.getElementById(\'shown\').textContent = this.files[0] ? this.files[0].name : \'\'">'
    '<span id="shown"></span>')


def test_a_resume_upload_that_shows_attached_is_verified(page, tmp_path, resume_file):
    page.set_content(UPLOAD_FORM)
    a = agent(resume_file, tmp_path)
    box = control_for(a, page, "button", "Resume *")
    assert a.do(page, page_agent.Answer(box.ref, "Resume", "upload_resume", "resume"), box) is True
    assert a.resume_uploaded is True


def test_a_resume_upload_that_never_shows_attached_is_not_verified(page, tmp_path, resume_file):
    """The file input accepts the file (set_input_files never throws) but nothing on the page
    ever shows the filename -- the old, unverified upload path set resume_uploaded=True the
    instant the call succeeded, regardless."""
    # A page that removes the file input (and shows nothing else naming the file) right after
    # a file is chosen -- set_input_files() itself still succeeds against the detached-from-DOM
    # value, but no evidence of attachment is ever shown.
    page.set_content(
        '<label for="r">Resume *</label><input type="file" id="r" aria-label="Resume *" '
        'onchange="this.remove()">')
    a = agent(resume_file, tmp_path)
    box = control_for(a, page, "button", "Resume *")
    assert a.do(page, page_agent.Answer(box.ref, "Resume", "upload_resume", "resume"), box) is False
    assert a.resume_uploaded is False


# --- repeated entries: a post-fill count mismatch is surfaced, not silently accepted --------

class _CountingAdapter:
    """Reports a WRONG entry count after fill_experience_section runs -- standing in for a
    site filler that threw no exception but left the wrong number of entries."""
    def __init__(self, shown_before=0, shown_after=0):
        self._shown_before, self._shown_after = shown_before, shown_after
        self.filled = False

    def entry_count(self, tab, heading):
        return self._shown_after if self.filled else self._shown_before

    def fill_experience_section(self, tab, entries):
        self.filled = True


def test_a_repeated_entry_fill_that_leaves_the_wrong_count_is_marked_uncertain(tmp_path):
    tracker = job_tracker.JobTracker(tmp_path / "applications.db")
    tracker.create(dedup_key="k1", title="Engineer", company="Acme", location="Remote",
                   url="https://jobs.example.com/apply")
    a = agent(tmp_path=tmp_path, tracker=tracker, key="k1")
    a.history = {"experience": [{"company": "Acme", "title": "Engineer"}, {"company": "Globex", "title": "Lead"}]}
    adapter = _CountingAdapter(shown_before=0, shown_after=1)   # two entries given, only one lands
    a.assistant = SimpleNamespace(adapter=lambda tab: adapter,
                                  fill_experience_section=adapter.fill_experience_section)
    snapshot = '- heading "Work Experience"'
    a.add_entries_the_site_way(SimpleNamespace(url="https://jobs.example.com/apply"), snapshot)
    assert a.uncertain_action_labels() == ("entries:Work Experience@jobs.example.com",)


def test_a_repeated_entry_fill_with_the_right_count_is_not_marked_uncertain(tmp_path):
    tracker = job_tracker.JobTracker(tmp_path / "applications.db")
    tracker.create(dedup_key="k1", title="Engineer", company="Acme", location="Remote",
                   url="https://jobs.example.com/apply")
    a = agent(tmp_path=tmp_path, tracker=tracker, key="k1")
    a.history = {"experience": [{"company": "Acme", "title": "Engineer"}]}
    adapter = _CountingAdapter(shown_before=0, shown_after=1)
    a.assistant = SimpleNamespace(adapter=lambda tab: adapter,
                                  fill_experience_section=adapter.fill_experience_section)
    a.add_entries_the_site_way(SimpleNamespace(url="https://jobs.example.com/apply"),
                               '- heading "Work Experience"')
    assert a.uncertain_action_labels() == ()


# --- action_result.py: pure outcome model ----------------------------------------------------

def test_action_result_defaults_toward_not_claiming_success():
    r = action_result.not_attempted("fill", target="First Name")
    assert r.attempted is False and r.outcome == action_result.NOT_ATTEMPTED and not r.verified


def test_action_result_rejects_an_unknown_outcome():
    with pytest.raises(ValueError):
        action_result.ActionResult(action="fill", outcome="MADE_UP_OUTCOME")


@pytest.mark.parametrize("builder, expected", [
    (lambda: action_result.verified("fill", evidence_kind="input_value"), action_result.VERIFIED),
    (lambda: action_result.no_change("fill"), action_result.NO_CHANGE),
    (lambda: action_result.validation_failed("press_next"), action_result.VALIDATION_FAILED),
    (lambda: action_result.outcome_unknown("upload_resume"), action_result.OUTCOME_UNKNOWN),
    (lambda: action_result.owner_required("choose"), action_result.OWNER_REQUIRED),
])
def test_action_result_constructors_set_the_right_outcome(builder, expected):
    assert builder().outcome == expected
