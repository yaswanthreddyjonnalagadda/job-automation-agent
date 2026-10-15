"""The phone console and alert are part of the agent's wait for the owner -- and nothing when it is not set up.

The wait loop already carries on by itself once a CAPTCHA is gone or the blanks are filled. This only gives it two
things at a stop that needs the owner: an alert on the phone, and a console the loop keeps serving while it waits.
"""
from types import SimpleNamespace

import pytest

import phone_alert
import phone_console
from browser_automation import JobApplicationAssistant

NEEDS = {"company": "Acme", "title": "Engineer", "kind": "owner_needed",
         "reasons": ["required field is still blank: Notice period"]}


def assistant():
    return JobApplicationAssistant.__new__(JobApplicationAssistant)


class FakeConsole:
    url = "http://100.101.102.103:8765/c/tok/"

    def __init__(self):
        self.served, self.closed = 0, False

    def service(self, page):
        self.served += 1

    def close(self):
        self.closed = True


def test_a_stop_that_needs_the_owner_opens_the_console_and_closes_it_after(tmp_path, monkeypatch):
    fake, opened = FakeConsole(), []
    monkeypatch.setattr(phone_console, "begin", lambda signal_path, needs: opened.append(needs) or fake)
    signal = tmp_path / "_signal_job.txt"
    signal.write_text("continue", encoding="utf-8")
    a = assistant()
    a._owner_needs = NEEDS
    assert a.wait_for_signal(signal, timeout_seconds=5) == "continue"
    assert opened == [NEEDS] and fake.closed is True
    assert a._phone is None


def test_a_wait_with_nothing_needed_opens_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(phone_console, "begin", lambda *a: pytest.fail("no stop needs the owner here"))
    signal = tmp_path / "_signal_job.txt"
    signal.write_text("continue", encoding="utf-8")
    assert assistant().wait_for_signal(signal, timeout_seconds=5) == "continue"


def test_the_console_is_closed_even_when_the_wait_times_out(tmp_path, monkeypatch):
    fake = FakeConsole()
    monkeypatch.setattr(phone_console, "begin", lambda signal_path, needs: fake)
    a = assistant()
    a._owner_needs = NEEDS
    with pytest.raises(TimeoutError):
        a.wait_for_signal(tmp_path / "_signal_never.txt", poll_seconds=0.05, timeout_seconds=0.1)
    assert fake.closed is True


def test_the_wait_serves_the_console_between_checks(monkeypatch):
    fake = FakeConsole()
    a = assistant()
    a._phone = fake
    page = SimpleNamespace(top=None)
    a._sleep_for_phone(0.7, page)
    assert fake.served >= 2                      # several beats inside one poll, not one per two seconds


def test_the_frame_a_page_lives_in_is_not_what_the_phone_sees():
    fake, seen = FakeConsole(), []
    fake.service = lambda page: seen.append(page)
    a = assistant()
    a._phone = fake
    tab = object()
    a._sleep_for_phone(0.1, SimpleNamespace(top=tab))
    assert seen and seen[0] is tab


def test_without_a_console_the_wait_just_sleeps():
    a = assistant()
    a._phone = None
    a._sleep_for_phone(0.01, None)               # no error, no console


def test_begin_alerts_with_the_link_when_the_console_is_up(monkeypatch, tmp_path):
    fake, sent = FakeConsole(), []
    monkeypatch.setattr(phone_console, "open_for", lambda signal_path, needs: fake)
    monkeypatch.setattr(phone_alert, "send", lambda *args, **kw: sent.append((args, kw)) or True)
    assert phone_console.begin(tmp_path / "s.txt", NEEDS) is fake
    args, kw = sent[0]
    assert "Acme" in args[0] and kw["click_url"] == fake.url and kw["priority"] == 4


def test_begin_still_alerts_when_there_is_no_console(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(phone_console, "open_for", lambda signal_path, needs: None)
    monkeypatch.setattr(phone_alert, "send", lambda *args, **kw: sent.append((args, kw)) or True)
    assert phone_console.begin(tmp_path / "s.txt", NEEDS) is None
    assert sent and sent[0][1]["click_url"] == ""


def test_one_stop_one_alert_the_need_is_cleared_after_the_wait(tmp_path, monkeypatch):
    monkeypatch.setattr(phone_console, "begin", lambda signal_path, needs: None)
    signal = tmp_path / "_signal_job.txt"
    signal.write_text("continue", encoding="utf-8")
    a = assistant()
    a._owner_needs = NEEDS
    a.wait_for_signal(signal, timeout_seconds=5)
    assert a._owner_needs is None
