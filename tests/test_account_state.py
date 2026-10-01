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
    (A.LOCKED, A.FOR_OWNER), (A.VERIFY_EMAIL, A.VERIFY_BY_LINK), (A.WRONG_PASSWORD, A.FOR_OWNER),
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


def test_the_account_is_created_first_whatever_the_record_says():
    """The owner's order (1 October): create first; an existing account is said so by the site (ACCOUNT_EXISTS), and
    only then does the agent sign in."""
    assert step(A.CREATE_FORM, account_exists=True).action == A.CREATE
    assert step(A.ACCOUNT_EXISTS).action == A.OPEN_SIGN_IN


def test_a_sign_in_page_that_offers_create_account_creates_first():
    page = A.AccountState(A.SIGN_IN_FORM, "Sign In", can_create=True)
    assert A.next_step(page, A.Memory()).action == A.OPEN_CREATE
    assert A.next_step(page, A.Memory(created=True)).action == A.SIGN_IN          # created this run: now sign in
    assert A.next_step(A.AccountState(A.SIGN_IN_FORM, "Sign In"), A.Memory()).action == A.SIGN_IN   # no create offered


def test_a_sign_in_page_says_whether_it_offers_create_account():
    snapshot = ('- heading "Sign In" [level=2] [ref=e1]\n- textbox "Password" [ref=e2]\n'
                '- button "Create Account" [ref=e3]')
    assert A.read_state(snapshot).can_create is True


def test_a_held_account_is_left_for_the_owner_with_the_reason():
    result = step(A.SIGN_IN_FORM, held="an account was already tried 2 times in the last 24 hours")
    assert result.action == A.FOR_OWNER and "2 times" in result.why


def test_the_verification_link_is_opened_once_from_the_mail_then_it_is_the_owners():
    """The owner's decision of 30 September 2026: the agent opens the link the site emailed; once per site per run."""
    assert step(A.VERIFY_EMAIL).action == A.VERIFY_BY_LINK
    assert "click its link" in step(A.VERIFY_EMAIL, verify_tried=True).why


def test_a_verify_your_account_alert_on_a_sign_in_form_is_a_verification_step():
    """Ciena on Workday, 30 September: the alert sits on the Sign In form, above an email and a password box."""
    snapshot = "\n".join([
        '- heading "Sign In" [level=3] [ref=e1]',
        '- alert [ref=e2]:',
        '  - paragraph [ref=e3]: Verify your account before you sign in or request a verification email.',
        '- textbox "Email Address" [ref=e4]',
        '- textbox "Password" [ref=e5]',
        '- button "Sign In" [ref=e6]',
    ])
    state = A.read_state(snapshot)
    assert state.kind == A.VERIFY_EMAIL
    assert A.next_step(state, A.Memory(refused_before=True)).action == A.VERIFY_BY_LINK


@pytest.mark.parametrize("wording", ["Login with Google", "Log in with Google", "Sign-in using Google",
                                     "Sign up with Google", "Continue with Google", "Google Sign-In"])
def test_google_sign_in_is_seen_however_the_site_words_it(wording):
    assert A.GOOGLE_SIGN_IN.search(wording)


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


# --- a refused password on an account the site knows (CLAUDE.md §5) --------------------------------------------------

@pytest.mark.parametrize("known", [{"account_exists": True}, {"email_given": True}])
def test_a_refused_password_on_a_known_account_is_reset_not_handed_over(known):
    """Mutual of Enumclaw (iCIMS), 29 September: the site took the email, asked for the password and refused it; the
    run handed the sign-in page to the owner although the owner's rule says reset it to the ATS password with the
    emailed code."""
    assert step(A.WRONG_PASSWORD, **known).action == A.RESET_PASSWORD


def test_a_refused_password_is_reset_once_and_then_it_is_the_owners():
    result = step(A.WRONG_PASSWORD, account_exists=True, reset_tried=True)
    assert result.action == A.FOR_OWNER and "refused" in result.why


def test_a_refused_password_on_an_account_nobody_knows_is_the_owners():
    """'Wrong email or password' alone does not say the account exists: no reset is asked for."""
    assert step(A.WRONG_PASSWORD).action == A.FOR_OWNER
    assert step(A.WRONG_PASSWORD, signed_in_tried=True).action == A.FOR_OWNER


def test_a_locked_account_is_never_reset_by_the_agent():
    assert step(A.LOCKED, account_exists=True, email_given=True).action == A.FOR_OWNER


# --- a refusal remembered from an earlier run (Mutual of Enumclaw, iCIMS, 29 September, 18:54) ------------------------

def test_a_password_refused_in_an_earlier_run_is_reset_not_retyped_or_handed_over():
    """The earlier run's refusal was kept (login_guard) and the next run stopped at the password page with 'sign-in
    paused' -- neither retyping the password (right) nor resetting it (the owner's rule)."""
    assert step(A.SIGN_IN_FORM, refused_before=True, email_given=True).action == A.RESET_PASSWORD
    assert step(A.SIGN_IN_FORM, refused_before=True, account_exists=True).action == A.RESET_PASSWORD


def test_a_password_refused_before_is_never_retyped():
    for memory in ({"reset_tried": True, "email_given": True}, {}):
        result = step(A.SIGN_IN_FORM, refused_before=True, **memory)
        assert result.action == A.FOR_OWNER and "refused" in result.why


def test_google_still_comes_first_on_a_sign_in_page_after_a_refusal():
    assert step(A.SIGN_IN_FORM, google=True, refused_before=True, email_given=True).action == A.GOOGLE



def test_a_box_for_the_code_makes_it_a_code_step_whatever_the_page_says():
    """Code or link is read from the page: a box for the emailed code means the code, even beside 'not verified'."""
    snapshot = "\n".join([
        '- heading "Sign In" [ref=e1]',
        '- alert: Your account is not verified. Enter the verification code we sent to your email.',
        '- textbox "Email" [ref=e2]',
        '- textbox "Password" [ref=e3]',
        '- textbox "Verification code" [ref=e4]',
        '- button "Sign In" [ref=e5]',
    ])
    assert A.read_state(snapshot).kind == A.CODE_ENTRY
