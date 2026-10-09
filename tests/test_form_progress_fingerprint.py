from types import SimpleNamespace
import pytest
import page_agent
from state_machine import StateFingerprintCircuitBreaker, compute_state_fingerprint


def test_changed_answers_allow_continue_but_unchanged_form_still_trips():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content('''<div aria-current="step">My Information</div>
            <input id="first" value="Jane"><input type="password" id="secret">
            <div role="listbox" aria-label="items selected"><div role="option" id="source"></div></div>''')
        guard = StateFingerprintCircuitBreaker(consecutive_threshold=3)
        assert not guard.check(page)[0]
        assert not guard.check(page)[0]
        page.locator('#source').evaluate('(e) => e.textContent="LinkedIn"')
        assert not guard.check(page)[0]
        assert not guard.check(page)[0]
        assert guard.check(page)[0]
        fingerprint, metadata = compute_state_fingerprint(page)
        assert 'Jane' not in str(metadata)
        page.locator('#secret').fill('private-value')
        assert compute_state_fingerprint(page)[0] == fingerprint
        page.locator('#first').fill('Janet')
        assert compute_state_fingerprint(page)[0] != fingerprint
        browser.close()


@pytest.mark.parametrize('preferred,expected', [('', 'unchecked'), ('Jane', 'checked')])
def test_preferred_name_toggle_uses_boolean_not_name(preferred, expected):
    agent = page_agent.PageAgent.__new__(page_agent.PageAgent)
    agent.profile = SimpleNamespace(preferred_name=preferred)
    control = page_agent.Control('e1', 'checkbox', name='I have a preferred name')
    assert agent.known_answer(control) == (expected, 'profile.preferred_name')


def test_covered_radio_uses_its_associated_label(tmp_path):
    from playwright.sync_api import sync_playwright
    from test_page_agent import make_agent
    import config
    resume = tmp_path / 'resume.pdf'
    resume.write_bytes(b'%PDF-1.4 fixture')
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content('''<fieldset><legend>Previously worked here?</legend>
          <label for="yes">Yes</label><input id="yes" type="radio" name="worked">
          <span style="position:relative;display:inline-block;width:20px;height:20px">
            <input id="no" type="radio" name="worked" style="position:absolute;inset:0;margin:0">
            <span style="position:absolute;inset:0;background:white"></span>
          </span><label for="no">No</label></fieldset>
          <input type="checkbox" id="other"><label for="other">Unrelated choice</label>''')
        agent = make_agent(SimpleNamespace(), resume, profile=config.UserProfile())
        controls = page_agent.parse_snapshot(agent.snapshot(page))
        control = next(c for c in controls if c.role == 'radio' and c.name == 'No')
        assert agent.do(page, page_agent.Answer(control.ref, control.question, 'choose', 'No', 'profile'), control)
        assert page.locator('#no').is_checked()
        assert not page.locator('#yes').is_checked()
        assert not page.locator('#other').is_checked()
        browser.close()
