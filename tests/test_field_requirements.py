"""A field's requirement is decided from its own section and label, never from the whole page.

Synthetic snapshots only: no browser, no profile, no network.
"""
import pytest

import account_state
import field_requirements as fr

OPTIONAL_ACCOUNT = """- heading "Self Identification" [level=2] [ref=e1]
- combobox "Gender*" [ref=e2]
- heading "Create a Career Profile account (optional)" [level=2] [ref=e3]
- textbox "Email" [ref=e4]
- textbox "Password*" [ref=e5]
- textbox "Confirm password*" [ref=e6]
"""
SIGN_IN = """- heading "Sign In" [level=2] [ref=e7]
- textbox "Password" [ref=e8]
- button "Sign In" [ref=e9]
"""


def test_passwords_inside_an_optional_section_are_conditional_not_required():
    statuses = {f.question: r.status for f, r in fr.account_password_fields(OPTIONAL_ACCOUNT)}
    assert statuses == {"Password*": fr.CONDITIONAL, "Confirm password*": fr.CONDITIONAL}
    assert fr.secret_boxes_in_use(OPTIONAL_ACCOUNT) == 0
    assert account_state.read_state(OPTIONAL_ACCOUNT).kind == account_state.NONE


def test_a_required_password_in_another_section_is_never_let_off():
    page = OPTIONAL_ACCOUNT + SIGN_IN
    assert fr.secret_boxes_in_use(page) == 1
    assert fr.optional_section_of(page) is None
    assert fr.requirement_for(page, "Password", marked_required=True).status == fr.REQUIRED
    assert account_state.read_state(page).kind == account_state.SIGN_IN_FORM


@pytest.mark.parametrize("sections", [(), ("Your details",), ("Apply", "Contact information")])
@pytest.mark.parametrize("question, expected", [
    ("First name*", fr.REQUIRED),
    ("Middle name (optional)", fr.OPTIONAL),
    ("Is sponsorship required?", fr.UNKNOWN),
    ("Phone (required)", fr.REQUIRED),
])
def test_a_fields_own_label_decides_outside_optional_sections(sections, question, expected):
    assert fr.requirement(question, sections).status == expected


def test_a_section_closes_at_the_next_heading_of_its_level():
    page = """- heading "Account (optional)" [level=2] [ref=a]
- heading "Password rules" [level=3] [ref=b]
- textbox "Password*" [ref=c]
- heading "Your information" [level=2] [ref=d]
- textbox "Last name*" [ref=e]
"""
    by_ref = {f.ref: f for f in fr.fields(page)}
    assert by_ref["c"].sections == ("Account (optional)", "Password rules")
    assert by_ref["e"].sections == ("Your information",)
    assert fr.requirement_for(page, "Last name*").status == fr.REQUIRED


@pytest.mark.parametrize("status, answered, stops", [
    (fr.REQUIRED, False, True), (fr.REQUIRED, True, False), (fr.OPTIONAL, False, False),
    (fr.CONDITIONAL, False, False), (fr.UNKNOWN, False, False),
])
def test_only_a_blank_required_field_needs_the_owner(status, answered, stops):
    assert fr.needs_owner(fr.Requirement(status, "x"), answered) is stops
