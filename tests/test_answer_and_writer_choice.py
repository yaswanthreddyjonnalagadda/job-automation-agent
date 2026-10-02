"""The owner chooses two things on Settings: who answers the forms, and who writes the resume. Each is honoured.

29 September 2026: answering and writing shared one fixed order (Claude, then Gemini, then OpenAI), OpenAI could
not answer forms at all, and the OpenAI writer needed a package that was never installed.
"""
import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest

import ai_choice
import claude_answers
import model_ladder
import openai_integration as oa


class Reply:
    def __init__(self, status, body, headers=None):
        self.status_code, self.text, self.headers = status, json.dumps(body), headers or {}

    def json(self):
        return json.loads(self.text)


def chat(text):
    return Reply(200, {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]})


@pytest.fixture
def openai(monkeypatch):
    state = SimpleNamespace(script={}, sent=[], sleeps=[])

    def post(url, json=None, headers=None, **_):
        state.sent.append((json["model"], json, headers, url))
        queue = state.script.get(json["model"]) or []
        return queue.pop(0) if queue else chat('{"answer": "English"}')
    monkeypatch.setattr(oa.requests, "post", post)
    monkeypatch.setattr(model_ladder.time, "sleep", lambda s: state.sleeps.append(s))
    return state


def config(**over):
    values = dict(openai_api_key="sk-test-key", openai_base_url="https://o.example/v1", openai_model="gpt-a",
                  openai_page_models=("gpt-big", "gpt-small"), openai_quick_models=("gpt-small", "gpt-big"),
                  anthropic_api_key="", gemini_api_key="", claude_request_timeout=5, claude_max_retries=3,
                  resume_writer="", resume_writer_fallback="")
    values.update(over)
    return SimpleNamespace(**values)


# --- OpenAI answers the forms -----------------------------------------------------------------------------------

def test_openai_plans_a_page_with_json_and_the_key_in_a_header(openai):
    client = oa.OpenAIClient(config())
    assert client.plan_page("- textbox City [ref=e1]", {"city": "Fairfax"}) == {"answer": "English"}
    model, body, headers, url = openai.sent[0]
    assert model == "gpt-big" and url == "https://o.example/v1/chat/completions"
    assert headers["Authorization"] == "Bearer sk-test-key" and "sk-test-key" not in url
    assert body["response_format"] == {"type": "json_object"} and "temperature" not in body
    assert body["messages"][0]["role"] == "system" and "Fairfax" in body["messages"][1]["content"]


def test_openai_quick_questions_start_with_the_quick_ladder(openai):
    oa.OpenAIClient(config()).answer_single_question("Preferred language?", options=["English", "Spanish"])
    assert openai.sent[0][0] == "gpt-small"


def test_openai_sees_the_screenshot(openai):
    openai.script["gpt-small"] = [chat('{"page": "job_description", "click": "Apply", "why": "x"}')]
    oa.OpenAIClient(config()).read_page(b"\x89PNG", "https://x.example", "apply")
    parts = openai.sent[0][1]["messages"][1]["content"]
    assert parts[0]["type"] == "image_url" and parts[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_an_openai_account_out_of_credit_is_not_asked_again_for_an_hour(openai):
    no_credit = Reply(429, {"error": {"code": "insufficient_quota", "message": "You exceeded your quota"}})
    openai.script["gpt-big"] = [no_credit]
    openai.script["gpt-small"] = [no_credit]
    client = oa.OpenAIClient(config())
    with pytest.raises(Exception, match="no credit"):
        client._call(system="s", user_message="u")
    openai.sent.clear()
    with pytest.raises(Exception):
        oa.OpenAIClient(config())._call(system="s", user_message="u")
    assert openai.sent == []


def test_an_openai_rate_limit_moves_to_the_next_model(openai):
    openai.script["gpt-big"] = [Reply(429, {"error": {"code": "rate_limit_exceeded", "message": "slow down"}},
                                      {"retry-after": "7"})]
    oa.OpenAIClient(config()).plan_page("p", {})
    assert [m for m, *_ in openai.sent] == ["gpt-big", "gpt-small"] and openai.sleeps == []


# --- Claude answers the forms from the owner's ladder ------------------------------------------------------------

class FakeAnthropic:
    def __init__(self, script):
        self.script, self.sent = script, []
        self.messages = self

    def create(self, *, model, **_):
        self.sent.append(model)
        outcome = (self.script.get(model) or [None]).pop(0) if self.script.get(model) else None
        if outcome is not None:
            raise outcome
        return SimpleNamespace(content=[SimpleNamespace(type="text", text='{"answer": "Yes"}')])


def anthropic_error(cls, status, message):
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
                              headers={"retry-after": "3"})
    return cls(message, response=response, body=None)


