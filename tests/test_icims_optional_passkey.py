from playwright.sync_api import sync_playwright
from sites.icims import IcimsAdapter


def test_optional_device_enrollment_is_skipped_without_trusting_device():
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page()
        page.route('https://login.icims.com/**', lambda route: route.fulfill(body='<h1>Log In Faster on This Device</h1><p>Trust this device? You can quickly and securely log in the next time using this device\'s fingerprint or face recognition.</p><button onclick="window.trusted=true">Continue</button><button onclick="window.later=true">Remind me later</button><button onclick="window.notThisDevice=true">Not on this device</button>'))
        page.goto('https://login.icims.com/u/mfa-webauthn-platform-enrollment')
        assert IcimsAdapter().dismiss_optional_passkey_enrollment(page)
        assert page.evaluate('window.later') is True
        assert page.evaluate('window.trusted') is None
        browser.close()


def test_submitted_job_status_wins_over_optional_followup_form():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.route('https://example.icims.com/**', lambda route: route.fulfill(
            content_type='text/html', body='''<iframe name="icims_content_iframe" srcdoc="
              <h1>Network Engineering Lead</h1><p>Your application was submitted successfully.</p>
              <p>You are currently submitted to this job.</p>"></iframe>
              <iframe name="icims_formFrame" srcdoc="<form><input><button>Submit</button></form>"></iframe>'''))
        page.goto('https://example.icims.com/jobs/123/form')
        assert IcimsAdapter().submission_confirmed(page)
        browser.close()
