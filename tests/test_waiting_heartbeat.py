"""A waiting run proves it is alive; the dashboard hides notes no run touches (f331).

Analog Devices, 3 November: four runs stopped at its account form and the
dashboard kept offering Continue on a dead run's fossilized signal -- a
_waiting_ note only proved a run had waited once. The wait loop now touches
its note every poll, and notes untouched for five minutes are not listed.
"""
import os
import time

import pytest

import web_ui
from browser_automation import JobApplicationAssistant, waiting_note_for


def test_a_waiting_run_touches_its_note(tmp_path):
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    signal = tmp_path / "_signal_Acme_Engineer.txt"
    note = waiting_note_for(signal)
    note.write_text("2026-11-03T19:02:46", encoding="utf-8")
    fossil = time.time() - 601
    os.utime(note, (fossil, fossil))
    with pytest.raises(TimeoutError):
        assistant._wait_for_signal(signal, poll_seconds=0.01, timeout_seconds=0.05, page=None)
    assert note.stat().st_mtime > fossil


def test_the_dashboard_lists_live_notes_and_hides_fossils(tmp_path):
    live = tmp_path / "_waiting_Live_Co_Engineer.txt"
    dead = tmp_path / "_waiting_Dead_Co_Engineer.txt"
    live.write_text("2026-11-03T19:02:46", encoding="utf-8")
    dead.write_text("2026-11-03T19:02:46", encoding="utf-8")
    labels = [run["label"] for run in web_ui.waiting_runs(tmp_path)]
    assert any("Live Co" in label for label in labels)
    ancient = time.time() - 601
    os.utime(dead, (ancient, ancient))
    labels = [run["label"] for run in web_ui.waiting_runs(tmp_path)]
    assert all("Dead Co" not in label for label in labels)
    assert any("Live Co" in label for label in labels)
