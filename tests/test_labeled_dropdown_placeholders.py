"""Labelled dropdown buttons must remain visible to answer resolution (ADP incident)."""
from dataclasses import replace

import pytest

import concept_matcher
import config
import page_agent
import option_match
from test_workday_prompt_textbox import agent, browser, page  # noqa: F401


@pytest.mark.parametrize("label", ["State", "Phone Type", "Application source"])
@pytest.mark.parametrize("placeholder", ["Select...", "Select One"])
def test_nested_placeholder_button_is_an_unanswered_question(label, placeholder):
    snapshot = f'''- generic [ref=e1]:
  - generic [ref=e2]: {label}*
  - button "{label} {placeholder}" [ref=e3]:
    - generic [ref=e4]: {placeholder}
  - button "Next" [ref=e5]
'''
    controls = page_agent.parse_snapshot(snapshot)
    control = next(c for c in controls if c.ref == "e3")
    assert control.question == label
    assert control.answer == ""
    assert page_agent.is_workday_choice_button(control)
    assert page_agent._blank_choice_on_page(controls)
    assert next(c for c in controls if c.ref == "e5").question == "Next"


def test_button_prose_is_not_treated_as_a_dropdown_value():
    controls = page_agent.parse_snapshot('''- button "Step Status current Contact Information" [ref=e1]:
  - generic [ref=e2]: Step Status current
  - generic [ref=e3]: Contact Information
- button "Select files" [ref=e4]:
  - generic [ref=e5]: Select...
''')
    assert all(not page_agent.is_workday_choice_button(c) for c in controls)
    assert controls[0].question == "Step Status current Contact Information"
    assert controls[1].question == "Select files"


def test_selected_button_answer_is_separate_from_its_matching_field_label():
    snapshot='''- generic [ref=e1]:
  - generic [ref=e2]: Have you worked here before?*
  - button "Have you worked here before? No" [ref=e3]:
    - generic [ref=e4]: No
'''
    control=next(c for c in page_agent.parse_snapshot(snapshot) if c.ref=='e3')
    assert control.question == 'Have you worked here before?'
    assert control.answer == 'No'


def test_nested_button_text_without_a_matching_field_label_is_not_an_answer():
    snapshot='''- generic [ref=e1]: Other information
- button "Step Status current Contact Information" [ref=e2]:
  - generic [ref=e3]: Contact Information
'''
    control=next(c for c in page_agent.parse_snapshot(snapshot) if c.ref=='e2')
    assert control.value == ''
    assert control.question == 'Step Status current Contact Information'


@pytest.mark.parametrize("label", ["Phone Type", "Phone device type", "Telephone type"])
def test_phone_type_comes_from_the_matching_typed_profile_number(label):
    profile = config.UserProfile(phone="2025550123", phone_mobile="(202) 555-0123")
    concept = concept_matcher.match_concept(question=label)
    assert concept == "PHONE_TYPE"
    value, source = concept_matcher.resolve_profile_value(concept, profile, question=label)
    assert value == "Mobile"
    assert source == "profile.phone_mobile"
    assert concept_matcher.resolve_profile_value(concept, replace(profile, phone_mobile=""))[0] == ""
    assert concept_matcher.resolve_profile_value(concept, replace(profile, phone="2025550199"))[0] == ""


def test_phone_type_does_not_replace_phone_number_or_contact_preference():
    assert concept_matcher.match_concept(question="Phone number") == "PHONE_MOBILE"
    assert concept_matcher.match_concept(question="Preferred contact method") != "PHONE_TYPE"
    assert option_match.best_option(["Cell", "Home", "Work"], "Mobile") == 0


@pytest.mark.parametrize('question', ['Candidate Source', 'Application source'])
def test_candidate_source_uses_the_approved_profile_source(question):
    concept=concept_matcher.match_concept(question=question)
    assert concept == 'HOW_DID_YOU_HEAR'
    assert concept_matcher.resolve_profile_value(concept, config.UserProfile(how_did_you_hear='LinkedIn'))[0] == 'LinkedIn'
    assert concept_matcher.match_concept(question='Source code repository') != 'HOW_DID_YOU_HEAR'


@pytest.mark.parametrize('qualifier',['completed','attained','achieved'])
def test_highest_completed_education_uses_the_saved_degree(qualifier):
    question=f'What is your highest level of {qualifier} education?'
    concept=concept_matcher.match_concept(question=question)
    assert concept == 'DEGREE_LEVEL'
    profile=config.UserProfile(education=(("Master's",'Engineering','Example University','2020'),))
    assert concept_matcher.resolve_profile_value(concept,profile,question=question)[0] == "Master's"


def test_known_profile_fills_labelled_custom_dropdowns(page):
    page.set_content('''<main id="form"></main><script>
    const values = {};
    for (const [key, label, options] of [
      ['state', 'State', ['Maryland', 'Virginia']],
      ['phoneType', 'Phone Type', ['Home', 'Mobile']]
    ]) {
      const field = document.createElement('div');
      field.innerHTML = `<span id="${key}-label">${label}</span>
        <div role="button" tabindex="0" aria-expanded="false"
          aria-labelledby="${key}-label ${key}-value"><span id="${key}-value">Select...</span></div>`;
      const button = field.querySelector('[role=button]');
      button.onclick = () => {
        if (field.querySelector('[role=listbox]')) return;
        button.setAttribute('aria-expanded', 'true');
        const list = document.createElement('div'); list.setAttribute('role', 'listbox');
        for (const option of options) {
          const row = document.createElement('div'); row.setAttribute('role','option');
          row.textContent=option;
          row.onclick=() => {
            values[key]=option;
            document.getElementById(key+'-value').textContent=option;
            button.setAttribute('aria-expanded','false'); list.remove();
          };
          list.append(row);
        }
        field.append(list);
      };
      document.querySelector('main').append(field);
    }
    document.addEventListener('keydown',e=>{
      if(e.key==='Escape')document.querySelectorAll('[role=listbox]').forEach(el=>el.remove());
    });
    </script>''')
    a = agent()
    a.profile = config.UserProfile(state="Virginia", phone="2025550123", phone_mobile="2025550123")
    a._profile_answer_library = {}
    a._published_questions = lambda: {}
    a._current_host = 'jobs.example.invalid'
    controls = page_agent.parse_snapshot(a.snapshot(page))
    filled, unanswered = a.answer_what_is_known(page, controls, {"State", "Phone Type"})
    assert page.evaluate('values') == {"state": "Virginia", "phoneType": "Mobile"}
    assert filled == 2
    assert unanswered == []


def test_denied_dropdown_click_never_dispatches_on_its_parent(page, monkeypatch):
    page.set_content('''<div onclick="document.body.dataset.parentClicked='yes'">
      <button type="button">State Select...</button></div>''')
    a = agent()
    control = next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.role == "button")
    monkeypatch.setattr(a.assistant, '_click_resiliently', lambda *args, **kwargs: False)
    assert not a._choose_exact(page, control, "Virginia")
    assert page.evaluate('document.body.dataset.parentClicked') is None
