"""
The "waiting for you" note never has the signal file's own name (f193).

browser_automation.waiting_note_for replaced "_signal_" with "_waiting_"; a signal path without "_signal_" came back
unchanged, so wait_for_signal wrote its timestamp into the signal file and read it straight back as an instruction
(Datadog audit run, 18 October: the review stop kept resuming). For every signal path the note is a different file in
the same folder.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from browser_automation import waiting_note_for  # noqa: E402

NAMES = ["_signal_datadog_20261018.txt", "datadog_audit.txt", "signal.txt", "_waiting_x.txt", "x", "_signal_",
         "my_signal_file.txt", "SIGNAL.TXT", "a_signal_b_signal_c.txt"]


@pytest.mark.parametrize("name", NAMES)
def test_the_note_is_a_different_file_beside_the_signal(tmp_path, name):
    signal = tmp_path / name
    note = waiting_note_for(signal)
    assert note != signal
    assert note.parent == signal.parent


def test_the_usual_name_keeps_its_usual_note(tmp_path):
    assert waiting_note_for(tmp_path / "_signal_62.txt").name == "_waiting_62.txt"
