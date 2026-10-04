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



def test_composite_date_segment_keeps_neighboring_parts(page):
    page.set_content('<div id="date">'+''.join(
        f'<input aria-label="{part}" role="spinbutton" data-automation-id="dateSection{part}-input" value="{value}">'
        for part,value in (("Month","6"),("Day","3"),("Year","2026")))+'</div>')
    page.evaluate(r"""() => {
      const fields=[...document.querySelectorAll('input')];
      fields.forEach((field,index)=>{
        field.dataset.stored=field.value;
        field.oninput=()=>field.value=field.dataset.stored;
        field.onkeydown=e=>{
          if(e.ctrlKey && e.key.toLowerCase()==='a'){
            e.preventDefault(); fields.forEach(f=>{f.value='';f.dataset.stored='';});
          } else if(/^\d$/.test(e.key)){
            e.preventDefault(); field.value+=e.key;field.dataset.stored=field.value;
            if(field.value.length===(index===2?4:2) && fields[index+1])fields[index+1].focus();
          }
        };
      });
    }""")
    a=agent()
    control=next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.name=="Month")
    assert a.do(page,page_agent.Answer(control.ref,control.question,"fill","10","job"),control)
    assert page.locator('input').evaluate_all('(es)=>es.map(e=>e.value)')==["10","03","2026"]
