"""Greenhouse-style paragraph sections must retain each job's own facts."""
from pathlib import Path
from types import SimpleNamespace

import pytest
import page_agent
import repeated_entries as r
import provenance
from test_workday_prompt_textbox import agent, browser, page
from test_repeated_entries import HISTORY


def job_markup():
    return '''<div class="job"><label>Company name<input></label><label>Title<input></label>
    <label>Start date month<input></label><label>Start date year<input></label>
    <label>End date month<input></label><label>End date year<input></label>
    <label>Current role<input type=checkbox onchange="this.closest('.job').querySelectorAll('input').forEach(e=>{if(e.parentElement.textContent.startsWith('End date'))e.disabled=this.checked})"></label></div>'''


def form(page):
    page.set_content('''<p>Employment</p><div id=jobs>''' + job_markup() + '''</div>
    <button id=addwork onclick="document.querySelector('#jobs').insertAdjacentHTML('beforeend',document.querySelector('#template').innerHTML)">Add another</button>
    <hr><p>Education</p><label>School<input></label><label>End date year<input></label>
    <button id=addedu>Add another</button><hr><label>Linkedin Profile URL<input></label>
    <button>Submit application</button><template id=template>''' + job_markup() + '</template>')
    provenance.install_on_page(page)


def test_saved_coinbase_page_has_scoped_employment_and_education():
    path = Path('output/Coinbase_Senior_Network_Engineer/pages/20261022_201348/page_01.txt')
    if not path.exists():
        pytest.skip('local production snapshot is not distributed')
    entries = r.entry_map(path.read_text(encoding='utf-8'))
    assert entries['f3e237'].section == 'work'
    assert r.answer(entries['f3e237'], HISTORY)[2] is True
    assert entries['f3e260'].section == 'education'
    assert 'f3e304' not in entries  # LinkedIn belongs to neither repeated section


def test_paragraph_sections_and_separator_boundaries(page):
    form(page)
    a = agent()
    a.history = HISTORY
    snap = a.read_when_loaded(page)
    controls = page_agent.parse_snapshot(snap)
    work_end = next(c for c in controls if c.name == 'End date year')
    assert a.known_answer(work_end)[0] == ''
    assert work_end.ref in a._entry_blank
    outside = next(c for c in controls if c.name == 'Linkedin Profile URL')
    assert outside.ref not in a._entries
    education = next(c for c in controls if c.name == 'School')
    assert a.known_answer(education)[0] == 'State University'


def test_add_and_fill_all_jobs_without_ai_or_duplicate_entries(page):
    form(page)
    a = agent()
    a.history = {'experience': HISTORY['experience']}
    a.assistant.adapter = lambda tab: SimpleNamespace()
    for _ in range(5):
        snap = a.read_when_loaded(page)
        if not a.add_missing_inline_entries(page, snap):
            break
    assert page.locator('#jobs .job').count() == 3
    assert page.locator('#addedu').is_visible()
    for _ in range(3):
        snap = a.read_when_loaded(page)
        a.answer_what_is_known(page, page_agent.parse_snapshot(snap), set())
    for i, record in enumerate(HISTORY['experience']):
        row = page.locator('#jobs .job').nth(i)
        assert row.get_by_label('Company name').input_value() == record['company']
        assert row.get_by_label('Title', exact=True).input_value() == record['title']
        assert row.get_by_label('Current role').is_checked() is record['current']
    assert page.locator('#jobs .job').first.get_by_label('End date year').input_value() == ''
    assert page.locator('#jobs .job').nth(1).get_by_label('End date year').input_value() == '2024'
    assert page.locator('#jobs .job').nth(2).get_by_label('End date year').input_value() == '2020'
    assert not a.add_missing_inline_entries(page, a.read_when_loaded(page))
    assert page.locator('#jobs .job').count() == 3


def test_failed_add_is_not_repeated_and_reports_incomplete_history(page):
    form(page)
    page.locator('#addwork').evaluate('e => e.onclick = () => { window.presses=(window.presses||0)+1; }')
    a = agent()
    a.history = HISTORY
    a.assistant.adapter = lambda tab: SimpleNamespace()
    snap = a.read_when_loaded(page)
    assert not a.add_missing_inline_entries(page, snap)
    assert a._inline_entry_blocker
    assert not a.add_missing_inline_entries(page, a.read_when_loaded(page))
    assert page.evaluate('window.presses') == 1


def test_greenhouse_repeated_paragraphs_with_separators(page):
    """Greenhouse repeats <hr><p>Employment</p> before each card; entries must not reset to index 0."""
    page.set_content('''<p>Employment</p><div id=jobs>''' + job_markup() + '''</div>
    <button id=addwork onclick="document.querySelector(\'#jobs\').insertAdjacentHTML(\'beforeend\',document.querySelector(\'#template\').innerHTML)">Add another</button>
    <hr><p>Education</p><label>School<input></label><label>End date year<input></label>
    <button id=addedu>Add another</button><hr><label>Linkedin Profile URL<input></label>
    <button>Submit application</button><template id=template><hr><p>Employment</p>''' + job_markup() + '</template>')
    provenance.install_on_page(page)

    a = agent()
    a.history = {'experience': HISTORY['experience']}
    a.assistant.adapter = lambda tab: SimpleNamespace()
    for _ in range(5):
        snap = a.read_when_loaded(page)
        if not a.add_missing_inline_entries(page, snap):
            break
    assert page.locator('#jobs .job').count() == 3

    for _ in range(3):
        snap = a.read_when_loaded(page)
        a.answer_what_is_known(page, page_agent.parse_snapshot(snap), set())

    for i, record in enumerate(HISTORY['experience']):
        row = page.locator('#jobs .job').nth(i)
        assert row.get_by_label('Company name').input_value() == record['company']
        assert row.get_by_label('Title', exact=True).input_value() == record['title']
        assert row.get_by_label('Current role').is_checked() is record['current']


def test_disabled_end_date_not_counted_in_find_required_blanks(page):
    """When Current role is checked and disables required End date fields, they are not required blanks."""
    page.set_content('''
    <form>
      <label for=comp>Company name*</label><input required id=comp value="Capital One">
      <label for=edm>End date month*</label><input required id=edm>
      <label for=edy>End date year*</label><input required id=edy>
      <label for=cr>Current role</label><input type=checkbox id=cr onchange="document.getElementById(\'edm\').disabled = this.checked; document.getElementById(\'edy\').disabled = this.checked;">
    </form>
    ''')
    a = agent()
    # Before checking Current role, edm and edy are required blanks
    blanks_before = a.assistant.find_required_blanks(page)
    assert any("End date month" in b for b in blanks_before["required_still_blank"])
    assert any("End date year" in b for b in blanks_before["required_still_blank"])

    # Tick Current role
    page.locator('#cr').check()
    blanks_after = a.assistant.find_required_blanks(page)
    assert not any("End date month" in b for b in blanks_after["required_still_blank"])
    assert not any("End date year" in b for b in blanks_after["required_still_blank"])
