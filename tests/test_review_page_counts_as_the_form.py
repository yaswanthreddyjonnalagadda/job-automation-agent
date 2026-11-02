"""
A review step shows the answers as text, with a Submit button and no inputs (f186).

Invesco's Workday "Review" step (step 5 of 5) was reported "The application form was never reached --
nothing has been filled in": `hand_over` judged "form reached" by the inputs on the page in front of it, and
a review page has none. The page that holds the application's own Submit button is the application form,
whatever it shows its answers as. A page with neither inputs nor that button is still "never reached".

    venv\\Scripts\\python -m pytest tests/test_review_page_counts_as_the_form.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import apply_flow  # noqa: E402
import safety  # noqa: E402


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    pg = browser.new_page()
    yield pg
    pg.close()


class Profile:
    full_name = "Test Person"
    email = "me@example.com"
    city = "Fairfax"
    state = "Virginia"


class Tracker:
    def __init__(self):
        self.status = self.notes = None

    def update_status(self, key, status, notes=None):
        self.status, self.notes = status, notes

    def record_event(self, *args, **kwargs):
        pass

    def document_matches(self, key, kind, path):
        return True


class Job:
    title, company, url = "Network Engineer", "Example Corp", "https://jobs.example.com/apply/42"


class Config:
    auto_submit_verified_only = False


def assistant_for(button_name: str | None):
    class Assistant:
        values = safety.AgentValues()

        def validate_application(self, page, resume_name=""):
            return {"required_still_blank": [], "errors_shown": [], "unanswered_questions": [],
                    "attestations_pending": [], "warnings_shown": [], "ambiguous_choices": [],
                    "unsupported_questions": [], "identity_checks": [], "attached_documents": [],
                    "captcha": False, "resume_attached": True, "page_url": page.url}

        def read_back_fields(self, page):
            return []                      # a review page: answers as text, no inputs

        def attached_document_names(self, page):
            return ["Resume.pdf"]

        def raise_window(self, page):
            pass

        def find_submit_button(self, page):
            if not button_name:
                return None
            button = page.get_by_role("button", name=button_name)
            return button if button.count() else None

        def click_verified_submit(self, page, authorization):
            raise AssertionError("never submits here")

    return Assistant()


REVIEW = ("<h2>Review</h2><p>City</p><p>Fairfax</p><p>Resume.pdf successfully uploaded</p>"
          "<button>Back</button><button>Submit</button>")


@pytest.mark.parametrize("body", [
    REVIEW,
    "<h3>Review your application</h3><dl><dt>Email</dt><dd>me@example.com</dd></dl><button>Submit application</button>",
])
def test_a_review_step_with_submit_and_no_inputs_is_the_form(page, tmp_path, body):
    page.set_content(body)
    name = "Submit application" if "Submit application" in body else "Submit"
    tracker = Tracker()
    summary = tmp_path / "summary.json"
    summary.write_text("{}", encoding="utf-8")
    status = apply_flow.hand_over(assistant_for(name), page, tracker, "key", Job(), tmp_path, "Resume.pdf",
                                  summary, config=Config(), profile=Profile(), documents={})
    assert "never reached" not in (tracker.notes or "")
    assert status == "ready_to_submit"


def test_a_page_with_no_inputs_and_no_submit_is_still_never_reached(page, tmp_path):
    page.set_content("<h1>Sign in with Google</h1><button>Continue</button>")
    tracker = Tracker()
    summary = tmp_path / "summary.json"
    summary.write_text("{}", encoding="utf-8")
    status = apply_flow.hand_over(assistant_for(None), page, tracker, "key", Job(), tmp_path, "Resume.pdf",
                                  summary, config=Config(), profile=Profile(), documents={})
    assert status == "needs_user_review"
    assert "never reached" in (tracker.notes or "")
