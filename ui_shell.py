"""The dashboard's look, in one place: design tokens, the page head (favicon, viewport) and the app header.

Every page (applications, progress, profile, saved answers, settings, an application's details, first-run setup)
is built from these, so the navigation, colours, spacing and dark mode are the same everywhere. Nothing is
loaded from the internet: the dashboard works offline.
"""
from __future__ import annotations

# A briefcase with a tick, drawn inline: the tab icon and the header mark.
LOGO_SVG = ('<svg viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'
            '<rect x="2" y="8" width="28" height="20" rx="5" fill="#2f5bd3"/>'
            '<path d="M11 8V6.5A2.5 2.5 0 0 1 13.5 4h5A2.5 2.5 0 0 1 21 6.5V8" fill="none" stroke="#2f5bd3" '
            'stroke-width="2.4"/><path d="M10 18.5l4 4 8-8" fill="none" stroke="#fff" stroke-width="3" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg>')
FAVICON = "data:image/svg+xml," + LOGO_SVG.replace("#", "%23").replace('"', "'").replace("<", "%3C").replace(">", "%3E")

BASE_CSS = """
:root {
  --bg:#f5f7fb; --surface:#ffffff; --surface-2:#f8fafd; --ink:#101828; --ink-2:#344054; --muted:#667085;
  --line:#e4e7ec; --line-2:#eef1f6; --accent:#2f5bd3; --accent-ink:#ffffff; --accent-soft:#eaf0ff;
  --ok:#067647; --ok-soft:#dcfae6; --warn:#b54708; --warn-soft:#fef0c7; --bad:#b42318; --bad-soft:#fee4e2;
  --info:#175cd3; --info-soft:#d1e9ff; --violet:#6941c6; --violet-soft:#f4ebff; --grey-soft:#f2f4f7;
  --radius:12px; --radius-sm:8px; --shadow:0 1px 2px rgba(16,24,40,.05), 0 1px 3px rgba(16,24,40,.06);
  --shadow-lg:0 12px 24px -6px rgba(16,24,40,.12), 0 4px 8px -4px rgba(16,24,40,.06);
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg:#0c111d; --surface:#161b26; --surface-2:#1b2230; --ink:#f5f5f6; --ink-2:#cecfd2; --muted:#94969c;
    --line:#2a3140; --line-2:#222938; --accent:#5b83f0; --accent-soft:#1d2a4d; --ok:#47cd89; --ok-soft:#0b2a1c;
    --warn:#fdb022; --warn-soft:#3a2a0a; --bad:#f97066; --bad-soft:#3b1414; --info:#53b1fd; --info-soft:#0e2940;
    --violet:#b692f6; --violet-soft:#2a1d47; --grey-soft:#222938; --shadow:none; --shadow-lg:0 12px 24px rgba(0,0,0,.4);
    color-scheme: dark;
  }
}
* { box-sizing:border-box; }
html { -webkit-text-size-adjust:100%; }
body { margin:0; background:var(--bg); color:var(--ink);
       font:14px/1.55 "Inter","Segoe UI Variable Text","Segoe UI",-apple-system,Roboto,Helvetica,Arial,sans-serif;
       -webkit-font-smoothing:antialiased; }
a { color:var(--accent); text-decoration:none; }
a:hover { text-decoration:underline; }
code { background:var(--grey-soft); padding:1px 6px; border-radius:6px; font-size:12px; }

/* --- app header ---------------------------------------------------------------------------- */
.topbar { position:sticky; top:0; z-index:20; background:color-mix(in srgb, var(--surface) 88%, transparent);
          backdrop-filter:saturate(1.4) blur(10px); border-bottom:1px solid var(--line); }
.topbar-inner { max-width:1200px; margin:0 auto; padding:0 24px; height:60px; display:flex; align-items:center; gap:28px; }
.brand { display:flex; align-items:center; gap:10px; font-weight:700; font-size:15px; color:var(--ink); letter-spacing:-.01em; }
.brand:hover { text-decoration:none; }
.brand svg { width:28px; height:28px; }
.brand small { display:block; font-weight:500; font-size:11px; color:var(--muted); letter-spacing:0; }
.nav { display:flex; gap:4px; height:100%; overflow-x:auto; scrollbar-width:none; }
.nav a { display:flex; align-items:center; padding:0 12px; color:var(--muted); font-weight:500; white-space:nowrap;
         border-bottom:2px solid transparent; margin-bottom:-1px; }
.nav a:hover { color:var(--ink); text-decoration:none; }
.nav a.active { color:var(--ink); border-bottom-color:var(--accent); font-weight:600; }

/* --- page ------------------------------------------------------------------------------------ */
.wrap { max-width:1200px; margin:0 auto; padding:28px 24px 80px; }
.page-head { display:flex; align-items:flex-end; justify-content:space-between; gap:16px; flex-wrap:wrap; margin-bottom:20px; }
h1 { font-size:24px; line-height:1.25; letter-spacing:-.02em; margin:0; }
h2 { font-size:15px; margin:32px 0 12px; font-weight:650; color:var(--ink); letter-spacing:-.005em; }
h2 .count { color:var(--muted); font-weight:500; margin-left:6px; }
.sub { color:var(--muted); margin:6px 0 0; max-width:62ch; }
.muted { color:var(--muted); }
.card { background:var(--surface); border:1px solid var(--line); border-radius:var(--radius);
        padding:18px; margin-bottom:16px; box-shadow:var(--shadow); }
.card.flush { padding:0; }   /* not overflow:hidden: a row's menu must be able to leave the card */
.card.flush > :first-child, .card.flush > table:first-child th:first-child { border-top-left-radius:var(--radius); }
.card.flush > :first-child, .card.flush > table:first-child th:last-child { border-top-right-radius:var(--radius); }
.card.flush > :last-child { border-bottom-left-radius:var(--radius); border-bottom-right-radius:var(--radius); }
.card h3 { margin:0 0 4px; font-size:15px; }
.section-title { font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:.07em; color:var(--muted); margin:22px 0 4px; }
.hint { color:var(--muted); font-size:13px; margin:4px 0 0; }
.err { background:var(--bad-soft); color:var(--bad); padding:12px 14px; border-radius:var(--radius-sm);
       margin-bottom:16px; border:1px solid color-mix(in srgb, var(--bad) 25%, transparent); }
.ok { background:var(--ok-soft); color:var(--ok); padding:12px 14px; border-radius:var(--radius-sm); margin-bottom:16px; }

/* --- stat tiles -------------------------------------------------------------------------------- */
.stats { display:grid; grid-template-columns:repeat(auto-fit, minmax(170px, 1fr)); gap:12px; margin-bottom:20px; }
.stat { position:relative; display:block; background:var(--surface); border:1px solid var(--line); border-radius:var(--radius);
        padding:14px 16px 14px 18px; box-shadow:var(--shadow); color:var(--ink); overflow:hidden; }
.stat::before { content:""; position:absolute; left:0; top:0; bottom:0; width:4px; background:var(--tone, var(--line)); }
a.stat:hover { text-decoration:none; box-shadow:var(--shadow-lg); }
.stat b { display:block; font-size:28px; line-height:1.15; letter-spacing:-.02em; font-variant-numeric:tabular-nums; }
.stat span { color:var(--muted); font-size:13px; font-weight:500; }
.stat.tone-ok { --tone:var(--ok); }
.stat.tone-warn { --tone:var(--warn); }
.stat.tone-warn.hot { background:var(--warn-soft); border-color:color-mix(in srgb, var(--warn) 35%, transparent); }
.stat.tone-warn.hot b { color:var(--warn); }
.stat.tone-violet { --tone:var(--violet); }
.stat.tone-grey { --tone:var(--muted); }
.stat.tone-info { --tone:var(--info); }

/* --- forms and buttons ---------------------------------------------------------------------- */
button, .button { display:inline-flex; align-items:center; justify-content:center; gap:6px; background:var(--accent);
         color:var(--accent-ink); border:1px solid transparent; border-radius:var(--radius-sm); padding:9px 16px;
         font:inherit; font-weight:600; cursor:pointer; line-height:1.3; white-space:nowrap; }
button:hover { filter:brightness(1.07); }
button:focus-visible, a:focus-visible, input:focus-visible, select:focus-visible, summary:focus-visible
  { outline:3px solid color-mix(in srgb, var(--accent) 40%, transparent); outline-offset:2px; }
button.ghost { background:var(--surface); color:var(--ink-2); border-color:var(--line); font-weight:500; padding:6px 12px; }
button.ghost:hover { background:var(--surface-2); filter:none; }
button.small { padding:5px 10px; font-size:13px; }
button.danger { color:var(--bad); }
button.success { color:var(--ok); }
input[type=text], input[type=url], input[type=email], input[type=number], input[type=password], input[type=search],
select, textarea { width:100%; padding:10px 12px; border:1px solid var(--line); border-radius:var(--radius-sm);
  background:var(--surface); color:var(--ink); font:inherit; }
input:focus, select:focus, textarea:focus { outline:none; border-color:var(--accent);
  box-shadow:0 0 0 4px color-mix(in srgb, var(--accent) 18%, transparent); }
form.apply { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
form.apply input[type=url] { flex:1; min-width:260px; padding:12px 14px; font-size:15px; }
form.apply button { padding:12px 22px; font-size:15px; }
.apply-card { background:linear-gradient(135deg, var(--accent-soft), var(--surface) 60%); }
.apply-card label { display:block; font-weight:600; margin-bottom:8px; }

/* --- status pills ------------------------------------------------------------------------------- */
.pill { display:inline-flex; align-items:center; gap:6px; padding:3px 10px; border-radius:99px; font-size:12px;
        font-weight:600; white-space:nowrap; background:var(--grey-soft); color:var(--ink-2); }
.pill::before { content:""; width:6px; height:6px; border-radius:50%; background:currentColor; opacity:.8; }
.pill.submitted { background:var(--ok-soft); color:var(--ok); }
.pill.ready_to_submit { background:var(--info-soft); color:var(--info); }
.pill.needs_user_review, .pill.blocked_validation_loop { background:var(--warn-soft); color:var(--warn); }
.pill.form_filled, .pill.prepared { background:var(--accent-soft); color:var(--accent); }
.pill.interviewing, .pill.offer { background:var(--violet-soft); color:var(--violet); }
.pill.rejected { background:var(--bad-soft); color:var(--bad); }
.pill.skipped, .pill.disqualified_policy_mismatch { background:var(--grey-soft); color:var(--muted); }

/* --- tables --------------------------------------------------------------------------------------- */
table { width:100%; border-collapse:collapse; }
th, td { text-align:left; padding:12px 16px; border-bottom:1px solid var(--line-2); vertical-align:middle; }
th { color:var(--muted); font-weight:600; font-size:12px; white-space:nowrap; background:var(--surface-2);
     border-bottom:1px solid var(--line); }
tbody tr:hover td { background:var(--surface-2); }
tr:last-child td { border-bottom:0; }
table.fixed { table-layout:fixed; }
.truncate { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.clamp-2 { display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
td.company strong { display:block; font-weight:600; }
td.company .loc { display:block; color:var(--muted); font-size:12.5px; }
td.when, th.when { white-space:nowrap; color:var(--muted); font-variant-numeric:tabular-nums; }
td.actions, th.actions { text-align:right; white-space:nowrap; }
td.num, th.num { text-align:right; white-space:nowrap; }
td.label, th.label { width:150px; white-space:nowrap; color:var(--muted); font-size:12.5px; font-weight:600; }
tr.note-row td { padding:0 16px 12px; color:var(--muted); font-size:13px; border-bottom:1px solid var(--line-2); }
tr.note-row td::before { content:"↳ "; }
.row-actions { display:inline-flex; gap:6px; align-items:center; justify-content:flex-end; }
.row-actions form { margin:0; display:inline-flex; }

/* --- an overflow menu for row actions -------------------------------------------------------------- */
details.menu { position:relative; display:inline-block; }
details.menu > summary { list-style:none; cursor:pointer; border:1px solid var(--line); border-radius:var(--radius-sm);
  padding:5px 10px; background:var(--surface); color:var(--ink-2); font-weight:700; line-height:1.2; }
details.menu > summary::-webkit-details-marker { display:none; }
details.menu[open] > summary { background:var(--surface-2); }
details.menu .menu-items { position:absolute; right:0; top:calc(100% + 6px); z-index:30; min-width:200px;
  background:var(--surface); border:1px solid var(--line); border-radius:var(--radius-sm); box-shadow:var(--shadow-lg);
  padding:6px; display:flex; flex-direction:column; gap:2px; }
details.menu .menu-items form { margin:0; }
details.menu .menu-items button { width:100%; justify-content:flex-start; background:transparent; color:var(--ink-2);
  border:0; padding:8px 10px; font-weight:500; border-radius:6px; }
details.menu .menu-items button:hover { background:var(--surface-2); }
details.menu .menu-items button.danger { color:var(--bad); }
tr.app:nth-last-child(-n+2):not(:nth-child(-n+3)) details.menu .menu-items { top:auto; bottom:calc(100% + 6px); }
details.menu .menu-items hr { border:0; border-top:1px solid var(--line-2); margin:4px 0; }

/* --- toolbar: filters and search ------------------------------------------------------------------- */
.toolbar { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; padding:12px 16px;
           border-bottom:1px solid var(--line); background:var(--surface); }
.chips { display:flex; gap:6px; flex-wrap:wrap; }
.chip { border:1px solid var(--line); background:var(--surface); color:var(--ink-2); padding:5px 12px; border-radius:99px;
        font-size:13px; font-weight:500; }
.chip:hover { background:var(--surface-2); filter:none; }
.chip[aria-pressed="true"] { background:var(--ink); color:var(--surface); border-color:var(--ink); }
.chip .n { opacity:.65; margin-left:4px; font-variant-numeric:tabular-nums; }
.toolbar input[type=search] { width:240px; padding:7px 12px; }
.more { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:12px 16px;
        border-top:1px solid var(--line); background:var(--surface-2); }

/* --- attention panel, runs ------------------------------------------------------------------------ */
.attention { border-left:4px solid var(--warn); }
.attention .item { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; padding:10px 0;
                   border-top:1px solid var(--line-2); }
.attention .item:first-of-type { border-top:0; }
.attention .actions { display:flex; gap:6px; flex-wrap:wrap; justify-content:flex-end; }
.live { display:inline-flex; align-items:center; gap:8px; font-weight:600; color:var(--ok); }
.live::before { content:""; width:8px; height:8px; border-radius:50%; background:var(--ok);
                box-shadow:0 0 0 0 color-mix(in srgb, var(--ok) 50%, transparent); animation:pulse 1.8s infinite; }
@keyframes pulse { 0% { box-shadow:0 0 0 0 color-mix(in srgb, var(--ok) 45%, transparent); }
                   70% { box-shadow:0 0 0 8px transparent; } 100% { box-shadow:0 0 0 0 transparent; } }
@media (prefers-reduced-motion: reduce) { .live::before { animation:none; } }
.empty { text-align:center; padding:48px 16px; color:var(--muted); }
.empty b { display:block; color:var(--ink); font-size:16px; margin-bottom:4px; }

/* --- small things ----------------------------------------------------------------------------------- */
.badge { font-size:11px; font-weight:700; padding:2px 8px; border-radius:99px; letter-spacing:.02em; }
.badge.active { background:var(--ok-soft); color:var(--ok); }
.badge.inactive { background:var(--grey-soft); color:var(--muted); }
.crumbs { font-size:13px; color:var(--muted); margin:0 0 8px; }
.crumbs a { color:var(--muted); }
.head-facts { display:flex; flex-wrap:wrap; gap:6px 14px; align-items:center; color:var(--muted); margin-top:8px; }
.run { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:12px 20px; align-items:center; }
.run .where { min-width:0; }
.run .url { color:var(--muted); font-size:13px; display:block; max-width:100%; }
.run .links { display:flex; gap:14px; font-size:13px; margin-top:4px; }
.stepper { display:flex; gap:0; margin:6px 0 14px; padding:0; list-style:none; }
.stepper li { flex:1; position:relative; padding-top:22px; font-size:12.5px; color:var(--muted); text-align:center; }
.stepper li::before { content:""; position:absolute; top:6px; left:50%; width:12px; height:12px; margin-left:-6px;
  border-radius:50%; background:var(--surface); border:2px solid var(--line); z-index:1; }
.stepper li::after { content:""; position:absolute; top:11px; left:-50%; width:100%; height:2px; background:var(--line); }
.stepper li:first-child::after { display:none; }
.stepper li.done { color:var(--ink-2); }
.stepper li.done::before { background:var(--accent); border-color:var(--accent); }
.stepper li.done::after { background:var(--accent); }
.stepper li.blocked::before { background:var(--warn); border-color:var(--warn); }
.shot { display:block; border:1px solid var(--line); border-radius:var(--radius-sm); overflow:hidden; }
.shot img { display:block; width:100%; height:auto; }
.grid-2 { display:grid; grid-template-columns:repeat(auto-fit, minmax(320px, 1fr)); gap:16px; }
ul.plain { margin:6px 0 0 18px; padding:0; }

/* --- phones: rows become cards --------------------------------------------------------------------- */
@media (max-width:760px) {
  .topbar-inner { padding:0 16px; gap:14px; }
  .brand small { display:none; }
  .wrap { padding:20px 16px 64px; }
  .toolbar input[type=search] { width:100%; }
  table.cards thead, table.cards tr.head { display:none; }
  table.cards, table.cards tbody, table.cards tr, table.cards td { display:block; width:100%; }
  table.cards tr.app { border-bottom:1px solid var(--line); padding:12px 16px; }
  table.cards td { border:0; padding:3px 0; }
  table.cards td.actions { text-align:left; margin-top:6px; }
  table.cards .row-actions { justify-content:flex-start; }
  table.cards details.menu .menu-items { right:auto; left:0; }
  table.cards tr.note-row td { padding:0 16px 12px; }
  .run { grid-template-columns:1fr; }
}
"""

