"""A school that is not in Workday's list is entered as "Other", whatever words the tenant uses to say so (f332).

Analog Devices (3 November): the Education 2 school (JNTU Hyderabad) is not in the list, and the section says
'If your institution does not appear in the pre-populated list ... type "Other," press Enter, and then select the
country'. The agent looked for "Other" only after the wordings 'not listed' (f281) and 'cannot find' (f282), so a third
wording sent it searching for JNTU alone for a minute, three times, and the Degree and Field of Study after it failed
with the School box still open. The rule is now about what the text asks for -- type, search or select a quoted
"Other" -- not about which sentence leads up to it.
"""
import pytest

from sites import workday
from sites.workday import WorkdayAdapter, education_text_offers_other

OFFERS = [
    'To enter your university or school, type the name of your institution in the text box, and press Enter. If your '
    'institution does not appear in the pre-populated list, or if your highest degree is High School Diploma, type '
    '“Other,” press Enter, and then select the appropriate country.',
    'Instructions: Enter your highest level of education. If your institution is not listed, please select “Other.”',
    'How to Enter a School: If you cannot find your school in the list, please search "other" and press enter.',
    "Schools missing from the catalogue can be entered by choosing 'Other' from the list.",
    'No match for your school? Search for “Other” and pick it.',
    'Your school is absent from our list: use "Other" instead.',
    'If you went to a school we do not list, type ‘Other’ and press Enter.',
]

DOES_NOT = [
    'Enter your highest level of education.',
    'To enter your university or school, type the name of your institution in the text box, and press Enter.',
    'Select your degree. Other qualifications can be added under Certifications.',
    '',
]


@pytest.mark.parametrize("text", OFFERS)
def test_every_wording_that_points_to_other_adds_it(text):
    assert education_text_offers_other(text)


@pytest.mark.parametrize("text", DOES_NOT)
def test_text_that_does_not_point_to_other_does_not(text):
    assert not education_text_offers_other(text)


class Group:
    def __init__(self, text):
        self.text = text

    def count(self):
        return 1

    def inner_text(self, timeout=0):
        return self.text


class FakePage:
    def __init__(self, text):
        self.text = text

    def get_by_role(self, role, name=None, exact=False):
        return Group(self.text)


class Assistant:
    _profile = None


def test_the_school_names_end_with_other_for_the_analog_devices_wording():
    names = WorkdayAdapter._school_names(Assistant(), "JNTU Hyderabad", page=FakePage(OFFERS[0]))
    assert names == ["JNTU Hyderabad", "Other"]


def test_the_school_names_stay_alone_when_the_text_does_not_offer_other():
    names = WorkdayAdapter._school_names(Assistant(), "JNTU Hyderabad", page=FakePage(DOES_NOT[1]))
    assert names == ["JNTU Hyderabad"]


def test_the_module_has_one_rule_for_it():
    source = open(workday.__file__, encoding="utf-8").read()
    assert source.count(r"not\s+listed") <= 1, "the wording list must live in education_text_offers_other only"



@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    page = context.new_page()
    yield page
    context.close()

ANALOG_EDUCATION = """<div>
  <div><h4>Education</h4>
    <p>To enter your university or school, type the name of your institution in the text box, and press Enter. If your
    institution does not appear in the pre-populated list, or if your highest degree is High School Diploma, type
    &ldquo;Other,&rdquo; press Enter, and then select the appropriate country.</p>
    <div><h5>Education 1</h5><label for="education-1--school">School or University*</label>
      <input id="education-1--school"></div></div>
  <div><h4>Certifications</h4><button>Add</button></div></div>"""


def test_an_education_section_with_no_accessible_name_is_still_read(page):
    """The Analog Devices Education block is a bare group: a heading 'Education' and its text, no aria-label. Looking
    the section up as a group NAMED Education found nothing, so even a recognised wording never reached the rule."""
    page.set_content(ANALOG_EDUCATION)
    names = WorkdayAdapter._school_names(Assistant(), "JNTU Hyderabad", page=page)
    assert names == ["JNTU Hyderabad", "Other"]


def test_a_named_education_group_is_still_read(page):
    page.set_content('<div role="group" aria-label="Education"><p>If your school is not listed, please select '
                     '“Other.”</p></div><div><h4>Certifications</h4><p>Please select “Other”.</p></div>')
    assert WorkdayAdapter._school_names(Assistant(), "X University", page=page)[-1] == "Other"


def test_other_named_in_another_section_does_not_count(page):
    page.set_content('<div><h4>Education</h4><p>Enter your school.</p></div>'
                     '<div><h4>Languages</h4><p>Please select “Other” if yours is not shown.</p></div>')
    assert WorkdayAdapter._school_names(Assistant(), "X University", page=page) == ["X University"]


