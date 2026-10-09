"""A former employer's current checkbox must reveal its own end date."""
from test_workday_prompt_textbox import agent, browser, page
import page_agent
import repeated_entries
import provenance
import pytest

HISTORY = {"experience": [
    {"company": "Acme", "start": "02/2025", "end": "", "current": True},
    {"company": "Globex", "start": "02/2023", "end": "01/2025", "current": False},
    {"company": "Initech", "start": "05/2019", "end": "07/2021", "current": False},
]}

def work_form():
    entries = []
    for i, job in enumerate(HISTORY["experience"]):
        entries.append(f'''<fieldset><legend>Work Experience {i+1}</legend>
        <label>Company<input value="{job['company']}"></label>
        <label>I currently work here<input id="current-{i}" type="checkbox"
          onchange="document.getElementById('end-{i}').hidden=this.checked"></label>
        <div role="group" aria-label="From"><label>Month<input role="spinbutton" value="{job['start'].split('/')[0]}"></label>
        <label>Year<input role="spinbutton" value="{job['start'].split('/')[1]}"></label></div>
        <div id="end-{i}" role="group" aria-label="To"><label>Month<input role="spinbutton"></label>
        <label>Year<input role="spinbutton"></label></div></fieldset>''')
    return '<h2>Work Experience</h2>' + ''.join(entries)

@pytest.mark.parametrize("already_checked", [False, True])
def test_each_current_checkbox_uses_its_own_job_not_the_entry_group(page, already_checked):
    page.set_content(work_form())
    if already_checked:
        for checkbox in page.get_by_role("checkbox").all():
            checkbox.check()
    provenance.install_on_page(page)
    a = agent()
    a.history = HISTORY
    snapshot = a.snapshot(page)
    a._entries = repeated_entries.entry_map(snapshot)
    controls = page_agent.parse_snapshot(snapshot)
    a._correct_entries(page, controls, "correct")
    a.answer_what_is_known(page, controls, set())
    assert page.get_by_role("checkbox").evaluate_all('(es)=>es.map(e=>e.checked)') == [True, False, False]
    assert page.locator('#end-1').is_visible() and page.locator('#end-2').is_visible()
    assert page.locator('#end-0').is_hidden()


def test_former_jobs_end_segments_take_their_own_to_dates(page):
    page.set_content(work_form())
    provenance.install_on_page(page)
    a = agent()
    a.history = HISTORY
    for _ in range(2):
        snapshot = a.snapshot(page)
        a._entries = repeated_entries.entry_map(snapshot)
        a.answer_what_is_known(page, page_agent.parse_snapshot(snapshot), set())
    assert page.locator('#end-1 input').evaluate_all('(es)=>es.map(e=>e.value)') == ['January', '2025']
    assert page.locator('#end-2 input').evaluate_all('(es)=>es.map(e=>e.value)') == ['July', '2021']
    assert page.locator('[aria-label="From"] input').evaluate_all('(es)=>es.map(e=>e.value)') == ['02', '2025', '02', '2023', '05', '2019']


def test_owner_current_checkbox_is_preserved(page):
    page.set_content(work_form())
    provenance.install_on_page(page)
    provenance.set_agent_busy(page, False)
    page.locator('#current-1').check()
    provenance.set_agent_busy(page, True)
    a = agent()
    a.history = HISTORY
    snapshot = a.snapshot(page)
    a._entries = repeated_entries.entry_map(snapshot)
    a._correct_entries(page, page_agent.parse_snapshot(snapshot), "correct")
    assert page.locator('#current-1').is_checked()


def test_legacy_workday_filler_sets_each_current_flag_before_end_dates(page, monkeypatch):
    from sites.workday import WorkdayAdapter
    from types import SimpleNamespace
    page.set_content(''.join(f'<input id="workExperience-{i}--currentlyWorkHere" type="checkbox" checked>' for i in range(3)))
    adapter = WorkdayAdapter()
    for name in ['delete_all_entries', '_ensure_entry_slot', 'fill_by_id_suffix']:
        monkeypatch.setattr(adapter, name, lambda *args: None)
    monkeypatch.setattr(adapter, '_date_year_is', lambda *args: True)
    monkeypatch.setattr(page, 'wait_for_timeout', lambda *args: None)
    dates = []
    monkeypatch.setattr(adapter, 'fill_date_spinner', lambda assistant, pg, section, kind, index, month, year:
        dates.append((kind, index, month, year, pg.locator('input').evaluate_all('(es)=>es.map(e=>e.checked)'))))
    adapter.fill_experience_section(SimpleNamespace(), page, HISTORY['experience'])
    assert page.locator('input').evaluate_all('(es)=>es.map(e=>e.checked)') == [True, False, False]
    ends = [d for d in dates if d[0] == 'endDate']
    assert [(d[1], d[2], d[3]) for d in ends] == [(0, '1', '2025'), (1, '7', '2021')]
    assert not ends[0][4][1] and not ends[1][4][2]
