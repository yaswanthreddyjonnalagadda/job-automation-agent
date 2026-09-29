"""The agent's sign-in step follows account_state on a real page: KBI Biopharma and OCC, 28 September.

The assistant is a stand-in that records what it was asked to do; the page is real HTML in a browser.
"""
from types import SimpleNamespace

import pytest

import config
import page_agent


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    pg = context.new_page()
    yield pg
    context.close()


class Assistant:
    def __init__(self, held="", exists=False, gate=False, refuses=False, resets=False):
        self._login_paused, self._exists, self._gate, self.calls = held, exists, gate, []
        self._refuses, self._resets = refuses, resets
        self._last_login_rejected = False
        self.values = None

    def adapter(self, tab):
        return SimpleNamespace(candidate_account_state=lambda tab: "")

    def account_on_record(self):
        return self._exists

    def handle_auth_gate(self, tab, email):
        self.calls.append(("gate", email))
        self._last_login_rejected = self._refuses
        return self._gate

    def recover_rejected_sign_in(self, tab, email):
        self.calls.append(("reset", email))
        return self._resets

    def _sign_in_with_google(self, tab, button, email, host, still_offered):
        self.calls.append(("google", button.inner_text().strip()))
        return True

    def _goto_login_page(self, tab):
        self.calls.append(("login_page",))
        return True


def agent(assistant, tmp_path):
    job = SimpleNamespace(title="Engineer", company="Example", url="https://jobs.example.com/apply")
    a = page_agent.PageAgent(assistant, SimpleNamespace(), SimpleNamespace(auto_submit=False, ats_email="jane@example.com"),
                             config.UserProfile(email="jane@example.com"), SimpleNamespace(raw_text="x"), job,
                             resume_file=None, job_dir=tmp_path)
    return a


def serve(page, body):
    page.route("https://jobs.example.com/**", lambda route: route.fulfill(
        status=200, content_type="text/html", body=f"<html><body>{body}</body></html>"))
    page.goto("https://jobs.example.com/apply")


def step(page, a):
    controls = page_agent.parse_snapshot(a.snapshot(page))
    return a.sign_in_step(page, controls)


CREATE = """<h2>Create Account</h2><label>Email Address<input type="email"></label>
<label>Password<input type="password"></label><label>Verify New Password<input type="password"></label>
<button>Create Account</button>"""
SIGN_IN = """<h2>Sign In</h2><label>Email Address<input type="email"></label>
<label>Password<input type="password"></label><button>Sign In</button>"""


def test_a_verification_email_stops_the_run_with_what_to_do(page, tmp_path):
    serve(page, "<h2>Check your email</h2><p>We have sent you an email to verify your account.</p>")
    assistant = Assistant()
    a = agent(assistant, tmp_path)
    assert step(page, a) is False
    assert "click its link" in a.account_blocker and assistant.calls == []


@pytest.mark.parametrize("message", ["Wrong email address or password", "Your account has been locked."])
def test_a_refused_or_locked_account_stops_the_run(page, tmp_path, message):
    serve(page, SIGN_IN + f'<div role="alert">{message}</div>')
    assistant = Assistant()
    a = agent(assistant, tmp_path)
    assert step(page, a) is False and a.account_blocker and assistant.calls == []


def test_google_is_found_by_the_words_inside_its_button(page, tmp_path):
    serve(page, "<h2>Sign In</h2><button><span>Sign in with Apple</span></button>"
                "<button><span>Sign in with Google</span></button><button>Sign in with email</button>")
    assistant = Assistant()
    assert step(page, agent(assistant, tmp_path)) is True
    assert assistant.calls == [("google", "Sign in with Google")]


def test_a_new_account_form_creates_the_account_once(page, tmp_path):
    serve(page, CREATE)
    assistant = Assistant(gate=True)
    a = agent(assistant, tmp_path)
    assert step(page, a) is True and assistant.calls == [("gate", "jane@example.com")]
    assert step(page, a) is False and "still showing" in a.account_blocker      # not a second account
    assert assistant.calls == [("gate", "jane@example.com")]


