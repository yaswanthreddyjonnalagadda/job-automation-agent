"""The owner makes both AI choices on Settings, and the next application uses them without a restart."""
import re

import pytest

import ai_models
import web_ui


@pytest.fixture
def home(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("GEMINI_API_KEY=AIza" + "x" * 35 + "\nOPENAI_API_KEY=sk-test\nOTHER=kept\n",
                                   encoding="utf-8")
    monkeypatch.setattr(web_ui, "BASE_DIR", tmp_path)
    monkeypatch.setattr(ai_models, "FETCH", {
        "gemini": lambda cfg: ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-embedding-2"],
        "claude": lambda cfg: ["claude-sonnet-5"],
        "openai": lambda cfg: ["gpt-5", "gpt-5-mini"]})
    web_ui.app.config["TESTING"] = True
    return tmp_path


def client_and_token():
    client = web_ui.app.test_client()
    page = client.get("/settings").get_data(as_text=True)
    return client, page, re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)


def env(home):
    return (home / ".env").read_text(encoding="utf-8")


def test_the_page_offers_each_keys_models_and_both_choices(home):
    _, page, _ = client_and_token()
    assert "Answering the forms" in page and "Writing the resume and cover letter" in page
    assert 'type="checkbox" name="gemini_page_' in page and 'value="gemini-3.5-flash-lite"' in page
    assert '<option value="openai:gpt-5-mini"' in page and 'name="openai_quick_sent"' in page


def test_saving_writes_both_choices_and_keeps_everything_else(home):
    client, _, token = client_and_token()
    reply = client.post("/settings/ai", data={
        "csrf_token": token, "form_answer_mode": "openai",
        "openai_page_sent": "1", "openai_page_1": "gpt-5", "openai_page_3": "gpt-5-mini",
        "openai_quick_sent": "1", "openai_quick_2": "gpt-5-mini",
        "resume_writer": "gemini:gemini-3.8-flash", "resume_writer_fallback": "openai:gpt-5"})
    assert reply.status_code == 302
    saved = env(home)
    for line in ("FORM_ANSWER_MODE=openai", "OPENAI_PAGE_MODELS=gpt-5,gpt-5-mini", "OPENAI_QUICK_MODELS=gpt-5-mini",
                 "RESUME_WRITER=gemini:gemini-3.8-flash", "RESUME_WRITER_FALLBACK=openai:gpt-5", "OTHER=kept"):
        assert line in saved
    assert "GEMINI_PAGE_MODELS" not in saved          # a provider's lists not sent keep their saved order


def test_the_saved_choices_are_shown_again(home):
    client, _, token = client_and_token()
    client.post("/settings/ai", data={"csrf_token": token, "form_answer_mode": "openai", "openai_page_sent": "1",
                                      "openai_page_1": "gpt-5-mini", "resume_writer": "openai:gpt-5"})
    page = client.get("/settings").get_data(as_text=True)
    assert re.search(r'value="openai" checked', page)
    assert re.search(r'name="openai_page_1" value="gpt-5-mini" checked', page)      # ticked, and first
    assert re.search(r'name="openai_page_2" value="gpt-5"\s*>', page)                  # offered, not ticked
    assert re.search(r'<option value="openai:gpt-5" selected', page)


@pytest.mark.parametrize("field, value", [("resume_writer", "someone:model"), ("openai_page_1", "gpt 5\nX=1")])
def test_a_value_the_page_did_not_offer_is_refused(home, field, value):
    client, _, token = client_and_token()
    before = env(home)
    client.post("/settings/ai", data={"csrf_token": token, "openai_page_sent": "1", field: value})
    assert env(home) == before


def test_the_next_run_reads_env_as_it_is_now(home, monkeypatch):
    monkeypatch.setenv("FORM_ANSWER_MODE", "gemini")                   # what the server read when it started
    (home / ".env").write_text("FORM_ANSWER_MODE=openai\n", encoding="utf-8")
    assert web_ui.run_environment()["FORM_ANSWER_MODE"] == "openai"


def test_only_models_that_write_text_are_listed(monkeypatch):
    class Reply:
        def raise_for_status(self):
            pass

        def json(self):
            names = ["gemini-3.8-flash", "gemini-embedding-2", "gemini-3.8-flash-tts", "gemma-4-31b-it",
                     "gemini-flash-latest", "veo-3", "gemini-3.1-flash-image"]
            return {"models": [{"name": f"models/{n}", "supportedGenerationMethods": ["generateContent"]}
                               for n in names]}
    monkeypatch.setattr(ai_models.requests, "get", lambda *a, **k: Reply())
    from types import SimpleNamespace
    assert ai_models._google(SimpleNamespace(gemini_api_key="k", gemini_base_url="https://g.example")) == [
        "gemini-3.8-flash", "gemma-4-31b-it"]


def test_a_list_with_nothing_ticked_is_refused_not_saved(home):
    client, _, token = client_and_token()
    before = env(home)
    reply = client.post("/settings/ai", data={"csrf_token": token, "gemini_quick_sent": "1"})
    assert env(home) == before and "error=" in reply.headers["Location"]
