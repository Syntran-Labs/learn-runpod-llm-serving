"""Unit tests for scenario/payload construction. No network calls."""

from __future__ import annotations

import pytest

from scripts.llm_bench import scenarios


def test_build_payload_short_sets_required_flags():
    payload = scenarios.build_payload("short", model="gpt-oss-20b")

    assert payload["model"] == "gpt-oss-20b"
    assert payload["max_tokens"] == scenarios.MAX_TOKENS
    assert payload["cache_prompt"] is False
    assert payload["stream"] is False
    assert payload["messages"] == [{"role": "user", "content": scenarios.SHORT_PROMPT}]


def test_build_payload_long_repeats_dense_context():
    payload = scenarios.build_payload("long", model="gpt-oss-20b")

    content = payload["messages"][0]["content"]
    assert content.startswith(scenarios._LONG_SENTENCE)
    assert content.endswith(scenarios.LONG_SUFFIX)
    assert content.count("elementary cellular automaton") >= scenarios._LONG_REPEAT_COUNT


def test_build_payload_unknown_scenario_raises():
    with pytest.raises(ValueError):
        scenarios.build_payload("medium", model="gpt-oss-20b")
