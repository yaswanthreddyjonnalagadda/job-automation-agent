"""Greenhouse 8-character alphanumeric code & embedded boards, recurring screening questions, and diagnostics/SQLite fallback (f188).

1. Greenhouse's 8-character security code ('EvPGU6Qy') holds letters and digits. `PageAgent.complete_account_code`
   previously ran `re.sub(r"\\D", "", code)` whenever `expected > 0`, stripping letters and rejecting valid 8-character
   Greenhouse codes as not having 8 digits. Also `GreenhouseAdapter.matches` recognizes `?gh_jid=` embedded URLs.
2. 'Which country are you located in?' (Paradox/Olivia) previously tied between `RESIDES_IN` and `COUNTRY` and picked
   `RESIDES_IN`, which returned `""` when no country was named in the question itself. Excluding `Which/What/Where`
   questions from `RESIDES_IN` routes it to `COUNTRY`.
3. 'Where would you like to receive a verification code?' (ADP) routes to `profile.preferred_contact_method` (`Email`
   by default) on both dropdowns and radio buttons; WOTC / TANF / AFDC government-assistance questions map to
   `GOVERNMENT_ASSISTANCE` (`profile.received_government_assistance`).
4. `diagnostics.py` preserves `EDUCATION_SKIPPED` and `OPTIONAL_LEFT_BLANK` categories and standard wizard step/button
   labels, and `db.get_tracker` probes the Postgres TCP port with a 200ms timeout before waiting on a multi-second
   driver timeout.
"""
from __future__ import annotations

from types import SimpleNamespace

import psycopg
import pytest

import concept_matcher
import diagnostics
import page_agent
from sites.greenhouse import GreenhouseAdapter


def test_greenhouse_adapter_matches_embedded_gh_jid_urls():
    assert GreenhouseAdapter.matches("https://boards.greenhouse.io/acme/jobs/123")
    assert GreenhouseAdapter.matches("https://www.example.com/careers/job?gh_jid=456789")
    assert not GreenhouseAdapter.matches("https://www.example.com/careers/job?id=456789")


def test_complete_account_code_accepts_8_char_alphanumeric_greenhouse_code(monkeypatch):
    monkeypatch.setattr("emailed_codes.why_not", lambda *a, **k: None)
    ag = page_agent.PageAgent.__new__(page_agent.PageAgent)
    ag._code_tries = 0
    ag._last_code = ""
    ag._asked_for_new_code = False
    ag.notes = []
    ag.profile = SimpleNamespace(check_gmail_for_confirmation=True)
    ag.config = SimpleNamespace(ats_email="applicant@example.com")
    ag.assistant = SimpleNamespace(passcode_from_gmail=lambda tab, previous="", length=0: "EvPGU6Qy" if length == 8 else "")

    typed = []
    pressed = []
    fake_loc = SimpleNamespace(
        click=lambda **k: None,
        fill=lambda val, **k: typed.append(val),
        evaluate=lambda *a, **k: None,
        dispatch_event=lambda *a, **k: None,
        press=lambda key, **kw: pressed.append(key),
    )
    monkeypatch.setattr(page_agent, "fill_and_dispatch", lambda loc, val, **k: typed.append(val))
    ag.locate = lambda page, ref: fake_loc
    ag._code_boxes = lambda page: []
    ag.snapshot = lambda page: '- textbox "Security code" [ref=e1]'

    fake_page = SimpleNamespace(url="https://job-boards.greenhouse.io/praxis/jobs/1", wait_for_timeout=lambda ms: None)
    monkeypatch.setattr("safety.captcha_visible", lambda page: False)

    snapshot = (
        '- paragraph: "Greenhouse emailed an 8-character code to confirm you are a human."\n'
        '- textbox "Security code" [ref=e1]'
    )
    controls = [page_agent.Control(ref="e1", role="textbox", name="Security code")]
    assert ag.complete_account_code(fake_page, controls, snapshot) is True
    assert typed == ["EvPGU6Qy"]
    assert pressed == ["Enter"]


def test_which_country_are_you_located_in_matches_country_and_not_resides_in():
    q = "Which country are you located in?"
    assert concept_matcher.match_concept(q) == "COUNTRY"
    val, src = concept_matcher.resolve_profile_value(
        "COUNTRY", SimpleNamespace(country="United States"), question=q
    )
    assert val == "United States"
    assert src == "profile.country"
    # Yes/No residence questions still match RESIDES_IN.
    assert concept_matcher.match_concept("Do you currently reside in the United States?") == "RESIDES_IN"


def test_wotc_government_assistance_and_verification_channel_questions():
    q = (
        "Did you or a member of your family receive any type of government assistance such as TANF "
        "(Temporary Assistance for Needy Families), AFDC or any other type of welfare program in the "
        "form of a check, debit card or direct deposit in the past 2 years?"
    )
    assert concept_matcher.match_concept(q) == "GOVERNMENT_ASSISTANCE"
    val, src = concept_matcher.resolve_profile_value(
        "GOVERNMENT_ASSISTANCE",
        SimpleNamespace(received_government_assistance="No"),
        options=["Yes", "No"],
        question=q,
    )
    assert val == "No"
    assert src == "profile.received_government_assistance"

    ag = page_agent.PageAgent.__new__(page_agent.PageAgent)
    ag.profile = SimpleNamespace(preferred_contact_method="Email")
    ctrl = page_agent.Control(
        ref="c1",
        role="combobox",
        name="Where would you like to receive a verification code?",
        options=("Send Email", "Send Phone SMS"),
    )
    assert ag.known_answer(ctrl) == ("Send Email", "profile.preferred_contact_method")


def test_diagnostics_preserves_education_skipped_and_optional_left_blank_and_fast_tcp_probe(monkeypatch):
    out_edu = diagnostics.sanitize_log_line(
        "EDUCATION_SKIPPED: this employer's school list is empty -- Education is optional\n"
    )
    assert out_edu.startswith("EDUCATION_SKIPPED:")
    assert "this employer's school list is empty" in out_edu

    out_opt = diagnostics.sanitize_log_line("OPTIONAL_LEFT_BLANK: left 'Veteran Status' blank\n")
    assert out_opt == "OPTIONAL_LEFT_BLANK: private details omitted\n"

    snap = diagnostics.sanitize_snapshot('- heading "Application Questions"\n- button "Save and Continue"\n')
    assert '"Application Questions"' in snap
    assert '"Save and Continue"' in snap

    import db
    import socket

    monkeypatch.setattr(db, "dsn_from_env", lambda: "postgresql://u:p@127.0.0.1:5432/job_agent")
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *a, **k: (_ for _ in ()).throw(OSError("Connection refused")),
    )
    with pytest.raises(psycopg.OperationalError):
        db.get_tracker()

