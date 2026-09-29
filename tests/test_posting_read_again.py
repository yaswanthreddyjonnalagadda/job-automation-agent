"""A posting that will not load right now is read again, then taken from the copy an earlier run saved.

Secunetics (BambooHR), 29 September: the page is built by script; one read came back empty and the run stopped
at 'Could not read a job description', although the same posting had been read and saved an hour before.
"""
import json

import pytest

import apply

URL = "https://acme.bamboohr.com/careers/59"
JOB = {"title": "Network Firewall Engineer", "company": "Acme", "location": "Sterling",
       "url": URL + "?source=LinkedIn", "raw_text": "We are hiring."}


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(apply, "DATA_DIR", tmp_path)
    return tmp_path


def test_a_second_read_is_tried(data, monkeypatch):
    reads = iter([None, dict(JOB)])
    monkeypatch.setattr(apply, "resolve_job", lambda url: next(reads))
    assert apply.read_job(URL)["title"] == "Network Firewall Engineer"


def test_the_copy_saved_by_an_earlier_run_is_used_when_the_page_will_not_load(data, monkeypatch):
    (data / "_job_Acme_Network_Firewall_Engineer.json").write_text(json.dumps(JOB), encoding="utf-8")
    monkeypatch.setattr(apply, "resolve_job", lambda url: None)
    job = apply.read_job(URL)                      # the address without the tracking parameter still matches
    assert job and job["company"] == "Acme" and job["location"] == "Sterling"


def test_a_posting_never_read_still_stops_the_run(data, monkeypatch):
    (data / "_job_Other_Role.json").write_text(json.dumps(dict(JOB, url="https://other.example.com/9")), encoding="utf-8")
    monkeypatch.setattr(apply, "resolve_job", lambda url: None)
    assert apply.read_job(URL) is None
