"""The replay guard reads what KIND of page each saved page is, not only its questions (f174).

Marathon's footer 'will review your request' made the agent read a job posting as the application's review step (f170),
and the replay guard -- which replays every saved page through the old and the new code -- saw nothing, because it only
compared questions and answers. The words that decide 'is this a posting?' are now one function, and the guard compares
its verdict on every saved page, so a change to those words that flips any real page is shown before it is committed.
"""
import browser_automation
import replay_guard

POSTING = ('- heading "Senior Network Engineer"\n- paragraph: Responsibilities and qualifications for this job.\n'
           '- button "Apply"\n')
FOOTER = "- paragraph: A Human Resources representative will review your request and contact you.\n"


def test_one_function_decides_whether_the_words_are_a_posting():
    assert browser_automation.looks_like_posting_text(POSTING + FOOTER)
    assert not browser_automation.looks_like_posting_text(POSTING + "- paragraph: Review your application\n")
    assert not browser_automation.looks_like_posting_text("- heading \"Sign In\"\n")
    assert not browser_automation.looks_like_posting_text("")


def test_the_replay_reads_the_verdict_for_every_saved_page():
    entry = replay_guard.page_kind_entry(POSTING + FOOTER)
    assert entry["kind"] == "page" and entry["answer"] == "posting"
    assert replay_guard.page_kind_entry(POSTING + "- paragraph: Review and submit\n")["answer"] == "not a posting"


def test_a_page_whose_verdict_flips_is_reported_as_a_change():
    before = {"output/a/pages/1/page_01.txt": {"folder": "a", "entries": [replay_guard.page_kind_entry(POSTING + FOOTER)]}}
    after = {"output/a/pages/1/page_01.txt": {"folder": "a", "entries": [
        {**replay_guard.page_kind_entry(POSTING + FOOTER), "answer": "not a posting"}]}}
    lines = replay_guard.compare(before, after)
    assert len(lines) == 1 and "page" in lines[0] and "not a posting" in lines[0]
