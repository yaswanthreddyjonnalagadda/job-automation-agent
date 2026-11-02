"""
When several saved answers match a question, the most specific one answers it, not the first in the file (f194).

The OPT question "Are you currently in a pre or post completion OPT ...?" has its own saved answer ("No"), but the
broad pattern re:status sits earlier in data/profile_answers.json and answered it "Graduated". library_answer now
ranks every match: a saved exact question first, then the pattern that covers the most of the question.
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from browser_automation import JobApplicationAssistant  # noqa: E402
from page_agent import PageAgent  # noqa: E402

OPT_Q = "Are you currently in a pre or post completion OPT or STEM OPT period?"
LIBRARY = {
    "re:status": "Graduated",
    r"re:are\s+you\s+currently\s+in\s+a\s+pre\s+or\s+post\s+completion\s+opt": "No",
    "What is your current status?": "Graduated",
}


def agent(library: dict):
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    a = PageAgent(assistant, None, SimpleNamespace(), config.UserProfile(), None,
                  SimpleNamespace(title="Engineer", company="Example", url="https://example.com"))
    a._profile_answer_library = dict(library)
    a._answer_library = lambda: a._profile_answer_library
    return a


@pytest.mark.parametrize("order", list(itertools.permutations(LIBRARY.items())))
def test_the_specific_answer_wins_whatever_the_order(order):
    a = agent(dict(order))
    assert a.library_answer(OPT_Q) == "No"
    assert a.library_answer("What is your current status?") == "Graduated"


def test_a_broad_pattern_still_answers_what_only_it_matches():
    assert agent(LIBRARY).library_answer("Education status") == "Graduated"


def test_exact_only_still_ignores_patterns():
    assert agent(LIBRARY).library_answer(OPT_Q, exact_only=True) == ""
