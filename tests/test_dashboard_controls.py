"""Every control on the Applications page does something real, can be reached and named by a person using a keyboard or a
screen reader, and shows employer text as text (f227).

Before 23 October the page offered a Pause button that could only answer "unavailable", a Resume that answered "resumed"
when nothing was running, an events route that crashed when a browser left, and an activity feed that put an employer
page's own words into the page as HTML. These tests hold the whole class: no control without a route and a handler, no
control without a name, no dynamic text built as markup, and the colour pairs the page uses stay readable.
"""
import re
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest

import profile_setup
import ui_shell
import web_ui
from test_workday_prompt_textbox import browser, page  # noqa: F401  (the shared browser fixtures)

JOB = {"id": 7, "company_name": "Acme", "job_title": "Network <b>Engineer</b>", "location": "Remote", "is_remote": 1,
       "url": "https://boards.example.com/acme/1", "match_score": 92, "ats_type": "greenhouse",
       "matched_skills": ["BGP", "Terraform"]}


@pytest.fixture
def served(monkeypatch):
    rows = [SimpleNamespace(id=i + 1, company=f"Company {i}", location="Austin, Texas", title="Engineer", status=s, notes="",
                            url=f"https://jobs.example.com/{i}", last_page_url="", updated_at=None, created_at=None,
                            dedup_key=f"k{i}")
            for i, s in enumerate(["prepared", "ready_to_apply", "needs_user_review", "submitted", "skipped", "form_filled"])]
    tracker = SimpleNamespace(list_all=lambda: rows, documents_for=lambda _i: [], answers_for=lambda _i: [],
                              events=lambda *_a, **_k: [])
    monkeypatch.setattr(web_ui, "get_tracker", lambda: tracker)
    monkeypatch.setattr(web_ui, "_current_runs", lambda: {})
    monkeypatch.setattr(web_ui, "_running_url", lambda: "https://jobs.example.com/5")
    monkeypatch.setattr(web_ui, "waiting_runs", lambda _d: [{"signal": "_signal_x.txt", "label": "Engineer at Company 5"}])
    monkeypatch.setattr(web_ui, "clear_waiting_files", lambda _p: None)
    monkeypatch.setattr(profile_setup, "needs_setup", lambda: False)
    import job_discovery
    monkeypatch.setattr(job_discovery.JobDiscoveryEngine, "get_discovered_jobs", lambda self, **k: [dict(JOB)])
    web_ui.app.config["TESTING"] = True
    return web_ui.app.test_client()


class Page(HTMLParser):
    """What a person (or a screen reader) meets: forms, links, controls, and the names they have."""

    def __init__(self):
        super().__init__()
        self.forms, self.links, self.controls, self._stack, self.labels = [], [], [], [], set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self.forms.append((a.get("action", ""), (a.get("method") or "get").upper()))
        if tag == "label" and a.get("for"):
            self.labels.add(a["for"])
        if tag in ("button", "a", "summary") or (tag == "input" and a.get("type") != "hidden") or tag in ("select", "textarea"):
            if tag == "a" and not a.get("href"):
                return
            entry = {"tag": tag, "a": a, "text": ""}
            self.controls.append(entry)
            if tag == "a":
                self.links.append(a["href"])
            self._stack.append(entry)
        elif tag == "span" and "sr-only" in (a.get("class") or ""):
            pass

    def handle_endtag(self, tag):
        if self._stack and self._stack[-1]["tag"] == tag:
            self._stack.pop()

    def handle_data(self, data):
        for entry in self._stack:
            entry["text"] += data


def parse(html):
    p = Page()
    p.feed(html)
    return p


def routes(path, method):
    from werkzeug.exceptions import HTTPException
    adapter = web_ui.app.url_map.bind("localhost")
    try:
        adapter.match(path.split("?")[0], method=method)
        return True
    except HTTPException:
        return False


def test_every_form_and_link_on_the_page_leads_to_a_real_route(served):
    page_ = parse(served.get("/?show=20").get_data(as_text=True))
    assert page_.forms
    missing = [(action, method) for action, method in page_.forms if not routes(action, method)]
    assert not missing, f"forms with no route behind them: {missing}"
    internal = [h for h in page_.links if h.startswith("/") and not h.startswith("/static")]
    dead = [h for h in internal if not routes(h, "GET")]
    assert not dead, f"links with no route behind them: {dead}"


