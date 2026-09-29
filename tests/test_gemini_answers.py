"""Gemini answers the form; Claude writes the resume and the cover letter.

The owner's split (25 September 2026), after the Anthropic account reached its usage limit: everything that
answers a question on an application page goes to Google's Gemini, and the documents (resume, cover letter) stay
with Claude. FORM_ANSWER_MODE=gemini switches it on.

GeminiClient is a ClaudeClient with a different model behind it, so the prompts, the JSON reading and the checks
on what comes back (a dropdown answer that is not one of the offered choices is dropped, and so on) are the same
code for both. Google is faked here by a small local server: nothing in these tests needs, or touches, a real key.
"""
import base64
import json
import logging
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

import config
import gemini_integration as gemini
from claude_integration import ClaudeClient, ClaudeIntegrationError

KEY = "AIza" + "x" * 35        # the shape of a Google key (39 characters); not a real one
SHOT = b"\x89PNG...."


# --- a stand-in for Google ------------------------------------------------------------------------------

def ok_reply(text, finish="STOP", before=()):
    parts = [*before, {"text": text}]
    return 200, {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": finish}]}


def error_reply(status, message, name="INVALID_ARGUMENT", retry_delay=None):
    error = {"code": status, "message": message, "status": name}
    if retry_delay:
        error["details"] = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}]
    return status, {"error": error}


class FakeGoogle:
    def __init__(self):
        self.requests = []                      # what was asked: path, the key header, the body
        self.script = []                        # the next replies, in order; then `default`
        self.default = ok_reply('{"page_kind": "other", "answers": []}')
        self.url = ""


@pytest.fixture
def google():
    fake = FakeGoogle()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            fake.requests.append({"path": self.path, "key": self.headers.get("x-goog-api-key"), "body": body})
            status, payload = fake.script.pop(0) if fake.script else fake.default
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    fake.url = f"http://127.0.0.1:{server.server_port}/v1beta"
    yield fake
    server.shutdown()
    server.server_close()


@pytest.fixture(autouse=True)
def slept(monkeypatch):
    waits = []
    monkeypatch.setattr(time, "sleep", lambda seconds: waits.append(seconds))
    return waits


def make_config(google_or_url, **over):
    url = google_or_url if isinstance(google_or_url, str) else google_or_url.url
    values = dict(gemini_api_key=KEY, gemini_model="gemini-test", gemini_base_url=url, claude_max_retries=3,
                  claude_request_timeout=10.0, anthropic_model="claude-x", anthropic_api_key="sk-ant-not-real",
                  form_answer_mode="gemini", agent_brain="api")
    values.update(over)
    return SimpleNamespace(**values)


@pytest.fixture
def client(google):
    return gemini.GeminiClient(make_config(google))


# --- what Gemini is sent --------------------------------------------------------------------------------

def test_a_page_is_planned_with_claudes_own_prompt_and_the_key_in_a_header_not_the_address(google, client):
    plan = client.plan_page('- textbox "City" [ref=e1]', {"profile": {"city": "Fairfax"}}, feedback="it did not stay")
    assert plan == {"page_kind": "other", "answers": []}
    sent = google.requests[0]
    assert sent["path"] == "/v1beta/models/gemini-test:generateContent"
    assert sent["key"] == KEY and KEY not in sent["path"]
    body = sent["body"]
    assert body["systemInstruction"]["parts"][0]["text"] == ClaudeClient.PLAN_PAGE_SYSTEM     # one prompt, not two
    user = body["contents"][0]["parts"][0]["text"]
    assert body["contents"][0]["role"] == "user"
    assert "Fairfax" in user and "[ref=e1]" in user and "it did not stay" in user
    settings = body["generationConfig"]
    assert settings["temperature"] == 0 and settings["responseMimeType"] == "application/json"
    assert settings["maxOutputTokens"] >= 16_000


