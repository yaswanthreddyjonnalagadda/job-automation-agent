"""No source file holds an invisible control character.

29 September: three regular expressions in page_agent.py and browser_automation.py held a backspace (0x08) where a
word boundary (\b) was meant -- written by a script that turned '\b' into the character. Each silently never
matched: 'browse' as an upload button, 'I agree' as a consent button, 'Country' as a phone's dial code.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCES = sorted(p for p in list(ROOT.glob("*.py")) + list(ROOT.glob("sites/*.py")) + list(ROOT.glob("tests/*.py")))


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_control_characters(path):
    text = path.read_text(encoding="utf-8")
    found = [(n, repr(ch)) for n, line in enumerate(text.splitlines(), 1) for ch in line
             if ord(ch) < 32 and ch not in "\t"]
    assert found == [], f"{path.name}: control characters at {found[:5]}"