HEAD = """<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<link rel="icon" href=\"""" + FAVICON + """\">
<style>""" + BASE_CSS + """</style>
"""

# The header every page shows. `request` is Flask's; the tab for the current page is marked.
HEADER = """
<header class="topbar"><div class="topbar-inner">
  <a class="brand" href="/">""" + LOGO_SVG + """<span>Job Agent<small>applies for you, you submit</small></span></a>
  <nav class="nav" aria-label="Main">
    <a href="/" class="{{ 'active' if request.path == '/' or request.path.startswith('/application/') }}">Applications</a>
    <a href="/progress" class="{{ 'active' if request.path == '/progress' }}">Progress</a>
    <a href="/profile" class="{{ 'active' if request.path in ('/profile', '/setup') }}">Profile</a>
    <a href="/answers" class="{{ 'active' if request.path == '/answers' }}">Saved answers</a>
    <a href="/settings" class="{{ 'active' if request.path == '/settings' }}">Settings</a>
  </nav>
</div></header>
"""


def page(title: str, body: str, header: bool = True) -> str:
    """A whole page template: head (title, favicon, styles), the app header, then `body` (Jinja)."""
    return HEAD + f"<title>{title}</title>\n" + (HEADER if header else "") + body


# Statuses as a person reads them; the pill's colour comes from the status itself.
STATUS_LABELS = {
    "prepared": "Prepared", "form_filled": "Filling in", "ready_to_submit": "Ready to submit",
    "needs_user_review": "Needs you", "submitted": "Submitted", "skipped": "Skipped",
    "DISQUALIFIED_POLICY_MISMATCH": "Skipped (policy)", "BLOCKED_VALIDATION_LOOP": "Stuck (needs you)",
    "interviewing": "Interviewing", "rejected": "Rejected", "offer": "Offer",
}


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status or "", (status or "").replace("_", " ").capitalize())


def status_class(status: str) -> str:
    return (status or "").strip().lower()


# Which filter chip (and stat tile) a status belongs to.
_GROUPS = {
    "needs": {"ready_to_submit", "needs_user_review", "blocked_validation_loop"},
    "done": {"submitted", "interviewing", "offer", "rejected"},
    "skipped": {"skipped", "disqualified_policy_mismatch"},
}


def status_group(status: str) -> str:
    s = status_class(status)
    return next((group for group, members in _GROUPS.items() if s in members), "working")


def group_counts(records) -> dict:
    counts = {"all": 0, "needs": 0, "working": 0, "done": 0, "skipped": 0, "talking": 0}
    for record in records:
        counts["all"] += 1
        counts[status_group(record.status)] += 1
        if status_class(record.status) in {"interviewing", "offer"}:
            counts["talking"] += 1
    return counts


def install(app) -> None:
    """Make the helpers above available to every template."""
    app.jinja_env.globals.update(status_label=status_label, status_class=status_class, status_group=status_group)
