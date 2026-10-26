"""The resume an employer sees is named after the applicant, even when no tailored one could be written.

Roblox, 26 October 2026: every writer failed, the generic resume was attached with the email refreshed, and the file
was called 'current_contact_resume.pdf' -- on the employer's form. The name is the applicant's ('Jane_Doe_Resume.pdf'),
and it must still not look like a tailored resume (those end 'Resume_<Company>.pdf'): a name that did would be reused
by the next run as if it were tailored, locking the posting to the generic resume.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

import apply_flow
from job_tracker import JobTracker

PDF = b"%PDF-1.4\n1 0 obj << /Type /Page >> endobj\n" + b"x" * 4_000 + b"\n%%EOF\n"


class Writer:
    def __init__(self):
        self.calls = 0

    def tailor_resume(self, resume, job, profile, extra_instruction=""):
        self.calls += 1
        return "Jane Doe\njane@example.com\n\nSUMMARY\nNetwork engineer.\n" + "Routing and switching. " * 400


@pytest.fixture
def setting(tmp_path, monkeypatch):
    generic = tmp_path / "Generic_Resume.pdf"
    generic.write_bytes(PDF)
    monkeypatch.setattr(apply_flow.config_module, "RESUME_PATH", generic)
    tracker = JobTracker(tmp_path / "t.db")
    job = SimpleNamespace(title="Network Engineer", company="Acme", url="https://jobs.example.com/1",
                          raw_text="Network engineer", location="")
    key = "acme-network-engineer"
    tracker.create(dedup_key=key, title=job.title, company=job.company)
    writer = Writer()
    working = {"on": False}
    monkeypatch.setattr(apply_flow.ai_choice, "writers", lambda cfg: [("Gemini", writer)] if working["on"] else [])
    monkeypatch.setattr(apply_flow.safety, "unsupported_claims", lambda before, after: [])
    monkeypatch.setattr(apply_flow, "build_resume_pdf", lambda txt, pdf: Path(pdf).write_bytes(PDF))
    profile = SimpleNamespace(first_name="Jane", last_name="Doe", full_name="Jane Doe", email="jane@example.com",
                              phone="", linkedin_url="", city="", state="")
    # The generic resume still carries the owner's old address: that is what makes the agent write a refreshed copy.
    resume = SimpleNamespace(raw_text="Jane Doe old@example.com network engineer")
    monkeypatch.setattr(apply_flow, "parse_resume", lambda path: resume)
    folder = tmp_path / "out"
    folder.mkdir()
    return SimpleNamespace(tracker=tracker, job=job, key=key, writer=writer, profile=profile, resume=resume,
                           folder=folder, working=working)


def prepare(s):
    return apply_flow.prepare_materials(None, s.resume, s.job, s.profile, s.folder, s.tracker, s.key)


def test_the_refreshed_generic_resume_is_named_after_the_applicant(setting):
    result = prepare(setting)                                  # no writer at all: the local resume is attached
    assert result.name == "Jane_Doe_Resume.pdf"
    assert "contact" not in result.name.lower()


def test_that_name_is_not_taken_for_a_tailored_resume(setting):
    fallback = prepare(setting)
    assert not fallback.name.endswith("Resume_Acme.pdf")       # tailored ones end with the company
    apply_flow.store_materials(setting.tracker, setting.key, fallback, None)       # as the run does at the form
    setting.working["on"] = True                                # a later run, with a writer that works
    again = prepare(setting)
    assert setting.writer.calls == 1                            # it was tailored, not reused
    assert again.name == "Jane_Doe_Resume_Acme.pdf"
