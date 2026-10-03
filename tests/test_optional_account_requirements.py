"""An optional account exempts its own passwords, never another section's credentials.

All pages and profiles are synthetic. Browser cases read local HTML only; they do
not sign in, read email, or submit an application.
"""
from types import SimpleNamespace

import pytest

import account_state
import config
import page_agent
import safety


OPTIONAL = '\n'.join([
    '- heading "Create a Career Profile account (optional)" [level=2] [ref=e1]',
    '- textbox "Password" [ref=e2]',
    '- textbox "Confirm password" [ref=e3]',
])
REQUIRED = '\n'.join([
    '- heading "Sign In" [level=2] [ref=e4]',
    '- textbox "Password" [ref=e5]',
])


def password_fields(snapshot):
    # Import here so the baseline run also exercises existing auth and blockers.
    from field_requirements import account_password_fields
    return account_password_fields(snapshot)


@pytest.fixture
def agent(tmp_path, monkeypatch):
    assistant = SimpleNamespace(pending_attestations=lambda tab: [])
    job = SimpleNamespace(title="Engineer", company="Example", url="https://jobs.example.com/apply")
    result = page_agent.PageAgent(
        assistant, SimpleNamespace(), SimpleNamespace(auto_submit=False, ats_email=""),
        config.UserProfile(email="candidate@example.com"), SimpleNamespace(raw_text="synthetic"), job,
        resume_file=None,
    )
    result.unanswered_path = tmp_path / "unanswered.json"
    result._profile_answer_library = {}
    monkeypatch.setattr(safety, "captcha_visible", lambda page: False)
    return result


def blockers(agent, snapshot, question="Password", ref=None):
    agent.snapshot = lambda page: snapshot
    item = {"question": question, "required": True, "reason": "No answer available"}
    if ref is not None:
        item["ref"] = ref
    return agent.blockers(
        SimpleNamespace(), page_agent.PagePlan(for_owner=[item]),
        page_agent.parse_snapshot(snapshot), about_to_send=True,
    )


def test_optional_account_fields_have_their_own_section_evidence():
    fields = password_fields(OPTIONAL)
    assert [(field.ref, field.question, field.optional_account) for field in fields] == [
        ("e2", "Password", True), ("e3", "Confirm password", True),
    ]
    assert all("account" in field.section.lower() and "optional" in field.section.lower() for field in fields)


@pytest.mark.parametrize("later_level", [1, 2])
def test_a_sibling_or_higher_heading_ends_the_optional_section(later_level):
    snapshot = OPTIONAL + '\n' + REQUIRED.replace("level=2", f"level={later_level}")
    assert [(field.ref, field.optional_account) for field in password_fields(snapshot)] == [
        ("e2", True), ("e3", True), ("e5", False),
    ]


def test_a_nested_heading_stays_inside_the_optional_account():
    snapshot = OPTIONAL + '\n- heading "Password details" [level=3] [ref=e4]\n' \
        '- textbox "Retype password" [ref=e5]'
    assert all(field.optional_account for field in password_fields(snapshot))


def test_a_heading_without_levels_ends_the_preceding_optional_section():
    snapshot = OPTIONAL + '\n- heading "Sign In" [ref=e4]\n- textbox "Password" [ref=e5]'
    assert password_fields(snapshot)[-1].optional_account is False


def test_dedenting_out_of_the_optional_accounts_container_ends_its_scope():
    snapshot = '\n'.join([
        '- region "Account offer" [ref=e1]:',
        '  - heading "Create account (optional)" [level=2] [ref=e2]',
        '  - textbox "Password" [ref=e3]',
        '- textbox "Current password" [ref=e4]',
    ])
    assert [(field.ref, field.optional_account) for field in password_fields(snapshot)] == [
        ("e3", True), ("e4", False),
    ]


