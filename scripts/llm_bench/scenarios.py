"""Prompt builders for the SHORT and LONG benchmark scenarios.

Mirrors scripts/bench.sh's prompt content (kept as unmodified reference
behavior) so results from either tool are comparable. The LONG repeat count
is recalibrated below: bench.sh's 300 repeats measured at 9,680 tokens
against gpt-oss-20b's real tokenizer on 2026-07-02, which exceeds an
8192-token --ctx-size server (see RUNBOOK.md) on its own, before any
completion tokens.
"""

from __future__ import annotations

from typing import Any, Callable

MAX_TOKENS = 300

# ~80-token conversational prompt.
SHORT_PROMPT = (
    "Briefly explain the difference between an elementary cellular automaton "
    "and a two-dimensional one."
)

# Dense repeated sentence, simulates retrieved RAG chunks at ~5-6K tokens.
_LONG_SENTENCE = (
    "An elementary cellular automaton is a discrete dynamical system defined "
    "over a one-dimensional tape of binary cells that evolve according to a "
    "deterministic local rule applied synchronously. "
)
# Calibrated from a live measurement: 300 repeats -> 9,680 prompt tokens
# (~32.27 tok/repeat) on gpt-oss-20b. 174 repeats targets ~5.6K tokens,
# leaving headroom under an 8192-token context for the 300-token completion.
_LONG_REPEAT_COUNT = 174
LONG_SUFFIX = " Summarize the text above in 3 bullet points."


def build_short_messages() -> list[dict[str, str]]:
    return [{"role": "user", "content": SHORT_PROMPT}]


def build_long_messages() -> list[dict[str, str]]:
    context = _LONG_SENTENCE * _LONG_REPEAT_COUNT
    return [{"role": "user", "content": context + LONG_SUFFIX}]


SCENARIOS: dict[str, Callable[[], list[dict[str, str]]]] = {
    "short": build_short_messages,
    "long": build_long_messages,
}


def build_payload(scenario: str, model: str) -> dict[str, Any]:
    """Build an OpenAI-compatible chat completion request body.

    cache_prompt is llama-server-specific and harmlessly ignored by other
    OpenAI-compatible backends (Ollama, OpenRouter).
    """
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}, expected one of {sorted(SCENARIOS)}")
    return {
        "model": model,
        "messages": SCENARIOS[scenario](),
        "max_tokens": MAX_TOKENS,
        "cache_prompt": False,
        "stream": False,
    }
