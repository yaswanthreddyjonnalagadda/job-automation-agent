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
    assert source.count("not\s+listed") <= 1, "the wording list must live in education_text_offers_other only"



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
