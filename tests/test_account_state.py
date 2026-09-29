"""The account step, read from what a page shows, on any employer site.

Real pages are in tests/fixtures/account_pages/ (recorded from Workday runs, personal details replaced); the rest
are the messages sites give, one per state. The decision table is tested for every state, and the rules the owner
set: Google first; a verification link is the owner's; nothing tried twice in one run.
"""
from pathlib import Path

import pytest

import account_state as A

PAGES = Path(__file__).parent / "fixtures" / "account_pages"


def page(name: str) -> str:
    return (PAGES / name).read_text(encoding="utf-8")


# --- real pages ---------------------------------------------------------------------------------

@pytest.mark.parametrize("fixture, kind", [
    ("workday_create_account.txt", A.CREATE_FORM),        # KBI: email, password, verify password
    ("workday_sign_in_chooser.txt", A.CHOOSER),           # OCC: Apple / Google / LinkedIn / email
    ("workday_step1_loading.txt", A.LOADING),             # OCC: step 1 before its form is drawn
    ("workday_my_information.txt", A.SIGNED_IN),          # OCC: step 1 of 5, My Information
    ("google_choose_account.txt", A.NONE),                # Google's own picker, mid sign-in
])
def test_real_pages_are_read_as_what_they_are(fixture, kind):
    assert A.read_state(page(fixture)).kind == kind


def test_the_chooser_knows_google_is_offered():
    assert A.read_state(page("workday_sign_in_chooser.txt")).google_offered


def test_the_robots_only_box_is_not_counted_as_a_password_box():
    assert A.read_state(page("workday_create_account.txt")).password_boxes == 2


# --- what sites say -------------------------------------------------------------------------------

def form(message: str = "", passwords=("Password",), heading="Sign In") -> str:
    lines = [f'- heading "{heading}" [level=2] [ref=e1]']
    if message:
        lines.append(f"- alert [ref=e2]: {message}")
    lines.append('- textbox "Email Address" [ref=e3]')
    lines += [f'- textbox "{p}" [ref=e{10 + i}]' for i, p in enumerate(passwords)]
    lines.append(f'- button "{heading}" [ref=e9]')
    return "\n".join(lines)


@pytest.mark.parametrize("message, kind", [
    ("Your account has been locked. Try again later.", A.LOCKED),
    ("Too many failed sign-in attempts.", A.LOCKED),
    ("Account is Inactive", A.LOCKED),
    ("Wrong email address or password", A.WRONG_PASSWORD),
    ("The password is incorrect.", A.WRONG_PASSWORD),
    ("We couldn't sign you in.", A.WRONG_PASSWORD),
    ("Invalid username or password", A.WRONG_PASSWORD),
])
def test_what_the_site_says_outranks_the_form_it_says_it_on(message, kind):
    assert A.read_state(form(message)).kind == kind


@pytest.mark.parametrize("message", ["An account with this email already exists.",
                                     "This email address is already registered",
                                     "You already have an account with this email"])
def test_an_email_that_already_has_an_account(message):
    state = A.read_state(form(message, passwords=("Password", "Verify New Password"), heading="Create Account"))
    assert state.kind == A.ACCOUNT_EXISTS


@pytest.mark.parametrize("snapshot, kind", [
    ('- heading "Create Account" [ref=e1]\n- textbox "Email Address" [ref=e2]\n- textbox "Password" [ref=e3]\n'
     '- textbox "Verify New Password" [ref=e4]\n- button "Create Account" [ref=e5]', A.CREATE_FORM),
    ('- heading "Register" [ref=e1]\n- textbox "Email" [ref=e2]\n- textbox "Password" [ref=e3]\n'
     '- textbox "Confirm Password" [ref=e4]', A.CREATE_FORM),
    ('- heading "Sign In" [ref=e1]\n- textbox "Email Address" [ref=e2]\n- textbox "Password" [ref=e3]\n'
     '- button "Sign In" [ref=e4]', A.SIGN_IN_FORM),
    ('- heading "Sign in" [ref=e1]\n- textbox "Email address" [ref=e2]\n- button "Next" [ref=e3]', A.EMAIL_FIRST),
    ('- heading "Check your email" [ref=e1]\n- text: We have sent you an email to verify your account.', A.VERIFY_EMAIL),
    ('- text: Please verify your email address before signing in.', A.VERIFY_EMAIL),
    ('- heading "Enter the code" [ref=e1]\n- text: We sent a verification code to j***@example.com\n'
     '- textbox "Verification Code" [ref=e2]', A.CODE_ENTRY),
    ('- heading "Apply" [ref=e1]\n- button "Sign in with Google" [ref=e2]\n- button "Apply Manually" [ref=e3]', A.CHOOSER),
    ('- heading "Senior Engineer" [ref=e1]\n- button "Apply" [ref=e2]', A.NONE),
    ('- text: First Name\n- textbox "First Name" [ref=e1]\n- textbox "Email" [ref=e2]\n- button "Sign in" [ref=e3]', A.NONE),
])
def test_each_account_state_is_recognised(snapshot, kind):
    assert A.read_state(snapshot).kind == kind


