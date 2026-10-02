"""Each kind of list, search box, tick-all-that-apply group and date box is filled the way its portal takes it
(reference/ats_fields/), and the answer is confirmed on the page.

Built on pages shaped like the portals studied on 29 September: react-select (Greenhouse), a server-backed
location search (Lever, Rippling), a button menu with its own search box (BambooHR), a checkbox group (Lever
languages), date boxes (native, mm/dd/yyyy, mm/yyyy).
"""
import pytest

import form_fields as ff
import option_match as om


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


def field(page, html, question):
    page.set_content(f"<html><head><meta charset='utf-8'></head><body>{html}</body></html>")
    return next(f for f in ff.inventory(page) if question in (f.question or f.label))


# --- the matcher ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("options, answer, expected", [
    (["Select...", "Vermont", "Virginia", "West Virginia"], "Virginia", "Virginia"),
    (["West Virginia", "Wisconsin"], "Virginia", None),                          # never 'contains'
    (["Female", "Male"], "Male", "Male"),
    (["United States +1", "United Kingdom +44"], "United States", "United States +1"),
    (["United States of America (+1)", "Canada (+1)"], "United States", "United States of America (+1)"),
    (["VA", "MD"], "Virginia", "VA"),
    (["Associate", "Bachelor", "Master", "Ph.D."], "Masters of Science", "Master"),
    (["Native", "Advanced", "Intermediate", "Beginner"], "Fluent", "Advanced"),
    (["Graduated", "Now attending", "Incomplete"], "Completed", "Graduated"),
    (["Yes", "No, I do not have a disability"], "No", "No, I do not have a disability"),
    (["Yes, I am", "Yes, I have"], "Yes", None),                                   # two equally good: no guess
    (["-- Make a Selection --", "Yes", "No"], "Yes", "Yes"),
])
def test_the_option_that_is_the_answer(options, answer, expected):
    index = om.best_option(options, answer)
    assert (options[index] if index is not None else None) == expected


# --- react-select (Greenhouse) -----------------------------------------------------------------------------------

REACT_SELECT = """
<label id="l" for="country">Country*</label>
<div class="select__control"><div class="select__value-container">
  <div class="select__single-value" id="shown"></div>
  <input role="combobox" id="country" aria-labelledby="l" aria-autocomplete="list" aria-controls="lb" aria-expanded="false">
</div></div>
<div id="lb" role="listbox" style="display:none"></div>
<script>
  const all = ['Afghanistan +93', 'United Arab Emirates +971', 'United Kingdom +44', 'United States +1'];
  const input = document.getElementById('country'), lb = document.getElementById('lb');
  function render() {
    const q = input.value.toLowerCase();
    lb.innerHTML = all.filter(o => o.toLowerCase().includes(q)).map(o => `<div role="option">${o}</div>`).join('');
    lb.style.display = 'block';
  }
  // like react-select: opens on a real press, not on a script's key event
  input.addEventListener('mousedown', render); input.addEventListener('input', render);
  lb.addEventListener('click', e => { if (e.target.getAttribute('role') === 'option') {
    document.getElementById('shown').textContent = e.target.textContent; input.value = ''; lb.style.display = 'none'; } });
  input.addEventListener('keydown', e => { if (e.key === 'Enter') document.getElementById('shown').textContent = lb.firstChild ? lb.firstChild.textContent : ''; });
</script>"""


def test_react_select_takes_the_right_country_not_the_first_row(page):
    f = field(page, REACT_SELECT, "Country")
    assert f.kind == "combobox"
    assert ff.choose(page, f, "United States")
    assert page.inner_text("#shown") == "United States +1"


# --- a location that a server suggests (Lever, Rippling) -------------------------------------------------------------

LOCATION = """
<label for="loc">Current location ✱</label><input id="loc" type="text" aria-autocomplete="list" role="combobox">
<input type="hidden" id="picked">
<div id="results"></div>
<script>
  const loc = document.getElementById('loc'), results = document.getElementById('results');
  loc.addEventListener('input', () => {
    results.innerHTML = '';
    // the server answers a moment later
    setTimeout(() => {
      results.innerHTML = ['Fairfax, Virginia, United States', 'Fairfax, California, United States']
        .filter(r => r.toLowerCase().startsWith(loc.value.toLowerCase().slice(0, 4)))
        .map(r => `<div role="option">${r}</div>`).join('');
    }, 600);
  });
  results.addEventListener('click', e => { if (e.target.getAttribute('role') === 'option') {
    loc.value = e.target.textContent; document.getElementById('picked').value = e.target.textContent; results.innerHTML = ''; } });
</script>"""


def test_a_location_is_picked_from_the_servers_suggestions(page):
    f = field(page, LOCATION, "Current location")
    assert ff.choose(page, f, "Fairfax, Virginia, United States")
    assert page.input_value("#picked") == "Fairfax, Virginia, United States"   # the pick, not only typed text


# --- BambooHR's button menu with its own search box -----------------------------------------------------------------

