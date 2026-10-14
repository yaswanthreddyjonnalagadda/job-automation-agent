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
    for stamp, text in (("20260929_100000", '- textbox "Email": privacy-test@example.invalid'), ("20260929_110000", '- button "Continue"')):
        a = agent(tmp_path, stamp)
        a.pages_read = 1
        a._save(text)
    assert (tmp_path / "pages" / "20260929_100000" / "page_01.txt").read_text() == '- textbox "Email": privacy-test@example.invalid'
    assert (tmp_path / "pages" / "20260929_110000" / "page_01.txt").read_text() == '- button "Continue"'


def test_a_saved_page_never_carries_the_diagnostic_redaction_placeholder(tmp_path):
    """f148: every question and answer on a recorded page became `<redacted-value>`, so a stuck run's
    page could not be read afterwards. Recordings are written as `snapshot()` returned them (secrets
    already hidden upstream, f015); a label the structural sanitizer's allow-list does not know must survive."""
    text = '- textbox "Street Address": 221B Baker Street\n- radio "Are you legally authorized to work in the US?"'
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
