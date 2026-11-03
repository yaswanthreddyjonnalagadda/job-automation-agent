"""Employer emails become tracker events (f321)."""
from types import SimpleNamespace

import employer_signals as sig


def test_each_signal_classifies():
    cases = {
        "Unfortunately we will not be moving forward": "rejection",
        "Thank you for applying. We have decided to pursue other candidates.": "rejection",
        "Your offer letter from Acme is ready": "offer",
        "Pleased to offer you the Network Engineer role": "offer",
        "Schedule a call with our team to discuss next steps": "interview",
        "Complete your technical assessment on HackerRank": "assessment",
        "Your verification code is 123456": "verification",
        "I came across your profile and would love to chat": "recruiter",
        "Reminder: finish your application": "reminder",
        "Thank you for applying to Acme": "confirmation",
        "Your application to Acme has been received": "confirmation",
        "Weekly newsletter: tech news": None,
    }
    for text, kind in cases.items():
        assert sig.classify(text) == kind, text


def test_a_rejection_beats_its_thanks():
    assert sig.classify("Thank you for your interest. Unfortunately we cannot offer you this role.") == "rejection"


def test_scan_keeps_the_confirmation_rule_and_adds_the_signal():
    texts = ["Acme weekly deals newsletter",
             "Thank you for applying to Acme Network Engineer, we received it",
             "Acme Network Engineer: unfortunately not moving forward"]
    found = sig.scan_rows(texts, "Acme", "Network Engineer")
    assert found["confirmation"].startswith("Thank you for applying")
    assert found["signal"][0] == "rejection"


def test_other_jobs_are_ignored():
    # The Gmail search already scopes rows to the company; the title rule keeps other roles out.
    texts = ["Thank you for applying to Acme Support Rep", "Acme Support Rep interview invite"]
    found = sig.scan_rows(texts, "Acme", "Network Engineer")
    assert found == {"confirmation": None, "signal": None}


def test_recording_is_a_noop_without_a_signal_or_a_tracker():
    events = []
    tracker = SimpleNamespace(record_event=lambda k, kind, msg="", payload=None: events.append((kind, payload)))
    assert sig.record_employer_signal(tracker, "k", SimpleNamespace()) is False
    assert events == []
    assistant = SimpleNamespace(_last_employer_signal=("interview", "Acme interview invite"))
    assert sig.record_employer_signal(SimpleNamespace(), "k", assistant) is False
    assert sig.record_employer_signal(tracker, "k", assistant) is True
    assert events == [("employer_email", {"signal": "interview"})]
