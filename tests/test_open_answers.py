"""Open questions written in plain English, inside the form's limit.

Writer (Ashby), 28 September: "Why are you interested in joining WRITER?" and "Please give an example from
your professional experience that aligns with one or more of our cultural values" were handed over blank.
The owner's rule: the agent writes them from his profile and resume, in plain English a non-native speaker
understands, with no AI wording, and never over the form's word or character limit.
"""
import json
import re
from types import SimpleNamespace

import pytest
from hypothesis import given, settings, strategies as st

import open_answers
from open_answers import Limits
from claude_integration import ClaudeClient

WHY = "Why are you interested in joining WRITER?"
EXAMPLE = "Please give an example from your professional experience that aligns with one or more of our cultural values:"

GOOD = ("I build and run cloud networks on AWS and Azure. At my last job I moved 40 branch offices to an "
        "SD-WAN overlay with no downtime. Your posting says the team runs the platform that serves every "
        "customer. I want to do that work. I like owning a system from design to support. I also want to "
        "learn from a team that ships to production every day.")
AI_ISH = ("I am thrilled to apply! I am deeply passionate about leveraging cutting-edge cloud technologies — "
          "and I truly believe I would be a great fit for this dynamic, fast-paced team; furthermore, my "
          "journey has honed my skills.")


# --- which questions are open, and what kind -----------------------------------

@pytest.mark.parametrize("question, kind", [
    (WHY, "why"),
    ("What interests you about this role?", "why"),
    ("What motivates you to apply?", "why"),
    (EXAMPLE, "example"),
    ("Tell us about a time when you fixed an outage.", "example"),
    ("Describe a project you are proud of.", "example"),
    ("Is there anything else you would like us to know?", "other"),
])
def test_open_questions_are_recognised_and_sorted(question, kind):
    assert open_answers.is_open(question)
    assert open_answers.kind(question) == kind


@pytest.mark.parametrize("question", ["LinkedIn Profile", "Current company", "Phone number", "City"])
def test_a_fact_box_is_not_an_open_question(question):
    assert not open_answers.is_open(question)


def test_any_multi_line_box_is_open():
    assert open_answers.is_open("Additional comments", multiline=True)
    assert open_answers.is_open("Notes", multiline=True)


# --- limits, however the form words them ----------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Why us? (max 150 words)", Limits(max_words=150)),
    ("Maximum of 200 words", Limits(max_words=200)),
    ("Please answer in 100 words or less.", Limits(max_words=100)),
    ("Up to 250 words", Limits(max_words=250)),
    ("No more than 300 words", Limits(max_words=300)),
    ("Answer in about 120 words", Limits(max_words=120)),
    ("Please keep it between 100 and 200 words", Limits(min_words=100, max_words=200)),
    ("150-250 words", Limits(min_words=150, max_words=250)),
    ("At least 50 words please", Limits(min_words=50)),
    ("Limit: 1,000 characters", Limits(max_chars=1000)),
    ("(500 characters max)", Limits(max_chars=500)),
    ("0/500", Limits(max_chars=500)),
    ("Tell us why", Limits()),
    ("Available 24/7 on call, started 9/28", Limits()),
])
def test_limits_are_read_from_the_forms_words(text, expected):
    assert open_answers.limits_from(text) == expected


def test_the_box_limit_and_the_words_limit_both_count():
    assert open_answers.limits_from("max 200 words", maxlength=600) == Limits(max_words=200, max_chars=600)
    assert open_answers.limits_from("max 800 characters", maxlength=500).max_chars == 500   # the smaller wins


@pytest.mark.parametrize("limits, kind, expected", [
    (Limits(), "why", (50, 100)),
    (Limits(), "example", (80, 150)),
    (Limits(max_words=50), "example", (45, 45)),            # a tight limit wins over the default
    (Limits(min_words=200), "why", (200, 250)),              # a stated minimum wins too
    (Limits(max_chars=300), "why", (41, 41)),                # ~6.5 characters a word
])
def test_the_target_length_sits_inside_the_forms_limit(limits, kind, expected):
    assert limits.target(kind) == expected


# --- checking and tidying a draft -------------------------------------------------

def test_ai_wording_is_found():
    found = " ".join(open_answers.problems(AI_ISH, Limits(), "why"))
    for word in ("thrilled", "passionate", "cutting-edge", "great fit", "dynamic", "journey", "honed"):
        assert word in found
    assert "dashes" in found


def test_a_plain_answer_passes():
    assert open_answers.problems(GOOD, Limits(), "why") == []


def test_a_long_sentence_is_found():
    long_one = "I " + "really " * 25 + "like networks."
    assert any("longer than" in p for p in open_answers.problems(long_one, Limits(), "other"))


def test_tidy_fixes_what_code_can_fix_without_changing_the_meaning():
    tidied = open_answers.tidy('"We utilize BGP — and OSPF; it works! Furthermore, I lead the team."')
    assert tidied == "We use BGP, and OSPF. It works. Also, I lead the team."


def test_tidy_removes_lists_and_markdown():
    assert open_answers.tidy("- **I build** networks\n- I run them") == "I build networks I run them"


def test_the_style_file_is_where_the_words_live():
    data = json.loads(open_answers.STYLE_FILE.read_text(encoding="utf-8"))
    assert "passionate" in data["avoid"] and data["replace"]["utilize"] == "use"


# --- never over the limit ---------------------------------------------------------

