"""PLAN-003 section 6: "cost per application is measured in Phase 1, capped
from then on, and reported by every phase." These tests cover the counter
itself (Usage, its cost math, and the pricing table's model lookup) and that
every one of ClaudeClient's three separate places that call
self._client.messages.create actually feeds it -- a hand-edited third call
site is exactly the kind of thing a copy-paste miss hides in.
"""

from __future__ import annotations

import types

import pytest

from claude_integration import ClaudeClient, ClaudeIntegrationError, Usage, _pricing_for


def _fake_config(**overrides):
    defaults = dict(claude_max_retries=1, claude_request_timeout=5.0, anthropic_model="claude-sonnet-5")
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


def _fake_response(text: str, *, input_tokens=100, output_tokens=50,
                    cache_creation_input_tokens=None, cache_read_input_tokens=None):
    usage = types.SimpleNamespace(
        input_tokens=input_tokens, output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
    )
    block = types.SimpleNamespace(type="text", text=text)
    return types.SimpleNamespace(content=[block], usage=usage)


def _client_with(create_fn) -> ClaudeClient:
    client = ClaudeClient.__new__(ClaudeClient)
    client._config = _fake_config()
    client.usage = Usage()
    client._client = types.SimpleNamespace(messages=types.SimpleNamespace(create=create_fn))
    return client


# --- Usage and its cost math, in isolation ---------------------------------

def test_usage_accumulates_token_counts_and_call_counts():
    total = Usage()
    total += Usage(input_tokens=100, output_tokens=20, calls=1)
    total += Usage(input_tokens=300, output_tokens=40, calls=1)
    assert (total.input_tokens, total.output_tokens, total.calls) == (400, 60, 2)


def test_cost_is_computed_at_the_configured_models_published_rate():
    # $2/MTok in, $10/MTok out, $2.50/MTok cache write, $0.20/MTok cache read
    # (platform.claude.com/docs/en/about-claude/pricing, checked 24 Sep 2026).
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000,
                  cache_creation_input_tokens=1_000_000, cache_read_input_tokens=1_000_000)
    assert usage.cost_usd("claude-sonnet-5") == pytest.approx(2.00 + 10.00 + 2.50 + 0.20)


def test_an_unpriced_model_is_none_not_a_silent_zero():
    usage = Usage(input_tokens=1_000, output_tokens=1_000)
    assert usage.cost_usd("some-model-not-yet-in-the-table") is None
    assert _pricing_for("some-model-not-yet-in-the-table") is None


def test_a_dated_model_id_still_prices_by_its_family():
    # ANTHROPIC_MODEL can be a dated snapshot id; the family's rate still applies.
    assert _pricing_for("claude-sonnet-5-20261201") == _pricing_for("claude-sonnet-5")


def test_zero_usage_costs_zero_not_none_when_the_model_is_priced():
    assert Usage().cost_usd("claude-sonnet-5") == 0.0


# --- Every messages.create call site actually records usage ----------------

def test_call_records_the_response_it_receives():
    client = _client_with(lambda **kw: _fake_response("hello", input_tokens=120, output_tokens=30))
    result = client._call(system="s", user_message="u")
    assert result == "hello"
    assert (client.usage.input_tokens, client.usage.output_tokens, client.usage.calls) == (120, 30, 1)
    summary = client.cost_summary()
    assert summary["estimated_cost_usd"] == pytest.approx((120 * 2.00 + 30 * 10.00) / 1_000_000)


def test_two_calls_on_one_client_both_count_towards_its_one_total():
    # brain_for() makes one ClaudeClient per application, so this total IS
    # the per-application figure PLAN-003 section 6 asks for -- proven here
    # by checking it actually adds up across more than one call.
    client = _client_with(lambda **kw: _fake_response("x", input_tokens=100, output_tokens=10))
    client._call(system="s", user_message="one")
    client._call(system="s", user_message="two")
    assert client.usage.calls == 2
    assert client.usage.input_tokens == 200


def test_plan_page_records_its_own_call_sites_usage():
    client = _client_with(None)
    client._client.messages.create = lambda **kw: _fake_response(
        '{"page_kind": "application_form", "answers": [], "leave_for_owner": [], '
        '"mismatches": [], "next": {"ref": "", "label": "", "kind": "none"}}',
        input_tokens=500, output_tokens=80,
    )
    plan = client.plan_page("snapshot text", {"profile": {}})
    assert plan["page_kind"] == "application_form"
    assert (client.usage.input_tokens, client.usage.output_tokens) == (500, 80)


def test_a_cut_off_reply_still_counts_its_tokens_before_the_retry():
    """A reply cut off mid-JSON still cost real tokens -- plan_page's own
    retry-with-a-shorter-prompt loop must not lose that spend just because
    the content wasn't usable."""
    attempts = []

    def create(**kw):
        attempts.append(kw)
        if len(attempts) == 1:
            return _fake_response('{"page_kind": "application_form", "answers": [' , input_tokens=9_000, output_tokens=16_000)
        return _fake_response(
            '{"page_kind": "application_form", "answers": [], "leave_for_owner": [], '
            '"mismatches": [], "next": {"ref": "", "label": "", "kind": "none"}}',
            input_tokens=9_200, output_tokens=200,
        )

    client = _client_with(None)
    client._config = _fake_config(claude_max_retries=2)
    client._client.messages.create = create
    plan = client.plan_page("snapshot text", {"profile": {}})
    assert plan["page_kind"] == "application_form"
    assert len(attempts) == 2, "the cut-off reply should have triggered exactly one retry"
    # Both the wasted cut-off attempt and the successful retry must be counted.
    assert client.usage.calls == 2
    assert client.usage.input_tokens == 9_000 + 9_200
    assert client.usage.output_tokens == 16_000 + 200


def test_read_page_records_its_own_call_sites_usage():
    client = _client_with(None)
    client._client.messages.create = lambda **kw: _fake_response(
        '{"page": "application_form", "click": "", "why": "fields waiting"}',
        input_tokens=1_500, output_tokens=40,
    )
    result = client.read_page(b"\x89PNG....", "https://example.com/apply", "apply")
    assert result["page"] == "application_form"
    assert (client.usage.input_tokens, client.usage.output_tokens) == (1_500, 40)


def test_cache_tokens_are_counted_even_when_the_sdk_leaves_them_none():
    # anthropic.types.Usage types cache_*_input_tokens as Optional[int]; a
    # response with no cache activity at all must not raise or corrupt the
    # running total when they come back None.
    client = _client_with(lambda **kw: _fake_response("hi", input_tokens=10, output_tokens=5))
    client._call(system="s", user_message="u")
    assert client.usage.cache_creation_input_tokens == 0
    assert client.usage.cache_read_input_tokens == 0


def test_cost_summary_reports_the_configured_model_and_totals():
    client = _client_with(lambda **kw: _fake_response("hi", input_tokens=10, output_tokens=5))
    client._call(system="s", user_message="u")
    summary = client.cost_summary()
    assert summary["model"] == "claude-sonnet-5"
    assert summary["calls"] == 1
    assert summary["input_tokens"] == 10 and summary["output_tokens"] == 5
