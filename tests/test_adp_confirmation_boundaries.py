"""Live DOM regressions for ADP receipt boundaries (synthetic data only)."""
from types import SimpleNamespace

import pytest

from sites import adapter_for
from sites.adp import is_adp_applied_posting


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.mark.parametrize("host,path,markup,expected", [
    ("myjobs.adp.com", "/tenant/cx/job-details?reqId=123", '<div class="cx-apply-row"><button disabled>Applied</button></div>', True),
    ("myjobs.adp.com", "/tenant/cx/job-details?reqId=123", '<div class="cx-apply-row"><button>Applied filters</button></div>', False),
    ("myjobs.adp.com", "/tenant/cx/job-details?reqId=123", '<button>Applied</button>', False),
    ("myjobs.adp.com", "/tenant/cx/job-details?reqId=123", '<div class="cx-apply-row"><button>Applied</button></div><form><input></form>', False),
    ("myjobs.adp.com.attacker.test", "/tenant/cx/job-details?reqId=123", '<div class="cx-apply-row"><button>Applied</button></div>', False),
    ("myjobs.adp.com", "/tenant/cx/job-details", '<div class="cx-apply-row"><button>Applied</button></div>', False),
    ("myjobs.adp.com", "/tenant/cx/job-details?reqId=123", '<div class="cx-apply-row" style="display:none"><button>Applied</button></div>', False),
    ("myjobs.adp.com", "/tenant/cx/job-details?reqId=123", '<div class="cx-apply-row" style="opacity:0"><button>Applied</button></div>', False),
    ("myjobs.adp.com", "/tenant/cx/job-details?reqId=123", '<cx-job-details-page></cx-job-details-page><script>document.querySelector("cx-job-details-page").attachShadow({mode:"open"}).innerHTML = \'<div class="cx-apply-row"><button disabled>Applied</button></div>\';</script>', True),
])
def test_receipt_requires_live_scoped_exact_control(browser, host, path, markup, expected):
    context = browser.new_context()
    page = context.new_page()
    page.route("**/*", lambda route: route.fulfill(body=markup, content_type="text/html"))
    try:
        page.goto(f"https://{host}{path}")
        assert is_adp_applied_posting(page) is expected
    finally:
        context.close()


def test_stale_snapshot_and_body_text_cannot_confirm_submission():
    page = SimpleNamespace(
        url="https://myjobs.adp.com/tenant/cx/job-details?reqId=123",
        _snapshot='- heading "Job Description"\n- button "Applied"',
        inner_text=lambda *_: "Job Description: Applied engineering experience",
    )
    assert not is_adp_applied_posting(page)


def test_adapter_matches_hostname_only():
    assert adapter_for("https://example.test/adp.com").name != "adp"
    assert adapter_for("https://adp.com.attacker.test/").name != "adp"
    assert adapter_for("https://myjobs.adp.com/tenant").name == "adp"


def test_live_evaluation_failure_cannot_use_cached_receipt():
    def failed(*_):
        raise RuntimeError("page is navigating")
    page = SimpleNamespace(
        url="https://myjobs.adp.com/tenant/cx/job-details?reqId=123",
        evaluate=failed,
        _snapshot='- heading "Job Description"\n- button "Applied"',
        inner_text=lambda *_: "Job Description Applied",
    )
    assert not is_adp_applied_posting(page)