def test_every_control_has_a_name_a_person_can_hear(served):
    page_ = parse(served.get("/?show=20").get_data(as_text=True))
    nameless = []
    for c in page_.controls:
        a = c["a"]
        if a.get("aria-label") or a.get("aria-labelledby") or c["text"].strip() or a.get("title") and c["tag"] != "input":
            continue
        if c["tag"] in ("input", "select", "textarea") and a.get("id") in page_.labels:
            continue
        nameless.append((c["tag"], a))
    assert not nameless, nameless[:5]


def test_there_is_no_pause_control_and_no_dead_button(served):
    html = served.get("/").get_data(as_text=True)
    assert not re.search(r">\s*(?:⏸\s*)?Pause\s*<", html)
    # every card button names an action the script handles
    card = (web_ui.BASE_DIR / "templates" / "cockpit_card.html").read_text(encoding="utf-8")
    script = (web_ui.BASE_DIR / "static" / "cockpit.js").read_text(encoding="utf-8")
    for action in set(re.findall(r'data-action="([\w-]+)"', card)):
        assert f"'{action}'" in script, f"data-action={action} has no handler in cockpit.js"
    for tab in set(re.findall(r'data-tab="([\w-]+)"', card)):
        assert f"'{tab}'" in script
    assert not [r for r in web_ui.app.url_map.iter_rules() if r.rule in ("/api/cockpit/pause", "/api/cockpit/takeover", "/api/events/stream")]


def test_only_one_continue_while_the_needs_you_strip_shows_it(served):
    html = served.get("/").get_data(as_text=True)
    assert 'id="btn-continue"' not in html and "Needs your action" in html


def test_the_activity_feed_never_runs_employer_text_as_markup(page):
    """The feed shows what an employer's page said. A page that says <img onerror=...> must be shown, not run."""
    from flask import render_template
    with web_ui.app.test_request_context("/"):
        markup = render_template("cockpit_card.html", is_running=True, signals=[])
    page.set_content(markup)
    page.evaluate("""state => { window.fetch = async () => ({ok: true, json: async () => state}); }""", {
        "is_running": True, "is_waiting": False, "stage": "Fill form", "stage_step": 3, "loop_status": "normal",
        "job_title": "<img src=x onerror=window.__ran=1>", "employer": "<script>window.__ran=2</script>",
        "current_action": "<b onmouseover=window.__ran=3>x</b>", "last_verified_action": "ok",
        "active_handoff": {"category": "CAPTCHA", "employer": "<i>Acme</i>", "reason": "<img src=y onerror=window.__ran=4>",
                           "handoff_id": "'\"><img src=z onerror=window.__ran=5>", "status": "ACTIVE"},
        "recent_events": [{"timestamp": "2026-10-23T05:00:00Z", "event": "WARNING_RETRY", "is_verified": True,
                           "display_message": "<img src=q onerror=window.__ran=6>", "evidence": "<script>window.__ran=7</script>"}],
        "recent_answers": [{"id": 1, "question_text": "'<img src=w onerror=window.__ran=8>", "final_answer": "\"><script>window.__ran=9</script>",
                            "source": "<u>PROFILE</u>", "status": "ACCEPTED", "previous_value": "<b>old</b>", "correction_reason": "x"}],
    })
    page.add_script_tag(content=(web_ui.BASE_DIR / "static" / "cockpit.js").read_text(encoding="utf-8"))
    page.evaluate("window.Cockpit.startPolling()")
    page.wait_for_function("document.getElementById('cockpit-job-title').textContent.includes('<img')")
    page.wait_for_timeout(300)
    assert page.evaluate("window.__ran === undefined")
    assert "<img src=q" in page.inner_text("#cockpit-timeline-list")
    page.locator("#tab-btn-ledger").click()
    assert "<script>" in page.inner_text("#cockpit-ledger-body")