BAMBOO_STATE = """
<label id="stl">State *</label>
<button id="stb" type="button" aria-haspopup="true" aria-labelledby="stl"
        onclick="document.getElementById('menu').style.display='block'">State –Select–</button>
<div id="menu" role="menu" style="display:none"><input aria-label="Search" placeholder="Search...">
  <div role="menuitem" onclick="pick(this)">West Virginia</div><div role="menuitem" onclick="pick(this)">Virginia</div></div>
<script>function pick(el){ document.getElementById('stb').textContent = 'State ' + el.textContent;
  document.getElementById('menu').style.display = 'none'; }</script>"""


def test_a_button_menu_takes_the_exact_state(page):
    f = field(page, BAMBOO_STATE, "State")
    assert ff.choose(page, f, "Virginia")
    assert page.inner_text("#stb") == "State Virginia"


# --- a native select with degree levels in the portal's own words --------------------------------------------------

def test_a_native_select_takes_the_portals_word_for_the_degree(page):
    f = field(page, """<label for="d">Type of Degree</label><select id="d"><option>Select an option</option>
        <option>Associate</option><option>Bachelor</option><option>Master</option><option>Ph.D.</option></select>""",
              "Type of Degree")
    assert ff.choose(page, f, "Masters of Science")
    assert page.eval_on_selector("#d", "e => e.options[e.selectedIndex].text") == "Master"


def test_a_list_without_the_answer_is_left_alone(page):
    f = field(page, """<label for="s">State</label><select id="s"><option>Select...</option>
        <option>West Virginia</option><option>Wisconsin</option></select>""", "State")
    assert not ff.choose(page, f, "Virginia")
    assert page.eval_on_selector("#s", "e => e.selectedIndex") == 0


# --- tick all that apply (Lever languages) -----------------------------------------------------------------------------

def test_tick_all_that_apply(page):
    page.set_content("""<fieldset><legend>Language Skill(s) (Check all that apply)</legend>
      <label><input type="checkbox" name="lang" value="en">English (ENG)</label>
      <label><input type="checkbox" name="lang" value="te">Telugu (TEL)</label>
      <label><input type="checkbox" name="lang" value="hi">Hindi (HIN)</label></fieldset>""")
    boxes = [f for f in ff.inventory(page) if f.kind == "checkbox"]
    assert ff.choose_several(page, boxes, ["English", "Telugu"]) == ["English (ENG)", "Telugu (TEL)"]
    assert page.eval_on_selector_all("input:checked", "els => els.map(e => e.value)") == ["en", "te"]


# --- dates ------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("html, value, expected", [
    ('<label for="d">Start date</label><input id="d" type="date">', "02/2025", "2025-02-01"),
    ('<label for="d">Date Available</label><input id="d" type="text" placeholder="mm/dd/yyyy">', "2026-10-15",
     "10/15/2026"),
    ('<label for="d">From</label><input id="d" type="text" placeholder="MM/YYYY">', "February 2023", "02/2023"),
])
def test_a_date_goes_in_the_way_the_box_takes_it(page, html, value, expected):
    f = field(page, html, html.split(">")[1].split("<")[0])
    assert ff.fill_date(page, f, value)
    assert page.input_value("#d") == expected


@pytest.mark.parametrize("text, parts", [
    ("02/2025", (2025, 2, 1)), ("February 2025", (2025, 2, 1)), ("Feb 2025", (2025, 2, 1)),
    ("2025-02-14", (2025, 2, 14)), ("02/14/2025", (2025, 2, 14)), ("2019", (2019, 1, 1)), ("soon", None),
])
def test_the_dates_the_profile_writes(text, parts):
    assert ff.parse_date(text) == parts


# --- a whole run: the country list is chosen right, and nothing is sent ----------------------------------------------

def test_a_run_chooses_the_country_in_a_react_select_and_stops_before_submitting(browser, tmp_path):
    from types import SimpleNamespace
    import config
    import page_agent
    body = f"""<html><head><meta charset='utf-8'></head><body><h1>Apply</h1>
      <form onsubmit="event.preventDefault(); location.href='/sent'">
      <label for="fn">First Name*</label><input id="fn" type="text" required>
      {REACT_SELECT}
      <button type="submit">Submit Application</button></form></body></html>"""
    context = browser.new_context()
    pg = context.new_page()
    pg.route("https://jobs.example.com/**", lambda r: r.fulfill(status=200, content_type="text/html; charset=utf-8",
                                                               body=body))
    pg.goto("https://jobs.example.com/apply/1")

    class NoAI:
        def plan_page(self, *a, **k):
            raise RuntimeError("no AI in this test")

    job = SimpleNamespace(title="Engineer", company="Acme", url="https://jobs.example.com/apply/1")
    agent = page_agent.PageAgent(SimpleNamespace(values=SimpleNamespace(record=lambda *a, **k: None)), NoAI(),
                                 SimpleNamespace(auto_submit=True, ats_email=""),
                                 config.UserProfile(first_name="Jane", email="jane@example.com", country="United States"),
                                 SimpleNamespace(raw_text="x"), job, resume_file=None)
    agent.run(pg)
    assert pg.inner_text("#shown") == "United States +1"
    assert not pg.url.endswith("/sent")
    context.close()
