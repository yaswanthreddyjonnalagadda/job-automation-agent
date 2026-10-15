from types import SimpleNamespace
from concept_matcher import resolve_profile_value

def test_any_certification_uses_explicit_profile_list():
    assert resolve_profile_value(concept="CERTIFICATIONS",profile=SimpleNamespace(certifications=("CCNA",)),options=["Yes","No"],question="Do you have any certifications? *") == ("Yes", "profile.certifications")

def test_empty_default_is_not_evidence_of_no_certifications():
    value, _ = resolve_profile_value(concept="CERTIFICATIONS",profile=SimpleNamespace(certifications=()),options=["Yes","No"],question="Do you have any certifications?")
    assert value == ""

def test_any_certification_does_not_prove_specific_credential():
    value, _ = resolve_profile_value(concept="CERTIFICATIONS",profile=SimpleNamespace(certifications=("CCNA",)),options=["Yes","No"],question="Do you have CCNP certification?")
    assert value != "Yes"