def test_a_verify_message_beside_a_sign_in_form_is_the_sign_in_form():
    """'Verify your password' hint text on a sign-in form is not a verification email."""
    snapshot = form() + "\n- text: Verify your email address and password below."
    assert A.read_state(snapshot).kind == A.SIGN_IN_FORM


# --- what to do ------------------------------------------------------------------------------------

def step(kind, google=False, **memory):
    return A.next_step(A.AccountState(kind, "said so", google), A.Memory(**memory))


@pytest.mark.parametrize("kind, action", [
    (A.SIGNED_IN, A.NOTHING), (A.NONE, A.NOTHING), (A.LOADING, A.WAIT),
    (A.LOCKED, A.FOR_OWNER), (A.VERIFY_EMAIL, A.FOR_OWNER), (A.WRONG_PASSWORD, A.FOR_OWNER),
    (A.CODE_ENTRY, A.ENTER_CODE), (A.CREATE_FORM, A.CREATE), (A.SIGN_IN_FORM, A.SIGN_IN),
    (A.EMAIL_FIRST, A.GIVE_EMAIL), (A.ACCOUNT_EXISTS, A.OPEN_SIGN_IN), (A.CHOOSER, A.NOTHING),
])
def test_every_state_has_one_next_step(kind, action):
    assert step(kind).action == action


@pytest.mark.parametrize("kind", [A.CREATE_FORM, A.SIGN_IN_FORM, A.CHOOSER, A.EMAIL_FIRST])
def test_google_comes_first_when_the_site_offers_it(kind):
    assert step(kind, google=True).action == A.GOOGLE
    assert step(kind, google=True, google_tried=True).action != A.GOOGLE
    assert step(kind, google=True, google_refused=True).action != A.GOOGLE


@pytest.mark.parametrize("kind", [A.LOCKED, A.VERIFY_EMAIL, A.WRONG_PASSWORD, A.CODE_ENTRY])
def test_what_the_site_says_is_answered_before_google_is_tried(kind):
    assert step(kind, google=True).action != A.GOOGLE


def test_nothing_is_tried_twice_in_one_run():
    assert step(A.CREATE_FORM, created=True).action == A.FOR_OWNER
    assert step(A.SIGN_IN_FORM, signed_in_tried=True).action == A.FOR_OWNER
    assert step(A.ACCOUNT_EXISTS, signed_in_tried=True).action == A.FOR_OWNER
    assert step(A.EMAIL_FIRST, email_given=True).action == A.NOTHING


def test_an_account_known_to_exist_is_signed_in_to_not_created_again():
    assert step(A.CREATE_FORM, account_exists=True).action == A.OPEN_SIGN_IN


def test_a_held_account_is_left_for_the_owner_with_the_reason():
    result = step(A.SIGN_IN_FORM, held="an account was already tried 2 times in the last 24 hours")
    assert result.action == A.FOR_OWNER and "2 times" in result.why


def test_the_verification_link_is_always_the_owners():
    assert "click its link" in step(A.VERIFY_EMAIL).why


def test_password_boxes_the_snapshot_does_not_name_are_counted_from_the_page():
    unlabelled = '- button "Create Account" [ref=e1]\n- textbox [ref=e2]\n- textbox [ref=e3]'
    assert A.read_state(unlabelled).kind == A.NONE
    assert A.read_state(unlabelled, password_boxes=2).kind == A.CREATE_FORM


@pytest.mark.parametrize("error", ["Error: Passwords do not match", "Please check the box", "This field is required"])
def test_a_form_the_site_says_is_unfinished_is_finished_first(error):
    state = A.read_state(form(error, passwords=("Password", "Verify New Password"), heading="Create Account"))
    assert state.kind == A.CREATE_FORM and state.form_error
    assert A.next_step(state, A.Memory(account_exists=True)).action == A.CREATE
    assert A.next_step(state, A.Memory(created=True)).action == A.CREATE
