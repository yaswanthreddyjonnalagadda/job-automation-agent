"""Every Gemini model has its own free allowance: a spent one hands over to the next, and stays skipped.

29 September 2026: the agent asked only gemini-3.8-flash, whose free tier allows 20 requests a day. It ran out
after two or three applications while eight other models' allowances (two of them 500 a day) went unused.
"""
import json
from types import SimpleNamespace

import pytest

import gemini_integration as g

DAILY = ('{"error": {"code": 429, "message": "Quota exceeded", "details": [{"@type": '
         '"type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": '
         '"GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}, {"retryDelay": "40s"}]}}')
PER_MINUTE = DAILY.replace("PerDay", "PerMinute")


class Reply:
    def __init__(self, status, text):
        self.status_code, self.text = status, text

    def json(self):
        return json.loads(self.text)


def ok(answer='{"answer": "English"}'):
    return Reply(200, json.dumps({"candidates": [{"content": {"parts": [{"text": answer}]},
                                                  "finishReason": "STOP"}]}))


@pytest.fixture
def google(monkeypatch):
    """Answers per model: {model: [replies...]}; a model with no script answers ok()."""
    state = SimpleNamespace(script={}, sent=[], sleeps=[])

    def post(url, json=None, **_):
        model = url.rsplit("/models/", 1)[1].split(":")[0]
        state.sent.append((model, json))
        queue = state.script.get(model) or []
        return queue.pop(0) if queue else ok()
    monkeypatch.setattr(g.requests, "post", post)
    monkeypatch.setattr(g.time, "sleep", lambda s: state.sleeps.append(s))
    return state


def config(**over):
    values = dict(gemini_api_key="AIza" + "x" * 35, gemini_base_url="https://g.example", gemini_model="big-1",
                  gemini_page_models=("big-1", "big-2", "lite-1"), gemini_quick_models=("lite-1", "lite-2", "big-1"),
                  claude_request_timeout=5, claude_max_retries=3)
    values.update(over)
    return SimpleNamespace(**values)


def models(google):
    return [m for m, _ in google.sent]


def test_a_spent_model_hands_over_to_the_next_in_the_same_call(google):
    google.script["big-1"] = [Reply(429, DAILY)]
    client = g.GeminiClient(config())
    assert client.plan_page("page", {}) == {"answer": "English"}
    assert models(google) == ["big-1", "big-2"] and google.sleeps == []


def test_the_next_run_does_not_ask_a_spent_model_again(google):
    google.script["big-1"] = [Reply(429, DAILY)]
    g.GeminiClient(config()).plan_page("page", {})
    google.sent.clear()
    g.GeminiClient(config()).plan_page("page 2", {})                   # a new run: a new client, the same book
    assert models(google) == ["big-2"]


def test_a_spent_model_rests_until_googles_midnight():
    now = 1_790_000_000.0
    reset = g.next_reset(now)
    assert 0 < reset - now <= 24 * 3600
    assert g._pacific(reset).hour == 0 and g._pacific(reset).minute == 0


def test_quick_questions_start_with_the_models_with_big_allowances(google):
    client = g.GeminiClient(config())
    client.answer_single_question("What is your preferred language for written communications?",
                                  options=["English", "Spanish"])
    client.plan_page("page", {})
    assert models(google) == ["lite-1", "big-1"]


def test_when_every_model_is_spent_the_run_is_told_at_once(google):
    for m in ("big-1", "big-2", "lite-1"):
        google.script[m] = [Reply(429, DAILY)]
    client = g.GeminiClient(config())
    with pytest.raises(Exception) as caught:
        client._call(system="s", user_message="u")
    assert "daily allowance" in str(caught.value)
    assert models(google) == ["big-1", "big-2", "lite-1"] and google.sleeps == []
    google.sent.clear()
    with pytest.raises(Exception):
        client._call(system="s", user_message="u")
    assert google.sent == []                                            # nothing more is sent today


def test_a_per_minute_limit_moves_on_without_waiting(google):
    google.script["big-1"] = [Reply(429, PER_MINUTE)]
    g.GeminiClient(config()).plan_page("page", {})
    assert models(google) == ["big-1", "big-2"] and google.sleeps == []


def test_a_model_the_key_cannot_use_is_skipped_for_a_week(google):
    google.script["big-1"] = [Reply(404, '{"error": {"message": "models/big-1 is not found"}}')]
    g.GeminiClient(config()).plan_page("page", {})
    why, until = g._resting("big-1")
    assert why == "missing" and until - g.time.time() > 6 * 24 * 3600
    assert models(google) == ["big-1", "big-2"]


def test_gemma_gets_the_instructions_inside_the_message(google):
    client = g.GeminiClient(config(gemini_quick_models=("gemma-4-31b-it",)))
    client.choose_option("Masters of Science", ["Bachelors", "Masters"], "Degree")
    model, body = google.sent[0]
    assert model == "gemma-4-31b-it" and "systemInstruction" not in body
    assert "responseMimeType" not in body["generationConfig"]
    assert "dropdown option" in body["contents"][0]["parts"][0]["text"]


def test_the_same_question_in_one_run_is_asked_once(google):
    client = g.GeminiClient(config())
    for _ in range(3):
        client.answer_single_question("Preferred language?", options=["English", "Spanish"])
    assert len(google.sent) == 1


def test_calls_are_counted_per_model_for_the_dashboard(google):
    client = g.GeminiClient(config())
    client.plan_page("page", {})
    client.answer_single_question("Preferred language?", options=["English", "Spanish"])
    rows = {r["model"]: r for r in g.usage_today(config())}
    assert rows["big-1"]["calls"] == 1 and rows["lite-1"]["calls"] == 1 and rows["big-2"]["calls"] == 0


def test_an_old_config_with_one_model_still_uses_just_that_model(google):
    old = SimpleNamespace(gemini_api_key="AIza" + "x" * 35, gemini_base_url="https://g.example", gemini_model="m",
                          claude_request_timeout=5, claude_max_retries=3)
    g.GeminiClient(old).answer_single_question("Preferred language?", options=["English"])
    assert models(google) == ["m"]
