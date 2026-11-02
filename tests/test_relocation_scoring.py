"""A job elsewhere in the owner's country counts when the owner is open to relocating (f299).

2 November 2026: of 614 jobs posted in the last week on the owner's employers' lists, 600 were dropped for scoring under
60, among them "Engineer III - Network", "Data Center Network Operations Engineer" and "Network Cyber Security
Engineer": a wanted title with the wrong place scored 50 (a partial title match) and a place the owner does not live in
could never lift it. The owner's profile says open to relocation, and their applications are in Seattle and Las Vegas.
Place is a bonus, not a gate -- but the owner's own country still is one.
"""
import pytest

import job_discovery as jd

TITLES = ["Network Engineer", "Cloud Network Engineer", "Senior Network Engineer"]


def profile(relocate=True, country="United States"):
    return {"target_titles": TITLES, "locations": ["Remote", "Fairfax", "VA", "Arlington", "VA"],
            "country_code": jd._home_country(country), "open_to_relocation": relocate}


def score(title, location, **kw):
    return jd.calculate_match_score(title, location, profile(**kw))[0]


@pytest.mark.parametrize("title,place", [
    ("Engineer III - Network", "Phoenix, Arizona"),
    ("Data Center Network Operations Engineer", "Hybrid"),
    ("Network Cyber Security Engineer", "Ashburn, Virginia"),
    ("Engineer III - Network", ""),                                   # the system names no place
])
def test_a_wanted_title_in_another_place_counts_when_the_owner_will_relocate(title, place):
    assert score(title, place) >= 60


def test_the_same_job_does_not_count_when_the_owner_will_not_relocate():
    assert score("Engineer III - Network", "Phoenix, Arizona", relocate=False) < 60


def test_the_owners_own_place_still_ranks_above_another():
    assert score("Network Engineer", "Fairfax, VA") > score("Network Engineer", "Phoenix, Arizona")
    assert score("Network Engineer", "Remote - USA") > score("Network Engineer", "Phoenix, Arizona")


@pytest.mark.parametrize("title", ["Sr. IT Systems Administrator", "Senior Infrastructure Security Engineer",
                                   "Launch Pad Engineer, Fluid Systems (Falcon 9)"])
def test_a_loose_match_is_not_made_a_match_by_willingness_to_move(title):
    assert score(title, "Memphis, Tennessee") < 60


def test_another_country_is_still_out_of_reach_however_willing_the_owner_is():
    assert score("Network Engineer", "Bengaluru, India") == 0
    assert score("Cloud Engineer I ( Azure ) - IN", "IND-Work from Home-Remote") < 60


def test_the_profile_the_scan_uses_carries_the_owners_willingness(monkeypatch):
    import config
    monkeypatch.setattr(config, "get_user_profile", lambda: type("P", (), {
        "target_titles": TITLES, "locations": ["Remote"], "requires_visa_sponsorship": True,
        "country": "United States", "open_to_relocation": True})())
    assert jd.load_profile()["open_to_relocation"] is True
