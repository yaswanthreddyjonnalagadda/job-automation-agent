"""Collapsed numbered ADP cards keep their own records and current-job dates."""
from types import SimpleNamespace

from sites.adp import AdpAdapter
import repeated_entries
from test_workday_prompt_textbox import agent, browser, page  # noqa: F401


SNAPSHOT='''- heading "Employment History" [ref=e1]
- button "Employer 1 Hide content" [ref=e2]
  - button "Type Select..." [ref=e3]
  - textbox "Employer" [ref=e4]: Acme
  - textbox "End Date" [ref=e5]: 10/25/2026
- button "Employer 2 Hide content" [ref=e6]
  - button "Type Select..." [ref=e7]
  - textbox "Employer" [ref=e8]: Globex
  - textbox "End Date" [ref=e9]: 01/01/2025
'''
HISTORY={'experience':[{'company':'Acme','current':True,'end':''},
                       {'company':'Globex','current':False,'end':'01/2025'}]}


def test_sibling_card_regions_preserve_record_scope():
    snapshot='''- heading "Employment History" [ref=e1]
- button "Employer 1 Hide content" [expanded] [ref=e2]
- region "Employer 1" [ref=e10]:
  - button "Type Select..." [ref=e3]
  - textbox "Employer" [ref=e4]: Acme
  - textbox "End Date" [ref=e5]: 10/26/2026
- button "Employer 2 Hide content" [expanded] [ref=e6]
- region "Employer 2" [ref=e11]:
  - button "Type Select..." [ref=e7]
  - textbox "Employer" [ref=e8]: Globex
  - textbox "End Date" [ref=e9]: 01/01/2025
'''
    entries=AdpAdapter().entry_map(snapshot)
    assert entries['e4'].index == 0
    assert entries['e8'].index == 1
    assert repeated_entries.answer(entries['e3'],HISTORY)[0] == 'Current'
    assert repeated_entries.answer(entries['e7'],HISTORY)[0] == 'Previous'
    assert repeated_entries.answer(entries['e5'],HISTORY)[2] is True


def test_numbered_cards_keep_their_history_and_employment_type():
    entries=AdpAdapter().entry_map(SNAPSHOT)
    assert entries['e4'].index == 0
    assert entries['e8'].index == 1
    assert repeated_entries.answer(entries['e3'],HISTORY)[0] == 'Current'
    assert repeated_entries.answer(entries['e7'],HISTORY)[0] == 'Previous'
    assert repeated_entries.answer(entries['e5'],HISTORY)[2] is True
    assert repeated_entries.answer(entries['e9'],HISTORY)[0] == 'January 2025'


def test_only_existing_collapsed_cards_are_expanded(page):
    page.set_content('''<button onclick="this.textContent=this.textContent.replace('Show','Hide')">Employer 1 Show content</button>
    <button onclick="this.textContent=this.textContent.replace('Show','Hide')">Employer 2 Show content</button>
    <button onclick="document.body.dataset.added='yes'">Add employer</button>''')
    a=agent(); adapter=AdpAdapter()
    assert adapter.expand_form_sections(a.assistant,page)
    assert adapter.expand_form_sections(a.assistant,page)
    assert not adapter.expand_form_sections(a.assistant,page)
    assert page.evaluate('document.body.dataset.added') is None