# --- "Other" is a family of rows, one per country: "Other Institution - India" -----------------------------------------

from types import SimpleNamespace

import config
import page_agent
import safety
from browser_automation import JobApplicationAssistant

OWNER = SimpleNamespace(
    education=(("Master's", "Computer Science", "Eastern Illinois University", "2022"),
               ("Bachelor's", "Electrical and Electronics Engineering", "JNTU Hyderabad", "2019")),
    education_details=[{"school": "Eastern Illinois University", "country": "United States"},
                       {"school": "JNTU Hyderabad", "country": "India"}],
    answer_alternatives={})


class Owner(Assistant):
    _profile = OWNER


def test_other_is_offered_first_as_the_row_for_the_schools_own_country():
    """Analog Devices lists no row called 'Other': typing Other offers 'Other Institution - <Country>' for each country
    ('then select the appropriate country'). The country is the school's, from the owner's education details."""
    names = WorkdayAdapter._school_names(Owner(), "JNTU Hyderabad", page=FakePage(OFFERS[0]))
    assert names == ["JNTU Hyderabad", "Other Institution - India", "Other Institution - Republic of India", "Other"]


def test_without_a_country_in_the_details_only_plain_other_is_offered():
    class NoCountry(Assistant):
        _profile = SimpleNamespace(education=OWNER.education, education_details=[], answer_alternatives={})
    names = WorkdayAdapter._school_names(NoCountry(), "JNTU Hyderabad", page=FakePage(OFFERS[0]))
    assert names == ["JNTU Hyderabad", "Other"]


def test_a_school_in_the_list_does_not_get_other_rows():
    names = WorkdayAdapter._school_names(Owner(), "Eastern Illinois University", page=FakePage(DOES_NOT[0]))
    assert names == ["Eastern Illinois University"]


def _agent():
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    assistant._profile = OWNER
    return page_agent.PageAgent(assistant, SimpleNamespace(), SimpleNamespace(auto_submit=False),
                               config.UserProfile(full_name="Example Candidate"),
                               SimpleNamespace(raw_text=""), SimpleNamespace(url="https://jobs.example.com"))


def test_the_school_search_chooses_the_other_institution_row_of_the_right_country(page):
    page.set_content('''<div><h4>Education</h4>
      <p>If your institution does not appear in the pre-populated list, type &ldquo;Other,&rdquo; press Enter, and then
      select the appropriate country.</p>
      <label for="education-1--school">School or University*</label>
      <div data-automation-id="multiSelectContainer"><div data-automation-id="monikerSearchBox">
        <input id="education-1--school" enterkeyhint="search" data-uxi-widget-type="selectinput"
          data-uxi-multiselect-id="school-picker"
          onkeydown="if(event.key==='Enter'){window.schoolSearch(this.value);event.preventDefault();}">
      </div><span data-automation-id="selectedItem"></span>
      <div id="results" data-automation-id="responsiveMonikerPrompt" data-associated-widget="school-picker"></div>
      </div></div><script>window.schoolSearch=(query)=>{
        const box=document.getElementById('results');
        const rows=/other/i.test(query)?['Other Institution - Armenia','Other Institution - India','Other Institution - Japan']:[];
        box.innerHTML=rows.length?'':'<div data-automation-id="promptOption">No Items.</div>';
        rows.forEach(label=>{const row=document.createElement('div');
          row.setAttribute('data-automation-id','promptOption');row.setAttribute('data-automation-label',label);
          row.textContent=label;
          row.onclick=()=>{document.querySelector('[data-automation-id=selectedItem]').textContent=label;
            document.querySelector('input').value='';box.hidden=true;};
          box.appendChild(row);});
      };</script>''')
    a = _agent()
    control = next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.role == "textbox")
    assert a.do(page, page_agent.Answer(control.ref, control.question, "fill", "JNTU Hyderabad", "profile.education"), control)
    assert page.locator("[data-automation-id=selectedItem]").inner_text() == "Other Institution - India"


# --- Field of Study: the first 100 rows show until the search is submitted (f332) -------------------------------------

