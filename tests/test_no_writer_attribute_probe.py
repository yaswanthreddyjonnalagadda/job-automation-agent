"""
With no writer key, asking whether the brain has an optional feature must say "no", not crash the run (f190).

Datadog audit, 18 October: a run with FORM_ANSWER_MODE=profile and no writer key stopped before the browser opened.
apply_flow asks `hasattr(claude, "cost_summary")`; the Brain sends that name to Writers, whose __getattr__ raised
ClaudeIntegrationError -- not AttributeError -- so hasattr let it through. Every attribute probe (hasattr, getattr with
a default) on any name that is not a document must answer "absent"; asking for a document still says why it cannot.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ai_choice  # noqa: E402
from claude_integration import ClaudeIntegrationError  # noqa: E402


class Answers:
    def plan_page(self, *a, **k):
        return {}


PROBED = ["cost_summary", "usage", "model", "close", "anything_new_later", "last_error"]


@pytest.mark.parametrize("name", PROBED)
def test_probing_a_brain_with_no_writer_answers_absent(name):
    brain = ai_choice.Brain(Answers(), ai_choice.Writers([]))
    assert hasattr(brain, name) is False
    assert getattr(brain, name, "default") == "default"
    assert hasattr(ai_choice.Writers([]), name) is False


@pytest.mark.parametrize("name", sorted(ai_choice.DOCUMENTS))
def test_asking_for_a_document_with_no_writer_still_says_why(name):
    brain = ai_choice.Brain(Answers(), ai_choice.Writers([]))
    with pytest.raises(ClaudeIntegrationError, match="no writer"):
        getattr(brain, name)


def test_the_answering_side_is_untouched():
    brain = ai_choice.Brain(Answers(), ai_choice.Writers([]))
    assert brain.plan_page() == {}