sentence = st.lists(st.sampled_from(["I", "run", "cloud", "networks", "on", "AWS", "for", "six", "years",
                                     "and", "fixed", "outages", "fast"]), min_size=1, max_size=30) \
    .map(lambda ws: " ".join(ws) + ".")


@settings(max_examples=300, deadline=None)
@given(st.lists(sentence, min_size=1, max_size=12), st.one_of(st.none(), st.integers(3, 120)),
       st.one_of(st.none(), st.integers(20, 800)))
def test_fit_never_leaves_an_answer_over_the_limit(parts, max_words, max_chars):
    text = " ".join(parts)
    limits = Limits(max_words=max_words, max_chars=max_chars)
    fitted = open_answers.fit(text, limits)
    if max_words:
        assert open_answers.words(fitted) <= max_words
    if max_chars:
        assert len(fitted) <= max_chars
    assert fitted.endswith(".")
    if (not max_words or open_answers.words(text) <= max_words) and (not max_chars or len(text) <= max_chars):
        assert fitted == text                                   # nothing is cut when it already fits


def test_fit_cuts_at_the_end_of_a_sentence():
    fitted = open_answers.fit(GOOD, Limits(max_words=30))
    assert open_answers.words(fitted) <= 30 and fitted in GOOD and fitted.endswith(".")


# --- writing: one redo with the problems named, then the limit ---------------------

class Model:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, system, user, tokens):
        self.calls.append((system, user))
        return json.dumps({"answer": self.replies.pop(0)}) if self.replies else "{}"


def test_an_ai_sounding_draft_is_sent_back_once_with_its_problems():
    model = Model(AI_ISH, GOOD)
    answer, left = open_answers.write(WHY, ask=model, facts="Name: Test", resume_text="SD-WAN, AWS",
                                      job_text="Writer builds AI tools for enterprises.")
    assert answer == GOOD and left == []
    assert len(model.calls) == 2
    assert "thrilled" in model.calls[1][1] and "FIX THESE PROBLEMS" in model.calls[1][1]


def test_the_prompt_carries_the_rules_and_only_the_owners_facts():
    model = Model(GOOD)
    open_answers.write(WHY, ask=model, facts="Name: Test", resume_text="RESUME-TEXT",
                       job_text="POSTING-TEXT", hint="Writer's values of Connect, Challenge, Own")
    system, user = model.calls[0]
    assert "second language" in system and "Never invent" in system and "passionate" in system
    assert "RESUME-TEXT" in user and "POSTING-TEXT" in user and "Connect, Challenge, Own" in user


def test_a_long_draft_is_cut_to_the_forms_limit():
    model = Model(GOOD, GOOD)
    answer, _left = open_answers.write(WHY + " (max 40 words)", ask=model, facts="", resume_text="")
    assert open_answers.words(answer) <= 40


def test_a_character_limit_is_kept():
    model = Model(GOOD, GOOD)
    answer, _left = open_answers.write(WHY, ask=model, facts="", resume_text="", limits=Limits(max_chars=200))
    assert 0 < len(answer) <= 200


def test_no_answer_from_the_model_leaves_the_box_blank():
    answer, left = open_answers.write(WHY, ask=Model(), facts="", resume_text="")
    assert answer == "" and left


def test_the_better_of_two_bad_drafts_is_kept_and_what_is_wrong_is_reported():
    model = Model(AI_ISH, "I am passionate about networks. I build them on AWS for six years now at work.")
    answer, left = open_answers.write(WHY, ask=model, facts="", resume_text="")
    assert "thrilled" not in answer and any("passionate" in p for p in left)


# --- on the page ---------------------------------------------------------------------

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


def test_the_box_says_what_it_takes(page):
    page.set_content(f"""<div class="field"><label for="a">{EXAMPLE}</label>
        <p>Writer's values of Connect, Challenge, Own can be reviewed here. Max 120 words.</p>
        <textarea id="a" maxlength="900" placeholder="Type here..."></textarea></div>""")
    box = open_answers.read_box(page.locator("#a"))
    assert box["multiline"] and box["maxlength"] == 900
    assert "Connect, Challenge, Own" in box["hint"]
    assert open_answers.limits_from(EXAMPLE, box["hint"], maxlength=box["maxlength"]) == \
        Limits(max_words=120, max_chars=900)


def test_answer_single_question_writes_an_open_answer_inside_the_limit():
    client = ClaudeClient.__new__(ClaudeClient)
    model = Model(GOOD)
    client._call = lambda system, user_message, max_tokens: model(system, user_message, max_tokens)
    profile = SimpleNamespace(full_name="Test Person", current_position_title="Network Engineer",
                              current_employer="Example", years_experience=6, target_titles=("Cloud Engineer",),
                              education=())
    answer = client.answer_single_question(WHY, resume_text="SD-WAN", profile=profile, job_title="Infrastructure "
                                           "engineer", company="Writer", job_text="posting",
                                           box={"multiline": True, "maxlength": 250, "hint": "max 45 words"})
    assert answer and len(answer) <= 250 and open_answers.words(answer) <= 45
    assert "Current role: Network Engineer at Example" in model.calls[0][1]


def test_a_question_with_choices_is_not_written_as_an_essay():
    client = ClaudeClient.__new__(ClaudeClient)
    client._call = lambda system, user_message, max_tokens: '{"answer": "Yes"}'
    assert client.answer_single_question("Why not? Are you over 18?", options=["Yes", "No"]) == "Yes"
