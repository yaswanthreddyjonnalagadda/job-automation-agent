"""Workday skills are separate committed selections, never one comma-separated search."""
import pytest
from test_workday_prompt_textbox import browser, page, agent
import page_agent


def widget(required=False):
    return '''<input type="checkbox" aria-label="I currently work here" checked>
    <label for="skills">Type to Add Skills</label>
    <div data-automation-id="multiSelectContainer">
      <input id="skills" data-uxi-widget-type="selectinput" data-uxi-multiselect-id="skill-widget"
        aria-required="REQUIRED" oninput="document.getElementById('popup').hidden=false;
        for(const r of document.querySelectorAll('[data-automation-id=promptOption]'))
          r.hidden=!r.textContent.toLowerCase().includes(this.value.toLowerCase())">
      <div id="tags"></div>
    </div>
    <div id="popup" data-automation-id="responsiveMonikerPrompt" data-associated-widget="skill-widget" hidden>
      <div data-automation-id="promptOption" onclick="add(this)">AWS</div>
      <div data-automation-id="promptOption" onclick="add(this)">BGP</div>
    </div>
    <script>function add(row){const tag=document.createElement('span');
      tag.dataset.automationId='selectedItem';tag.textContent=row.textContent;
      document.getElementById('tags').appendChild(tag);document.getElementById('skills').value='';
      document.getElementById('popup').hidden=true;}
      document.addEventListener('keydown', e=>{if(e.key==='Escape') document.getElementById('popup').hidden=true;});</script>
    '''.replace('REQUIRED',str(required).lower())


@pytest.mark.parametrize('value,expected,required,success',[
    ('AWS, BGP',['AWS','BGP'],False,True),
    ('AWS, Missing',['AWS'],False,True),
    ('Missing',[],False,True),
    ('Missing',[],True,False),
])
def test_skills_use_separate_scoped_verified_rows(page,value,expected,required,success):
    page.set_content(widget(required))
    a=agent()
    control=next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.name=='Type to Add Skills')
    assert a.do(page,page_agent.Answer(control.ref,control.question,'fill',value,'profile'),control)==success
    assert page.locator('[data-automation-id="selectedItem"]').all_text_contents()==expected
    assert page.get_by_role('checkbox').is_checked()
    assert page.locator('#skills').input_value()==''
    assert not page.locator('#popup').is_visible()


def test_an_unexpected_tab_opened_during_a_skills_search_is_closed_not_left_open(page):
    """f134: whatever opens it -- a misclick on the widget, or the page's own script reacting
    to the search -- a tab this loop did not ask for must never be left dangling. Several
    separate searches can run in one call (one per skill), and an accumulating stray tab is
    exactly the kind of resource pressure that can destabilize a long-running Workday page."""
    page.set_content(widget() + '''<script>
      document.getElementById('skills').addEventListener('input', () => {
        if (document.getElementById('skills').value.toLowerCase() === 'missing')
          window.open('about:blank', '_blank');
      });
    </script>''')
    a = agent()
    control = next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.name == 'Type to Add Skills')
    before = set(page.context.pages)

    a.do(page, page_agent.Answer(control.ref, control.question, 'fill', 'Missing', 'profile'), control)

    assert set(page.context.pages) == before


def test_resumed_skills_keep_existing_selections_without_duplicates(page):
    page.set_content(widget())
    page.evaluate("add(document.querySelector('[data-automation-id=promptOption]'))")
    a=agent()
    control=next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.name=='Type to Add Skills')
    a.do(page,page_agent.Answer(control.ref,control.question,'fill','AWS, BGP','profile'),control)
    assert page.locator('[data-automation-id="selectedItem"]').all_text_contents()==['AWS','BGP']


def test_layout_group_does_not_inherit_the_preceding_skills_question():
    snapshot='''- generic [ref=s1]: Type to Add Skills
- textbox "Type to Add Skills" [ref=s2]:
- group [ref=s3]:
  - heading "Resume/CV" [level=4] [ref=s4]
  - button "Select files" [ref=s5]'''
    group=next(c for c in page_agent.parse_snapshot(snapshot) if c.ref=='s3')
    assert group.question==''


