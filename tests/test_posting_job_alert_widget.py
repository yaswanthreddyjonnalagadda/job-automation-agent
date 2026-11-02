"""A job posting's 'Get notified for similar jobs' / job-alert widget and 'Similar jobs' cards are not the application (f280).

Pacific Life (Phenom People), 31 October 2026: the posting page carried an 'Apply Now Sr Network Engineer' link/button,
a 'Get notified for similar jobs' ('Sign up to receive job alerts') region with an Email address box and a 'Submit'
button, and a 'Similar jobs' section with 'Apply Now <Other Job>' links. Because:
  1. browser_automation.is_review_step() matched the job-alert widget's 'Submit' button, on_job_description() returned
     False and apply_flow never clicked Apply before handing the page to PageAgent;
  2. parse_snapshot() included the job-alert Email address box and Submit button as application controls;
  3. profile_forward() did not recognise 'Apply Now <Job Title>' and chose the job-alert 'Submit' button instead;
the agent filled the user's email into the job-alert box and clicked Submit, subscribing to job alerts instead of
opening the application.
"""
from types import SimpleNamespace

import pytest

import config
import page_agent
from browser_automation import JobApplicationAssistant
from page_agent import PageAgent, parse_snapshot
from submission_guard import SubmissionGuardV0


PACIFIC_LIFE_SNAPSHOT = """\
- link "Skip to main content" [ref=e1] [cursor=pointer]:
  - /url: "#acc-skip-content"
- generic [ref=e84]:
  - heading "Sr Network Engineer" [level=1] [ref=e85]
  - link "Apply Now Sr Network Engineer" [ref=e88] [cursor=pointer]:
    - /url: https://careers.pacificlife.com/us/en/hvhapply?jobSeqNo=PLBPLAUSR17701EXTERNALENUS
    - text: Apply Now
  - heading "Job Description:" [level=2] [ref=e89]
  - paragraph [ref=e95]: You will design, implement, and support enterprise network infrastructure.
  - button "Apply Now Sr Network Engineer" [ref=e170] [cursor=pointer]: Apply now
- region "Get notified for similar jobs" [ref=e177]:
  - heading "Looking for more?" [level=2] [ref=e178]
  - generic [ref=e179]: Sign up to receive job alerts
  - generic: Email address
  - generic [ref=e181]:
    - textbox "Email address" [ref=e186]:
      - /placeholder: name@email.com
    - generic:
      - alert
    - button "Submit" [ref=e187] [cursor=pointer]
  - button "Manage Alerts" [ref=e189] [cursor=pointer]
- generic [ref=e193]:
  - heading "Similar jobs" [level=2] [ref=e207]
  - list [ref=e210]:
    - listitem [ref=e211]:
      - heading "Data Lead Architect- Institutional Division" [level=3] [ref=e216]
      - link "Apply Now Data Lead Architect- Institutional Division" [ref=e255] [cursor=pointer]:
        - /url: https://careers.pacificlife.com/us/en/hvhapply?jobSeqNo=PLBPLAUSR20146EXTERNALENUS
        - text: Apply Now
"""


def _make_agent():
    job = SimpleNamespace(
        title="Sr Network Engineer",
        company="Pacific Life",
        url="https://careers.pacificlife.com/us/en/job/PLBPLAUSR17701EXTERNALENUS/Sr-Network-Engineer",
    )
    return PageAgent(
        SimpleNamespace(values=None),
        SimpleNamespace(),
        SimpleNamespace(auto_submit=False, ats_email="", form_answer_mode="profile"),
        config.UserProfile(email="jane@example.com"),
        SimpleNamespace(raw_text="Network engineer"),
        job,
        resume_file=None,
    )


def test_parse_snapshot_ignores_job_alert_widget_and_similar_jobs_cards():
    controls = parse_snapshot(PACIFIC_LIFE_SNAPSHOT)
    refs = {c.ref for c in controls}
    assert "e88" in refs and "e170" in refs
    # Job-alert email box and Submit / Manage Alerts buttons must never be parsed as application controls.
    assert "e186" not in refs
    assert "e187" not in refs
    assert "e189" not in refs
    # Other jobs' Apply Now links under "Similar jobs" must never be parsed as controls for this application.
    assert "e255" not in refs


def test_profile_plan_and_posting_plan_choose_apply_now_with_job_title():
    ag = _make_agent()
    controls = parse_snapshot(PACIFIC_LIFE_SNAPSHOT)
    plan = ag.profile_plan(controls, page_agent.required_questions(PACIFIC_LIFE_SNAPSHOT), [], PACIFIC_LIFE_SNAPSHOT)
    assert plan.page_kind == "job_description"
    assert plan.next_kind == "open_application"
    assert plan.next_ref == "e88"
    assert not plan.answers

    posting = ag.posting_plan(controls, set(), [], PACIFIC_LIFE_SNAPSHOT)
    assert posting is not None
    assert posting.next_kind == "open_application"
    assert posting.next_ref == "e88"


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        instance = p.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    pg = context.new_page()
    yield pg
    context.close()


POSTING_WITH_JOB_ALERT_HTML = """\
<html><body>
  <div>
    <h1>Sr Network Engineer</h1>
    <a href="https://careers.example-employer.test/us/en/hvhapply?jobSeqNo=1"
       aria-label="Apply Now Sr Network Engineer">Apply Now</a>
    <h2>Job Description</h2>
    <p>Responsibilities and qualifications for the role. Experience with BGP, OSPF, and enterprise networks required.</p>
    <button type="button" aria-label="Apply Now Sr Network Engineer" onclick="window.opened = true">Apply now</button>
  </div>
  <div role="region" aria-label="Get notified for similar jobs">
    <h2>Looking for more?</h2>
    <div>Sign up to receive job alerts</div>
    <input type="email" aria-label="Email address" placeholder="name@email.com">
    <button type="button" onclick="window.subscribed = true">Submit</button>
  </div>
  <section>
    <h2>Similar jobs</h2>
    <a href="https://careers.example-employer.test/us/en/hvhapply?jobSeqNo=2"
       aria-label="Apply Now Other Engineer">Apply Now</a>
  </section>
</body></html>
"""


