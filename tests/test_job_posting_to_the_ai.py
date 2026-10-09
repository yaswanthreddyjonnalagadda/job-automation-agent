"""The AI that answers a form is given the job posting, so its answers fit what the employer asks for.

The owner's request (29 September 2026). It was given the profile, the resume and the job's title and company,
but not the posting: 'Do you have experience with the tools this role uses?' was answered without knowing them.
The posting is described to the AI as the employer's words -- never as a fact about the applicant.
"""
from types import SimpleNamespace

import config
import page_agent
from claude_integration import JOB_POSTING_CHARS, ClaudeClient

POSTING = "We need 5+ years with BGP, Terraform and AWS. " + "Benefits include snacks. " * 400


def client():
    c = ClaudeClient.__new__(ClaudeClient)
    c.sent = []

    def call(system, user_message, max_tokens):
        c.sent.append((system, user_message))
        return '{"answer": "Yes"}'
    c._call = call
    return c


def test_the_posting_goes_with_a_question_capped_in_length():
    c = client()
    c.answer_single_question("Do you have experience with the tools listed in this posting?", options=["Yes", "No"],
                             resume_text="BGP, Terraform, AWS", job_title="Network Engineer", company="Acme",
                             job_text=POSTING)
    system, user = c.sent[0]
    assert "JOB POSTING:\nWe need 5+ years with BGP" in user
    assert len(user) < JOB_POSTING_CHARS + 3_500
    assert "never as a fact about the candidate" in system


def test_no_posting_no_empty_section():
    c = client()
    c.answer_single_question("Preferred shift?", options=["Day", "Night"], job_text="")
    assert "JOB POSTING" not in c.sent[0][1]


def test_a_legal_question_still_never_goes_to_the_ai():
    c = client()
    assert c.answer_single_question("Do you require H-1B visa sponsorship?", options=["Yes", "No"],
                                    job_text=POSTING) == ""
    assert c.sent == []


def test_the_page_planner_is_given_the_posting_as_the_jobs():
    job = SimpleNamespace(title="Network Engineer", company="Acme", url="u", raw_text=POSTING)
    agent = page_agent.PageAgent(SimpleNamespace(values=None), SimpleNamespace(),
                                 SimpleNamespace(auto_submit=False, ats_email=""),
                                 config.UserProfile(email="jane@example.com"), SimpleNamespace(raw_text="resume"),
                                 job, resume_file=None)
    facts = agent.facts([])
    assert facts["job"]["description"].startswith("We need 5+ years")
    assert len(facts["job"]["description"]) == JOB_POSTING_CHARS
    assert facts["resume_text"] == "resume"                       # the applicant's own facts stay separate


def test_the_planner_is_told_the_posting_is_not_about_the_applicant():
    assert "never a fact about the applicant" in ClaudeClient.PLAN_PAGE_SYSTEM
