"""A page that says the application was received is read as a receipt, in whatever words, by every reader (f297).

Fastly (Greenhouse), 2 November 2026: the owner pressed Submit, the application was accepted and the employer's email
arrived, but the agent kept waiting and the run stayed "ready to submit". Fastly's receipt reads "Thank you for your
interest in working at Fastly!" and "Your job application has been received. A member of our team will be in touch."
The waiting check wanted a heading that matched one of a few fixed phrases from start to end, and the agent kept four
separate lists of such phrases that did not agree: the page reader knew this page, the waiting check and the Greenhouse
module did not.

Wording that is NOT a receipt must stay one: "thank you for your interest" on its own opens a create-account page
(HonorHealth) and was once recorded as a submitted application.
"""
import pathlib
import tempfile
from types import SimpleNamespace

import pytest

import config
import page_agent
import safety
from browser_automation import JobApplicationAssistant

URL = "https://job-boards.greenhouse.io/embed/job_app/confirmation?for=example&token=1"
TITLE = "Senior Engineer - Network Management Platform"


def page_of(body: str) -> str:
    return f"<html><head><title>Job Application</title></head><body><main>{body}</main></body></html>"


RECEIPTS = {
    "Fastly": ("<h1>Thank you for your interest in working at Fastly!</h1>"
               "<p>Your job application has been received. A member of our team will be in touch with you soon.</p>"
               "<p>Good luck!</p><a href='https://x.example/jobs'>View more jobs</a>"),
    "Greenhouse standard": ("<h1>Thank you for applying.</h1><p>We've received your application. Our Recruiting team "
                            "will review your information as soon as possible.</p><a href='#'>Back to job post</a>"),
    "submitted successfully": "<h2>Your application has been submitted successfully.</h2><p>We will email you.</p>",
    "thanks, then received": "<h1>Thanks!</h1><p>Your application was received and is being reviewed by our team.</p>",
    "we have received": "<p>We have received your application for the Network Engineer role. We will be in touch.</p>",
    "thank you, then job application sent": ("<h1>Thank you.</h1><p>Your job application has been sent to our "
                                              "hiring team. You will hear from us soon.</p>"),
    "plain heading": "<h1>Application received</h1><p>Check your email for next steps.</p>",
    "success wording": "<h1>Success!</h1><p>Your application has been successfully submitted.</p>",
}

NOT_RECEIPTS = {
    "create-account page (HonorHealth)": (
        "<h1>Thank you for your interest in a career with HonorHealth.</h1><p>Please create an account to apply.</p>"
        "<input type='email' aria-label='Email'><input type='password' aria-label='Password'>"
        "<input type='password' aria-label='Confirm password'><button>Create account</button>"),
    "interest only, nothing received": "<h1>Thank you for your interest in working at Fastly!</h1><p>Browse our open roles below.</p>",
    "FAQ about when applications are received": (
        "<h2>FAQ</h2><p>How long until we answer? Applications received after 5pm are reviewed the next business day.</p>"),
    "future tense": "<p>Once you submit, your application will be received by our recruiting team.</p>",
    "still to do": ("<p>Your application has not been sent yet. You need to complete your application before it "
                    "can be submitted.</p><input aria-label='a'><input aria-label='b'><input aria-label='c'>"),
    "the form itself": ("<h1>Apply for this job</h1><input aria-label='First name'><input aria-label='Last name'>"
                        "<input aria-label='Email'><button type='submit'>Submit application</button>"),
    "posting with an Apply button": "<h1>Network Engineer</h1><p>We review every application carefully.</p><button>Apply now</button>",
}


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    pg = context.new_page()
    yield pg
    context.close()


def show(page, body):
    page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=page_of(body)))
    page.goto(URL)


def assistant():
    a = JobApplicationAssistant.__new__(JobApplicationAssistant)
    a.values = safety.AgentValues()
    return a


def agent():
    resume = pathlib.Path(tempfile.mkdtemp()) / "Resume_Test.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    job = SimpleNamespace(title=TITLE, company="Example", url=URL)
    return page_agent.PageAgent(assistant(), SimpleNamespace(), SimpleNamespace(auto_submit=False, ats_email=""),
                                config.get_user_profile(), SimpleNamespace(raw_text=""), job, resume_file=resume)


@pytest.mark.parametrize("name", RECEIPTS)
def test_the_waiting_check_sees_the_receipt(page, name):
    """The check that runs while the owner reads the form and presses Submit."""
    show(page, RECEIPTS[name])
    assert assistant().submission_confirmed(page, TITLE) is True, f"{name!r} was not read as a receipt"


@pytest.mark.parametrize("name", RECEIPTS)
def test_the_page_reader_sees_the_receipt(page, name):
    show(page, RECEIPTS[name])
    assert agent()._site_confirms(page) is True, f"{name!r} was not read as a receipt"


@pytest.mark.parametrize("name", NOT_RECEIPTS)
def test_the_waiting_check_is_not_fooled(page, name):
    show(page, NOT_RECEIPTS[name])
    assert assistant().submission_confirmed(page, TITLE) is False, f"{name!r} was taken for a receipt"


@pytest.mark.parametrize("name", NOT_RECEIPTS)
def test_the_page_reader_is_not_fooled(page, name):
    show(page, NOT_RECEIPTS[name])
    assert agent()._site_confirms(page) is False, f"{name!r} was taken for a receipt"


def blocks_of(body: str) -> list[str]:
    """The page's blocks of text, as the check reads them: one heading or paragraph at a time."""
    import re
    parts = re.split(r"</?(?:h[1-6]|p|a|div|main)\b[^>]*>", body)
    return [" ".join(part.split()) for part in parts if part.strip()]


def test_one_shared_definition_of_a_receipt():
    """Both readers use the same wording: it lives in one module, not in lists that drift apart."""
    import confirmation_text
    for name, body in RECEIPTS.items():
        assert any(confirmation_text.says_received(block) for block in blocks_of(body)), name
    for name in ("interest only, nothing received", "FAQ about when applications are received", "future tense"):
        assert not any(confirmation_text.says_received(block) for block in blocks_of(NOT_RECEIPTS[name])), name