@pytest.mark.parametrize("state, visible, hidden", [
    ({"is_running": True, "is_waiting": True, "stage": "Waiting for you", "loop_status": "normal"}, ["btn-continue", "btn-raise", "btn-stop"], []),
    ({"is_running": True, "is_waiting": False, "stage": "Fill form", "loop_status": "normal"}, ["btn-raise", "btn-stop"], ["btn-continue"]),
    ({"is_running": False, "is_waiting": False, "stage": "Idle", "loop_status": "normal"}, [], ["btn-continue", "btn-raise", "btn-stop"]),
])
def test_a_button_shows_only_when_it_can_do_something(page, state, visible, hidden):
    """Resume used to answer "resumed" with nothing running; Stop and Show browser had nothing to act on."""
    from flask import render_template
    with web_ui.app.test_request_context("/"):
        markup = render_template("cockpit_card.html", is_running=state["is_running"], signals=[])
    page.set_content(f"<style>{ui_shell.BASE_CSS}</style><style>{(web_ui.BASE_DIR / 'static' / 'cockpit.css').read_text(encoding='utf-8')}</style>" + markup)
    page.evaluate("state => { window.fetch = async () => ({ok: true, json: async () => state}); }", state)
    page.add_script_tag(content=(web_ui.BASE_DIR / "static" / "cockpit.js").read_text(encoding="utf-8"))
    page.evaluate("window.Cockpit.startPolling()")
    page.wait_for_timeout(300)
    for name in visible:
        assert page.locator(f"#{name}").is_visible(), name
    for name in hidden:
        assert not page.locator(f"#{name}").is_visible(), name


def test_a_form_that_asks_to_be_confirmed_waits_for_the_owner(page):
    sent = []
    page.route("http://owner.test/**", lambda route: (sent.append(route.request.url), route.fulfill(status=200, body="ok")))
    page.goto("http://owner.test/start")
    page.set_content('<form method="post" action="http://owner.test/delete/1" data-confirm="Delete it?" data-danger="1">'
                     '<button>Delete</button></form>')
    page.add_script_tag(content=(web_ui.BASE_DIR / "static" / "cockpit.js").read_text(encoding="utf-8"))
    page.click("text=Delete")
    page.wait_for_selector("dialog[open]")
    page.wait_for_timeout(300)
    assert [u for u in sent if "/delete/" in u] == []              # asked first, nothing sent yet
    page.get_by_role("button", name="Cancel").click()
    page.wait_for_timeout(300)
    assert [u for u in sent if "/delete/" in u] == [] and page.locator("dialog").count() == 0
    page.click("text=Delete")
    page.get_by_role("button", name="Yes").click()
    page.wait_for_timeout(600)
    assert [u for u in sent if "/delete/" in u] == ["http://owner.test/delete/1"]


