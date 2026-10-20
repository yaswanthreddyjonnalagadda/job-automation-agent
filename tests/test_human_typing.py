"""Human-paced typing (owner's request, 20 October 2026).

Boxes are typed one key at a time at a person's speed instead of being filled instantly, which
anti-bot scripts score as automation. A box that does not keep what was typed falls back to the
old instant fill, so typing can never leave a field worse off than before.
"""
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright

import interaction

KEY_LOG = """
<input id="box" type="text" />
<script>
  window.keys = []; window.events = [];
  const box = document.getElementById('box');
  box.addEventListener('keydown', e => window.keys.push(performance.now()));
  ['input', 'change', 'blur'].forEach(t => box.addEventListener(t, e => window.events.push(t)));
</script>
"""


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    pg = context.new_page()
    yield pg
    context.close()


@pytest.fixture(autouse=True)
def typing_on(monkeypatch):
    monkeypatch.setenv("HUMAN_TYPING", "1")
    monkeypatch.setenv("HUMAN_TYPING_DELAY_MS", "20")
    monkeypatch.setattr(interaction, "_pause", lambda seconds: None)


def test_a_short_answer_is_typed_one_key_at_a_time(page):
    page.set_content(KEY_LOG)
    box = page.locator("#box")
    interaction.fill_and_dispatch(box, "Jonna")
    assert box.input_value() == "Jonna"
    keys = page.evaluate("() => window.keys")
    assert len(keys) == len("Jonna")                       # a keydown per character, not one instant fill
    assert keys[-1] - keys[0] >= (len(keys) - 1) * 10      # spaced out at a person's pace, not all at once
    events = page.evaluate("() => window.events")
    assert {"input", "change", "blur"} <= set(events)      # reactive forms still register the value


def test_typing_replaces_what_was_in_the_box(page):
    page.set_content(KEY_LOG)
    box = page.locator("#box")
    box.fill("stale value")
    interaction.fill_and_dispatch(box, "fresh")
    assert box.input_value() == "fresh"


def test_a_long_answer_is_inserted_at_once_like_a_paste(page):
    page.set_content(KEY_LOG.replace('<input id="box" type="text" />', '<textarea id="box"></textarea>'))
    box = page.locator("#box")
    essay = "I have built and run enterprise networks for years. " * 6
    assert len(essay) > interaction.HUMAN_TYPING_MAX_CHARS
    interaction.fill_and_dispatch(box, essay)
    assert box.input_value() == essay
    assert page.evaluate("() => window.keys.length") == 0


def test_typing_can_be_turned_off(page, monkeypatch):
    monkeypatch.setenv("HUMAN_TYPING", "0")
    page.set_content(KEY_LOG)
    box = page.locator("#box")
    interaction.fill_and_dispatch(box, "Jonna")
    assert box.input_value() == "Jonna"
    assert page.evaluate("() => window.keys.length") == 0


def test_a_box_that_drops_keystrokes_falls_back_to_the_instant_fill(page):
    # A widget that swallows key presses: typing leaves it empty, so the old fill sets the value.
    page.set_content(KEY_LOG + "<script>box.addEventListener('keydown', e => e.preventDefault());</script>")
    box = page.locator("#box")
    interaction.fill_and_dispatch(box, "Network Engineer")
    assert box.input_value() == "Network Engineer"


def test_a_masked_box_that_adds_its_own_punctuation_counts_as_typed():
    assert interaction._same_text("(555) 123-4567", "5551234567")
    assert not interaction._same_text("555123", "5551234567")
    assert not interaction._same_text("", "abc")


def test_clearing_a_box_keeps_the_plain_fill():
    calls = []
    box = SimpleNamespace(fill=lambda value, **k: calls.append(("fill", value)),
                          press_sequentially=lambda *a, **k: calls.append(("typed",)),
                          evaluate=lambda *a, **k: None)
    interaction.fill_and_dispatch(box, "")
    assert calls == [("fill", "")]


def test_a_locator_that_cannot_type_still_gets_its_value():
    calls = []
    box = SimpleNamespace(fill=lambda value, **k: calls.append(value), evaluate=lambda *a, **k: None)
    interaction.fill_and_dispatch(box, "Seattle")
    interaction.type_text(box, "secret-free text")
    assert calls == ["Seattle", "secret-free text"]


def test_type_text_types_a_sign_in_box(page):
    page.set_content(KEY_LOG.replace('type="text"', 'type="password"'))
    box = page.locator("#box")
    interaction.type_text(box, "Synthetic#Pass1")
    assert box.input_value() == "Synthetic#Pass1"
    assert page.evaluate("() => window.keys.length") == len("Synthetic#Pass1")


def test_the_key_delay_varies_but_stays_in_a_human_range(monkeypatch):
    monkeypatch.setenv("HUMAN_TYPING_DELAY_MS", "45")
    delays = {interaction.keystroke_delay_ms() for _ in range(50)}
    assert len(delays) > 1
    assert all(30 <= d <= 64 for d in delays)
    monkeypatch.setenv("HUMAN_TYPING_DELAY_MS", "not a number")
    assert 30 <= interaction.keystroke_delay_ms() <= 64