@pytest.mark.parametrize("role", ["group", "region", "form", "dialog"])
def test_named_optional_account_groups_scope_only_their_descendants(role):
    snapshot = '\n'.join([
        f'- {role} "Create account (optional)" [ref=e1]:',
        '  - textbox "Password" [ref=e2]',
        f'- {role} "Sign In" [ref=e3]:',
        '  - textbox "Password" [ref=e4]',
    ])
    assert [(field.ref, field.optional_account) for field in password_fields(snapshot)] == [
        ("e2", True), ("e4", False),
    ]


@pytest.mark.parametrize("heading", ["Additional details (optional)", "Create account"])
def test_both_account_and_optional_must_be_present_in_the_section(heading):
    snapshot = f'- heading "{heading}" [level=2] [ref=e1]\n- textbox "Password" [ref=e2]'
    assert password_fields(snapshot)[0].optional_account is False


def test_codes_do_not_qualify_for_the_optional_password_exception():
    snapshot = OPTIONAL + '\n- textbox "Passcode" [ref=e4]\n' \
        '- textbox "One-time code" [ref=e5]\n- textbox "Verification code" [ref=e6]'
    assert [field.ref for field in password_fields(snapshot)] == ["e2", "e3"]


def test_an_optional_account_alone_is_not_an_authentication_gate():
    assert account_state.read_state(OPTIONAL, password_boxes=2).kind == account_state.NONE


@pytest.mark.parametrize("snapshot", [OPTIONAL + '\n' + REQUIRED, REQUIRED + '\n' + OPTIONAL])
def test_required_password_remains_an_authentication_gate_beside_optional_account(snapshot):
    state = account_state.read_state(snapshot, password_boxes=3)
    assert state.kind == account_state.SIGN_IN_FORM
    assert state.password_boxes == 1


def test_an_unaccounted_dom_password_is_not_assumed_optional():
    state = account_state.read_state(OPTIONAL, password_boxes=3)
    assert state.kind in (account_state.SIGN_IN_FORM, account_state.CREATE_FORM)
    assert state.password_boxes == 1


def test_optional_account_does_not_hide_an_unrelated_verification_step():
    snapshot = OPTIONAL + '\n- heading "Verify your email" [level=2] [ref=e4]\n' \
        '- textbox "Verification code" [ref=e5]'
    assert account_state.read_state(snapshot, password_boxes=2).kind == account_state.CODE_ENTRY


def test_optional_password_is_not_left_for_owner_even_when_planner_calls_it_required(agent):
    assert blockers(agent, OPTIONAL) == []
    assert not agent.unanswered_path.exists()


@pytest.mark.parametrize("question", ["Passcode", "One-time code", "Verification code", "One-time password",
                                      "Password reset code", "Password verification code"])
def test_required_code_beside_optional_account_keeps_its_owner_blocker(agent, question):
    snapshot = OPTIONAL + f'\n- heading "Verify email" [level=2] [ref=e4]\n- textbox "{question}" [ref=e5]'
    assert any("needs your answer" in reason for reason in blockers(agent, snapshot, question))


@pytest.mark.parametrize("question", ["One-time code", "Password reset code", "Password verification code"])
def test_required_code_within_optional_account_is_not_a_password_exception(agent, question):
    snapshot = OPTIONAL + f'\n- textbox "{question}" [ref=e4]'
    assert blockers(agent, snapshot, question)


def test_an_out_of_section_required_password_is_still_left_for_owner(agent):
    snapshot = OPTIONAL + '\n- heading "Sign In" [level=2] [ref=e4]\n' \
        '- textbox "Current password" [ref=e5]'
    assert blockers(agent, snapshot, "Current password")


def test_duplicate_password_labels_require_unambiguous_field_identity(agent):
    assert blockers(agent, OPTIONAL + '\n' + REQUIRED)


def test_matching_reference_can_identify_optional_password_among_duplicates(agent):
    assert blockers(agent, OPTIONAL + '\n' + REQUIRED, ref="e2") == []