def test_field_of_study_beyond_the_first_hundred_rows_is_found_by_submitting_the_search(page):
    """Workday's Field of Study 'will alphabetically return the first 100 results and can also return specified
    matches': typing alone leaves the A-to-C start on screen, and a subject further down ('Electrical Engineering', the
    Analog Devices Bachelor's) appears only once Enter submits the search. The first entry's 'Computer Science' sits in
    the first hundred, which is why only the second entry failed."""
    page.set_content('''<div><h4>Education</h4>
      <label for="education-1--fieldOfStudy">Field of Study*</label>
      <div data-automation-id="multiSelectContainer"><div data-automation-id="monikerSearchBox">
        <input id="education-1--fieldOfStudy" data-uxi-widget-type="selectinput" data-uxi-multiselect-id="field-picker"
          onkeydown="if(event.key==='Enter'){window.fieldSearch(this.value);event.preventDefault();}">
      </div><span data-automation-id="selectedItem"></span>
      <div id="results" data-automation-id="responsiveMonikerPrompt" data-associated-widget="field-picker"></div>
      </div></div><script>
        const box=document.getElementById('results');
        const show=labels=>{box.innerHTML='';labels.forEach(label=>{const row=document.createElement('div');
          row.setAttribute('data-automation-id','promptOption');row.setAttribute('data-automation-label',label);
          row.textContent=label;
          row.onclick=()=>{document.querySelector('[data-automation-id=selectedItem]').textContent=label;
            document.querySelector('input').value='';box.hidden=true;};box.appendChild(row);});};
        show(['Accounting','Actuarial Science','Advertising']);
        window.fieldSearch=q=>show(/electrical/i.test(q)?['Electrical Engineering','Electrical Engineering Technology']:[]);
      </script>''')
    a = _agent()
    control = next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.role == "textbox")
    assert a.do(page, page_agent.Answer(control.ref, control.question, "fill", "Electrical Engineering", "profile.education"), control)
    assert page.locator("[data-automation-id=selectedItem]").inner_text() == "Electrical Engineering"


# --- Degree: another list left open over the button, and options read from the right list (f332) ---------------------

DEGREE_PAGE = '''<div>
  <ul aria-label="items selected" role="listbox"><li role="option">Eastern Illinois University</li></ul>
  <button id="education-2--degree" name="degree" aria-haspopup="listbox" aria-label="Degree Select One Required"
    type="button" onclick="openDegree(this)">Select One</button>
  <div id="field-popup" style="position:fixed;left:0;top:0;width:100%;height:100%;z-index:50;background:#ddd">
    Field of Study list, left open</div>
  </div><script>
    document.addEventListener('keydown',e=>{if(e.key==='Escape'){const p=document.getElementById('field-popup');if(p)p.remove();}});
    function openDegree(button){
      const ul=document.createElement('ul');ul.setAttribute('role','listbox');ul.id='pop';
      ['Select One','Associate of Science (AS)','Bachelor of Science (BS)','Bachelor of Arts (BA)','Bachelor - Other',
       'Master of Science (MS)'].forEach(t=>{const li=document.createElement('li');li.setAttribute('role','option');
        li.innerHTML='<div>'+t+'</div>';li.onclick=()=>{button.textContent=t;ul.remove();};ul.appendChild(li);});
      document.body.appendChild(ul);button.setAttribute('aria-controls','pop');
    }</script>'''


def test_a_degree_button_under_another_open_list_is_still_opened_and_the_right_row_chosen(page):
    """Analog Devices Education 2: the Field of Study list stayed open over the Degree button, so a plain click timed out
    ('Locator.click: Timeout 5000ms exceeded', five times) and the Degree stayed 'Select One'. Whatever list was left
    open is closed first, and the row is read from the list this button opened, not from the schools' selected items."""
    from sites.workday import _degree_candidates
    page.set_content(DEGREE_PAGE)
    a = _agent()
    ok = WorkdayAdapter().select_from_button_dropdown(a.assistant, page, "--degree", 0, _degree_candidates("Bachelor of Technology (BTech)"))
    assert ok
    assert page.locator("#education-2--degree").inner_text() == "Bachelor of Science (BS)"


SLOW_DEGREE_PAGE = DEGREE_PAGE.replace("function openDegree(button){", "function openDegree(button){\n      window.clicks=(window.clicks||0)+1;\n      if(window.clicks<%CLICKS%)return;\n      setTimeout(()=>{").replace(
    "button.setAttribute('aria-controls','pop');\n    }", "button.setAttribute('aria-controls','pop');},%DELAY%);\n    }")


@pytest.mark.parametrize("clicks_needed,delay_ms", [(1, 1800), (2, 100), (2, 1800)])
def test_a_degree_list_that_opens_late_or_on_the_second_click_is_still_read(page, clicks_needed, delay_ms):
    """Analog Devices Education 2: 'DROPDOWN_OPTIONS[--degree][1]: []' -- the list had not drawn 700 ms after the click
    (or the click only dismissed something), so no row was ever read and the Degree stayed blank. The rows are waited
    for, and the button is pressed once more when nothing opened."""
    from sites.workday import _degree_candidates
    page.set_content(SLOW_DEGREE_PAGE.replace("%CLICKS%", str(clicks_needed)).replace("%DELAY%", str(delay_ms)))
    a = _agent()
    ok = WorkdayAdapter().select_from_button_dropdown(a.assistant, page, "--degree", 0, _degree_candidates("Bachelor's"))
    assert ok
    assert page.locator("#education-2--degree").inner_text() == "Bachelor of Science (BS)"
