"""Failed authentication can still replace the page that owns field refs."""
from test_account_step import Assistant, CREATE, SIGN_IN, agent, browser, page, serve, step


def test_failed_registration_login_transition_is_read_again(page, tmp_path):
    serve(page, CREATE)
    assistant = Assistant()
    calls = []

    def failed_gate(tab, email):
        calls.append('create')
        tab.set_content(SIGN_IN + '<div role="alert">Wrong email address or password</div>')
        assistant._last_login_rejected = True
        return False

    assistant.handle_auth_gate = failed_gate
    assistant.verify_account_by_email_link = lambda tab, **kw: calls.append('verify') or False
    assistant.recover_rejected_sign_in = lambda tab, email: calls.append('recover') or False
    reader = agent(assistant, tmp_path)
    # True means the main loop discards its create-form controls and rereads.
    # It must not mean the account was successfully authenticated.
    assert step(page, reader) is True
    assert not reader.account_blocker
    assert step(page, reader) is False
    assert reader.account_blocker
    assert calls == ['create', 'verify', 'recover']


def test_unchanged_failed_create_retains_precise_blocker(page, tmp_path):
    serve(page, CREATE)
    reader = agent(Assistant(gate=False), tmp_path)
    assert step(page, reader) is False
    assert 'Create Account was not completed' in reader.account_blocker


def test_failed_signin_replaced_with_code_prompt_is_read_again(page, tmp_path):
    serve(page, SIGN_IN)
    assistant = Assistant()

    def gate(tab, email):
        tab.set_content('<h2>Enter verification code</h2><label>Verification code<input></label><button>Verify</button>')
        return False

    assistant.handle_auth_gate = gate
    reader = agent(assistant, tmp_path)
    assert step(page, reader) is True
    assert not reader.account_blocker
