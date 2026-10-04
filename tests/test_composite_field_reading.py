import page_agent
import pytest
from types import SimpleNamespace
import concept_matcher

def test_phone_wrapper_and_dial_code_are_not_extra_questions():
    snapshot = '''- textbox [ref=e1]:
  - generic:
    - text: Primary Phone Number
    - generic: "*"
  - button [ref=e2]:
    - paragraph: "+1"
  - textbox [ref=e3]:
    - /placeholder: xxx xxx xxxx
    - text: (202) 555-0100
'''
    controls = page_agent.parse_snapshot(snapshot)
    boxes = [c for c in controls if c.role == 'textbox']
    assert len(boxes) == 1
    assert boxes[0].ref == 'e3'
    assert boxes[0].question == 'Primary Phone Number'
    assert boxes[0].answer == '(202) 555-0100'

def test_link_fragments_do_not_replace_radio_question():
    snapshot = '''- paragraph [ref=e1]:
  - generic:
    - text: Do you consent to receiving text communications related to your job application via SMS?
    - link "Terms of Use" [ref=e2]
    - text: and
    - link "Privacy Policy" [ref=e3]
    - text: for more details.
- radiogroup [ref=e4]:
  - radio [ref=e5]
  - paragraph: Yes
  - radio [ref=e6]
  - paragraph: No
'''
    controls = page_agent.parse_snapshot(snapshot)
    radios = [c for c in controls if c.role == 'radio']
    assert len(radios) == 2
    assert all(c.question.startswith('Do you consent to receiving text communications') for c in radios)
    assert [c.name for c in radios] == ['Yes', 'No']

@pytest.mark.parametrize('role',['radio','radiogroup','combobox'])
@pytest.mark.parametrize('preference,expected',[('Email','No'),('SMS','Yes')])
def test_sms_consent_uses_contact_preference_instead_of_phone(role, preference, expected):
    question='Do you consent to receiving text communications via SMS at the mobile number provided?'
    agent=page_agent.PageAgent.__new__(page_agent.PageAgent)
    agent.profile=SimpleNamespace(preferred_contact_method=preference,phone_mobile='202-555-0100')
    agent._profile_answer_library={}
    control=page_agent.Control(ref='e1',role=role,name=question,options=['Yes','No'])
    assert agent.known_answer(control)==(expected,'profile.preferred_contact_method')
    assert concept_matcher.match_concept(question) != 'PHONE_MOBILE'

def test_sms_consent_keeps_an_explicit_saved_answer():
    question='Do you consent to receiving SMS at the mobile number provided?'
    agent=page_agent.PageAgent.__new__(page_agent.PageAgent)
    agent.profile=SimpleNamespace(preferred_contact_method='Email')
    agent._profile_answer_library={question:'Yes'}
    assert agent.known_answer(page_agent.Control('e1','radiogroup',name=question))==('Yes','profile.answer_library')

def test_phone_and_sms_radio_are_filled_without_a_planner(tmp_path):
    from playwright.sync_api import sync_playwright
    from test_page_agent import make_agent
    import config
    profile=config.UserProfile(phone_mobile='202-555-0100',preferred_contact_method='Email')
    resume=tmp_path/'resume.pdf'
    resume.write_bytes(b'%PDF-1.4 fixture')
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page()
        page.set_content('''<div role="dialog">
          <div role="textbox" aria-label="Primary Phone Number">
            <span>Primary Phone Number</span><button>+1</button>
            <input aria-label="Primary Phone Number" id="phone" placeholder="xxx xxx xxxx">
          </div>
          <p>Do you consent to receiving text communications via SMS at the mobile number provided? See
            <a href="#">Terms of Use</a> and <a href="#">Privacy Policy</a> for more details.</p>
          <div role="radiogroup">
            <div><input id="yes" type="radio" name="sms"><p aria-hidden="true">Yes</p></div>
            <div><input id="no" type="radio" name="sms"><p aria-hidden="true">No</p></div>
          </div>
          <button>Continue To Application</button>
        </div>''')
        agent=make_agent(SimpleNamespace(),resume,profile=profile)
        agent._profile_answer_library={}
        controls=page_agent.parse_snapshot(agent.snapshot(page))
        agent.answer_what_is_known(page,controls,set())
        assert page.locator('#phone').input_value()=='202-555-0100'
        assert page.locator('#no').is_checked()
        assert not page.locator('#yes').is_checked()
        browser.close()
