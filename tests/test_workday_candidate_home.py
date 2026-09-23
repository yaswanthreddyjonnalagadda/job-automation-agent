import pytest

from sites.workday import WorkdayAdapter


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    page = browser.new_page()
    yield page
    page.close()


def test_workday_candidate_home_states(page):
    adapter = WorkdayAdapter()

    page.set_content("""<h3>Create Account</h3><form>
        <label>Email Address<input type=email></label>
        <label>Password<input type=password></label>
        <label>Verify New Password<input type=password></label>
        <p>Error: Passwords do not match</p>
        <label><input type=checkbox> Yes, I understand and acknowledge the terms and conditions.</label>
        <p>Error: Please check the box to continue</p><button>Create Account</button>
    </form><form data-automation-id=signInForm>
        <label>Password<input type=password></label><button>Sign In</button>
    </form>""")
    assert adapter.candidate_account_state(page) == "registration_error"

    page.set_content("""<h3>Create Account</h3><form>
        <label>Email Address<input type=email></label>
        <label>Password<input type=password></label>
        <label>Verify New Password<input type=password></label>
        <button>Create Account</button>
    </form>""")
    assert adapter.candidate_account_state(page) == "registration"

    page.set_content("""<h3>Sign In</h3><form data-automation-id=signInForm>
        <label>Email Address<input type=email></label><label>Password<input type=password></label>
        <button data-automation-id=signInSubmitButton>Sign In</button>
    </form>""")
    assert adapter.candidate_account_state(page) == "sign_in"

    page.set_content("""<h3>Verify your identity</h3>
        <label>Verification Code<input type=text></label>""")
    assert adapter.candidate_account_state(page) == "verification"

    page.set_content("""<div data-automation-id=progressBarActiveStep>My Experience</div>
        <h2>Application</h2>""")
    assert adapter.candidate_account_state(page) == "application"

    page.set_content("""<h1>Candidate Home</h1><a>My Applications</a>""")
    assert adapter.candidate_account_state(page) == "candidate_home"