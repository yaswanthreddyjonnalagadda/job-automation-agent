import page_agent,repeated_entries
from test_workday_prompt_textbox import agent,browser,page  # noqa: F401


def test_missing_degree_is_added_once_until_saved_history_is_complete(page):
    page.set_content('''<section><h2>Education History</h2><div id="degrees">
      <fieldset><legend>Education 1</legend><label>School<input value="Example University"></label></fieldset>
      </div><button onclick="document.querySelector('#degrees').insertAdjacentHTML('beforeend','<fieldset><legend>Education 2</legend><label>School<input></label></fieldset>')">Add education</button></section>''')
    a=agent();a.history={'education':[dict(school='Example University'),dict(school='Other College')]}
    s=a.snapshot(page);a._entries=repeated_entries.entry_map(s)
    assert a.add_missing_inline_entries(page,s)
    s=a.snapshot(page);a._entries=repeated_entries.entry_map(s)
    assert not a.add_missing_inline_entries(page,s)
    assert page.get_by_role('textbox',name='School',exact=True).count()==2


def test_adp_add_that_collapses_cards_is_counted_after_reopening(page,monkeypatch):
    import sites
    from sites.adp import AdpAdapter
    monkeypatch.setattr(sites,'adapter_for',lambda url:AdpAdapter())
    page.set_content('''<h2>Education History</h2><div id="degrees">
      <button onclick="this.nextElementSibling.hidden=false;this.textContent='Education 1 Hide content'">Education 1 Hide content</button>
      <div role="region" aria-label="Education 1"><label>School<input value="Example University"></label></div></div>
      <button onclick="document.querySelector('#degrees [role=region]').hidden=true;
        document.querySelector('#degrees button').textContent='Education 1 Show content';
        document.querySelector('#degrees').insertAdjacentHTML('beforeend',document.querySelector('template').innerHTML)">Add education</button>
      <template><button onclick="this.nextElementSibling.hidden=false;this.textContent='Education 2 Hide content'">Education 2 Show content</button>
      <div role="region" aria-label="Education 2" hidden><label>School<input></label></div></template>''')
    a=agent();a.history={'education':[dict(school='Example University'),dict(school='Other College')]}
    s=a.snapshot(page);a._entries=a._entry_map(page,s)
    assert a.add_missing_inline_entries(page,s)
    assert page.get_by_role('textbox',name='School',exact=True).count()==2
    assert not a.add_missing_inline_entries(page,a.snapshot(page))