# ---- colours: every text and background pair the page relies on stays readable (WCAG AA, 4.5:1) ----------------------------
def _hex(value):
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _lum(rgb):
    def f(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (f(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = _lum(_hex(a)), _lum(_hex(b))
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def tokens(block):
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{3,6})\b", block))


def _themes():
    css = ui_shell.BASE_CSS
    light = tokens(css[css.index(":root"):css.index("@media (prefers-color-scheme: dark)")])
    dark_block = css[css.index("@media (prefers-color-scheme: dark)"):css.index("* { box-sizing")]
    return {"light": light, "dark": {**light, **tokens(dark_block)}}


PAIRS = [("ink", "bg"), ("ink", "surface"), ("ink-2", "surface"), ("ink-2", "surface-2"), ("muted", "surface"),
         ("muted", "bg"), ("accent-ink", "accent"), ("ok-ink", "ok"), ("accent", "surface"), ("accent", "accent-soft"),
         ("ok", "ok-soft"), ("warn", "warn-soft"), ("bad", "bad-soft"), ("info", "info-soft"), ("violet", "violet-soft"),
         ("ink", "warn-soft"), ("ink-2", "warn-soft"), ("ink-2", "bad-soft"), ("bad", "surface"), ("ok", "surface"),
         ("muted", "grey-soft")]


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_colours_the_page_uses_are_readable(theme):
    t = _themes()[theme]
    weak = [(fg, bg, round(contrast(t[fg], t[bg]), 2)) for fg, bg in PAIRS if contrast(t[fg], t[bg]) < 4.5]
    assert not weak, f"{theme}: {weak}"


def test_the_start_button_gradient_keeps_white_text_readable_at_every_stop():
    css = (web_ui.BASE_DIR / "static" / "cockpit.css").read_text(encoding="utf-8")
    stops = re.findall(r"--grad:[^;]*", css)[0]
    colours = re.findall(r"#[0-9a-fA-F]{6}", stops)
    assert colours and all(contrast("#ffffff", c) >= 4.5 for c in colours), [(c, round(contrast("#ffffff", c), 2)) for c in colours]


# ---- a run is its own process: the card must show its progress, not an empty feed -----------------------------------------------
def test_the_card_shows_a_running_applications_own_progress_and_how_long_since_it_moved(monkeypatch, tmp_path):
    """The dashboard's in-memory events never hold a run's events (the run is another process), so a working run showed
    'Agent processing form' and an empty feed for as long as it ran. It reads the run's own log now."""
    import json
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    logs = tmp_path / "logs"
    logs.mkdir()
    rows = [("RUNTIME", 300), ("APPLY_ENTRY_NAVIGATED", 280), ("AUTOFILL", 200), ("FIELD_ACTION", 190), ("RUNTIME", 185),
            ("AI_ANSWERED_QUESTION", 130)]
    (logs / "run_20261023_000000.jsonl").write_text("\n".join(
        json.dumps({"time": (now - timedelta(seconds=age)).isoformat(), "level": "INFO", "logger": "runtime", "event": name,
                    "message": f"{name}: private details omitted"}) for name, age in rows), encoding="utf-8")
    url = "https://employer.example/job"
    monkeypatch.setattr(web_ui, "BASE_DIR", tmp_path)
    monkeypatch.setattr(web_ui, "_running_url", lambda: url)
    monkeypatch.setattr(web_ui, "waiting_runs", lambda _d: [])
    monkeypatch.setattr(web_ui.handoff, "get_active_handoff", lambda *a, **k: None)
    monkeypatch.setattr(web_ui.RuntimeEvent, "get_recent", classmethod(lambda cls, limit=40, **k: []))
    monkeypatch.setattr(web_ui, "get_tracker", lambda: SimpleNamespace(list_all=lambda: [], find_by_url=lambda u: None))
    monkeypatch.setattr(web_ui, "get_answers_for_application", lambda key: [])
    monkeypatch.setitem(web_ui._RUNS, url, {"state": "running", "started": now - timedelta(minutes=6), "log": "", "pid": None})
    web_ui.app.config["TESTING"] = True
    state = web_ui.app.test_client().get("/api/cockpit-state").get_json()
    words = [e["display_message"] for e in state["recent_events"]]
    assert words[0] == "Had the AI answer a question" and "Opened the application form" in words
    assert not any("private" in w.lower() or w == "Runtime" for w in words)          # quiet events and values never shown
    assert 100 < state["last_activity_seconds"] < 160
    assert state["stage"] == "Form Fill" and state["stage_step"] == 3
    web_ui._RUNS.pop(url, None)


def test_a_quiet_run_says_how_long_it_has_been_quiet(page):
    from flask import render_template
    with web_ui.app.test_request_context("/"):
        markup = render_template("cockpit_card.html", is_running=True, signals=[])
    page.set_content(markup)
    page.evaluate("state => { window.fetch = async () => ({ok: true, json: async () => state}); }", {
        "is_running": True, "is_waiting": False, "stage": "Form Fill", "stage_step": 3, "loop_status": "normal",
        "current_action": "Had the AI answer a question", "last_activity_seconds": 250, "recent_events": [], "recent_answers": []})
    page.add_script_tag(content=(web_ui.BASE_DIR / "static" / "cockpit.js").read_text(encoding="utf-8"))
    page.evaluate("window.Cockpit.startPolling()")
    page.wait_for_function("document.getElementById('cockpit-current-action').textContent.includes('last step')")
    text = page.inner_text("#cockpit-current-action")
    assert "4 min ago" in text and "AI model" in text
