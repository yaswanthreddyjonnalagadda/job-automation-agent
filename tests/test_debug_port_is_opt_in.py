"""The agent's browser holds the owner's Gmail and employer sign-ins, so a DevTools
port is opened only when the owner asks for one, and only on loopback."""
import pytest

from browser_automation import JobApplicationAssistant


def args():
    return JobApplicationAssistant._debug_port_args()


def test_no_port_is_opened_by_default(monkeypatch):
    monkeypatch.delenv("JAA_DEBUG_PORT", raising=False)
    assert args() == []


@pytest.mark.parametrize("value", ["", " ", "abc", "0", "80", "1023", "65536", "-9222", "9222; calc", "9222.5"])
def test_an_unusable_value_opens_nothing(monkeypatch, value):
    monkeypatch.setenv("JAA_DEBUG_PORT", value)
    assert args() == []


def test_a_chosen_port_is_loopback_only(monkeypatch):
    monkeypatch.setenv("JAA_DEBUG_PORT", "9333")
    assert args() == ["--remote-debugging-port=9333", "--remote-debugging-address=127.0.0.1"]
