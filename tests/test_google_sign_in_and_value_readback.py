"""Two things a live ADP application (Willdan, 24 September) got wrong.

1. "Sign in with Google" was pressed, the site ignored the press (its own script threw
   "Cannot read properties of undefined (reading 'googlePlusSocialURL')" and left the
   button dead until the page was loaded again), and the agent reported "LOGIN_OK" anyway,
   then filled the create-a-profile form underneath. A sign-in counts only once the site
   stops offering it; a press the site does not answer is answered by loading the page
   again, and after that by the site's own sign-in.

2. A text box that carries a placeholder shows what is typed in it as a child line of the
   snapshot ("- text: jane@example.com") rather than after its colon. The agent read such a
   box as empty, so it retyped its Email and Mobile Number on every pass and finally stopped
   with "could not set: Email" on a form that was in fact filled in.
"""
import re
from types import SimpleNamespace

import pytest

import config
import page_agent
import safety
from browser_automation import JobApplicationAssistant

EMAIL = "owner@example.com"
SITE = "https://jobs.example.com"


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


def controls_of(page):
    return page_agent.parse_snapshot(page.locator("body").aria_snapshot(mode="ai"))


# --- 2. a value the snapshot shows as a child line is still the box's value ---------------

ADP_SHAPE = '''- generic [ref=e298]:
  - generic [ref=e299]:
    - generic [ref=e301]: First Name*
    - textbox "First Name" [ref=e303]: Yaswanth Reddy
  - generic [ref=e308]:
    - generic [ref=e309]: Email*
    - textbox "Email" [ref=e311]:
      - /placeholder: ""
      - text: owner@example.com
    - alert
  - generic [ref=e312]:
    - generic [ref=e313]: Mobile Number*
    - textbox "Mobile Number" [ref=e327]:
      - /placeholder: Phone Number
      - text: +1 571 354 5212
    - generic [ref=e328]: Country code was added to the field.
  - button "Continue" [ref=e330] [cursor=pointer]'''


def test_a_value_shown_as_a_child_line_is_read_as_the_boxs_value():
    boxes = {c.name: c for c in page_agent.parse_snapshot(ADP_SHAPE) if c.role == "textbox"}
    assert boxes["Email"].answer == "owner@example.com"
    assert boxes["Mobile Number"].answer == "+1 571 354 5212"
    assert boxes["First Name"].answer == "Yaswanth Reddy"


def test_text_that_only_sits_beside_a_box_is_not_its_value():
    controls = page_agent.parse_snapshot('''- generic [ref=e1]:
  - textbox "Email" [ref=e2]:
    - /placeholder: name@example.com
  - text: We will never share your email
  - textbox "Phone" [ref=e3]''')
    assert [c.answer for c in controls if c.role == "textbox"] == ["", ""]


def test_only_the_first_child_line_is_the_value_and_a_later_sibling_is_left_alone():
    controls = page_agent.parse_snapshot('''- generic [ref=e1]:
  - generic [ref=e2]:
    - textbox "Email" [ref=e3]:
      - /placeholder: ""
    - generic [ref=e4]:
      - text: Required
  - textbox "Phone" [ref=e5]''')
    assert [c.answer for c in controls if c.role == "textbox"] == ["", ""]


@pytest.mark.parametrize("attrs", [
    'placeholder=""', 'placeholder="Phone Number"', 'placeholder="name@example.com" type="email"',
    'type="tel" placeholder="(555) 555-5555"', "", 'aria-label="Contact" placeholder="x"',
])
@pytest.mark.parametrize("value", ["owner@example.com", "+1 571 354 5212", "(571) 354-5212", "Yaswanth Reddy"])
def test_whatever_a_box_shows_it_is_read_back_as_typed(page, attrs, value):
    """The class: a box's value, however its placeholder makes the snapshot draw it."""
    page.set_content(f'<label for="b">Contact</label><input id="b" {attrs}>')
    page.locator("#b").fill(value)
    box = next(c for c in controls_of(page) if c.role == "textbox")
    assert box.answer == value


