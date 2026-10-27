"""The dashboard serves exactly these routes (code-quality audit, 26 October 2026).

web_ui.py is being split into modules and blueprints. A route that falls out of a move would break the page or the
agent's cockpit without any other test noticing, so the whole table is pinned. Adding a route means adding a line here.
"""
import web_ui

EXPECTED = [
    ("/", ("GET",)),
    ("/answers", ("GET", "POST")),
    ("/api/answers/update", ("POST",)),
    ("/api/application/<int:app_id>/answers", ("GET",)),
    ("/api/cockpit-state", ("GET",)),
    ("/api/cockpit/raise-browser", ("POST",)),
    ("/api/cockpit/resume", ("POST",)),
    ("/api/cockpit/stop", ("POST",)),
    ("/api/discovered/<int:job_id>/apply", ("POST",)),
    ("/api/discovered/<int:job_id>/dismiss", ("POST",)),
    ("/api/discovered/scan", ("POST",)),
    ("/api/handoff/resolve", ("POST",)),
    ("/api/webhook/command", ("POST",)),
    ("/api/webhook/telegram", ("POST",)),
    ("/application/<int:app_id>", ("GET",)),
    ("/application/<int:app_id>/status", ("POST",)),
    ("/apply", ("POST",)),
    ("/delete/<int:app_id>", ("POST",)),
    ("/document/<int:doc_id>", ("GET",)),
    ("/evidence", ("GET",)),
    ("/health", ("GET",)),
    ("/log", ("GET",)),
    ("/profile", ("GET", "POST")),
    ("/progress", ("GET",)),
    ("/reload-agent", ("POST",)),
    ("/restart", ("POST",)),
    ("/resume/<int:app_id>", ("POST",)),
    ("/runtime", ("GET",)),
    ("/settings", ("GET", "POST")),
    ("/settings/ai", ("POST",)),
    ("/setup", ("GET", "POST")),
    ("/signal/<path:signal_file>", ("POST",)),
    ("/static/<path:filename>", ("GET",)),
    ("/stop", ("POST",)),
    ("/stop-application/<int:app_id>", ("POST",)),
]


def served():
    return sorted((r.rule, tuple(sorted(r.methods - {"HEAD", "OPTIONS"}))) for r in web_ui.app.url_map.iter_rules())


def test_the_dashboard_serves_exactly_these_routes():
    got, want = served(), sorted((rule, tuple(m)) for rule, m in EXPECTED)
    assert got == want, {"missing": sorted(set(want) - set(got)), "unexpected": sorted(set(got) - set(want))}


def test_the_cockpit_api_is_its_own_blueprint():
    assert "cockpit_pages" in web_ui.app.blueprints
    assert web_ui.app.url_map.bind("localhost").match("/api/cockpit-state", method="GET")[0] == "cockpit_pages.api_cockpit_state"
