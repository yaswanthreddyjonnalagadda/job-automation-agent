"""The suggested-jobs feed stopped showing anything new (26 October 2026).

Twelve companies were configured, 31 jobs sat at exactly 70 (a generic tech word + "remote" + "senior"), roles that are
not engineering ("Security Sales Engineer", "Sr Manager, Infrastructure Sales") scored 70 too, and the dashboard shows the
top twelve in a fixed order: refreshing re-found the same few. A job that merely contains a tech word must rank below one
whose title is a role the owner targets; sales and management roles are not suggestions; and the list of boards is broad
enough that a refresh can find something new.
"""
import json
from pathlib import Path

import pytest

from job_discovery import calculate_match_score

PROFILE = {"target_titles": ["Network Engineer", "Cloud Network Engineer", "Senior Network Engineer"],
           "locations": ["Remote", "Fairfax"], "requires_visa_sponsorship": False}


@pytest.mark.parametrize("title", [
    "Senior Security Sales Engineer",
    "Sr Manager, Infrastructure Sales",
    "Director of Network Engineering",
    "Engineering Manager, Infrastructure",
    "Head of Platform Security",
    "Customer Success Manager, Networking",
])
def test_sales_and_management_roles_are_not_suggested(title):
    assert calculate_match_score(title, "Remote - USA", PROFILE)[0] == 0


def test_a_title_the_owner_targets_outranks_a_generic_tech_word():
    real, _ = calculate_match_score("Senior Network Engineer", "Remote", PROFILE)
    generic, _ = calculate_match_score("Staff Software Engineer, Developer Infrastructure", "Remote - USA", PROFILE)
    assert real >= 90 and generic < real and generic < 70       # the 31 identical 70s


def test_a_tech_word_in_a_title_that_is_not_an_engineering_role_scores_nothing():
    assert calculate_match_score("Infrastructure Procurement Coordinator", "Remote", PROFILE)[0] == 0


def test_the_target_boards_are_broad_and_each_names_a_supported_ats():
    boards = json.loads((Path(__file__).resolve().parent.parent / "data" / "target_companies.json")
                        .read_text(encoding="utf-8"))
    assert len(boards) >= 30
    assert {b["ats"] for b in boards} <= {"greenhouse", "lever"}
    assert len({b["token"] for b in boards}) == len(boards)      # no board listed twice


def test_a_scan_expires_jobs_the_board_no_longer_lists_or_that_no_longer_qualify(tmp_path, monkeypatch):
    """Old rows kept their old score and stayed in the feed: a closed posting, or one the new scoring rules out, was shown
    on every refresh. Only 'discovered' rows expire; queued, submitted and dismissed ones are the owner's."""
    import sqlite3

    import job_discovery as jd
    db = tmp_path / "d.db"
    conn = sqlite3.connect(db)
    jd.ensure_discovery_schema(conn)
    conn.commit()
    conn.close()
    board = tmp_path / "boards.json"
    board.write_text(json.dumps([{"company": "Acme", "token": "acme", "ats": "greenhouse"}]), encoding="utf-8")
    engine = jd.JobDiscoveryEngine(db_path=db, config_path=board)
    engine.profile = PROFILE

    def job(title, url):
        return {"company_name": "Acme", "company_token": "acme", "ats_type": "greenhouse", "job_title": title,
                "location": "Remote", "is_remote": 1, "url": url, "updated_at": "2026-10-26T00:00:00Z"}

    listed = [job("Senior Network Engineer", "https://x/1"), job("Senior Security Sales Engineer", "https://x/2"),
              job("Network Engineer", "https://x/3")]
    monkeypatch.setattr(jd, "fetch_jobs_for_company", lambda entry, timeout=8.0: listed)
    # Rows from an earlier scan: one still listed, one the rules now exclude, one closed, one the owner dismissed.
    conn = sqlite3.connect(db)
    for title, url, status in [("Senior Network Engineer", "https://x/1", "discovered"),
                               ("Senior Security Sales Engineer", "https://x/2", "discovered"),
                               ("Cloud Network Engineer", "https://x/closed", "discovered"),
                               ("Network Engineer", "https://x/3", "dismissed")]:
        conn.execute("INSERT INTO discovered_jobs (dedup_key, company_name, company_token, ats_type, job_title, location,"
                     " is_remote, url, match_score, matched_skills, status, discovered_at, updated_at)"
                     " VALUES (?, 'Acme', 'acme', 'greenhouse', ?, 'Remote', 1, ?, 70, '[]', ?, 't', 't')",
                     (jd.compute_job_hash("Acme", title, "Remote", url), title, url, status))
    conn.commit()
    conn.close()
    engine.scan_all()
    conn = sqlite3.connect(db)
    states = dict(conn.execute("SELECT url, status FROM discovered_jobs").fetchall())
    conn.close()
    assert states["https://x/1"] == "discovered"          # still listed and qualifying
    assert states["https://x/2"] == "expired"             # the rules now exclude it
    assert states["https://x/closed"] == "expired"        # the board no longer lists it
    assert states["https://x/3"] == "dismissed"           # the owner's decision stands


def test_a_board_that_failed_to_load_expires_nothing(tmp_path, monkeypatch):
    import sqlite3

    import job_discovery as jd
    db = tmp_path / "d.db"
    conn = sqlite3.connect(db)
    jd.ensure_discovery_schema(conn)
    conn.execute("INSERT INTO discovered_jobs (dedup_key, company_name, company_token, ats_type, job_title, location,"
                 " is_remote, url, match_score, matched_skills, status, discovered_at, updated_at)"
                 " VALUES ('k', 'Acme', 'acme', 'greenhouse', 'Network Engineer', 'Remote', 1, 'https://x/1', 90, '[]',"
                 " 'discovered', 't', 't')")
    conn.commit()
    conn.close()
    board = tmp_path / "boards.json"
    board.write_text(json.dumps([{"company": "Acme", "token": "acme", "ats": "greenhouse"}]), encoding="utf-8")
    monkeypatch.setattr(jd, "fetch_jobs_for_company", lambda entry, timeout=8.0: [])     # timeout / HTTP error
    jd.JobDiscoveryEngine(db_path=db, config_path=board).scan_all()
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT status FROM discovered_jobs").fetchone()[0] == "discovered"
    conn.close()


def test_a_posting_in_another_country_is_not_suggested():
    """Twilio's 'Senior Network Engineer', location 'Remote - India', scored 100, and Okta's in Bengaluru 70, for an owner
    living in the United States."""
    home = {**PROFILE, "country_code": "US"}
    for title_location in ["Remote - India", "Bengaluru, India", "Remote, Canada", "Remote - Poland"]:
        assert calculate_match_score("Senior Network Engineer", title_location, home)[0] == 0
    for fine in ["Remote - USA", "Remote, Canada; Remote, US", "Remote", "Palo Alto", "Hybrid"]:
        assert calculate_match_score("Senior Network Engineer", fine, home)[0] >= 70, fine
    # With no home country known the old rule stands.
    assert calculate_match_score("Senior Network Engineer", "Remote - India", PROFILE)[0] == 100


def test_the_profile_gives_the_discovery_its_home_country(monkeypatch):
    import config
    import job_discovery as jd
    monkeypatch.setattr(config, "get_user_profile", lambda: config.UserProfile(country="United States"))
    assert jd.load_profile()["country_code"] == "US"
