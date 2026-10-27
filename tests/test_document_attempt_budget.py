from types import SimpleNamespace

import pytest

import gemini_integration as gemini
import model_ladder


def transport(monkeypatch, send):
    monkeypatch.setattr(gemini.requests, "post", send)
    monkeypatch.setattr(model_ladder, "resting", lambda model: ("", 0))
    monkeypatch.setattr(model_ladder, "rest", lambda *args: None)
    monkeypatch.setattr(model_ladder, "count", lambda *args: None)
    return gemini._Messages(SimpleNamespace(
        gemini_api_key="synthetic", gemini_page_models=("first", "second"),
    ), json_reply=False)


def ask(messages):
    return messages.create(model="ignored", max_tokens=6000, system="Write truthfully",
                           messages=[{"role": "user", "content": "Network engineer"}], timeout=10)


def test_models_share_remaining_document_budget(monkeypatch):
    clock, timeouts = [0], []
    monkeypatch.setattr(gemini.time, "monotonic", lambda: clock[0])

    def send(*args, **kwargs):
        timeouts.append(kwargs["timeout"])
        clock[0] += 7
        return SimpleNamespace(status_code=503)

    with pytest.raises(gemini.GeminiTimeout):
        ask(transport(monkeypatch, send))
    assert timeouts == [10, 3]


def test_exhausted_budget_does_not_start_another_model(monkeypatch):
    clock, calls = [0], []
    monkeypatch.setattr(gemini.time, "monotonic", lambda: clock[0])

    def send(*args, **kwargs):
        calls.append(1)
        clock[0] = 10
        raise gemini.requests.exceptions.Timeout()

    with pytest.raises(gemini.GeminiTimeout):
        ask(transport(monkeypatch, send))
    assert calls == [1]


def test_document_rate_limits_do_not_sleep_before_provider_fallback(monkeypatch):
    def send(*args, **kwargs):
        return SimpleNamespace(status_code=429, text="rate limited",
                               json=lambda: {"error": {"message": "rate limited"}})

    monkeypatch.setattr(gemini.time, "sleep", lambda seconds: pytest.fail("document retry slept"))
    with pytest.raises(gemini.GeminiRateLimited):
        ask(transport(monkeypatch, send))


def test_successful_document_is_returned_within_budget(monkeypatch):
    def send(*args, **kwargs):
        return SimpleNamespace(status_code=200, json=lambda: {
            "candidates": [{"content": {"parts": [{"text": "Network engineer"}]},
                            "finishReason": "STOP"}],
        })

    assert ask(transport(monkeypatch, send)).content[0].text == "Network engineer"
