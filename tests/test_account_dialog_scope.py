"""Authentication state must count fields in the same scope as its snapshot."""
import pytest

import account_state
from page_agent import PageAgent


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


BACKGROUND = '<h2>Create Account</h2><input type=password><input type=password>'
SIGN_IN = '<section role=dialog><h2>Sign In</h2><label>Password<input type=password></label></section>'


def test_sign_in_modal_ignores_registration_passwords_behind_it(page):
    page.set_content(BACKGROUND + SIGN_IN)
    reader = PageAgent.__new__(PageAgent)
    state = account_state.read_state(reader.snapshot(page), password_boxes=reader._password_boxes(page))
    assert state.kind == account_state.SIGN_IN_FORM
    assert state.password_boxes == 1


def test_sign_in_modal_ignores_background_iframe_passwords(page):
    page.set_content('<iframe srcdoc="<input type=password>"></iframe>' + SIGN_IN)
    assert PageAgent._password_boxes(page) == 1


def test_password_frame_inside_active_dialog_is_counted(page):
    page.set_content(BACKGROUND + '<section role=dialog><h2>Sign In</h2>'
                     '<iframe srcdoc="<input type=password>"></iframe></section>')
    assert PageAgent._password_boxes(page) == 1


def test_password_frames_without_a_dialog_are_still_counted(page):
    page.set_content('<input type=password><iframe srcdoc="<input type=password>"></iframe>')
    assert PageAgent._password_boxes(page) == 2


def test_auth_helper_signs_in_modal_before_background_registration(page):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from browser_automation import JobApplicationAssistant

    page.set_content('<form><h2>Create Account</h2><input type=password><input type=password>'
                     '<button>Create Account</button></form><section role=dialog><form>'
                     '<h2>Sign In</h2><input type=email><input type=password><button>Sign In</button>'
                     '</form></section>')
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.adapter = lambda _: SimpleNamespace(candidate_account_state=lambda _: "registration")
    assistant._read_ats_password = lambda: "Synthetic-Password1!"
    assistant._account_exists_message = lambda _: False
    assistant.account_on_record = lambda: False
    assistant._goto_login_page = lambda _: False
    assistant.fill_create_account_form = Mock(return_value=True)
    assistant.attempt_auto_login = Mock(return_value=True)
    assert assistant.handle_auth_gate(page, "owner@example.com")
    assistant.fill_create_account_form.assert_not_called()
    assert assistant.attempt_auto_login.call_count == 1
    assert assistant.attempt_auto_login.call_args.kwargs["create_if_missing"] is False


@pytest.mark.parametrize("passwords", [1, 2])
def test_registration_dialog_is_not_a_sign_in_scope(page, passwords):
    from browser_automation import JobApplicationAssistant

    page.set_content('<section role=dialog><form><h2>Create Account</h2>'
                     '<input type=email>' + '<input type=password>' * passwords
                     + '<button>Create Account</button></form></section>')
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assert assistant._sign_in_scope(page) is None


def test_verification_requirement_still_outranks_sign_in_modal(page):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from browser_automation import JobApplicationAssistant

    page.set_content(SIGN_IN)
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.adapter = lambda _: SimpleNamespace(candidate_account_state=lambda _: "verification")
    assistant.attempt_auto_login = Mock()
    assert not assistant.handle_auth_gate(page, "owner@example.com")
    assistant.attempt_auto_login.assert_not_called()


def test_sign_in_heading_identifies_continue_form(page):
    from browser_automation import JobApplicationAssistant

    page.set_content('<section role=dialog><h2>Sign In</h2><form>'
                     '<input type=email><input type=password><button>Continue</button></form></section>')
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assert assistant._sign_in_scope(page) is not None
