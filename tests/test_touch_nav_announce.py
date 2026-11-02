"""Controls are big enough to touch, the nav copes with a narrow screen, and changes are announced (PR C of the
1 November 2026 UI review). Classes: every interactive control's height comes from a rule and is at least 40px;
status the sighted owner sees change is also put in a live region."""
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import profile_setup
import ui_shell
import web_ui

ROOT = Path(__file__).resolve().parent.parent
COCKPIT_CSS = (ROOT / "static" / "cockpit.css").read_text(encoding="utf-8")
COCKPIT_JS = (ROOT / "static" / "cockpit.js").read_text(encoding="utf-8")
ALL_CSS = ui_shell.BASE_CSS + "\n" + COCKPIT_CSS
MIN_TOUCH = 40

CONTROL = re.compile(r"(^|[\s,>])(button|summary|\.button|\.dash-chip|\.filter-chip|button\.linklike|\.btn-control|\.btn-solve)"
                     r"(?![\w-])")


def rules(css):
    for selectors, body in re.findall(r"([^{}@]+)\{([^{}]*)\}", css):
        yield [s.strip() for s in selectors.split(",") if s.strip()], body


def test_no_control_is_given_a_height_under_the_touch_minimum():
    low = []
    for selectors, body in rules(ALL_CSS):
        height = re.search(r"(?<![\w-])min-height:\s*(\d+)px", body)
        if height and int(height.group(1)) < MIN_TOUCH:
            low += [(s, height.group(1)) for s in selectors if CONTROL.search(s) and ".stepper" not in s]
    assert not low, low


@pytest.mark.parametrize("selector", ["button, .button", "details.menu > summary", ".dash-chip", ".filter-chip",
                                      "details.menu .menu-items button"])
def test_each_kind_of_control_states_a_minimum_height(selector):
    for selectors, body in rules(ALL_CSS):
        if all(part.strip() in selectors for part in selector.split(",")) and re.search(r"(?<![\w-])min-height:\s*(\d+)px", body):
            assert int(re.search(r"(?<![\w-])min-height:\s*(\d+)px", body).group(1)) >= MIN_TOUCH
            return
    pytest.fail(f"no rule gives {selector!r} a min-height")


def test_the_nav_scrolls_sideways_and_fades_at_the_edge_on_a_narrow_screen():
    css = ui_shell.BASE_CSS
    assert re.search(r"\.nav\s*\{[^}]*overflow-x:\s*auto", css)
    narrow = css[css.index("@media (max-width:760px)"):]
    assert re.search(r"\.nav\s*\{[^}]*mask-image:\s*linear-gradient", narrow)


# -- announcements -----------------------------------------------------------------------------------------------

def test_a_status_change_is_put_in_a_live_region():
    assert "sr-announce" in COCKPIT_JS and re.search(r"aria-live['\"]?\s*[:,]\s*['\"]polite", COCKPIT_JS)
    assert re.search(r"function announce\(", COCKPIT_JS)
    assert re.search(r"announce\(`?[^)]*(Waiting for you|Needs you|status)", COCKPIT_JS)


def test_the_first_status_read_is_not_announced():
    assert re.search(r"announced\s*===\s*null", COCKPIT_JS)


def _client(monkeypatch, statuses):
    rows = [SimpleNamespace(id=i + 1, company=f"C{i}", location="", title="Engineer", status=s, notes="",
                            url=f"https://jobs.example.com/{i}", last_page_url="", updated_at=None, created_at=None,
                            dedup_key=f"k{i}") for i, s in enumerate(statuses)]
    monkeypatch.setattr(web_ui, "get_tracker", lambda: SimpleNamespace(
        list_all=lambda: rows, documents_for=lambda _i: [], answers_for=lambda _i: [], events=lambda *_a, **_k: []))
    monkeypatch.setattr(web_ui, "_current_runs", lambda: {})
    monkeypatch.setattr(web_ui, "waiting_runs", lambda _d: [])
    monkeypatch.setattr(profile_setup, "needs_setup", lambda: False)
    web_ui.app.config["TESTING"] = True
    return web_ui.app.test_client()


def test_the_number_needing_the_owner_is_in_a_live_region(monkeypatch):
    html = _client(monkeypatch, ["needs_user_review", "ready_to_submit", "submitted"]).get("/").get_data(as_text=True)
    region = re.search(r'<p[^>]*id="needs-live"[^>]*>(.*?)</p>', html, re.S)
    assert region and 'aria-live="polite"' in region.group(0) and "2 applications need you" in region.group(1)


def test_the_live_region_says_nothing_needs_you_when_that_is_true(monkeypatch):
    html = _client(monkeypatch, ["submitted"]).get("/").get_data(as_text=True)
    assert "Nothing needs you" in re.search(r'id="needs-live"[^>]*>(.*?)</p>', html, re.S).group(1)