def test_a_short_reply_is_still_given_room_to_think(google, client):
    """Gemini's thinking is counted in the output budget: a 400-token limit would cut the answer off."""
    google.default = ok_reply(json.dumps({"page": "other", "click": "", "why": "x"}))
    client.read_page(SHOT, "https://x.example/y", "apply")
    assert google.requests[0]["body"]["generationConfig"]["maxOutputTokens"] >= 4_000


def test_fenced_json_and_the_models_thinking_are_read_past(google, client):
    google.script.append(ok_reply('```json\n{"page_kind": "review"}\n```', before=[{"text": "hmm", "thought": True}]))
    assert client.plan_page("p", {})["page_kind"] == "review"


def test_a_screenshot_goes_as_an_image_beside_the_goal(google, client):
    google.default = ok_reply(json.dumps({"page": "job_description", "click": "Apply", "why": "a posting"}))
    result = client.read_page(SHOT, "https://x.example/y", "apply")
    assert result == {"page": "job_description", "click": "Apply", "why": "a posting"}
    body = google.requests[0]["body"]
    parts = body["contents"][0]["parts"]
    assert parts[0]["inlineData"] == {"mimeType": "image/png", "data": base64.b64encode(SHOT).decode()}
    assert "Goal: apply" in parts[1]["text"]
    assert "never choose a control that submits" in body["systemInstruction"]["parts"][0]["text"]


def test_the_checks_on_a_dropdown_answer_are_the_same_with_gemini(google, client):
    google.default = ok_reply(json.dumps({"choice": "Masters", "equivalent": True, "reason": "same level"}))
    assert client.choose_option("Masters of Science", ["Bachelors", "Masters"], "Degree")["choice"] == "Masters"
    google.default = ok_reply(json.dumps({"choice": "Doctorate", "equivalent": True, "reason": "x"}))
    refused = client.choose_option("Masters of Science", ["Bachelors", "Masters"], "Highest degree")
    assert refused["choice"] == "" and refused["equivalent"] is False        # not on the page: never trusted


def test_a_screening_answer_that_is_not_an_offered_choice_is_dropped_with_gemini_too(google, client):
    google.default = ok_reply(json.dumps({"How did you hear about this job?": "LinkedIn", "Preferred contact method": "Fax"}))
    questions = [{"question_text": "How did you hear about this job?", "input_type": "select",
                  "options": ["LinkedIn", "Referral"]},
                 {"question_text": "Preferred contact method", "input_type": "select", "options": ["Email", "Phone"]}]
    got = client.answer_screening_questions(SimpleNamespace(raw_text="five years"),
                                            SimpleNamespace(title="Engineer", company="Praxis"),
                                            config.UserProfile(), questions)
    assert got == {"How did you hear about this job?": "LinkedIn"}


# --- when Google says no or is busy -----------------------------------------------------------------------

def test_a_busy_or_limited_reply_is_waited_for_and_asked_again(google, client, slept):
    google.script += [error_reply(429, "quota", "RESOURCE_EXHAUSTED", retry_delay="7s"),
                      error_reply(503, "overloaded", "UNAVAILABLE")]
    assert client.plan_page("p", {})["page_kind"] == "other"
    assert len(google.requests) == 3
    assert 7 in slept                                       # the wait Google asked for, not just a guess


def test_a_bad_key_is_one_request_and_a_plain_message_with_no_key_in_it(google, client, caplog):
    caplog.set_level(logging.DEBUG)
    google.script.append(error_reply(400, "API key not valid. Please pass a valid API key."))
    with pytest.raises(ClaudeIntegrationError) as caught:
        client.plan_page("p", {})
    assert len(google.requests) == 1                        # a refusal that will not change is not retried
    assert "API key not valid" in str(caught.value) and "Gemini" in str(caught.value)
    assert KEY not in str(caught.value) and KEY not in caplog.text


def test_a_reply_cut_off_mid_json_is_asked_again_shorter(google, client):
    google.script.append(ok_reply('{"page_kind": "form", "answers": [{"ref"', finish="MAX_TOKENS"))
    assert client.plan_page("p", {})["page_kind"] == "other"
    assert "cut off" in google.requests[1]["body"]["contents"][0]["parts"][0]["text"]


