"""The claim checker must not call a true claim invented because the PDF's text came out with a space (26 October 2026).

A PDF's text is extracted with its line breaks and kerning: "Palo Alto PA-5200" comes out as "PA -5200", "multi-cloud" as
"multi -cloud", "EVPN-VXLAN" as "EVPN -\nVXLAN". unsupported_claims compared the writer's correct "PA-5200" with that, found
it missing, and rejected the tailored resume; every writer repeats the model number, so all were rejected and the generic
resume went out (Roblox). A hyphenated term is the same term however the extraction spaced the hyphen; a term the resume
never had is still caught.
"""
import pytest

import safety

TERMS = ["PA-5200", "EVPN-VXLAN", "ANS-C01", "AZ-700", "SCS-C03", "MP-BGP"]
SPACINGS = [" -", "- ", " - ", " -\n", "-\n"]


@pytest.mark.parametrize("term", TERMS)
@pytest.mark.parametrize("spacing", SPACINGS)
def test_a_hyphenated_term_is_found_however_the_extraction_spaced_its_hyphen(term, spacing):
    left, right = term.split("-", 1)
    extracted = f"Experience with {left}{spacing}{right} in production."
    assert safety.unsupported_claims(extracted, f"Operated {term} in production.") == []


def test_a_term_the_resume_never_had_is_still_caught():
    source = "Palo Alto PA -5200 firewalls and Cisco Firepower."
    assert "PA-3400" in safety.unsupported_claims(source, "Palo Alto PA-3400 firewalls.")
    assert "CCIE" in safety.unsupported_claims(source, "Cisco CCIE certified.")
    assert "99.9%" in safety.unsupported_claims(source, "Raised uptime to 99.9%.")


def test_a_date_range_in_the_source_does_not_make_up_a_hyphenated_term():
    source = "Freddie Mac | February 2023 - January 2025 | Network Engineer"
    assert safety.unsupported_claims(source, "Network Engineer, February 2023 - January 2025.") == []
    assert "AZ-700" in safety.unsupported_claims(source, "Certified AZ-700.")
