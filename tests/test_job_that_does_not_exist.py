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


# ------------------------------------------------------------------ is it gone, or is the site down?
import io
import json as _json
import urllib.error

from sites import workday


def _reply(body, status=200):
    class Reply(io.BytesIO):
        def __init__(self):
            super().__init__(_json.dumps(body).encode())
            self.status = status
        def __enter__(self): return self
        def __exit__(self, *a): return False
    return Reply()


COPART_URL = "https://copart.wd12.myworkdayjobs.com/en-US/Copart/job/Las-Vegas-NV/Senior-Network-Engineer_JR110107?source=LinkedIn"


def test_a_requisition_the_listing_does_not_hold_is_gone(monkeypatch):
    seen = []
    def urlopen(request, timeout=None):
        seen.append((request.full_url, _json.loads(request.data)))
        return _reply({"total": 0, "jobPostings": []})
    monkeypatch.setattr(workday.urllib.request, "urlopen", urlopen)
    assert workday.posting_state(COPART_URL) == "gone"
    assert seen == [("https://copart.wd12.myworkdayjobs.com/wday/cxs/copart/Copart/jobs",
                     {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "JR110107"})]


def test_a_requisition_the_listing_holds_exists(monkeypatch):
    listing = {"total": 1, "jobPostings": [{"externalPath": "/job/Las-Vegas-NV/Senior-Network-Engineer_JR110107"}]}
    monkeypatch.setattr(workday.urllib.request, "urlopen", lambda request, timeout=None: _reply(listing))
    assert workday.posting_state(COPART_URL) == "exists"


@pytest.mark.parametrize("failure", [urllib.error.HTTPError("u", 502, "Service unavailable", {}, None),
                                     urllib.error.HTTPError("u", 403, "Forbidden", {}, None),
                                     urllib.error.URLError("offline"), TimeoutError("slow"), ValueError("not json")])
def test_a_listing_that_cannot_answer_is_not_a_verdict(monkeypatch, failure):
    def urlopen(request, timeout=None):
        raise failure
    monkeypatch.setattr(workday.urllib.request, "urlopen", urlopen)
    assert workday.posting_state(COPART_URL) == "down"


def test_a_listing_that_answers_without_postings_data_is_not_a_verdict(monkeypatch):
    monkeypatch.setattr(workday.urllib.request, "urlopen", lambda request, timeout=None: _reply({"errorCode": "HTTP_502"}))
    assert workday.posting_state(COPART_URL) == "down"


@pytest.mark.parametrize("url", ["https://boards.greenhouse.io/acme/jobs/1", "https://jobs.example.com/x",
                                 "https://tenant.wd1.myworkdayjobs.com/en-US/careers/login"])
def test_only_a_workday_posting_address_is_looked_up(monkeypatch, url):
    monkeypatch.setattr(workday.urllib.request, "urlopen", lambda *a, **k: pytest.fail("no lookup for this address"))
    assert workday.posting_state(url) is None


def verdict(state, text="The page you are looking for doesn't exist."):
    tab = FakeTab([text])
    tab.url = COPART_URL
    workday_state = state
    import sites.workday as w
    original = w.posting_state
    w.posting_state = lambda url: workday_state
    try:
        return agent()._posting_verdict(tab)
    finally:
        w.posting_state = original


def test_gone_in_the_listing_and_on_the_page_is_a_job_that_does_not_exist():
    assert verdict("gone")[0] == "gone"


def test_a_site_that_cannot_answer_is_reported_as_down_not_as_a_job_that_does_not_exist():
    kind, message = verdict("down")
    assert kind == "down" and "try again later" in message and "doesn't exist" not in message


def test_a_posting_the_listing_holds_is_not_called_gone_whatever_the_page_says():
    assert verdict("exists") == ("ok", "")


def test_another_portal_is_judged_by_its_own_page_after_the_reloads():
    assert verdict(None)[0] == "gone"


def test_a_normal_page_is_ok_and_asks_nobody():
    assert agent()._posting_verdict(FakeTab(["My Information\nFirst name"])) == ("ok", "")
