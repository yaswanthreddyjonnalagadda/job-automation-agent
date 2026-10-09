"""Every run keeps its own page recordings.

Each run numbered its pages from 1 in the same folder, so a run's pages were written over by the next run's, and
the pages a failure happened on were gone by the time anyone looked (only 330 of the agent's pages survived, one
run per job). Now each run has its own folder under pages/, and the latest ten runs of an application are kept.
"""
from types import SimpleNamespace

import config
import page_agent


def agent(job_dir, stamp):
    a = page_agent.PageAgent(SimpleNamespace(values=None), SimpleNamespace(), SimpleNamespace(auto_submit=False, ats_email=""),
                             config.UserProfile(email="jane@example.com"), SimpleNamespace(raw_text="x"),
                             SimpleNamespace(title="t", company="c", url="u"), resume_file=None, job_dir=job_dir)
    a._run_stamp = stamp
    return a


def test_two_runs_keep_their_own_pages(tmp_path):
    """Written raw, byte for byte -- never through diagnostics.py's structural sanitization
    (f134). `_save()` always receives the output of `self.snapshot()`, which already hides a
    typed secret (f015) before any caller, this one included, ever sees the text; a second,
    coarser redaction pass here would only destroy the fidelity replay_guard.py and this whole
    suite depend on, for no safety gain the upstream hide_secrets() had not already provided."""
    for stamp, text in (("20260929_100000", '- textbox "Email": privacy-test@example.invalid'), ("20260929_110000", '- button "Continue"')):
        a = agent(tmp_path, stamp)
        a.pages_read = 1
        a._save(text)
    assert (tmp_path / "pages" / "20260929_100000" / "page_01.txt").read_text() == '- textbox "Email": privacy-test@example.invalid'
    assert (tmp_path / "pages" / "20260929_110000" / "page_01.txt").read_text() == '- button "Continue"'


def test_a_saved_page_never_carries_diagnostics_pys_redaction_placeholder(tmp_path):
    """f134: a prior version of `_save()` routed every recording through
    `diagnostics.sanitize_snapshot()`, collapsing names, addresses, answers and most question
    text to `<redacted-value>` -- safe for a forensic/diagnostic copy, but not for the one
    mechanism replay_guard.py and this whole test suite use to prove a fix for one portal did
    not silently change another portal's answers. A real applicant answer that the structural
    sanitizer would have dropped (an address line, here) must survive unchanged."""
    text = '- textbox "Street Address": 221B Baker Street'
    a = agent(tmp_path, "20260929_120000")
    a.pages_read = 1
    a._save(text)
    saved = (tmp_path / "pages" / "20260929_120000" / "page_01.txt").read_text(encoding="utf-8")
    assert saved == text
    assert "<redacted-value>" not in saved


def test_only_the_latest_runs_are_kept(tmp_path):
    for hour in range(12):
        a = agent(tmp_path, f"20260929_{hour:02d}0000")
        a.pages_read = 1
        a._save("page")
    kept = sorted(p.name for p in (tmp_path / "pages").iterdir())
    assert len(kept) == page_agent.RUNS_KEPT and kept[0] == "20260929_020000" and kept[-1] == "20260929_110000"


def test_other_folders_are_never_deleted(tmp_path):
    (tmp_path / "pages" / "notes").mkdir(parents=True)
    page_agent.keep_latest_runs(tmp_path / "pages", keep=0)
    assert (tmp_path / "pages" / "notes").is_dir()