def test_layout_group_cannot_select_an_unrelated_checkbox(page):
    page.set_content('<div role="group"><h4>Resume/CV</h4></div><input type="checkbox" aria-label="AWS">')
    a=agent()
    group=next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.role=='group')
    assert not a.do(page,page_agent.Answer(group.ref,'Skills','choose','AWS','profile'),group)
    assert not page.get_by_role('checkbox').is_checked()


def test_empty_open_menu_is_not_a_second_form_field():
    snapshot='''- textbox "Type to Add Skills" [ref=k1]:
- listbox "Options Expanded" [ref=k2]:
  - option "No Items." [ref=k3]'''
    controls=page_agent.parse_snapshot(snapshot)
    assert not any(c.ref=='k2' for c in controls)
    assert any(c.ref=='k1' for c in controls)


def test_empty_skills_menu_is_dismissed_when_escape_is_ignored(page):
    html=widget().replace("if(e.key==='Escape') document.getElementById('popup').hidden=true;","")
    page.set_content('<h4 onclick="document.getElementById(\'popup\').hidden=true">Skills</h4>'+html)
    a=agent()
    control=next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.name=='Type to Add Skills')
    assert a.do(page,page_agent.Answer(control.ref,control.question,'fill','Missing','profile'),control)
    assert not page.locator('#popup').is_visible()
    assert control.ref in a._entry_blank


@pytest.mark.parametrize('value', ['AWS, BGP', 'AWS, Missing', 'Missing'])
def test_page_cycle_verifies_skill_tags_or_intentional_optional_blank(page, value):
    page.set_content(widget())
    a = agent()
    control = next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.name == 'Type to Add Skills')
    answer = page_agent.Answer(control.ref, control.question, 'fill', value, 'profile')
    given = a.apply_answers(page, page_agent.PagePlan(answers=[answer]), [control])
    assert given
    after = page_agent.parse_snapshot(a.snapshot(page))
    assert a.not_stuck(given, after) == []


def test_removed_skill_tags_are_not_accepted_from_stale_completion(page):
    page.set_content(widget())
    a = agent()
    control = next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.name == 'Type to Add Skills')
    answer = page_agent.Answer(control.ref, control.question, 'fill', 'AWS', 'profile')
    given = a.apply_answers(page, page_agent.PagePlan(answers=[answer]), [control])
    page.locator('#tags').evaluate('e => e.replaceChildren()')
    assert a.not_stuck(given, page_agent.parse_snapshot(a.snapshot(page)))


def test_optional_skill_blank_cannot_hide_a_new_required_field(page):
    page.set_content(widget())
    a = agent()
    control = next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.name == 'Type to Add Skills')
    answer = page_agent.Answer(control.ref, control.question, 'fill', 'Missing', 'profile')
    given = a.apply_answers(page, page_agent.PagePlan(answers=[answer]), [control])
    page.locator('#skills').evaluate('e => e.setAttribute("aria-required", "true")')
    assert a.not_stuck(given, page_agent.parse_snapshot(a.snapshot(page)))


def test_optional_skill_completion_does_not_repeat_unavailable_searches(page, monkeypatch):
    from sites.workday import WorkdayAdapter
    page.set_content(widget())
    a = agent()
    control = next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.name == 'Type to Add Skills')
    answer = page_agent.Answer(control.ref, control.question, 'fill', 'Missing', 'profile')
    assert a.do(page, answer, control)
    monkeypatch.setattr(WorkdayAdapter, 'select_skills', lambda *args: pytest.fail('repeated a verified optional search'))
    assert a.do(page, answer, control)


def test_skill_requirement_on_label_cannot_be_skipped_as_optional(page):
    page.set_content(widget().replace('>Type to Add Skills<', '>Type to Add Skills *<'))
    a = agent()
    control = next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.role == 'textbox')
    assert not a.do(page, page_agent.Answer(control.ref, control.question, 'fill', 'Missing', 'profile'), control)


def test_skill_requirement_metadata_stays_with_its_field_in_mixed_page(page):
    page.set_content('<label>Other required field<input required></label>' + widget())
    a = agent()
    control = next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.name == 'Type to Add Skills')
    answer = page_agent.Answer(control.ref, control.question, 'fill', 'Missing', 'profile')
    given = a.apply_answers(page, page_agent.PagePlan(answers=[answer]), [control])
    assert given
    assert a.not_stuck(given, page_agent.parse_snapshot(a.snapshot(page))) == []