def test_matching_reference_keeps_required_password_among_duplicates(agent):
    assert blockers(agent, OPTIONAL + '\n' + REQUIRED, ref="e5")


def test_reference_cannot_override_a_mismatched_question(agent):
    snapshot = OPTIONAL + '\n- heading "Verify email" [level=2] [ref=e4]\n' \
        '- textbox "Passcode" [ref=e5]'
    assert blockers(agent, snapshot, "Passcode", ref="e2")


def test_unknown_password_field_is_not_exempted_by_an_optional_heading(agent):
    assert blockers(agent, OPTIONAL, "New account password")


def test_unreadable_snapshot_does_not_suppress_a_required_password(agent):
    def unavailable(page):
        raise RuntimeError("Page is unavailable")
    agent.snapshot = unavailable
    plan = page_agent.PagePlan(for_owner=[{"question": "Password", "required": True}])
    assert agent.blockers(SimpleNamespace(), plan, [], about_to_send=True)


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    result = context.new_page()
    yield result
    context.close()


def test_browser_optional_passwords_are_left_blank_without_blocking(agent, page):
    page.set_content('''<h1>Application</h1><label>First name<input value="Candidate"></label>
        <h2>Create account (optional)</h2><label>Password<input type="password"></label>
        <label>Confirm password<input type="password"></label><button>Submit application</button>''')
    snapshot = agent.snapshot(page)
    assert account_state.read_state(snapshot, password_boxes=2).kind == account_state.NONE
    assert blockers(agent, snapshot) == []
    assert page.locator('input[type="password"]').evaluate_all("els => els.every(el => el.value === '')")


def test_browser_required_sign_in_survives_sibling_optional_account_fieldset(agent, page):
    page.set_content('''<fieldset><legend>Create account (optional)</legend>
        <label>Password<input type="password"></label><label>Confirm password<input type="password"></label>
        </fieldset><fieldset><legend>Sign In</legend><label>Password<input type="password" required></label>
        </fieldset>''')
    snapshot = agent.snapshot(page)
    state = account_state.read_state(snapshot, password_boxes=3)
    assert state.kind == account_state.SIGN_IN_FORM
    assert state.password_boxes == 1
    assert blockers(agent, snapshot)


def test_browser_required_code_after_optional_account_retains_its_blocker(agent, page):
    page.set_content('''<h2>Create account (optional)</h2><label>Password<input type="password"></label>
        <h2>Verify email</h2><label>One-time code<input required></label>''')
    snapshot = agent.snapshot(page)
    assert account_state.read_state(snapshot, password_boxes=1).kind == account_state.CODE_ENTRY
    assert blockers(agent, snapshot, "One-time code")


@pytest.mark.parametrize("mixed", [False, True])
def test_browser_account_step_never_fills_optional_passwords(agent, page, monkeypatch, mixed):
    page.set_content('''<h2>Create account (optional)</h2><label>Password<input type="password"></label>
        <label>Confirm password<input type="password"></label>''' +
        ('''<h2>Sign In</h2><label>Current password<input type="password" required></label>''' if mixed else ""))
    attempts = []
    agent.assistant.handle_auth_gate = lambda *args, **kwargs: attempts.append(True)
    monkeypatch.setattr(agent, "_note_account_state", lambda *args: None)
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    assert agent.sign_in_step(page, controls) is False
    assert bool(agent.account_blocker) is mixed
    assert attempts == []
    assert page.locator('input[type="password"]').evaluate_all("els => els.every(el => el.value === '')")


def test_answered_optional_copy_does_not_satisfy_required_copy(agent):
    snapshot = OPTIONAL.replace('[ref=e2]', '[ref=e2]: [hidden]') + '\n' + REQUIRED
    assert blockers(agent, snapshot, ref="e5")
    assert blockers(agent, snapshot)


