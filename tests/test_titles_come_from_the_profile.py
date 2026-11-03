"""f308: which jobs are suggested is decided by the person's own titles, never by titles built into the code."""
import pytest

import job_discovery as jd
import profile_setup

REMOTE = "Remote"


def score(title, titles, avoid=(), location=REMOTE):
    profile = {"target_titles": list(titles), "avoid_title_words": list(avoid), "locations": [REMOTE],
               "country_code": ""}
    return jd.calculate_match_score(title, location, profile)[0]


NETWORK = ["Network Engineer", "Network Security Engineer", "Cloud Security Engineer", "Data Center Network Engineer"]


@pytest.mark.parametrize("title", [
    "Senior Software Engineer, Backend (Batch Infrastructure)",
    "Software Engineer, Starlink Network",
    "Staff Application Security Engineer",
    "Sr. Data Engineer, Ground Network Engineering",
    "Staff Software Engineer, Mobile (App Infrastructure)",
])
def test_a_job_in_another_discipline_is_not_suggested(title):
    assert score(title, NETWORK, avoid=["Application Security"]) == 0


@pytest.mark.parametrize("title", [
    "Senior Network Engineer", "Network Security Engineer II", "Principal Cloud Security Engineer",
    "Data Center Network Engineer - Remote",
])
def test_a_job_with_one_of_the_persons_titles_is_suggested(title):
    assert score(title, NETWORK) >= 60


def test_a_title_the_person_does_not_have_is_not_matched_on_a_technical_word():
    assert score("Senior Systems Engineer", NETWORK) == 0
    assert score("Cloud Platform Engineer", NETWORK) == 0


@pytest.mark.parametrize("own, title", [
    (["Software Engineer"], "Senior Software Engineer, Backend"),
    (["Data Engineer"], "Staff Data Engineer"),
    (["Mobile Developer"], "Senior Mobile Developer"),
    (["Machine Learning Engineer"], "Machine Learning Engineer, Search"),
])
def test_a_discipline_is_left_out_only_for_people_whose_own_titles_do_not_name_it(own, title):
    assert score(title, own) > 0
    assert score(title, ["Network Engineer"]) == 0


def test_a_person_with_no_titles_is_suggested_nothing():
    assert score("Senior Network Engineer", []) == 0
    assert score("Software Engineer", []) == 0


def test_words_the_person_avoids_are_left_out_but_not_when_their_own_title_has_them():
    assert score("Network Engineer, Federal", ["Network Engineer"], avoid=["Federal"]) == 0
    assert score("Federal Network Engineer", ["Federal Network Engineer"], avoid=["Federal"]) > 0


def test_a_word_inside_another_word_is_not_the_discipline():
    assert score("Senior Network Engineer, Mlbb Platform", NETWORK) > 0
    assert score("Senior Network Engineer, Global Gaming Network", NETWORK, avoid=["Game"]) > 0


def test_no_persons_titles_are_built_into_the_code():
    assert jd.load_profile.__code__.co_consts.count("Senior Network Engineer") == 0


def test_the_draft_from_a_resume_keeps_up_to_twelve_titles():
    titles = [f"Title {n}" for n in range(15)]
    draft = profile_setup.draft_from_resume("R", ask=lambda s, u, n: '{"target_titles": %s}' % __import__("json").dumps(titles))
    assert draft["target_titles"] == tuple(titles[:12])
