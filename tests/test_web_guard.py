"""Only the person at this computer drives the dashboard, not a website they happen to visit.

The forms had a csrf_token field nothing filled in or checked: any page open in the browser could post to
127.0.0.1:5000 -- start an application, change saved answers, delete one.
"""
import re

import pytest

import web_guard


@pytest.fixture
def client(monkeypatch, tmp_path):
    import profile_setup
    import web_ui
    from job_tracker import JobTracker
    monkeypatch.setitem(web_ui.app.config, "TESTING", False)          # the real checks
    monkeypatch.setattr(profile_setup, "needs_setup", lambda: False)
    monkeypatch.setattr(profile_setup, "ANSWERS_PATH", tmp_path / "profile_answers.json")
    monkeypatch.setattr(profile_setup, "UNANSWERED_PATH", tmp_path / "unanswered.json")
    tracker = JobTracker(tmp_path / "a.db")
    monkeypatch.setattr(web_ui, "get_tracker", lambda: tracker)
    return web_ui.app.test_client()


def post_answer(client, token=None, **headers):
    data = {"new_question": "Preferred shift?", "new_answer": "Day"}
    if token is not None:
        data["csrf_token"] = token
    return client.post("/answers", data=data, headers=headers)


def test_a_form_from_the_dashboard_goes_through(client, tmp_path):
    assert post_answer(client, web_guard.TOKEN).status_code == 302
    assert (tmp_path / "profile_answers.json").read_text()


@pytest.mark.parametrize("token", [None, "", "guessed-token"])
def test_a_form_without_the_dashboards_token_is_refused(client, tmp_path, token):
    assert post_answer(client, token).status_code == 403
    assert not (tmp_path / "profile_answers.json").exists()


@pytest.mark.parametrize("header", ["Origin", "Referer"])
def test_a_form_posted_from_another_site_is_refused_even_with_the_token(client, header):
    assert post_answer(client, web_guard.TOKEN, **{header: "https://evil.example.com/page"}).status_code == 403


def test_the_dashboards_own_pages_may_post(client):
    assert post_answer(client, web_guard.TOKEN, Origin="http://localhost").status_code == 302


@pytest.mark.parametrize("host", ["evil.example.com", "evil.example.com:5000", "192.168.1.20:5000"])
def test_a_request_under_another_name_is_refused(client, host):
    assert client.get("/answers", headers={"Host": host}).status_code == 403


@pytest.mark.parametrize("host", ["127.0.0.1:5000", "localhost:5000", "[::1]:5000"])
def test_this_computers_own_names_are_served(client, host):
    assert client.get("/answers", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("path", ["/", "/answers", "/profile", "/setup", "/settings"])
def test_every_form_on_every_page_carries_the_token(client, path):
    page = client.get(path).data.decode()
    forms = re.findall(r"<form[^>]*method=\"post\"[^>]*>(.*?)</form>", page, re.DOTALL | re.IGNORECASE)
    for form in forms:
        assert f'name="csrf_token" value="{web_guard.TOKEN}"' in form
