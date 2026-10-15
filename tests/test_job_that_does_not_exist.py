"""A posting that no longer exists ends the run with its own status, instead of being handed to the owner (f153).

Copart's tenant answered "The page you are looking for doesn't exist." for a posting the owner wanted to apply to;
the agent tried for forty seconds, then stopped with 'no way forward was found on this page' and a
needs-your-review status, as if there were something for a person to do. The owner's rule (14 October 2026): if the
job does not exist, stop the run and record that the job does not exist.
"""
from types import SimpleNamespace

import pytest

import page_agent
from job_tracker import STATUS_JOB_NOT_FOUND
from page_agent import Outcome, posting_gone_message


@pytest.mark.parametrize("text,expected", [
    ("Copart Careers\nThe page you are looking for doesn't exist.\nSearch for Jobs", True),
    ("The page you are looking for does not exist", True),
    ("About the Job\nThe job position is no longer available.\nExplore other jobs", True),
    ("This job is no longer available", True),
    ("Contact us if the page you are looking for doesn't exist.", False),
    ("FAQs\nThe page you are looking for doesn't exist.", False),
    ("My Information\nFirst name\nLast name", False),
    ("", False),
])
def test_a_page_that_says_the_job_is_gone_is_recognised_only_as_its_own_notice(text, expected):
    assert bool(posting_gone_message(text)) is expected


def test_the_status_exists_and_reads_as_a_job_that_does_not_exist():
    import ui_shell
    assert STATUS_JOB_NOT_FOUND == "job_not_found"
    assert ui_shell.status_label(STATUS_JOB_NOT_FOUND) == "Job doesn't exist"
    assert ui_shell.status_group(STATUS_JOB_NOT_FOUND) == "skipped"


class FakeTab:
    """A tab whose body shows `texts` one after another, one per reload."""

    def __init__(self, texts):
        self.texts, self.reloads, self.url = list(texts), 0, "https://jobs.example.com/job/1"

    def locator(self, _selector):
        tab = self
        return SimpleNamespace(inner_text=lambda timeout=0: tab.texts[min(tab.reloads, len(tab.texts) - 1)])

    def reload(self, **_kwargs):
        self.reloads += 1

    def wait_for_timeout(self, _ms):
        pass


def agent():
    return page_agent.PageAgent.__new__(page_agent.PageAgent)


def test_a_posting_that_is_gone_after_every_reload_is_reported_gone():
    tab = FakeTab(["The page you are looking for doesn't exist."])
    assert agent()._posting_gone(tab)
    assert tab.reloads == 2          # looked again twice before saying so


def test_a_site_that_was_only_down_for_a_moment_is_not_a_job_that_does_not_exist():
    tab = FakeTab(["The page you are looking for doesn't exist.", "Senior Network Engineer\nApply"])
    assert agent()._posting_gone(tab) == ""
    assert tab.reloads == 1


def test_a_normal_page_is_never_reloaded_to_check():
    tab = FakeTab(["My Information\nFirst name"])
    assert agent()._posting_gone(tab) == ""
    assert tab.reloads == 0


def test_the_outcome_is_its_own_kind():
    assert Outcome("job_not_found", object(), ["gone"]).kind == "job_not_found"