def test_a_dial_code_the_site_put_in_a_phone_box_is_not_an_answer():
    """ADP starts a Mobile Number box with "+1" (its country's code). That is a prefix to
    type after, not a phone number: the box is still empty and must still be filled."""
    for prefix in ("+1", "+91", "+44"):
        controls = page_agent.parse_snapshot(f'''- textbox "Mobile Number" [ref=e1]:
  - /placeholder: Phone Number
  - text: "{prefix}"''')
        assert controls[0].answer == "", prefix


def test_a_number_with_its_dial_code_is_an_answer():
    controls = page_agent.parse_snapshot('''- textbox "Mobile Number" [ref=e1]:
  - /placeholder: Phone Number
  - text: +1 571 354 5212''')
    assert controls[0].answer == "+1 571 354 5212"


def test_a_box_already_holding_the_profile_value_is_not_corrected(page):
    """The loop that retyped Email on every pass: the value was there, the read said empty."""
    page.set_content(f'<label for="e">Email*</label><input id="e" placeholder="" value="{EMAIL}">'
                     '<label for="n">First Name*</label><input id="n" value="Yaswanth">')
    profile = config.UserProfile(first_name="Yaswanth", last_name="Jonnalagadda", email=EMAIL)
    agent = make_agent(profile)
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    assert agent.correct_from_profile(page, page_agent.PagePlan(), controls) == []
    assert agent.notes == [] or not any("Email" in n for n in agent.notes)


# --- 1. a sign-in is real only once the site stops offering it ------------------------------

def make_agent(profile=None):
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    assistant.GOOGLE_RESPONSE_WAIT_MS = 400       # the real waits are seconds
    cfg = SimpleNamespace(auto_submit=False, ats_email="")
    job = SimpleNamespace(title="Cloud Engineer", company="Example", url=f"{SITE}/apply")
    profile = profile or config.UserProfile(email=EMAIL)
    return page_agent.PageAgent(assistant, SimpleNamespace(), cfg, profile,
                                SimpleNamespace(raw_text="Cloud engineer"), job, resume_file=None)


SIGN_IN_PAGE = """<html><body><h2>Tell us about yourself.</h2>
<label for="e">Email*</label><input id="e">
<button id="continue">Continue</button>
<button id="g" aria-label="Sign in with Google">G</button>
<script>
  // The site's own script: on a bad load it throws and the button does nothing (ADP's
  // 'googlePlusSocialURL' of undefined); on a good load it hands over to Google in place.
  const alive = ALIVE;
  document.getElementById('g').addEventListener('click', () => {
    if (!alive) throw new TypeError("Cannot read properties of undefined (reading 'googlePlusSocialURL')");
    location.href = 'https://accounts.google.com/o/oauth2/auth?client=x';
  });
</script></body></html>"""

CHOOSER = """<html><body><h1>Choose an account</h1>
<div role="link" tabindex="0" id="pick" onclick="location.href='https://jobs.example.com/apply/in'">EMAIL</div>
</body></html>""".replace("EMAIL", EMAIL)

SIGNED_IN = "<html><body><h2>Application Information</h2><label for='f'>First Name*</label><input id='f'></body></html>"


class Site:
    """The employer's page and Google's chooser, both made up. `dead_loads` is how many loads
    of the sign-in page come up with a dead Google button before one comes up working."""

    def __init__(self, page, dead_loads):
        self.dead_loads, self.loads, self.presses_after_load = dead_loads, 0, 0

        def employer(route):
            path = route.request.url.split("/", 3)[3].split("?")[0]
            if path == "apply/in":
                body = SIGNED_IN
            else:
                self.loads += 1
                body = SIGN_IN_PAGE.replace("ALIVE", "true" if self.loads > self.dead_loads else "false")
            route.fulfill(status=200, content_type="text/html", body=body)

        page.route(f"{SITE}/**", employer)
        page.route("https://accounts.google.com/**",
                   lambda route: route.fulfill(status=200, content_type="text/html", body=CHOOSER))
        page.goto(f"{SITE}/apply")


