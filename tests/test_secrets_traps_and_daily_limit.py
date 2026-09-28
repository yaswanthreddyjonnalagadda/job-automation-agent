"""KBI Biopharma (Workday), 28 September.

The per-question AI step asked Gemini to answer the 'Password' box and Workday's robots-only decoy
('Enter website. This input is for robots only, do not enter if you're human.'), and every question
waited three minutes on a Gemini key whose daily allowance was spent. The reading agent knew no
honeypot rule (only the old form filler did), and the Gemini client retried a limit that lasts a day.
"""
from types import SimpleNamespace

import pytest

import gemini_integration
import page_agent
from browser_automation import JobApplicationAssistant
from perception import is_honeypot, is_secret_box

WORKDAY_DECOY = "Enter website. This input is for robots only, do not enter if you're human."


@pytest.mark.parametrize("label", [
    WORKDAY_DECOY,
    "This field is for robots only",
    "Do not fill this in if you are human",
    "Leave this field blank",
    "leave blank",
    "honeypot",
])
def test_decoy_boxes_are_recognised(label):
    assert is_honeypot(label)
    assert JobApplicationAssistant._is_honeypot(label)        # the old filler asks the same rule


@pytest.mark.parametrize("label", ["Website", "Personal website", "Email Address", "Why us?",
                                   "Please do not leave any required field empty"])
def test_ordinary_boxes_are_not_decoys(label):
    assert not is_honeypot(label)


@pytest.mark.parametrize("label", ["Password", "Verify New Password", "Passcode", "One-time code", "PIN"])
def test_secret_boxes_are_recognised(label):
    assert is_secret_box(label)


def test_the_reader_never_offers_a_decoy_as_a_question():
    snapshot = f"""- generic [ref=e1]:
  - generic [ref=e2]: Email Address*
  - textbox "Email Address" [ref=e3]
  - generic [ref=e4]: {WORKDAY_DECOY}
  - textbox "{WORKDAY_DECOY}" [ref=e5]
  - button "Create Account" [ref=e6]"""
    names = [c.name for c in page_agent.parse_snapshot(snapshot)]
    assert names == ["Email Address", "Create Account"]


# --- Gemini: a spent daily allowance is not waited for ---------------------------------

class Reply:
    def __init__(self, status, text):
        self.status_code, self.text = status, text

    def json(self):
        import json
        return json.loads(self.text)


DAILY = ('{"error": {"code": 429, "message": "Quota exceeded", "details": [{"@type": '
         '"type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": '
         '"GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}, {"retryDelay": "59s"}]}}')
PER_MINUTE = DAILY.replace("PerDay", "PerMinute")


@pytest.fixture
def gemini(monkeypatch):
    calls, sleeps = [], []
    monkeypatch.setattr(gemini_integration, "_DAILY_SPENT_AT", 0.0)
    monkeypatch.setattr(gemini_integration.time, "sleep", lambda s: sleeps.append(s))
    config = SimpleNamespace(gemini_api_key="AIza" + "x" * 35, gemini_base_url="https://g.example",
                             gemini_model="m", claude_request_timeout=5, claude_max_retries=3)
    client = gemini_integration.GeminiClient(config)

    def post(reply):
        def fake(*args, **kwargs):
            calls.append(1)
            return reply
        monkeypatch.setattr(gemini_integration.requests, "post", fake)
    return SimpleNamespace(client=client, calls=calls, sleeps=sleeps, post=post)


def test_a_spent_daily_allowance_is_asked_once_and_not_waited_for(gemini):
    gemini.post(Reply(429, DAILY))
    with pytest.raises(Exception) as first:
        gemini.client._call(system="s", user_message="u")
    assert "daily allowance" in str(first.value)
    assert gemini.calls == [1] and gemini.sleeps == []          # one request, no minute-long waits


def test_after_that_nothing_more_is_sent_this_hour(gemini):
    gemini.post(Reply(429, DAILY))
    with pytest.raises(Exception):
        gemini.client._call(system="s", user_message="u")
    with pytest.raises(Exception) as second:
        gemini.client._call(system="s", user_message="u")
    assert "daily allowance" in str(second.value) and gemini.calls == [1]


def test_a_per_minute_limit_is_still_waited_for_and_retried(gemini):
    gemini.post(Reply(429, PER_MINUTE))
    with pytest.raises(Exception):
        gemini.client._call(system="s", user_message="u")
    assert len(gemini.calls) == 3 and gemini.sleeps                # retried, with waits
