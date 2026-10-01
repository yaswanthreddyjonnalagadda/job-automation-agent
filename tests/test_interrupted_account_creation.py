"""Create Account pressed and the result never seen: the next run asks the owner, it does not make it again.

Review of 1 October: "for account creation and submission especially, an interrupted action should become
'outcome unknown -- check first'". The attempt is written before the press; reading the next page closes it;
the owner's Continue closes it too. No browser, no network: login_guard's file goes to the test's own folder.
"""
import login_guard

HOST, EMAIL = "acme.wd1.myworkdayjobs.com", "owner@example.com"


def test_a_press_whose_result_was_not_seen_holds_the_next_creation():
    login_guard.record_account_attempt(HOST, EMAIL)              # pressed; the run ended here
    why = login_guard.may_create_account(HOST, EMAIL)
    assert why and "result was not seen" in why and "press Continue" in why


def test_reading_the_page_after_the_press_closes_it():
    login_guard.record_account_attempt(HOST, EMAIL)
    login_guard.account_creation_seen(HOST, EMAIL)
    assert login_guard.may_create_account(HOST, EMAIL) is None   # one attempt so far: under the daily limit


def test_the_owners_continue_closes_it():
    login_guard.record_account_attempt(HOST, EMAIL)
    login_guard.owner_resumed(HOST)
    assert login_guard.may_create_account(HOST, EMAIL) is None


def test_the_daily_limit_still_counts_seen_attempts():
    for _ in range(login_guard.MAX_ACCOUNT_CREATIONS):
        login_guard.record_account_attempt(HOST, EMAIL)
        login_guard.account_creation_seen(HOST, EMAIL)
    assert "already tried" in (login_guard.may_create_account(HOST, EMAIL) or "")


def test_a_new_unverified_account_holds_every_sign_in_until_the_owner_looks():
    """Waystar, 1 October: created, then signed in at once and refused -- a refusal that counts towards a lock."""
    login_guard.hold_for_verification(HOST, EMAIL)
    why = login_guard.may_sign_in(HOST, EMAIL)
    assert why and "verification email" in why and "press Continue" in why
    login_guard.owner_resumed(HOST)
    assert login_guard.may_sign_in(HOST, EMAIL) is None


def test_with_the_account_on_record_the_next_run_signs_in_instead_of_creating():
    import account_state as a
    form = a.AccountState(a.CREATE_FORM, "Create Account")
    assert a.next_step(form, a.Memory(account_exists=True)).action == a.OPEN_SIGN_IN
    assert a.next_step(form, a.Memory()).action == a.CREATE
