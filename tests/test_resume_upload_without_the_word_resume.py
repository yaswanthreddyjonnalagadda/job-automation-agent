"""A required upload on a resume step is the resume's, even when the box beside it never says "resume".

Steelcase (Avature), 29 September: the "Select your resume" step showed "From Device *  No file selected
[Choose another file]". Neither the page's words nor the button's matched what the agent knew, so it pressed
Continue three times with nothing attached, and the loop guard stopped the run.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
from page_agent import PageAgent, parse_snapshot

FIXTURE = Path(__file__).parent / "fixtures" / "pages" / "avature_select_your_resume.txt"


def agent(tmp_path):
    resume = tmp_path / "Jane_Doe_Resume_Acme.pdf"
    resume.write_bytes(b"%PDF-1.4\n%%EOF\n")
    job = SimpleNamespace(title="Engineer", company="Acme", url="https://careers.example.com/apply")
    a = PageAgent(SimpleNamespace(values=None), SimpleNamespace(), SimpleNamespace(auto_submit=False, ats_email=""),
                  config.UserProfile(email="jane@example.com"), SimpleNamespace(raw_text="x"), job,
                  resume_file=resume)
    a._ensure_state()
    a.done = []
    a.do = lambda page, answer, control: a.done.append((answer.action, control.name)) or True
    a._file_input_for = lambda page, controls, section: None
    return a


def test_the_avature_resume_step_gets_the_resume(tmp_path):
    snapshot = FIXTURE.read_text(encoding="utf-8")
    a = agent(tmp_path)
    a.attach_documents(None, snapshot, parse_snapshot(snapshot))
    assert a.done == [("upload_resume", "Choose another file")]


@pytest.mark.parametrize("name", ["Choose another file", "Select files", "Choose a file", "Browse", "Upload"])
def test_the_ways_a_file_button_is_named(tmp_path, name):
    snapshot = ('- listitem: Select your resume\n- alert: This field is required\n'
                f'- button "{name}" [ref=e1]\n- button "Continue" [ref=e2]')
    a = agent(tmp_path)
    a.attach_documents(None, snapshot, parse_snapshot(snapshot))
    assert a.done == [("upload_resume", name)]


def test_a_file_button_on_a_page_not_about_the_resume_is_left_alone(tmp_path):
    snapshot = ('- heading "Transcripts"\n- alert: This field is required\n'
                '- button "Choose another file" [ref=e1]\n- button "Continue" [ref=e2]')
    a = agent(tmp_path)
    a.attach_documents(None, snapshot, parse_snapshot(snapshot))
    assert a.done == []
