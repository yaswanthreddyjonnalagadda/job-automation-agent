"""Tests for multi-select dropdown support for identity fields."""
import pytest
from config import UserProfile
import profile_setup
import web_setup
import concept_matcher


def test_user_profile_identities_helper():
    # 1. Single string
    p1 = UserProfile(ethnicity="South Asian", gender_identity="Man")
    assert p1.identities("ethnicity") == ["South Asian"]
    assert p1.identities("gender_identity") == ["Man"]

    # 2. Comma / semicolon separated string
    p2 = UserProfile(ethnicity="South Asian, Asian", pronouns="He/Him; They/Them")
    assert p2.identities("ethnicity") == ["South Asian", "Asian"]
    assert p2.identities("pronouns") == ["He/Him", "They/Them"]

    # 3. Tuple / List
    p3 = UserProfile(ethnicity=("Asian", "South Asian"), pronouns=["They/Them"])
    assert p3.identities("ethnicity") == ["Asian", "South Asian"]
    assert p3.identities("pronouns") == ["They/Them"]

    # 4. Empty / None
    p4 = UserProfile(ethnicity="", pronouns=None)
    assert p4.identities("ethnicity") == []
    assert p4.identities("pronouns") == []


def test_profile_setup_fields_and_choices():
    assert "ETHNICITY_CHOICES" in dir(profile_setup)
    assert "Asian" in profile_setup.ETHNICITY_CHOICES
    assert "South Asian" in profile_setup.ETHNICITY_CHOICES

    assert "GENDER_IDENTITY_CHOICES" in dir(profile_setup)
    assert "Man" in profile_setup.GENDER_IDENTITY_CHOICES
    assert "Non-binary" in profile_setup.GENDER_IDENTITY_CHOICES

    assert "PRONOUN_CHOICES" in dir(profile_setup)
    assert "He/Him" in profile_setup.PRONOUN_CHOICES

    # Check fields in Voluntary disclosures section
    fields = profile_setup.FIELDS
    assert fields["ethnicity"].kind == "multi_choice"
    assert fields["gender_identity"].kind == "multi_choice"
    assert fields["pronouns"].kind == "multi_choice"
    assert fields["sexual_orientation"].kind == "multi_choice"


def test_values_from_form_multi_choice():
    # Simulate form with getlist (like Flask request.form)
    class FakeForm(dict):
        def getlist(self, key):
            val = self.get(key)
            if isinstance(val, list):
                return val
            return [val] if val else []

    form = FakeForm({
        "full_name": "Test Candidate",
        "email": "test@example.com",
        "ethnicity": ["Asian", "South Asian"],
        "pronouns": ["He/Him"],
        "gender_identity": "Man",
    })

    values = profile_setup.values_from_form(form)
    assert values["ethnicity"] == ("Asian", "South Asian")
    assert values["pronouns"] == ("He/Him",)
    assert values["gender_identity"] == ("Man",)


def test_save_profile_round_trip(tmp_path):
    pfile = tmp_path / "profile.json"
    values = {
        "full_name": "Jane Doe",
        "email": "jane.doe@example.com",
        "ethnicity": ("Asian", "South Asian"),
        "gender_identity": ("Woman", "Non-binary"),
    }
    saved_path = profile_setup.save_profile(values, path=pfile)
    assert saved_path == pfile

    import json
    data = json.loads(pfile.read_text(encoding="utf-8"))
    assert data["ethnicity"] == ["Asian", "South Asian"]
    assert data["gender_identity"] == ["Woman", "Non-binary"]


def test_web_setup_profile_page_multi_choice_rendering():
    values = {
        "full_name": "Test User",
        "email": "test@example.com",
        "ethnicity": ["South Asian", "Asian"],
        "gender_identity": "Man",
    }
    # Test normalization in _profile_page helper
    shown = {}
    for name, value in values.items():
        field = profile_setup.FIELDS.get(name)
        if field and field.kind == "multi_choice":
            if isinstance(value, (list, tuple, set)):
                value = [str(x).strip() for x in value if str(x).strip()]
            else:
                value = [str(value).strip()] if value else []
        shown[name] = value

    assert shown["ethnicity"] == ["South Asian", "Asian"]
    assert shown["gender_identity"] == ["Man"]


def test_concept_matcher_best_option_match_multivalue():
    options = ["White", "Black or African American", "Asian", "Hispanic or Latino"]
    # Single value matches
    assert concept_matcher.best_option_match("Asian", options) == "Asian"

    # Multi-value fallback: "South Asian" not in list, but "Asian" is
    assert concept_matcher.best_option_match("South Asian; Asian", options) == "Asian"


def test_concept_matcher_resolve_profile_value_ethnicity():
    # Profile with tuple of ethnicities
    profile = UserProfile(ethnicity=("South Asian", "Asian"))

    # Single dropdown where only "Asian" is offered
    ans, src = concept_matcher.resolve_profile_value(
        concept="ETHNICITY_RACE",
        profile=profile,
        options=["White", "Asian", "Two or More Races"],
    )
    assert ans == "Asian"
    assert src == "profile.ethnicity"

    # Multi-select options question
    ans_multi, src_multi = concept_matcher.resolve_profile_value(
        concept="ETHNICITY_RACE",
        profile=profile,
        options=["White", "Asian", "South Asian"],
        several=True,
    )
    # Both offered should be picked
    assert "South Asian" in ans_multi
    assert "Asian" in ans_multi


def test_concept_matcher_resolve_profile_value_gender_identity_and_pronouns():
    profile = UserProfile(gender_identity=("Man", "Non-binary"), pronouns=("He/Him", "They/Them"))

    ans_gi, _ = concept_matcher.resolve_profile_value(
        concept="GENDER_IDENTITY",
        profile=profile,
        options=["Man", "Woman", "Non-binary", "Decline to state"],
        several=True,
    )
    assert "Man" in ans_gi
    assert "Non-binary" in ans_gi

    ans_pro, _ = concept_matcher.resolve_profile_value(
        concept="PRONOUNS",
        profile=profile,
        options=["He/Him", "She/Her", "They/Them"],
        several=True,
    )
    assert "He/Him" in ans_pro
    assert "They/Them" in ans_pro