def test_posting_with_job_alert_submit_button_is_recognised_as_job_description_not_review_step(page):
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = page_agent.safety.AgentValues()
    page.set_content(POSTING_WITH_JOB_ALERT_HTML)
    assert not assistant.is_review_step(page)
    assert assistant.find_submit_button(page) is None
    assert assistant.on_job_description(page)


def test_submission_guard_allows_apply_now_with_job_title_when_job_alert_widget_is_present(page):
    page.route("**/*", lambda route: route.fulfill(body=POSTING_WITH_JOB_ALERT_HTML, content_type="text/html"))
    page.goto("https://careers.example-employer.test/us/en/job/1/Sr-Network-Engineer")
    guard = SubmissionGuardV0(page.context)
    guard.activate(page)
    page.get_by_role("button", name="Apply Now Sr Network Engineer").click()
    assert page.evaluate("window.opened") is True
    assert not guard.denials(page)

    # Meanwhile, the job-alert widget's Submit button remains guarded against unverified clicks.
    page.get_by_role("button", name="Submit").click()
    assert page.evaluate("window.subscribed") is None
    assert guard.denials(page)


def test_page_errors_ignores_please_select_dropdown_placeholder_options():
    snapshot = """\
- combobox "Are you open to relocation?" [ref=f1e717]:
  - option "Please Select" [selected]
  - option "No"
  - option "Yes"
- button "Please Select" [ref=f1e718]
"""
    assert page_agent.page_errors(snapshot) == []
    with_real_error = snapshot + '\n- generic [ref=f1e900]: "Invalid phone number format."'
    assert page_agent.page_errors(with_real_error) == ["Invalid phone number format."]


def test_wizard_marker_ignores_slick_active_carousel_slides_until_review_is_current_step(page):
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = page_agent.safety.AgentValues()
    page.set_content("""
    <html><body>
      <ul class="progressbarlist slick-initialized slick-slider" role="list">
        <li class="slide slick-slide slick-active completed" role="listitem">My information</li>
        <li class="slide slick-slide slick-active completed" role="listitem">My experience</li>
        <li class="active slide slick-slide progress-current slick-current slick-active"
            aria-current="step" role="listitem">Application questions</li>
        <li class="slide slick-slide slick-active" role="listitem">Consents and Terms and Conditions</li>
        <li class="slide slick-slide slick-active" role="listitem">Review</li>
      </ul>
      <select aria-label="Are you open to relocation?"><option>Please Select</option><option>Yes</option></select>
      <button type="button">Next</button>
    </body></html>
    """)
    assert assistant.is_review_step_by_wizard_marker(page) is False
    assert assistant.is_review_step(page) is False

    page.set_content("""
    <html><body>
      <ul class="progressbarlist slick-initialized slick-slider" role="list">
        <li class="slide slick-slide slick-active completed" role="listitem">My information</li>
        <li class="active slide slick-slide progress-current slick-current slick-active"
            aria-current="step" role="listitem">Review</li>
      </ul>
      <button type="button">Submit</button>
    </body></html>
    """)
    assert assistant.is_review_step_by_wizard_marker(page) is True


def test_how_did_you_hear_matches_job_site_or_social_media_and_repeated_entries():
    import option_match
    import repeated_entries

    option_match._groups.cache_clear()
    ag = _make_agent()
    ctrl = page_agent.Control(
        ref="e1",
        role="combobox",
        name="How did you hear about us?",
        options=["Please Select", "Job Site or Social Media", "Current Employee", "Company Website", "Other"],
    )
    assert ag.known_answer(ctrl) == ("Job Site or Social Media", "profile.how_did_you_hear")

    # Repeated-entries work description must not match the job title rule ("role").
    snap = """\
- group "Work Experience 1" [ref=e10]:
  - textbox "Job Title*" [ref=e11]
  - textbox "Company*" [ref=e12]
  - textbox "Role description" [ref=e13]
"""
    controls = parse_snapshot(snap)
    assert controls
    entries = repeated_entries.entry_map(snap)
    val, _src, _blank = repeated_entries.answer(
        entries["e13"],
        {"experience": [{"title": "Network Engineer", "company": "Acme", "description": "Managed routers"}]},
    )
    assert val == "Managed routers"

    # A field of study is never swapped for a near one on the agent's own say-so (the built-in equivalents were removed
    # in PR #48): Computer Science is not "Computer and Information Science" unless the owner lists it as a substitute
    # (answer_alternatives, read by sites.workday._field_names).
    assert option_match.best_option(
        ["Please Select", "Electrical Engineering", "Computer and Information Science"],
        "Computer Science",
    ) is None


def test_profile_forward_prefers_submit_over_progress_bar_review_tab():
    ag = _make_agent()
    review_snapshot = """\
- toolbar [ref=f1e50]:
  - button "My information" [ref=f1e53]
  - button "Review" [ref=f1e73]
- button "Previous" [ref=f1e640]
- button "Submit" [ref=f1e994]
"""
    controls = parse_snapshot(review_snapshot)
    plan = ag.profile_plan(controls, set(), [], review_snapshot)
    assert plan.next_ref == "f1e994"
    assert plan.next_label == "Submit"
    assert plan.next_kind == "final_submit"

