from test_workday_prompt_textbox import agent, browser, page
import page_agent
import concept_matcher


def test_bare_name_is_personal_outside_an_education_entry():
    a=agent()
    control=page_agent.Control(ref="n",role="textbox",name="Name")
    assert a.known_answer(control)==("Example Candidate","profile.full_name")
    assert concept_matcher.match_concept("Name",container="Education")=="SCHOOL_UNIVERSITY"


def test_unqualified_date_parts_do_not_reuse_historical_answers(monkeypatch):
    monkeypatch.setattr(page_agent.answer_bank,"lookup",lambda *args: {"value":"6","source":"confirmed"})
    a=agent()
    for part in ("Month","Day","Year"):
        control=page_agent.Control(ref=part,role="spinbutton",name=part,container="Date")
        assert a.known_answer(control)==("", "")


def test_numeric_spinbutton_answer_uses_numeric_entry_even_when_plan_says_choose(page):
    page.set_content('<label for="m">Month</label><input id="m" type="number" min="1" max="12">')
    a=agent()
    c=next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.role=="spinbutton")
    assert a.do(page,page_agent.Answer(c.ref,c.question,"choose","10","job"),c)
    assert page.locator('input').input_value()=="10"
