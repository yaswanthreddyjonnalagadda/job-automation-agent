"""Fresh jobs from every system the owner applies through, with a real posting date, shown newest first (f299).

The agent read job lists from two systems (Greenhouse and Lever) and for twelve tech companies, so the owner's Workday,
iCIMS and Ashby employers never appeared, the last scan was days old, and nothing said when a job was posted. Here:
each system's list is read, each job carries when it was posted (as exactly as the system says), the companies are taken
from the applications already made, and a job already in the owner's history is never suggested again.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import discovery_sources as ds

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def reply(payload, status=200, text=""):
    return SimpleNamespace(status_code=status, ok=status < 400, json=lambda: payload, text=text,
                           raise_for_status=lambda: None)


# -- when a job was posted ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("value,age_hours,precision", [
    ("2026-11-02T10:00:00+00:00", 2, "minute"),                  # an exact time
    ("2026-11-02T10:00:00Z", 2, "minute"),
    ("2026-11-02T08:00:00-04:00", 0, "minute"),                  # an exact time in another zone (12:00 here)
    (int((NOW - timedelta(hours=5)).timestamp() * 1000), 5, "minute"),      # Lever: milliseconds since 1970
    ("2026-10-30", 84, "day"),                                  # a date with no time: counted from its midnight
    ("Posted Today", 24, "day"),                                 # a day-level date is held as the earliest it could be
    ("Posted Yesterday", 48, "day"),
    ("Posted 1 Day Ago", 48, "day"),
    ("Posted 3 Days Ago", 96, "day"),
])
def test_when_a_job_was_posted_and_how_exactly_it_is_known(value, age_hours, precision):
    """An exact time is that time. A day-level date ("Posted 3 Days Ago") is held as the earliest it could have been
    posted, so a job is only ever called recent when it certainly is."""
    posted = ds.parse_posted(value, now=NOW)
    assert posted is not None and posted.precision == precision
    assert (NOW - posted.at).total_seconds() / 3600 == pytest.approx(age_hours, abs=0.01)


@pytest.mark.parametrize("value", [None, "", "Posted 30+ Days Ago", "sometime", 0, "Posted a while ago"])
def test_an_unknown_posting_date_is_unknown_not_guessed(value):
    assert ds.parse_posted(value, now=NOW) is None


@pytest.mark.parametrize("value,hours,expected", [
    ("2026-11-02T11:30:00+00:00", 1, True),         # half an hour ago, within the last hour
    ("2026-11-02T10:30:00+00:00", 1, False),        # an hour and a half ago
    ("2026-11-02T10:30:00+00:00", 24, True),
    ("Posted Today", 1, False),                      # a day-level date cannot prove "within the hour"
    ("Posted Today", 24, True),
    ("Posted 1 Day Ago", 24, False),                 # 24 to 48 hours old: not certainly inside a day
    ("Posted 2 Days Ago", 72, True),                 # 48 to 72 hours old
    ("Posted 6 Days Ago", 168, True),
    ("Posted 7 Days Ago", 168, False),               # up to 8 days old
])
def test_a_job_is_inside_a_window_only_when_it_certainly_is(value, hours, expected):
    posted = ds.parse_posted(value, now=NOW)
    assert ds.within(posted, hours, now=NOW) is expected


def test_a_job_with_no_known_date_is_never_inside_a_window():
    assert ds.within(None, 168, now=NOW) is False


# -- reading each system's list ----------------------------------------------------------------------------------

def test_ashby_jobs_carry_their_published_time(monkeypatch):
    body = {"jobs": [{"title": "Network Engineer", "location": "Remote", "jobUrl": "https://jobs.ashbyhq.com/acme/1",
                      "publishedAt": "2026-11-02T09:00:00.000+00:00", "isRemote": True,
                      "descriptionPlain": "Requires BGP."}]}
    monkeypatch.setattr(ds.requests, "get", lambda url, **kw: reply(body))
    jobs = ds.fetch_ashby_feed("acme", "Acme")
    assert jobs[0]["job_title"] == "Network Engineer" and jobs[0]["ats_type"] == "ashby"
    assert jobs[0]["url"] == "https://jobs.ashbyhq.com/acme/1" and jobs[0]["is_remote"] == 1
    assert jobs[0]["posted_at"] == "2026-11-02T09:00:00+00:00" and jobs[0]["posted_precision"] == "minute"
    assert jobs[0]["description"] == "Requires BGP."


WORKDAY_ENTRY = {"company": "Rackspace", "ats": "workday", "host": "rackspace.wd1.myworkdayjobs.com",
                 "tenant": "rackspace", "site": "External"}


def test_workday_is_searched_by_title_and_each_job_carries_its_posted_day(monkeypatch):
    searched = []
    listing = {"total": 2, "jobPostings": [
        {"title": "Network Engineer IV - US", "externalPath": "/job/US-Remote/Network-Engineer-IV_R1",
         "locationsText": "US-Work from Home", "postedOn": "Posted 2 Days Ago"},
        {"title": "Network Engineer II", "externalPath": "/job/Seattle/Network-Engineer-II_R2",
         "locationsText": "Seattle, Washington", "postedOn": "Posted 26 Days Ago"}]}
    detail = {"jobPostingInfo": {"jobDescription": "<p>Requires BGP and OSPF.</p>",
                                 "externalUrl": "https://rackspace.wd1.myworkdayjobs.com/External/job/US-Remote/Network-Engineer-IV_R1"}}

    def post(url, json=None, **kw):
        searched.append((url, json["searchText"]))
        return reply(listing)

    def get(url, **kw):
        assert url.endswith("/wday/cxs/rackspace/External/job/US-Remote/Network-Engineer-IV_R1"), url
        return reply(detail)
    monkeypatch.setattr(ds.requests, "post", post)
    monkeypatch.setattr(ds.requests, "get", get)
    jobs = ds.fetch_workday_feed(WORKDAY_ENTRY, ["Network Engineer"], now=NOW, max_age_days=7)
    assert searched == [("https://rackspace.wd1.myworkdayjobs.com/wday/cxs/rackspace/External/jobs", "Network Engineer")]
    assert [j["job_title"] for j in jobs] == ["Network Engineer IV - US"]       # the 26-day-old one is not fetched
    job = jobs[0]
    assert job["ats_type"] == "workday" and job["posted_precision"] == "day"
    assert job["url"].startswith("https://rackspace.wd1.myworkdayjobs.com/External/job/US-Remote/")
    assert job["description"] == "Requires BGP and OSPF." and job["is_remote"] == 1


def test_workday_that_does_not_answer_gives_no_jobs_and_no_error(monkeypatch):
    monkeypatch.setattr(ds.requests, "post", lambda *a, **k: reply({}, status=500))
    assert ds.fetch_workday_feed(WORKDAY_ENTRY, ["Network Engineer"], now=NOW) == []


ICIMS_LIST = """<ul class="container-fluid iCIMS_JobsTable">
<li class="iCIMS_JobCardItem"><div class="row"><div class="col-xs-12 title">
<a href="https://jobs-acme.icims.com/jobs/2424/network-engineer/job?in_iframe=1" class="iCIMS_Anchor" title="2424 - Network Engineer">
<span class="sr-only field-label">Job Title</span> <h3 > Network Engineer</h3></a></div>
<dd class="iCIMS_JobHeaderData"><span > US-WA-Seattle</span></dd></div></li>
<li class="iCIMS_JobCardItem"><div class="row"><div class="col-xs-12 title">
<a href="https://jobs-acme.icims.com/jobs/2446/head-of-data/job?in_iframe=1"><h3> Head of Data &amp; AI</h3></a></div></div></li></ul>"""


def test_icims_jobs_are_read_from_the_list_and_dated_from_the_posting(monkeypatch):
    def get(url, **kw):
        if "/jobs/search" in url:
            return reply({}, text=ICIMS_LIST)
        return reply({}, text='<script type="application/ld+json">{"@type":"JobPosting","title":"Network Engineer",'
                              '"datePosted":"2026-11-01","description":"Requires BGP"}</script>')
    monkeypatch.setattr(ds.requests, "get", get)
    jobs = ds.fetch_icims_feed({"company": "Acme", "ats": "icims", "host": "jobs-acme.icims.com"}, ["Network Engineer"])
    assert {j["job_title"] for j in jobs} >= {"Network Engineer"}
    first = [j for j in jobs if j["job_title"] == "Network Engineer"][0]
    assert first["url"] == "https://jobs-acme.icims.com/jobs/2424/network-engineer/job"
    assert first["posted_at"].startswith("2026-11-01") and first["posted_precision"] == "day"
    assert first["location"] == "US-WA-Seattle"


def test_greenhouse_and_lever_carry_when_they_were_first_posted(monkeypatch):
    import job_discovery as jd
    gh = {"jobs": [{"title": "Network Engineer", "location": {"name": "Remote"}, "absolute_url": "https://x/1",
                    "first_published": "2026-11-02T07:00:00-04:00", "updated_at": "2026-11-02T11:00:00-04:00",
                    "content": ""}]}
    monkeypatch.setattr(jd.requests, "get", lambda url, **kw: reply(gh))
    job = jd.fetch_greenhouse_feed("acme", "Acme")[0]
    assert job["posted_at"] == "2026-11-02T11:00:00+00:00"              # first published, not the last edit
    lv = [{"text": "Network Engineer", "hostedUrl": "https://x/2", "createdAt": int(NOW.timestamp() * 1000),
           "categories": {"location": "Remote"}, "descriptionPlain": ""}]
    monkeypatch.setattr(jd.requests, "get", lambda url, **kw: reply(lv))
    assert jd.fetch_lever_feed("acme", "Acme")[0]["posted_at"] == NOW.isoformat()


# -- which companies --------------------------------------------------------------------------------------------

HISTORY = [
    ("Rackspace", "https://rackspace.wd1.myworkdayjobs.com/External/job/Seattle-Washington/Network-Engineer-IV_R-23931"),
    ("Rackspace", "https://rackspace.wd1.myworkdayjobs.com/en-US/External/job/Remote/Other_R-1"),
    ("Aristocrat", "https://aristocrat.wd3.myworkdayjobs.com/AristocratExternalCareersSite/job/Las-Vegas-NV-US/Network-Engineer_R0022692?rx=1"),
    ("Notion", "https://jobs.ashbyhq.com/notion/8d8e1e53-1111-2222-3333-444455556666"),
    ("Coinbase", "https://boards.greenhouse.io/coinbase/jobs/123"),
    ("Fastly", "https://job-boards.greenhouse.io/embed/job_app?for=fastly&token=7858609"),
    ("Palantir", "https://jobs.lever.co/palantir/abcd-1234"),
    ("Mascsr", "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=9b3cf408-35d5-47a9-b102-f592f8abbe88&ccId=19000101_000001&type=JS&lang=en_US&jobId=678818"),
    ("Mutual of Enumclaw", "https://jobs-mutualofenumclaw.icims.com/jobs/2424/network-engineer/job?mode=view"),
    ("Somewhere", "https://careers.somewhere.example/jobs/1"),            # a system with no public list: nothing to read
]


def test_the_companies_to_scan_are_taken_from_the_applications_already_made():
    entries = ds.companies_from_history(HISTORY)
    by_key = {(e["ats"], e.get("tenant") or e.get("token") or e.get("cid") or e.get("host")): e for e in entries}
    assert by_key[("workday", "rackspace")]["host"] == "rackspace.wd1.myworkdayjobs.com"
    assert by_key[("workday", "rackspace")]["site"] == "External"                    # not the locale "en-US"
    assert by_key[("workday", "aristocrat")]["site"] == "AristocratExternalCareersSite"
    assert ("ashby", "notion") in by_key and ("greenhouse", "coinbase") in by_key
    assert ("greenhouse", "fastly") in by_key and ("lever", "palantir") in by_key
    # ADP's list gives no link to a single job (its own apply address names no job it lists), so a suggestion could
    # point nowhere: ADP employers are left out until there is a link that opens the job itself.
    assert not any(e["ats"] == "adp" for e in entries)
    assert by_key[("icims", "jobs-mutualofenumclaw.icims.com")]["company"] == "Mutual of Enumclaw"
    assert len([e for e in entries if e["ats"] == "workday" and e["tenant"] == "rackspace"]) == 1     # once, not twice
    assert not any("somewhere" in str(e) for e in entries)


def test_listed_companies_are_added_once_to_those_from_history():
    listed = [{"company": "Cloudflare", "token": "cloudflare", "ats": "greenhouse"},
              {"company": "Coinbase", "token": "coinbase", "ats": "greenhouse"}]
    merged = ds.merge_companies(listed, ds.companies_from_history(HISTORY))
    assert sum(1 for e in merged if e["ats"] == "greenhouse" and e["token"] == "coinbase") == 1
    assert any(e["token"] == "cloudflare" for e in merged if e["ats"] == "greenhouse")
    assert any(e["ats"] == "workday" for e in merged)


# -- the scan and the window -------------------------------------------------------------------------------------

import json

import job_discovery as jd

PROFILE = {"target_titles": ["Network Engineer", "Senior Network Engineer"], "locations": ["Remote"],
           "requires_visa_sponsorship": False, "skills": []}


def listing(n, title="Senior Network Engineer", posted="", precision="", company="Acme"):
    return {"company_name": company, "company_token": company.lower(), "ats_type": "greenhouse", "job_title": title,
            "location": "Remote", "is_remote": 1, "url": f"https://x.example/{company}/{n}", "description": "",
            "updated_at": "2026-11-02T00:00:00+00:00", "posted_at": posted, "posted_precision": precision}


def make_engine(tmp_path, monkeypatch, by_company, history=()):
    board = tmp_path / "boards.json"
    board.write_text(json.dumps([{"company": name, "token": name.lower(), "ats": "greenhouse"} for name in by_company]),
                     encoding="utf-8")
    engine = jd.JobDiscoveryEngine(db_path=tmp_path / "d.db", config_path=board)
    engine.profile, engine.resume_text, engine.history = PROFILE, "", list(history)
    seen = []

    def fetch(entry, timeout=8.0):
        seen.append((entry, entry.get("titles")))
        return by_company.get(entry.get("company"), [])
    monkeypatch.setattr(jd, "fetch_jobs_for_company", fetch)
    engine.seen = seen
    return engine


def hours_ago(h):
    return (datetime.now(timezone.utc) - timedelta(hours=h)).isoformat()


def test_the_scan_keeps_when_each_job_was_posted_and_a_window_returns_only_certainly_recent_ones(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, {"Acme": [
        listing(1, posted=hours_ago(0.5), precision="minute"),            # half an hour ago
        listing(2, posted=hours_ago(5), precision="minute"),
        listing(3, posted=hours_ago(30), precision="minute"),
        listing(4, posted=hours_ago(24 * 4), precision="day"),            # dated only by its day
        listing(5, posted=hours_ago(24 * 30), precision="minute"),        # a month old
        listing(6, posted="", precision=""),                              # the system gave no date
    ]})
    engine.scan_all(min_match_threshold=0)

    def urls(**kw):
        return sorted(j["url"].rsplit("/", 1)[1] for j in engine.get_discovered_jobs(min_score=0, **kw))
    assert urls(within_hours=1) == ["1"]
    assert urls(within_hours=24) == ["1", "2"]
    assert urls(within_hours=72) == ["1", "2", "3"]
    assert urls(within_hours=24 * 7) == ["1", "2", "3", "4"]         # the month-old one and the undated one never appear
    assert urls() == ["1", "2", "3", "4", "5", "6"]                  # with no window, everything stored


def test_a_window_lists_the_newest_first(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, {"Acme": [
        listing(1, posted=hours_ago(30), precision="minute"), listing(2, posted=hours_ago(2), precision="minute"),
        listing(3, posted=hours_ago(10), precision="minute")]})
    engine.scan_all(min_match_threshold=0)
    got = engine.get_discovered_jobs(min_score=0, within_hours=24 * 7)
    assert [j["url"].rsplit("/", 1)[1] for j in got] == ["2", "3", "1"]
    assert got[0]["posted_at"].startswith(hours_ago(2)[:13])


def test_a_rescan_keeps_the_posting_date_and_never_loses_it_to_a_system_that_gives_none(tmp_path, monkeypatch):
    first = listing(1, posted=hours_ago(5), precision="minute")
    engine = make_engine(tmp_path, monkeypatch, {"Acme": [first]})
    engine.scan_all(min_match_threshold=0)
    engine_again = make_engine(tmp_path, monkeypatch, {"Acme": [listing(1, posted="", precision="")]})
    engine_again.db_path = engine.db_path
    engine_again.scan_all(min_match_threshold=0)
    assert engine_again.get_discovered_jobs(min_score=0, within_hours=24)[0]["posted_at"] == first["posted_at"]


def test_the_employers_the_owner_applied_through_are_scanned_with_the_wanted_titles(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, {"Acme": [listing(1, posted=hours_ago(1), precision="minute")]},
                         history=HISTORY)
    engine.scan_all(min_match_threshold=0)
    scanned = {(e["ats"], e.get("tenant") or e.get("token") or e.get("host")) for e, _t in engine.seen}
    assert ("workday", "rackspace") in scanned and ("ashby", "notion") in scanned and ("icims", "jobs-mutualofenumclaw.icims.com") in scanned
    assert ("greenhouse", "acme") in scanned                           # the owner's own list is still scanned
    assert all(titles == PROFILE["target_titles"] for _e, titles in engine.seen)


def test_workday_is_read_page_by_page_until_the_list_ends(monkeypatch):
    """Workday sorts a search by relevance, not date, so fresh jobs sit on later pages: Rackspace's second page held as
    many posted this week as its first."""
    asked = []

    def post(url, json=None, **kw):
        asked.append(json["offset"])
        count = 20 if json["offset"] < 40 else 4                     # 44 results: two full pages, then 4
        items = [{"title": f"Network Engineer {json['offset'] + i}", "locationsText": "Remote",
                  "externalPath": f"/job/Remote/Network-Engineer-{json['offset'] + i}_R", "postedOn": "Posted 2 Days Ago"}
                 for i in range(count)]
        return reply({"total": 44, "jobPostings": items})
    monkeypatch.setattr(ds.requests, "post", post)
    monkeypatch.setattr(ds.requests, "get", lambda url, **kw: reply({"jobPostingInfo": {"jobDescription": "x"}}))
    jobs = ds.fetch_workday_feed(WORKDAY_ENTRY, ["Network Engineer"], now=NOW, max_age_days=7, max_details=100)
    assert asked == [0, 20, 40]                                        # stops at the short page
    assert len(jobs) == 44


def test_workday_paging_has_a_limit(monkeypatch):
    asked = []

    def post(url, json=None, **kw):
        asked.append(json["offset"])
        return reply({"total": 10000, "jobPostings": [
            {"title": f"T{json['offset'] + i}", "externalPath": f"/job/x/{json['offset'] + i}", "postedOn": "Posted Today"}
            for i in range(20)]})
    monkeypatch.setattr(ds.requests, "post", post)
    monkeypatch.setattr(ds.requests, "get", lambda url, **kw: reply({}, status=404))
    ds.fetch_workday_feed(WORKDAY_ENTRY, ["Network Engineer"], now=NOW, max_details=1)
    assert len(asked) <= 5


@pytest.mark.parametrize("titles,expected", [
    (["Network Engineer", "Cloud Network Engineer", "Senior Network Engineer"], ["Network Engineer"]),
    (["Cloud Network Engineer", "Site Reliability Engineer"], ["Cloud Network Engineer", "Site Reliability Engineer"]),
    (["Network Engineer", "network engineer"], ["Network Engineer"]),
    (["Senior Network Engineer", "Network Engineer"], ["Network Engineer"]),
    ([], []),
])
def test_a_title_that_only_narrows_another_is_not_searched_separately(titles, expected):
    """A search for "Network Engineer" already returns every "Senior Network Engineer": asking for both only repeats it."""
    assert ds.minimal_titles(titles) == expected


def test_workday_is_searched_once_per_distinct_title(monkeypatch):
    searched = []
    monkeypatch.setattr(ds.requests, "post", lambda url, json=None, **kw: searched.append(json["searchText"]) or reply({"jobPostings": []}))
    ds.fetch_workday_feed(WORKDAY_ENTRY, ["Network Engineer", "Cloud Network Engineer", "Senior Network Engineer"], now=NOW)
    assert searched == ["Network Engineer"]
