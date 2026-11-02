import pytest
import form_fields
import page_agent
import repeated_entries
import provenance
from dataclasses import replace


def test_unscoped_from_date_does_not_borrow_current_employment_dates():
    a=agent();a.profile=replace(a.profile,current_employment_dates='February 2025 - Present')
    assert a.known_answer(page_agent.Control(ref='e1',role='textbox',name='From Date'))[0]==''
    assert a.known_answer(page_agent.Control(ref='e2',role='textbox',name='Start Date'))[0]=='February 2025'
from test_workday_prompt_textbox import agent, browser, page  # noqa: F401


@pytest.mark.parametrize('shown,expected,match',[
    ('02/01/2025','February 2025',True),
    ('01/31/2025','January 2025',True),
    ('02/01/2025','January 2025',False),
    ('01/30/2025','01/31/2025',False),
    ('2025-01-31','01/31/2025',True),
    ('not a date','January 2025',False),
])
def test_date_match_respects_the_precision_of_the_saved_fact(shown,expected,match):
    assert form_fields.date_matches(shown,expected) is match


def test_committed_dates_are_verified_without_rewriting_a_matching_month(page):
    page.set_content('''<fieldset><legend>Work Experience 1</legend>
      <label>Employer<input value="Acme"></label>
      <label>Start Date<input placeholder="MM/DD/YYYY" value="02/01/2023"></label>
      <label>End Date<input placeholder="MM/DD/YYYY" value="01/31/2025"></label>
      </fieldset>''')
    a=agent()
    provenance.install_on_page(page)
    a.history={'experience':[dict(company='Acme',start='02/2023',end='01/2025',current=False)]}
    snapshot=a.snapshot(page)
    a._entries=repeated_entries.entry_map(snapshot)
    controls=page_agent.parse_snapshot(snapshot)
    assert a._correct_entries(page,controls,'correct') == []
    assert page.get_by_role('textbox',name='End Date',exact=True).input_value() == '01/31/2025'
    start=next(c for c in controls if c.question=='Start Date')
    assert a.not_stuck([(page_agent.Answer(start.ref,start.question,'fill','February 2023','history'),start)],controls) == []
