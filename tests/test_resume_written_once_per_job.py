"""A tailored resume is filed the moment it is written, so a failed run's retry reuses it.

Writer, 28 September: the resume was filed only once the run reached the application form. Runs that failed
before it lost their resume, and 14 attempts at one job each asked the AI to write a new one.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

import apply_flow
from job_tracker import JobTracker


# A text-only tailored resume is this small (Aristocrat's was 5,339 bytes): small is not broken.
SMALL_PDF = b"%PDF-1.4\n1 0 obj << /Type /Page >> endobj\n" + b"x" * 4_000 + b"\n%%EOF\n"


class Writer:
    def __init__(self):
        self.calls = 0

    def tailor_resume(self, resume, job, profile, extra_instruction=""):
        self.calls += 1
        return "Jane Doe\njane@example.com\n\nSUMMARY\nNetwork engineer.\n" + "Routing and switching. " * 400


@pytest.fixture
def setting(tmp_path, monkeypatch):
    tracker = JobTracker(tmp_path / "t.db")
    job = SimpleNamespace(title="Network Engineer", company="Acme", url="https://jobs.example.com/1",
                          raw_text="Network engineer", location="")
    key = "acme-network-engineer"
    tracker.create(dedup_key=key, title=job.title, company=job.company)
    writer = Writer()
    monkeypatch.setattr(apply_flow.ai_choice, "writers", lambda cfg: [("Gemini", writer)])
    monkeypatch.setattr(apply_flow.safety, "unsupported_claims", lambda before, after: [])
    monkeypatch.setattr(apply_flow, "build_resume_pdf",
                        lambda txt, pdf: Path(pdf).write_bytes(SMALL_PDF))
    profile = SimpleNamespace(first_name="Jane", last_name="Doe", full_name="Jane Doe", email="jane@example.com",
                              phone="", linkedin_url="", city="", state="")
    resume = SimpleNamespace(raw_text="Jane Doe network engineer")
    return SimpleNamespace(tracker=tracker, job=job, key=key, writer=writer, profile=profile, resume=resume,
                           folder=tmp_path / "out")


def prepare(s):
    s.folder.mkdir(exist_ok=True)
    return apply_flow.prepare_materials(None, s.resume, s.job, s.profile, s.folder, s.tracker, s.key)


def test_the_resume_is_in_the_database_as_soon_as_it_is_written(setting):
    written = prepare(setting)
    stored = setting.tracker.latest_document(setting.key, "resume")
    assert stored and stored["filename"] == written.name and stored["content"] == written.read_bytes()


def test_a_retry_after_a_failed_run_reuses_it_without_asking_the_ai(setting):
    first = prepare(setting)
    for leftover in setting.folder.iterdir():          # the retry starts clean: only the database remembers
        leftover.unlink()
    again = prepare(setting)
    assert setting.writer.calls == 1
    assert again.name == first.name and again.read_bytes().startswith(b"%PDF")


def test_a_small_but_whole_pdf_is_reused_and_a_cut_off_one_is_not(setting):
    first = prepare(setting)
    assert len(first.read_bytes()) < 20_000
    assert apply_flow.reuse_stored_document(setting.tracker, setting.key, "resume", setting.folder) is not None
    assert not apply_flow._whole_pdf(SMALL_PDF[:2_000])
