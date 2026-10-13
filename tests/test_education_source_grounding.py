"""Generic profile degree levels must not endorse unsupported extracted subtypes."""
from types import SimpleNamespace
from repeated_entries import with_profile

def test_generic_level_uses_matching_original_resume_degree():
    history = {"education": [{"school":"JNTU Hyderabad", "degree":"Bachelor of Science", "field":"Electronics"}]}
    profile = SimpleNamespace(education=(("Bachelor's","Electrical Engineering","JNTU Hyderabad","2019"),), education_dates=())
    source = "Bachelor of Technology (BTech), Electrical Engineering | JNTU Hyderabad | April 2019"
    result = with_profile(history, profile, source)
    assert result["education"][0]["degree"] == "Bachelor of Technology"
    assert result["education"][0]["field"] == "Electrical Engineering"
    assert history["education"][0]["degree"] == "Bachelor of Science"

def test_explicit_owner_degree_outranks_resume():
    history = {"education": [{"school":"Example University", "degree":"Bachelor of Science"}]}
    profile = SimpleNamespace(education=(("Bachelor of Arts","Design","Example University","2020"),),education_dates=())
    result = with_profile(history, profile, "Bachelor of Science | Example University")
    assert result["education"][0]["degree"] == "Bachelor of Arts"

def test_unrelated_school_cannot_change_degree():
    history = {"education": [{"school":"Example University", "degree":"Bachelor of Science"}]}
    profile = SimpleNamespace(education=(("Bachelor's","Design","Example University","2020"),),education_dates=())
    assert with_profile(history, profile, "Bachelor of Technology | Other School")["education"][0]["degree"] == "Bachelor of Science"