def test_an_account_on_record_is_signed_in_to_not_made_again(page, tmp_path):
    serve(page, CREATE)
    assistant = Assistant(exists=True)
    assert step(page, agent(assistant, tmp_path)) is True
    assert assistant.calls == [("login_page",)]


def test_a_held_account_is_not_tried_and_the_reason_is_given(page, tmp_path):
    serve(page, SIGN_IN)
    assistant = Assistant(held="an account was already tried 2 times in the last 24 hours")
    a = agent(assistant, tmp_path)
    assert step(page, a) is False and "2 times" in a.account_blocker and assistant.calls == []


def test_every_account_state_is_logged_with_a_screenshot(page, tmp_path):
    serve(page, CREATE)
    step(page, agent(Assistant(gate=True), tmp_path))
    saved = sorted(p.suffix for p in (tmp_path / "account").iterdir())
    assert saved == [".png", ".txt"]


def test_an_application_page_is_left_to_the_rest_of_the_agent(page, tmp_path):
    serve(page, '<p>current step 2 of 5</p><h2>My Information</h2><label>First Name<input></label>')
    assistant = Assistant()
    a = agent(assistant, tmp_path)
    assert step(page, a) is False and a.account_blocker == "" and assistant.calls == []


# --- a refused password on an account the site knows (CLAUDE.md §5) --------------------------------------------------

REFUSED = SIGN_IN + '<div role="alert">Invalid username or password.</div>'


def test_a_password_refused_on_a_known_account_is_reset_with_the_emailed_code(page, tmp_path):
    """Mutual of Enumclaw (iCIMS), 29 September: the sign-in was refused and the run stopped with the username and
    password as questions for the owner. The owner's rule: reset it to the same ATS password with the emailed code."""
    serve(page, SIGN_IN)
    assistant = Assistant(exists=True, refuses=True, resets=True)
    assert step(page, agent(assistant, tmp_path)) is True
    assert assistant.calls == [("gate", "jane@example.com"), ("reset", "jane@example.com")]


def test_a_page_that_already_shows_the_refusal_is_reset_for_a_known_account(page, tmp_path):
    serve(page, REFUSED)
    assistant = Assistant(exists=True, resets=True)
    assert step(page, agent(assistant, tmp_path)) is True
    assert assistant.calls == [("reset", "jane@example.com")]


def test_a_reset_that_did_not_work_is_not_asked_for_again_and_says_why(page, tmp_path):
    serve(page, REFUSED)
    assistant = Assistant(exists=True, resets=False)
    a = agent(assistant, tmp_path)
    assert step(page, a) is False and "refused" in a.account_blocker
    assert step(page, a) is False and assistant.calls == [("reset", "jane@example.com")]


def test_a_refused_password_on_an_unknown_account_is_not_reset(page, tmp_path):
    serve(page, SIGN_IN)
    assistant = Assistant(refuses=True, resets=True)
    assert step(page, agent(assistant, tmp_path)) is False
    assert assistant.calls == [("gate", "jane@example.com")]


def test_a_new_account_form_that_is_refused_is_not_reset(page, tmp_path):
    serve(page, CREATE)
    assistant = Assistant(exists=False, refuses=True, resets=True)
    step(page, agent(assistant, tmp_path))
    assert ("reset", "jane@example.com") not in assistant.calls


def test_a_refusal_left_over_from_an_earlier_attempt_does_not_start_a_reset(page, tmp_path):
    serve(page, SIGN_IN)
    assistant = Assistant(exists=True, refuses=False, resets=True)
    assistant._last_login_rejected = True           # from an attempt before this one
    step(page, agent(assistant, tmp_path))
    assert ("reset", "jane@example.com") not in assistant.calls