def test_section_names_do_not_leak_through_sibling_containers():
    from hypothesis import given, settings, strategies as st

    @settings(max_examples=30, deadline=None)
    @given(indent=st.integers(min_value=0, max_value=8), level=st.integers(min_value=1, max_value=6))
    def check(indent, level):
        prefix = " " * indent
        snapshot = '\n'.join(prefix + line for line in [
            '- region "Optional offer" [ref=e1]:',
            f'  - heading "Create account (optional)" [level={level}] [ref=e2]',
            '  - textbox "Password" [ref=e3]',
            '- region "Required sign in" [ref=e4]:',
            '  - textbox "Password" [ref=e5]',
        ])
        assert [(field.ref, field.optional_account) for field in password_fields(snapshot)] == [
            ("e3", True), ("e5", False),
        ]
    check()


@pytest.mark.parametrize("heading", ["Account (not optional)", "Account is not optional", "Account is non-optional"])
def test_not_optional_is_never_interpreted_as_optional(heading):
    fields = password_fields(f'- heading "{heading}" [level=2]\n- textbox "Password" [ref=e2]')
    assert not fields[0].optional_account


UNNAMED_PASSWORDS = '\n'.join([
    '- heading [level=2] [ref=e1]: Create account (optional)',
    '- generic [ref=e2]:',
    '  - generic [ref=e3]:',
    '    - generic [ref=e4]: Password',
    '    - generic [ref=e5]:',
    '      - textbox [ref=e6]',
    '  - generic [ref=e7]:',
    '    - generic [ref=e8]: Confirm password',
    '    - textbox [ref=e9]',
])


def test_unnamed_passwords_use_labels_inside_their_own_container(agent):
    fields = password_fields(UNNAMED_PASSWORDS)
    assert [(field.ref, field.question, field.optional_account) for field in fields] == [
        ("e6", "Password", True), ("e9", "Confirm password", True),
    ]
    assert account_state.read_state(UNNAMED_PASSWORDS, password_boxes=2).kind == account_state.NONE
    assert blockers(agent, UNNAMED_PASSWORDS) == []


def test_a_label_does_not_name_inputs_in_a_different_container():
    snapshot = UNNAMED_PASSWORDS + '\n- textbox [ref=e10]'
    assert [field.ref for field in password_fields(snapshot)] == ["e6", "e9"]
    assert account_state.read_state(snapshot, password_boxes=3).password_boxes == 1


def test_browser_unnamed_optional_passwords_do_not_stop_the_form(agent, page):
    page.set_content('''<h2>Create account (optional)</h2>
        <div><div>Password</div><div><input type="password"></div></div>
        <div><div>Confirm password</div><input type="password"></div>''')
    snapshot = agent.snapshot(page)
    assert account_state.read_state(snapshot, password_boxes=2).kind == account_state.NONE
    assert blockers(agent, snapshot, "Password") == []
    assert blockers(agent, snapshot, "Confirm password") == []


@pytest.mark.parametrize("boundary", ["iframe", "document", "dialog"])
def test_optional_scope_does_not_enter_an_independent_document(boundary):
    snapshot = OPTIONAL + f'\n- {boundary} [ref=e4]:\n  - textbox "Password" [ref=e5]'
    assert password_fields(snapshot)[-1].optional_account is False
    nested = '- group "Create account (optional)" [ref=e0]:\n' + '\n'.join(
        "  " + line for line in snapshot.splitlines())
    assert password_fields(nested)[-1].optional_account is False


def test_an_iframes_heading_does_not_end_the_parent_optional_section():
    snapshot = OPTIONAL + '\n- iframe [ref=e4]:\n  - heading "Sign In" [level=1] [ref=e5]\n' \
        '  - textbox "Password" [ref=e6]\n- textbox "Retype password" [ref=e7]'
    assert [(field.ref, field.optional_account) for field in password_fields(snapshot)][-2:] == [
        ("e6", False), ("e7", True),
    ]
