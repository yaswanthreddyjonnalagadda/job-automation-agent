"""A job posting's own Apply button opens the application on any employer site; nothing else gets that pass (f269).

Walmart, 27 October 2026: the run stopped on the posting's "Apply now" with 'final submission is only available
through the verified gateway', because only ADP, Greenhouse and Bank of America had a test for "this click only
opens the application". posting_entry.py is the one structural test; these cases pin both halves of it -- the
posting page it must let through, and every page where there is something to submit, which must stay guarded.
"""
import pytest

from submission_guard import SubmissionGuardV0

HEADER = '<header><form role="search"><input type="text" aria-label="Search jobs"></form><nav><a href="#">Careers</a></nav></header>'


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    instance = context.new_page()
    yield instance
    context.close()


def click_apply(page, markup, label="Apply now", host="careers.example-employer.test"):
    """Click the control labelled `label` on `markup` served as `host`; return (clicked_through, guard_denials)."""
    page.route("**/*", lambda route: route.fulfill(body=markup, content_type="text/html"))
    page.goto(f"https://{host}/us/en/jobs/R-2684667")
    guard = SubmissionGuardV0(page.context)
    guard.activate(page)
    page.get_by_role("button", name=label, exact=True).click()
    return bool(page.evaluate("window.opened")), guard.denials(page)


def apply_button(label="Apply now"):
    return f'<button type="button" data-id="job-apply-button" aria-label="{label}" onclick="window.opened = true">{label}</button>'


@pytest.mark.parametrize("label", ["Apply", "Apply now", "Apply for this job"])
def test_a_posting_apply_button_on_any_employer_site_only_opens_the_application(page, label):
    """Walmart's shape: a header search box, a description, and an Apply button outside every form."""
    opened, denials = click_apply(page, f"{HEADER}<main><h1>Senior Network Engineer</h1>{apply_button(label)}</main>", label)
    assert opened and not denials


@pytest.mark.parametrize("variant", ["form_wrapped", "application_form", "body_input", "dialog", "embedded_application", "frame_holding_a_form", "submit_label"])
def test_a_page_with_something_to_submit_stays_guarded(page, variant):
    label = "Submit Application" if variant == "submit_label" else "Apply now"
    control = apply_button(label)
    body = {
        "form_wrapped": f"<form>{control}</form>",
        "application_form": f'{control}<form><input aria-label="First Name"></form>',
        "body_input": f'{control}<main><input aria-label="Email"></main>',
        "dialog": f'{control}<div role="dialog"><input aria-label="Email"></div>',
        # an application embedded from another origin (how Greenhouse boards embed theirs) cannot be looked into
        "embedded_application": f'{control}<iframe src="https://boards.example-ats.test/embed/job_app" width="300" height="200"></iframe>',
        "frame_holding_a_form": f'{control}<iframe srcdoc="&lt;form&gt;&lt;input aria-label=Email&gt;&lt;/form&gt;" width="300" height="200"></iframe>',
        "submit_label": control,
    }[variant]
    opened, denials = click_apply(page, f"{HEADER}<main>{body}</main>", label)
    assert not opened and denials


def test_an_empty_frame_of_the_page_does_not_hide_a_posting(page):
    """Walmart's posting carries a visible, empty, address-less frame (a map placeholder): nothing in it to submit."""
    markup = f'{HEADER}<main>{apply_button()}<iframe width="682" height="180"></iframe></main>'
    opened, denials = click_apply(page, markup)
    assert opened and not denials


@pytest.mark.parametrize("host", ["www.indeed.com", "jobs.linkedin.com", "boards.ziprecruiter.com", "m.facebook.com"])
def test_a_job_board_one_click_apply_stays_guarded(page, host):
    """Their Apply can submit for the person; the owner applies on the employer's own site."""
    opened, denials = click_apply(page, f"<main>{apply_button()}</main>", host=host)
    assert not opened and denials


def test_a_site_whose_name_merely_contains_a_social_host_is_not_a_job_board(page):
    """x.com must match the host x.com, not any name ending in 'x.com' such as box.com."""
    opened, denials = click_apply(page, f"{HEADER}<main>{apply_button()}</main>", host="careers.box.com")
    assert opened and not denials
