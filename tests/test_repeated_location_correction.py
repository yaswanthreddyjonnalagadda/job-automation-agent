from dataclasses import replace
import page_agent,repeated_entries,provenance
from test_workday_prompt_textbox import agent,browser,page  # noqa: F401


def test_home_city_does_not_replace_a_school_city(page,monkeypatch):
    page.set_content('''<div role="region" aria-label="Education 1">
      <label>School<input value="Example University"></label>
      <label>City<input value="College Town"></label></div>''')
    a=agent();a.profile=replace(a.profile,city='Home Town')
    provenance.install_on_page(page)
    s=a.snapshot(page);a._entries=repeated_entries.entry_map(s)
    a.history={'education':[dict(school='Example University')]}
    monkeypatch.setattr(a,'_open_answer_refusal',lambda *args:'')
    plan=page_agent.PagePlan(mismatches=[dict(question='City',correct_value='Home Town',source='profile.city')])
    a.correct_from_profile(page,plan,page_agent.parse_snapshot(s))
    assert page.get_by_role('textbox',name='City',exact=True).input_value()=='College Town'
