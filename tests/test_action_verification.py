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


# =============================================================================
# Phase 0-B4 closure: eliminating "verification unavailable -> return True" across
# every confirmed production mutation path. The common defect: a browser mutation is
# attempted, verification is unavailable or bypassed, the helper returns True anyway, and
# production records the answer as successfully committed. ATTEMPTED != VERIFIED: a helper
# returning True must mean deterministic browser evidence supports the intended state
# committed; a verification read failure is NOT verified, never converted to a silent True.
# =============================================================================

# --- 1. ordinary fill: the JS-setter fallback, and every readback failure ------------------

def test_the_js_setter_fallback_that_commits_is_verified(page, tmp_path, monkeypatch):
    """When fill_and_dispatch() itself raises, do() falls back to a native value-setter +
    synthetic events. That fallback used to report success unconditionally; it is now
    verified the same way the ordinary path is."""
    page.set_content('<label>First Name<input id="fn"></label>')
    a = agent(tmp_path=tmp_path)
    box = control_for(a, page, "textbox", "First Name")
    monkeypatch.setattr(page_agent, "fill_and_dispatch",
                        lambda *a_, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert a.do(page, page_agent.Answer(box.ref, "First Name", "fill", "Yaswanth"), box) is True
    assert page.locator("#fn").input_value() == "Yaswanth"


def test_the_js_setter_fallback_that_is_rejected_is_not_verified(page, tmp_path, monkeypatch):
    page.set_content('<label>First Name<input id="fn" oninput="this.value=\'\'"></label>')
    a = agent(tmp_path=tmp_path)
    box = control_for(a, page, "textbox", "First Name")
    monkeypatch.setattr(page_agent, "fill_and_dispatch",
                        lambda *a_, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert a.do(page, page_agent.Answer(box.ref, "First Name", "fill", "Yaswanth"), box) is False
    assert page.locator("#fn").input_value() == ""


def test_an_initial_readback_failure_is_not_treated_as_committed(page, tmp_path):
    """The element removes itself the instant it is filled -- the primary fill path's own
    input_value() readback then throws (the element can no longer be found). A verification
    read failure must not be converted into a silent True."""
    page.set_content('<label>First Name<input id="fn" oninput="this.remove()"></label>')
    a = agent(tmp_path=tmp_path)
    box = control_for(a, page, "textbox", "First Name")
    assert a.do(page, page_agent.Answer(box.ref, "First Name", "fill", "Yaswanth"), box) is False


def test_a_retry_readback_failure_is_not_treated_as_committed(page, tmp_path):
    """The element rejects the first commit (so the first readback legitimately disagrees
    and a retry is attempted), then removes itself on the retry's own input event -- the
    retry's readback throws. Still not verified."""
    page.set_content(
        '<label>First Name<input id="fn" oninput='
        '"if(!window.__tried){window.__tried=1;this.value=\'WRONG\'}else{this.remove()}">'
        '</label>')
    a = agent(tmp_path=tmp_path)
    box = control_for(a, page, "textbox", "First Name")
    assert a.do(page, page_agent.Answer(box.ref, "First Name", "fill", "Yaswanth"), box) is False


def test_apply_answers_does_not_remember_an_inconclusive_fill(page, tmp_path):
    """The production answer loop (apply_answers) must not put an inconclusive fill into
    `written` or remembered/learned state -- the exact production path this whole class of
    bug reaches the owner through."""
    page.set_content('<label>First Name<input id="fn" oninput="this.value=\'\'"></label>')
    a = agent(tmp_path=tmp_path)
    controls = page_agent.parse_snapshot(a.snapshot(page))
    box = next(c for c in controls if c.role == "textbox")
    plan = page_agent.PagePlan(answers=[page_agent.Answer(box.ref, "First Name", "fill", "Yaswanth")])
    given = a.apply_answers(page, plan, controls)
    assert given == []
    assert "First Name" not in a.written
    assert any("First Name" in f for f in a.failed)


# --- 2. cached native-select recipe: verified both ways, bounded recipe policy reused ------

def test_a_cached_native_select_recipe_that_commits_is_verified(page, tmp_path, monkeypatch):
    page.set_content('<label>Country<select id="c"><option value="">-</option>'
                     '<option>India</option><option>United States</option></select></label>')
    a = agent(tmp_path=tmp_path)
    box = control_for(a, page, "combobox", "Country")
    monkeypatch.setattr(a, "_recalled_form_recipe", lambda control, answer: {"method": "native_select"})
    monkeypatch.setattr(a, "_recipe_identity", lambda control, answer: ("host", "q", "combobox", "sig", "choose"))
    marked = []
    monkeypatch.setattr(a, "_mark_recipe_failed", lambda identity: marked.append(identity))
    assert a.do(page, page_agent.Answer(box.ref, "Country", "choose", "United States"), box) is True
    assert page.locator("#c").input_value() == "United States"
    assert marked == []


def test_a_cached_native_select_recipe_that_is_reset_is_marked_failed(page, tmp_path, monkeypatch):
    """The site resets the selection (a managed <select>) -- select_option() itself does not
    throw, so the old, unverified cached-recipe path reported this as chosen. Now verified,
    and the existing bounded recipe-failure policy (_mark_recipe_failed) is exercised exactly
    as it already is for a throwing select_option()."""
    page.set_content(
        '<label>Country<select id="c" onchange="this.selectedIndex=0"><option value="">-</option>'
        '<option>India</option><option>United States</option></select></label>')
    a = agent(tmp_path=tmp_path)
    box = control_for(a, page, "combobox", "Country")
    monkeypatch.setattr(a, "_recalled_form_recipe", lambda control, answer: {"method": "native_select"})
    monkeypatch.setattr(a, "_recipe_identity", lambda control, answer: ("host", "q", "combobox", "sig", "choose"))
    marked = []
    monkeypatch.setattr(a, "_mark_recipe_failed", lambda identity: marked.append(identity))
    result = a.do(page, page_agent.Answer(box.ref, "Country", "choose", "United States"), box)
    # The cached recipe is marked failed and the live dispatch is free to try its own,
    # independently-verified native-select path next -- which, on this exact page, also
    # cannot make a self-resetting select stick, so the overall answer is correctly False.
    assert marked == [("host", "q", "combobox", "sig", "choose")]
    assert result is False


# --- 3. "choose" dispatched onto a plain textbox/searchbox: verified both ways -------------

def test_a_choose_onto_a_textbox_that_commits_is_verified(page, tmp_path):
    page.set_content('<label>City<input id="city"></label>')
    a = agent(tmp_path=tmp_path)
    box = control_for(a, page, "textbox", "City")
    assert a.do(page, page_agent.Answer(box.ref, "City", "choose", "Fairfax"), box) is True
    assert page.locator("#city").input_value() == "Fairfax"


def test_a_choose_onto_a_textbox_that_is_rejected_is_not_verified(page, tmp_path):
    page.set_content('<label>City<input id="city" oninput="this.value=\'\'"></label>')
    a = agent(tmp_path=tmp_path)
    box = control_for(a, page, "textbox", "City")
    assert a.do(page, page_agent.Answer(box.ref, "City", "choose", "Fairfax"), box) is False
    assert page.locator("#city").input_value() == ""


# --- 4. cover-letter upload: verified, and never confused with an attached resume ----------

COVER_LETTER_FORM = (
    '<label for="cv">Resume *</label><input type="file" id="cv" aria-label="Resume *" '
    'onchange="document.getElementById(\'cv-shown\').textContent = this.files[0] ? this.files[0].name : \'\'">'
    '<span id="cv-shown"></span>'
    '<label for="cl">Cover Letter</label><input type="file" id="cl" aria-label="Cover Letter" '
    'onchange="document.getElementById(\'cl-shown\').textContent = this.files[0] ? this.files[0].name : \'\'">'
    '<span id="cl-shown"></span>')


@pytest.fixture
def letter_file(tmp_path):
    path = tmp_path / "Yaswanth_Jonnalagadda_Cover_Letter.pdf"
    path.write_bytes(b"%PDF-1.4 test letter")
    return path


def test_a_cover_letter_upload_that_shows_attached_is_verified(page, tmp_path, letter_file):
    page.set_content(COVER_LETTER_FORM)
    a = agent(tmp_path=tmp_path)
    a.cover_letter = lambda: (letter_file.with_suffix(".txt"), letter_file)
    box = control_for(a, page, "button", "Cover Letter")
    assert a.do(page, page_agent.Answer(box.ref, "Cover Letter", "upload_cover_letter", "letter"), box) is True
    assert a._letter_attached is True


def test_a_cover_letter_upload_that_never_shows_attached_is_not_verified(page, tmp_path, letter_file):
    page.set_content(
        '<label for="cl">Cover Letter</label>'
        '<input type="file" id="cl" aria-label="Cover Letter" onchange="this.remove()">')
    a = agent(tmp_path=tmp_path)
    a.cover_letter = lambda: (letter_file.with_suffix(".txt"), letter_file)
    box = control_for(a, page, "button", "Cover Letter")
    assert a.do(page, page_agent.Answer(box.ref, "Cover Letter", "upload_cover_letter", "letter"), box) is False
    assert a._letter_attached is False


def test_a_cover_letter_is_not_confused_with_an_attached_resume(page, tmp_path, resume_file, letter_file):
    """The resume's filename is visible elsewhere on the page; the cover letter's own
    section shows nothing and its own file input holds nothing. Verification must still
    correctly fail -- never satisfied by the resume's evidence from a different section."""
    page.set_content(
        f'<div><label>Resume</label><span>{resume_file.name}</span></div>'
        '<div><label for="cl">Cover Letter</label>'
        '<input type="file" id="cl" aria-label="Cover Letter"></div>')
    a = agent(resume_file, tmp_path)
    control = page_agent.Control(ref="e1", role="button", name="Cover Letter", container="Cover Letter")
    assert a._confirm_cover_letter_attached(page, letter_file, control) is False
    assert a._letter_attached is False


# --- 5a. _tick_several / answer_location_choices: verified both ways ------------------------

def test_tick_several_is_verified_when_every_pick_commits(page, tmp_path):
    page.set_content(
        '<fieldset><legend>Languages</legend>'
        '<label><input type="checkbox" name="lang" value="English"> English</label>'
        '<label><input type="checkbox" name="lang" value="Telugu"> Telugu</label>'
        '<label><input type="checkbox" name="lang" value="Hindi"> Hindi</label></fieldset>')
    a = agent(tmp_path=tmp_path)
    boxes = [c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.role == "checkbox"]
    assert a._tick_several(page, boxes, "English, Telugu") is True
    assert page.locator("input[value='English']").is_checked()
    assert page.locator("input[value='Telugu']").is_checked()


def test_tick_several_is_not_verified_when_a_pick_does_not_actually_check(page, tmp_path):
    page.set_content(
        '<fieldset><legend>Languages</legend>'
        '<label><input type="checkbox" name="lang" value="English"> English</label>'
        '<label><input type="checkbox" name="lang" value="Telugu" onclick="this.checked=false"> Telugu</label>'
        '<label><input type="checkbox" name="lang" value="Hindi"> Hindi</label></fieldset>')
    a = agent(tmp_path=tmp_path)
    boxes = [c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.role == "checkbox"]
    assert a._tick_several(page, boxes, "English, Telugu") is False
    assert page.locator("input[value='English']").is_checked()
    assert not page.locator("input[value='Telugu']").is_checked()


def test_answer_location_choices_only_writes_what_actually_committed(page, tmp_path, monkeypatch):
    page.set_content(
        '<fieldset><legend>Where would you like to work?</legend>'
        '<label><input type="checkbox" name="loc" value="Virginia"> Virginia</label>'
        '<label><input type="checkbox" name="loc" value="Remote" onclick="this.checked=false"> Remote</label>'
        '</fieldset>')
    a = agent(tmp_path=tmp_path)
    controls = page_agent.parse_snapshot(a.snapshot(page))
    import location_choice
    monkeypatch.setattr(location_choice, "choices", lambda *a_, **k: [0, 1])
    answered = a.answer_location_choices(page, controls)
    assert answered == 1
    assert "Virginia" in a.written["Where would you like to work?"]
    assert "Remote" not in a.written["Where would you like to work?"]


# --- 4b. the non-reader assistant.attach_cover_letter() production path, both branches ----

def test_assistant_attach_cover_letter_file_branch_is_verified(page, tmp_path, letter_file):
    page.set_content(
        '<label for="cl">Cover Letter *</label>'
        '<input type="file" id="cl" onchange="document.body.insertAdjacentHTML('
        '\'beforeend\', this.files[0] ? this.files[0].name : \'\')">')
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assert assistant.attach_cover_letter(page, letter_file.with_suffix(".txt"), letter_file) is True


def test_assistant_attach_cover_letter_file_branch_that_never_shows_is_not_verified(page, tmp_path, letter_file):
    page.set_content('<label for="cl">Cover Letter *</label><input type="file" id="cl" onchange="this.remove()">')
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assert assistant.attach_cover_letter(page, letter_file.with_suffix(".txt"), letter_file) is False


def test_assistant_attach_cover_letter_text_branch_is_verified(page, tmp_path):
    txt = tmp_path / "letter.txt"
    txt.write_text("Dear Hiring Manager, I am excited to apply.", encoding="utf-8")
    page.set_content('<label for="cl">Cover Letter *</label><textarea id="cl"></textarea>')
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assert assistant.attach_cover_letter(page, txt, tmp_path / "letter.pdf") is True
    assert "excited to apply" in page.locator("#cl").input_value()


def test_assistant_attach_cover_letter_text_branch_that_is_rejected_is_not_verified(page, tmp_path):
    txt = tmp_path / "letter.txt"
    txt.write_text("Dear Hiring Manager, I am excited to apply.", encoding="utf-8")
    page.set_content(
        '<label for="cl">Cover Letter *</label>'
        '<textarea id="cl" oninput="this.value=\'\'"></textarea>')
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assert assistant.attach_cover_letter(page, txt, tmp_path / "letter.pdf") is False
    assert page.locator("#cl").input_value() == ""
