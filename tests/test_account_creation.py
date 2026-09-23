from types import SimpleNamespace

from browser_automation import JobApplicationAssistant


def test_account_creation_ignores_null_alert_text():
    assert JobApplicationAssistant._nonempty_texts([None, "", "  Required field  "]) == ["Required field"]


def test_account_creation_requires_matching_password_pair():
    assert JobApplicationAssistant._password_pair_matches(["example-password", "example-password"], "example-password")
    assert not JobApplicationAssistant._password_pair_matches(["example-password", ""], "example-password")


def test_account_creation_recognizes_visible_validation_errors():
    assert JobApplicationAssistant._has_account_form_validation_error(["Passwords do not match"])
    assert JobApplicationAssistant._has_account_form_validation_error(["Please check the box to continue"])
    assert not JobApplicationAssistant._has_account_form_validation_error(["Candidate Privacy Notice"])


def test_account_creation_only_accepts_explicit_privacy_acknowledgment():
    label = "Yes, I understand and acknowledge the terms and conditions."
    privacy_context = "Candidate Privacy Notice and consent to the use of cookies."

    assert not JobApplicationAssistant._is_account_consent(label, privacy_context)
    assert not JobApplicationAssistant._is_account_consent(label, "Terms and conditions only")
    assert JobApplicationAssistant._is_account_consent(
        "I agree to the Candidate Privacy Notice.", privacy_context
    )
    assert not JobApplicationAssistant._is_account_consent(
        "I certify the information is true and complete.", privacy_context
    )


def test_account_terms_follow_the_owner_attestation_authorization():
    terms = "Yes, I understand and acknowledge the terms and conditions."

    assert JobApplicationAssistant._account_acknowledgment_is_authorized(
        terms, SimpleNamespace(sign_attestations=True)
    )
    assert not JobApplicationAssistant._account_acknowledgment_is_authorized(
        terms, SimpleNamespace(sign_attestations=False)
    )