def sign_in(agent, page):
    return agent.sign_in_step(page, page_agent.parse_snapshot(agent.snapshot(page)))


def test_a_google_button_the_site_ignores_is_not_a_sign_in(page, caplog):
    Site(page, dead_loads=99)
    agent = make_agent()
    caplog.set_level("INFO")
    assert sign_in(agent, page) is True                   # something was done: the page is read again
    assert "LOGIN_OK" not in caplog.text and "signed in with Google" not in caplog.text
    assert agent.assistant.google_press_ignored is True


def test_a_dead_google_button_is_answered_by_loading_the_page_again_and_then_working(page, caplog):
    site = Site(page, dead_loads=1)
    agent = make_agent()
    caplog.set_level("INFO")
    assert sign_in(agent, page) is True                   # dead: the page is loaded again
    assert site.loads == 2 and "LOGIN_OK" not in caplog.text
    assert sign_in(agent, page) is True                   # alive: Google's chooser, the account, back
    assert page.url.endswith("/apply/in")
    assert "LOGIN_OK" in caplog.text and "signed in with Google" in caplog.text
    assert agent._google_failed == set()


def test_a_site_whose_google_button_stays_dead_is_left_to_its_own_sign_in(page):
    site = Site(page, dead_loads=99)
    agent = make_agent()
    for _ in range(6):
        sign_in(agent, page)
        if agent._google_failed:
            break
    assert agent._google_failed == {"jobs.example.com"}
    assert site.loads == 1 + page_agent.PageAgent.GOOGLE_RELOADS       # reloaded a limited number of times


def test_the_sign_in_check_is_the_page_itself_not_a_placeholder(page):
    """`_sign_in_with_google` is told how to see whether the site still offers Google. Nothing
    was passed before, so every attempt counted as a success."""
    Site(page, dead_loads=0)
    agent = make_agent()
    seen = []
    agent.assistant._sign_in_with_google = lambda pg, button, email, host, header: seen.append(header) or True
    assert sign_in(agent, page) is True
    (header,) = seen
    assert header() is not None                                       # Google is still on the page
    page.set_content("<h2>Application Information</h2><label>First <input></label>")
    assert header() is None                                           # the site stopped offering it


def test_google_still_offered_inside_a_frame_is_still_offered(page):
    Site(page, dead_loads=0)
    agent = make_agent()
    seen = []
    agent.assistant._sign_in_with_google = lambda pg, button, email, host, header: seen.append(header) or True
    sign_in(agent, page)
    page.set_content('<iframe srcdoc=\'<button aria-label="Sign in with Google">G</button>\' width="300" height="100">'
                     '</iframe>')
    page.wait_for_timeout(300)
    assert seen[0]() is not None


# --- the press itself (browser_automation) ----------------------------------------------------

def test_a_press_the_site_never_answers_gives_up_quickly_and_says_so(page):
    Site(page, dead_loads=99)
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.GOOGLE_RESPONSE_WAIT_MS = 300
    button = page.locator("#g")
    assert assistant._sign_in_with_google(page, button, EMAIL, "jobs.example.com", lambda: button) is False
    assert assistant.google_press_ignored is True


def test_a_press_the_site_answers_is_never_marked_ignored(page):
    Site(page, dead_loads=0)
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.GOOGLE_RESPONSE_WAIT_MS = 400
    assistant.remember_account = lambda *a, **k: None
    button = page.locator("#g")
    still_offered = lambda: page.locator("#g").first if page.locator("#g").count() else None
    assert assistant._sign_in_with_google(page, button, EMAIL, "jobs.example.com", still_offered) is True
    assert assistant.google_press_ignored is False