def test_a_page_google_will_not_read_is_reported_not_retried(google, client):
    google.script.append((200, {"promptFeedback": {"blockReason": "SAFETY"}}))
    with pytest.raises(ClaudeIntegrationError, match="blocked"):
        client.plan_page("p", {})
    assert len(google.requests) == 1


def test_google_not_answering_at_all_is_retried_then_reported(slept, caplog):
    caplog.set_level(logging.DEBUG)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    unreachable = gemini.GeminiClient(make_config(f"http://127.0.0.1:{closed_port}/v1beta"))
    with pytest.raises(ClaudeIntegrationError):
        unreachable.plan_page("p", {})
    assert len(slept) >= 2                                  # it waited between the attempts
    assert KEY not in caplog.text


def test_the_key_is_in_no_log_line_on_a_good_call_either(google, client, caplog):
    caplog.set_level(logging.DEBUG)
    client.plan_page("p", {})
    assert KEY not in caplog.text


# --- the split: questions to Gemini, documents to Claude ------------------------------------------------

class Documents:
    """Stands in for Claude: records what it is asked."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append(name)
            return f"{name} by claude"
        return record


def test_questions_go_to_gemini_and_the_documents_stay_with_claude(google):
    documents = Documents()
    brain = gemini.GeminiBrain(make_config(google), documents=documents)
    assert brain.plan_page("p", {})["page_kind"] == "other"
    assert brain.tailor_resume("r", "j", "p") == "tailor_resume by claude"
    assert brain.generate_cover_letter("r", "j", "p") == "generate_cover_letter by claude"
    assert documents.calls == ["tailor_resume", "generate_cover_letter"]
    assert len(google.requests) == 1                        # the one page, and nothing of the resume


def test_every_model_call_is_sent_to_exactly_one_of_them():
    """A new method on ClaudeClient has to be classified: it must not go to the wrong company by default."""
    import inspect
    public = {name for name, value in vars(ClaudeClient).items()
              if inspect.isfunction(value) and not name.startswith("_")}
    assert public == gemini.ANSWERS | gemini.DOCUMENTS
    assert not gemini.ANSWERS & gemini.DOCUMENTS


def test_the_gemini_client_itself_will_not_write_a_document(google, client):
    for name in sorted(gemini.DOCUMENTS):
        with pytest.raises(ClaudeIntegrationError, match="Claude"):
            getattr(client, name)("anything")
    assert google.requests == []                            # nothing was sent


# --- switching it on ------------------------------------------------------------------------------------

def test_gemini_mode_builds_the_split_brain_whatever_agent_brain_says(google):
    import apply_flow
    job = SimpleNamespace(company="Praxis", title="Engineer")
    for brain_setting in ("session", "api"):
        brain = apply_flow.brain_for(make_config(google, agent_brain=brain_setting), job)
        assert isinstance(brain, gemini.GeminiBrain)


def test_the_other_modes_answer_with_their_own_provider_and_write_with_the_chosen_writers(google):
    import ai_choice
    import apply_flow
    import session_planner
    from claude_answers import ClaudeAnswerClient
    from openai_integration import OpenAIClient
    job = SimpleNamespace(company="Praxis", title="Engineer")
    profile = apply_flow.brain_for(make_config(google, form_answer_mode="profile"), job)
    assert isinstance(profile, ai_choice.Brain) and isinstance(profile._answers, ClaudeClient)
    claude = apply_flow.brain_for(make_config(google, form_answer_mode="claude", agent_brain="api"), job)
    assert isinstance(claude, ai_choice.Brain) and isinstance(claude._answers, ClaudeAnswerClient)
    openai = apply_flow.brain_for(make_config(google, form_answer_mode="openai", openai_api_key="sk-x"), job)
    assert isinstance(openai, ai_choice.Brain) and isinstance(openai._answers, OpenAIClient)
    assert isinstance(apply_flow.brain_for(make_config(google, form_answer_mode="claude", agent_brain="session"), job),
                      session_planner.SessionPlanner)
    assert isinstance(claude._documents, ai_choice.Writers)


@pytest.mark.parametrize("mode, brain, kind", [
    ("profile", "session", "profile"), ("gemini", "session", "gemini"), ("gemini", "api", "gemini"),
    ("claude", "session", "session"), ("claude", "api", "api"), ("openai", "session", "openai"),
])
def test_which_brain_a_configuration_means(mode, brain, kind):
    import apply_flow
    assert apply_flow.brain_kind(SimpleNamespace(form_answer_mode=mode, agent_brain=brain)) == kind


def test_gemini_mode_without_a_key_is_refused_up_front(google):
    import apply_flow
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        apply_flow.brain_for(make_config(google, gemini_api_key=""), SimpleNamespace(company="c", title="t"))


NEWER_KEY = "AQ." + "Ab8RN6" * 8 + "-_"                     # Google's newer key format: "AQ." and 50 more characters


def test_a_key_that_does_not_look_like_a_google_key_is_said_so_without_being_shown(google, caplog):
    caplog.set_level(logging.INFO)
    short = "x" * 35                                       # 4 short of a Google key, as one was once pasted
    problem = gemini.key_problem(short)
    assert problem and "39" in problem and "AIza" in problem and "AQ." in problem and short not in problem
    assert gemini.key_problem(KEY) is None
    assert gemini.key_problem("") and gemini.key_problem(f'"{KEY}"') and gemini.key_problem(f" {KEY}")
    gemini.GeminiBrain(make_config(google, gemini_api_key=short), documents=Documents())
    assert "AIza" in caplog.text and short not in caplog.text


def test_both_kinds_of_google_key_are_accepted(google, caplog):
    """The owner's key was the newer kind ("AQ." ...): the check I first wrote knew only "AIza" and would have
    called a working key wrong. It goes by what a key can look like, and only ever warns."""
    caplog.set_level(logging.INFO)
    assert gemini.key_problem(NEWER_KEY) is None
    assert gemini.key_problem("AQ.short") is not None                 # a prefix alone is not enough
    assert gemini.key_problem(NEWER_KEY[:-2] + " !") is not None      # characters a key does not have
    gemini.GeminiBrain(make_config(google, gemini_api_key=NEWER_KEY), documents=Documents())
    assert "GEMINI:" not in caplog.text


def test_a_model_google_has_retired_says_where_to_change_it(google, client):
    """gemini-2.5-flash, the first default, was 'no longer available to new users' -- a 404 that named no setting."""
    google.script.append(error_reply(404, "This model models/gemini-old is no longer available to new users.",
                                     "NOT_FOUND"))
    with pytest.raises(ClaudeIntegrationError) as caught:
        client.plan_page("p", {})
    assert len(google.requests) == 1
    assert "no longer available" in str(caught.value) and "GEMINI_MODEL" in str(caught.value)


def test_the_settings_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-elsewhere")
    monkeypatch.setenv("FORM_ANSWER_MODE", "gemini")
    cfg = config.AppConfig()
    assert (cfg.gemini_api_key, cfg.gemini_model, cfg.form_answer_mode) == (KEY, "gemini-elsewhere", "gemini")
    monkeypatch.delenv("GEMINI_MODEL")
    monkeypatch.delenv("GEMINI_BASE_URL", raising=False)
    fresh = config.AppConfig()
    assert fresh.gemini_model and fresh.gemini_base_url.startswith("https://generativelanguage.googleapis.com/")


def test_the_dropdown_matcher_asks_gemini_too_when_gemini_answers(google):
    """It builds its own client, apart from the brain: it must follow the same split."""
    from browser_automation import JobApplicationAssistant
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant._config = make_config(google, form_answer_mode="gemini")
    assert isinstance(assistant._claude_client(), gemini.GeminiClient)
    other = JobApplicationAssistant.__new__(JobApplicationAssistant)
    other._config = make_config(google, form_answer_mode="claude")
    assert type(other._claude_client()) is ClaudeClient
