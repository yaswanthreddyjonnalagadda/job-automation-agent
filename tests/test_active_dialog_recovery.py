from playwright.sync_api import sync_playwright
import page_agent


def test_completed_captcha_continues_active_form_not_background_posting():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content('''<h1>Network Architect</h1>
          <button onclick="window.wrong=true">Apply</button>
          <div role="dialog" style="position:fixed;inset:0;background:white">
            <h2>Getting You Started</h2>
            <input aria-label="First Name" value="Jane">
            <div id="challenge">CAPTCHA</div>
            <button onclick="document.querySelector('[role=dialog]').remove();window.advanced=true">
              Continue To Application</button>
          </div>''')
        agent = page_agent.PageAgent.__new__(page_agent.PageAgent)
        page.locator('#challenge').evaluate('(e) => e.remove()')
        snapshot = agent.snapshot(page)
        assert 'button "Apply"' not in snapshot
        forward = agent.profile_forward(page_agent.parse_snapshot(snapshot))
        assert forward.name == 'Continue To Application'
        agent.locate(page, forward.ref).click()
        assert page.evaluate('window.advanced === true && !window.wrong')
        assert 'button "Apply"' in agent.snapshot(page)
        browser.close()


def test_hidden_dialog_does_not_hide_current_page():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content('<button>Next</button><div role="dialog" hidden><button>Old action</button></div>')
        agent = page_agent.PageAgent.__new__(page_agent.PageAgent)
        assert 'button "Next"' in agent.snapshot(page)
        browser.close()
