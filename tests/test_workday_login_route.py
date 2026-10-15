"""Standalone login must preserve localized and locale-less Workday tenants."""
from types import SimpleNamespace
from urllib.parse import urlparse, parse_qs
import pytest
from browser_automation import JobApplicationAssistant

@pytest.mark.parametrize("path,prefix", [
    ("/Careers/job/Office/Engineer_R123/apply", "/Careers"),
    ("/en-US/Careers/job/Office/Engineer_R123/apply", "/en-US/Careers"),
    ("/fr-CA/External/job/Office/Engineer_R123", "/fr-CA/External"),
])
def test_login_preserves_tenant_and_redirect(path, prefix):
    calls = []
    page = SimpleNamespace(url="https://example.wd5.myworkdayjobs.com" + path,
        goto=lambda url, **kwargs: calls.append(url), wait_for_timeout=lambda ms: None)
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assert assistant._goto_login_page(page)
    destination = urlparse(calls[0])
    assert destination.netloc == "example.wd5.myworkdayjobs.com"
    assert destination.path == prefix + "/login"
    assert parse_qs(destination.query)["redirect"] == [path]

@pytest.mark.parametrize("path", ["/Careers/login", "/en-US/Careers/login", "/", "/en-US"])
def test_existing_login_or_incomplete_prefix_is_untouched(path):
    page = SimpleNamespace(url="https://example.wd5.myworkdayjobs.com" + path,
        goto=lambda *args, **kwargs: pytest.fail("unexpected navigation"))
    assert not JobApplicationAssistant.__new__(JobApplicationAssistant)._goto_login_page(page)
