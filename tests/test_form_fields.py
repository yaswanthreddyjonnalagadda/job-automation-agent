"""Every field on a page is found by what it is, in the shapes the portal research found (reference/ats_fields/).

Secunetics (BambooHR), 29 September: State is a styled button over a hidden one-option <select>, and the resume
a hidden file input behind 'Choose File*'; the agent's reading saw neither, reported the page complete, and the
resume was never attached.
"""
from pathlib import Path

import pytest

import form_fields as ff


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


def fields_of(page, html):
    page.set_content(f"<html><body>{html}</body></html>")
    return ff.inventory(page)


BAMBOO = """
<form>
  <input type="text" name="hp" aria-label="Please leave this field blank" style="position:absolute;left:-9999px">
  <label for="city">City *</label><input id="city" type="text" value="Fairfax">
  <div class="fab-FormField"><label id="stl">State *</label>
    <button type="button" aria-haspopup="true" id="stb" aria-labelledby="stl">State –Select–</button>
    <select required aria-hidden="true" style="opacity:0" name="state.value"><option value=""></option></select>
  </div>
  <div><p>Resume*</p><div><button type="button">Choose File*</button><p>No file selected</p>
    <input type="file" required aria-label="file-input" style="display:none"></div></div>
  <textarea aria-label="Please leave this field blank" style="display:none"></textarea>
</form>"""


def test_bamboohr_state_is_a_required_blank_list_and_the_mirror_select_is_not_a_field(page):
    fields = fields_of(page, BAMBOO)
    kinds = [(f.kind, f.question) for f in fields if not f.trap]
    assert ("button_list", "State *") in kinds
    assert not any(f.kind == "select" for f in fields)              # the one-option mirror is skipped
    assert [f.question for f in ff.blank_required(fields)] == ["State *"]


def test_bamboohr_resume_is_found_by_its_block_not_its_name(page):
    fields = fields_of(page, BAMBOO)
    resume = ff.resume_input(fields)
    assert resume is not None and resume.kind == "file"


def test_honeypots_are_traps(page):
    fields = fields_of(page, BAMBOO)
    assert [f.name for f in fields if f.trap and f.kind == "text"] == ["hp"]
    assert all(f.trap for f in fields if f.kind == "textarea")


GREENHOUSE_FILES = """
<div><h3>Resume/CV</h3><div><label>Attach</label><input type="file" accept=".pdf,.docx"></div></div>
<div><h3>Cover Letter</h3><div><label>Attach</label><input type="file" accept=".pdf,.docx"></div></div>"""


def test_greenhouse_two_attach_buttons_the_resume_is_the_one_under_its_heading(page):
    fields = fields_of(page, GREENHOUSE_FILES)
    resume = ff.resume_input(fields)
    assert resume is not None and resume.section == "Resume/CV"


ASHBY = """
<div><div><h4>Autofill from resume</h4><p>Upload your resume here to autofill key application fields.</p>
  <input type="file" accept="application/pdf"></div></div>
<div class="ashby-application-form-field-entry"><label class="_required_x" for="res">Resume</label>
  <input id="res" type="file" accept="application/pdf"></div>
<div class="ashby-application-form-field-entry"><label class="_required_x" for="q1">Are you legally authorized to work in the U.S.?</label>
  <div class="ashby-application-form-input-yesno" id="q1">
    <button aria-pressed="false" data-option="yes">Yes</button><button aria-pressed="false" data-option="no">No</button>
  </div><input type="checkbox" tabindex="-1" style="display:none" name="q1"></div>"""


def test_ashby_the_resume_field_not_the_autofill_upload(page):
    fields = fields_of(page, ASHBY)
    resume = ff.resume_input(fields)
    assert resume is not None and resume.label == "Resume"


def test_ashby_yes_no_buttons_are_one_question(page):
    fields = fields_of(page, ASHBY)
    yes_no = [f for f in fields if f.kind == "yes_no"]
    assert len(yes_no) == 1 and yes_no[0].question == "Are you legally authorized to work in the U.S.?"
    assert yes_no[0].empty and yes_no[0].required


RIPPLING = """
<div><div><div><p>Are you okay being in an on-call rotation?</p>
  <div><div><div><div role="combobox" aria-label="Select" aria-required="true" tabindex="0">Select</div></div></div></div>
</div></div></div>"""


def test_rippling_box_named_only_select_takes_the_question_above_it(page):
    fields = fields_of(page, RIPPLING)
    assert [f.question for f in fields] == ["Are you okay being in an on-call rotation?"]
    assert fields[0].required and fields[0].empty


def test_breezy_honeypot_by_name(page):
    fields = fields_of(page, '<input type="text" name="cName" placeholder="Full Name"><input type="text" name="hp_7f2b">')
    assert [f.name for f in fields if f.trap] == ["hp_7f2b"]
    assert [f.question for f in fields if not f.trap] == ["Full Name"]


SHADOW = """
<spl-input id="host"></spl-input>
<script>
  const root = document.getElementById('host').attachShadow({mode: 'open'});
  root.innerHTML = '<label for="fn">First name*</label><input id="fn" type="text" required>';
</script>"""


