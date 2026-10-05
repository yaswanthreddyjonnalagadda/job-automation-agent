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
