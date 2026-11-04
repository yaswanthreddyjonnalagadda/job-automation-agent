"""Country reading goes deeper: cities, state abbreviations, and second homes (f318).

'Bare 'Palo Alto' and 'Dublin' named no country, so they stayed unknown; a
two-letter code that is both a US state and a country ('CA', 'IN') always read
as the country, so 'Remote - CA' looked Canadian; and an owner who can work in
two countries still had one. Cities now resolve through reference/cities.json,
two-letter codes read as the US state the boards mean by them, and the profile
carries every accepted country. Stored countries are recomputed once with the
better reading.
"""
import json
import sqlite3

import geo_reference
import job_discovery as jd


def profile(**over):
    base = {"target_titles": ["Network Engineer"], "avoid_title_words": [],
            "locations": ["Remote"], "requires_visa_sponsorship": False,
            "country_code": "US", "open_to_relocation": False}
    base.update(over)
    return base


def test_bare_cities_resolve_to_their_country():
    assert geo_reference.location_countries("Palo Alto") == frozenset({"US"})
    assert geo_reference.location_countries("Dublin") == frozenset({"IE"})
    assert geo_reference.location_countries("Vancouver, BC") == frozenset({"CA"})
    assert geo_reference.location_countries("New York City") == frozenset({"US"})
    assert geo_reference.location_countries("Bengaluru") == frozenset({"IN"})
    assert geo_reference.location_countries("Nowhereville") == frozenset()


def test_two_letter_codes_read_as_the_state_the_boards_mean():
    assert geo_reference.location_countries("Remote - CA") == frozenset({"US"})
    assert geo_reference.primary_country("Remote - CA", "US") == "US"
    assert geo_reference.location_countries("Foster City, CA") == frozenset({"US"})
    assert geo_reference.location_countries("Austin, TX") == frozenset({"US"})
    assert geo_reference.location_countries("London, UK") == frozenset({"GB"})
    assert geo_reference.location_countries("Georgia") == frozenset({"GE"})
    assert geo_reference.location_countries("London, GB") == frozenset({"GB"})


def test_a_second_home_country_counts(tmp_path, monkeypatch):
    p = profile(accepted_country_codes=["US", "CA"])
    SHOULD = [
        ("Network Engineer", "Toronto, Canada", True),  # second home: in reach
        ("Network Engineer", "Bengaluru, India", False),
        ("Network Engineer", "Austin, TX", True),
    ]
    for title, location, wanted in SHOULD:
        score, _ = jd.calculate_match_score(title, location, p)
        assert (score > 0) == wanted, (title, location, score)


def test_postings_demanding_another_country_are_gated():
    import matching_engine as me
    us = dict(profile(), accepted_country_codes=["US"])
    out, reason = me.check_hard_gates("You must be based in India to apply.", us, "Remote")
    assert out is False and "India" in reason
    out, _ = me.check_hard_gates("Must be authorized to work in the United States.", us, "Remote")
    assert out is True
    out, _ = me.check_hard_gates("This role is based in London or New York.", us, "Remote")
    assert out is True
    both = dict(profile(), accepted_country_codes=["US", "CA"])
    out, _ = me.check_hard_gates("Candidates must reside in Canada.", both, "Remote")
    assert out is True
    out, _ = me.check_hard_gates("Candidates must reside in Canada.", us, "Remote")
    assert out is False


def test_load_profile_reads_every_accepted_country(monkeypatch):
    import config
    import job_discovery as jd
    monkeypatch.setattr(config, "get_user_profile",
                        lambda: config.UserProfile(country="United States",
                                                   available_work_locations="Toronto, Austin"))
    assert jd.load_profile()["accepted_country_codes"] == ["US", "CA"]


def test_stored_countries_are_recomputed_once_with_the_better_reading(tmp_path):
    db = tmp_path / "d.db"
    conn = sqlite3.connect(db)
    jd.ensure_discovery_schema(conn)
    conn.execute("INSERT INTO discovered_jobs (dedup_key, company_name, company_token, ats_type, job_title,"
                 " location, is_remote, url, match_score, discovered_at, updated_at, country_code)"
                 " VALUES ('k', 'Acme', 'acme', 'greenhouse', 'Network Engineer', 'Palo Alto', 0,"
                 " 'https://x.example/1', 90, 't', 't', ''),"
                 " ('k2', 'Acme', 'acme', 'greenhouse', 'Network Engineer', 'Austin, TX', 0,"
                 " 'https://x.example/2', 90, 't', 't', 'US')")
    conn.execute("DELETE FROM board_state WHERE identity = '__country_parser__'")
    conn.commit()
    jd.ensure_discovery_schema(conn)
    rows = dict(conn.execute("SELECT dedup_key, country_code FROM discovered_jobs").fetchall())
    version = conn.execute("SELECT jobs_seen FROM board_state WHERE identity = '__country_parser__'").fetchone()[0]
    conn.close()
    assert rows == {"k": "US", "k2": "US"} and version == jd.COUNTRY_PARSER_VERSION