def test_claude_answers_climb_the_owners_ladder(monkeypatch):
    monkeypatch.setattr(model_ladder.time, "sleep", lambda s: None)
    fake = FakeAnthropic({"claude-big": [anthropic_error(anthropic.RateLimitError, 429, "rate limited")]})
    cfg = config(claude_page_models=("claude-big", "claude-small"), claude_quick_models=("claude-small",),
                 anthropic_model="claude-big")
    client = claude_answers.ClaudeAnswerClient(cfg, client=fake)
    client.plan_page("p", {})
    client.answer_single_question("Relocate?", options=["Yes", "No"])
    assert fake.sent == ["claude-big", "claude-small", "claude-small"]


def test_a_claude_account_out_of_credit_rests(monkeypatch):
    error = anthropic_error(anthropic.BadRequestError, 400, "Your credit balance is too low")
    fake = FakeAnthropic({"claude-big": [error]})
    cfg = config(claude_page_models=("claude-big",), anthropic_model="claude-big")
    with pytest.raises(Exception, match="out of credit"):
        claude_answers.ClaudeAnswerClient(cfg, client=fake)._call(system="s", user_message="u")
    assert model_ladder.resting("claude-big")[0] == "no credit"


# --- the owner's writer ---------------------------------------------------------------------------------------

def test_the_chosen_writer_and_model_come_first_then_the_second_choice():
    cfg = config(openai_api_key="sk-x", gemini_api_key="AIza" + "x" * 35, anthropic_api_key="sk-ant-x",
                 gemini_model="g", gemini_page_models=("g",), anthropic_model="claude-a",
                 resume_writer="gemini:gemini-3.5-flash", resume_writer_fallback="openai:gpt-5-mini")
    found = ai_choice.writers(cfg)
    assert [label for label, _ in found] == ["Gemini (gemini-3.5-flash)", "OpenAI (gpt-5-mini)"]
    gemini_writer, openai_writer = (client for _, client in found)
    assert model_ladder.ladder_for(gemini_writer._config, "gemini", "page") == ["gemini-3.5-flash"]
    assert model_ladder.ladder_for(openai_writer._config, "openai", "page") == ["gpt-5-mini"]


def test_a_chosen_writer_without_a_key_is_skipped():
    cfg = config(openai_api_key="", resume_writer="openai:gpt-5", resume_writer_fallback="")
    assert ai_choice.writers(cfg) == []


def test_with_no_choice_every_provider_with_a_key_writes_claude_first():
    cfg = config(anthropic_api_key="sk-ant-x", anthropic_model="claude-a", openai_api_key="sk-x")
    assert [label for label, _ in ai_choice.writers(cfg)] == ["Claude", "OpenAI"]


def test_the_cover_letter_goes_to_the_next_writer_when_the_first_fails():
    class Broken:
        def generate_cover_letter(self, *a, **k):
            raise RuntimeError("out of credit")

    class Works:
        def generate_cover_letter(self, *a, **k):
            return "Dear team"
    assert ai_choice.Writers([("A", Broken()), ("B", Works())]).generate_cover_letter() == "Dear team"