def test_smartrecruiters_fields_inside_shadow_dom_are_found(page):
    fields = fields_of(page, SHADOW)
    assert [(f.question, f.shadow, f.required) for f in fields] == [("First name*", True, True)]
    fields[0].locator().fill("Jane")                                 # and can be found again to fill
    assert page.evaluate("document.getElementById('host').shadowRoot.getElementById('fn').value") == "Jane"


def test_react_select_value_is_read_from_beside_the_input(page):
    html = """<div class="select__control"><div class="select__value-container">
      <div class="select__single-value">United States +1</div>
      <input role="combobox" aria-autocomplete="list" aria-label="Country" id="country"></div></div>"""
    fields = fields_of(page, html)
    assert fields[0].kind == "combobox" and fields[0].value == "United States +1" and not fields[0].empty


def test_a_list_button_is_answered_by_the_exact_item_never_one_that_contains_it(page):
    page.set_content("""
      <label id="l">State *</label>
      <button id="b" type="button" aria-haspopup="true" aria-labelledby="l"
              onclick="document.getElementById('m').style.display='block'">State –Select–</button>
      <div id="m" role="menu" style="display:none">
        <div role="menuitem" onclick="pick(this)">West Virginia</div>
        <div role="menuitem" onclick="pick(this)">Virginia</div>
      </div>
      <script>function pick(el){ document.getElementById('b').textContent = 'State ' + el.textContent;
        document.getElementById('m').style.display='none'; }</script>""")
    state = [f for f in ff.inventory(page) if f.kind == "button_list"][0]
    assert ff.choose_from_button_list(page, state, "Virginia")
    assert page.inner_text("#b") == "State Virginia"


def test_the_resume_is_put_in_its_input(page, tmp_path):
    resume = tmp_path / "Jane_Doe_Resume.pdf"
    resume.write_bytes(b"%PDF-1.4\n%%EOF\n")
    page.set_content(BAMBOO)
    target = ff.attach_resume(page, resume)
    assert target is not None
    assert page.evaluate("document.querySelector('input[type=file]').files[0].name") == "Jane_Doe_Resume.pdf"


# --- a whole run on a BambooHR-shaped page -----------------------------------------------------------------------

BAMBOO_PAGE = """<html><body><h2>Network Firewall Engineer</h2>
<form onsubmit="event.preventDefault(); location.href='/sent'">
  <input type="text" name="hp" aria-label="Please leave this field blank" style="position:absolute;left:-9999px">
  <label for="fn">First Name *</label><input id="fn" type="text">
  <label for="em">Email *</label><input id="em" type="text">
  <label id="stl">State *</label>
  <button id="stb" type="button" aria-haspopup="true" aria-labelledby="stl"
          onclick="document.getElementById('menu').style.display='block'">State –Select–</button>
  <select required aria-hidden="true" style="opacity:0" name="state.value"><option value=""></option></select>
  <div id="menu" role="menu" style="display:none"><input aria-label="Search" placeholder="Search...">
    <div role="menuitem" onclick="pick(this)">Vermont</div><div role="menuitem" onclick="pick(this)">Virginia</div>
    <div role="menuitem" onclick="pick(this)">West Virginia</div></div>
  <div><p>Resume*</p><div><button type="button" onclick="document.getElementById('f').click()">Choose File*</button>
    <p>No file selected</p><input id="f" type="file" required aria-label="file-input" style="display:none"></div></div>
  <button type="submit">Submit Application</button>
</form>
<script>function pick(el){ document.getElementById('stb').textContent = 'State ' + el.textContent;
  document.getElementById('menu').style.display = 'none'; }</script></body></html>"""


def test_a_run_attaches_the_resume_and_answers_state_and_stops_before_submitting(browser, tmp_path):
    from types import SimpleNamespace
    import config
    import page_agent
    resume = tmp_path / "Jane_Doe_Resume_Acme.pdf"
    resume.write_bytes(b"%PDF-1.4\n%%EOF\n")
    context = browser.new_context()
    pg = context.new_page()
    pg.route("https://careers.example.com/**", lambda route: route.fulfill(
        status=200, content_type="text/html; charset=utf-8", body=BAMBOO_PAGE))
    pg.goto("https://careers.example.com/careers/59")

    class NoAI:
        def plan_page(self, *a, **k):
            raise RuntimeError("no AI in this test")

    job = SimpleNamespace(title="Engineer", company="Acme", url="https://careers.example.com/careers/59")
    profile = config.UserProfile(first_name="Jane", email="jane@example.com", state="Virginia")
    agent = page_agent.PageAgent(SimpleNamespace(values=SimpleNamespace(record=lambda *a, **k: None)), NoAI(),
                                 SimpleNamespace(auto_submit=False, ats_email=""), profile,
                                 SimpleNamespace(raw_text="x"), job, resume_file=resume)
    agent.run(pg)
    assert pg.evaluate("document.getElementById('f').files.length") == 1          # the resume went in
    assert pg.inner_text("#stb") == "State Virginia"                             # not West Virginia, not Vermont
    assert not pg.url.endswith("/sent")                                          # and nothing was submitted
    assert pg.input_value("input[name=hp]") == ""                                 # the trap stays empty
    context.close()
