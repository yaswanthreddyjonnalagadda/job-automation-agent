"""A box the site redraws while it is typed into is checked by a fresh read, not counted failed, and settling does not
wait for a page that never goes quiet (f306).

Rackspace's Workday, 2 November 2026: typing into a box made the site draw a new box in its place, so the agent's locator
was stale, its Tab and readback each waited out a timeout (3 s per box) and the fill was marked failed although the new box
held the right value. Separately, every settle waited the full 8 s for the network to go quiet on a site that never does:
settle(800 ms) took 8.8 s, and "Apply Manually" took 11.8 s.
"""
import time
from types import SimpleNamespace

import pytest

import page_agent
from test_action_verification import agent, browser, control_for, page  # noqa: F401

REDRAWN = '''<label for="f">First Name</label><div id="slot"><input id="f"></div>
<script>
  document.getElementById("slot").addEventListener("input", e => {
    const value = e.target.value;
    setTimeout(() => { document.getElementById("slot").innerHTML =
      '<input id="f" value="' + value + '">'; }, 30);
  });
</script>'''


def test_a_box_redrawn_while_typed_into_is_verified_by_a_fresh_read(page):
    page.set_content(REDRAWN)
    a = agent()
    control = control_for(a, page, "textbox", "First Name")
    answer = page_agent.Answer(control.ref, control.question, "fill", "Yaswanth Reddy", "profile")
    started = time.monotonic()
    assert a.do(page, answer, control) is True
    assert page.input_value("#f") == "Yaswanth Reddy"
    assert time.monotonic() - started < 3.0, "the stale box was waited on"


def test_a_redrawn_box_that_lost_the_value_is_still_failed(page):
    page.set_content(REDRAWN.replace('value="\' + value + \'"', 'value=""'))
    a = agent()
    control = control_for(a, page, "textbox", "First Name")
    answer = page_agent.Answer(control.ref, control.question, "fill", "Yaswanth Reddy", "profile")
    assert a.do(page, answer, control) is False


def test_a_site_that_never_goes_quiet_is_waited_on_once_not_every_time(monkeypatch):
    a = agent()
    waits = []

    class Tab:
        url = "https://rackspace.wd1.myworkdayjobs.com/en-US/External"

        def wait_for_load_state(self, state, timeout=0):
            if state == "networkidle":
                waits.append(timeout)
                raise TimeoutError("never idle")

        def wait_for_timeout(self, ms):
            pass
    monkeypatch.setattr(a, "tab", lambda page: Tab())
    for _ in range(3):
        a.settle(None, 0)
    assert waits == [a.NETWORK_QUIET_WAIT_MS, a.NEVER_QUIET_WAIT_MS, a.NEVER_QUIET_WAIT_MS]


def test_a_site_that_does_go_quiet_keeps_its_full_wait(monkeypatch):
    a = agent()
    waits = []

    class Tab:
        url = "https://quiet.example/apply"

        def wait_for_load_state(self, state, timeout=0):
            if state == "networkidle":
                waits.append(timeout)

        def wait_for_timeout(self, ms):
            pass
    monkeypatch.setattr(a, "tab", lambda page: Tab())
    a.settle(None, 0)
    a.settle(None, 0)
    assert waits == [a.NETWORK_QUIET_WAIT_MS, a.NETWORK_QUIET_WAIT_MS]
