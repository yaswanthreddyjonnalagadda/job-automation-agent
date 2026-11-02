"""An approved multi-choice answer preserves commas inside its option labels."""
import pytest

import page_agent
from test_workday_prompt_textbox import agent, browser, page  # noqa: F401

FIRST="Yes, at the company's expense"
SECOND="Yes, at my own expense under the right circumstances"


@pytest.mark.parametrize('value, expected, selected', [
    (FIRST+'; '+SECOND, True, [False,True,True]),
    (FIRST, True, [False,True,False]),
    (FIRST+'; Unknown choice', False, [True,False,False]),
    ('Yes', False, [True,False,False]),
])
@pytest.mark.parametrize('action',['choose','check'])
def test_approved_checkbox_set_replaces_stale_choices_only_after_full_match(page,value,expected,selected,action):
    page.set_content(f'''<fieldset><legend>Would you consider relocating?</legend>
      <label><input type=checkbox checked>No, I would not consider relocating</label>
      <label><input type=checkbox>{FIRST}</label><label><input type=checkbox>{SECOND}</label>
      </fieldset><fieldset><legend>Other question</legend><label><input type=checkbox checked>Keep this choice</label></fieldset>''')
    a=agent();controls=page_agent.parse_snapshot(a.snapshot(page));control=next(c for c in controls if c.role=='checkbox')
    assert a.do(page,page_agent.Answer(control.ref,control.question,action,value,'profile.answer_library'),control) is expected
    assert page.locator('fieldset').first.locator('input').evaluate_all('(es)=>es.map(e=>e.checked)')==selected
    assert page.get_by_role('checkbox',name='Keep this choice').is_checked()


def test_repeated_parent_questions_remain_separate_checkbox_groups(page):
    one='<fieldset><legend>Which arrangements apply?</legend><label><input type=checkbox>First</label><label><input type=checkbox>Second</label></fieldset>'
    page.set_content(one+one)
    a=agent();boxes=[c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.role=='checkbox']
    assert boxes[0].group==boxes[1].group and boxes[2].group==boxes[3].group
    assert boxes[0].group!=boxes[2].group


def test_independent_consent_statements_are_not_grouped_as_choices(page):
    page.set_content('<fieldset><legend>Do you agree?</legend><label><input type=checkbox>I consent to emails</label><label><input type=checkbox>I agree to terms</label></fieldset>')
    a=agent();boxes=[c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.role=='checkbox']
    assert all(not c.group for c in boxes)


def test_named_radio_check_resolves_answer_instead_of_referenced_no(page):
    page.set_content('<fieldset><legend>Available?</legend><label><input type=radio name=a>No</label><label><input type=radio name=a>Yes</label></fieldset>')
    a = agent()
    control = next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.role == 'radio')
    assert a.do(page, page_agent.Answer(control.ref, control.question, 'check', 'Yes', 'owner'), control)
    assert page.get_by_role('radio', name='Yes', exact=True).is_checked()
    assert not page.get_by_role('radio', name='No', exact=True).is_checked()


def test_literal_checked_still_addresses_individual_box(page):
    page.set_content('<fieldset><legend>Choices?</legend><label><input type=checkbox>First</label><label><input type=checkbox>Second</label></fieldset>')
    a = agent()
    control = next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.role == 'checkbox')
    assert a.do(page, page_agent.Answer(control.ref, control.question, 'check', 'checked', 'owner'), control)
    assert page.get_by_role('checkbox', name='First').is_checked()
    assert not page.get_by_role('checkbox', name='Second').is_checked()
