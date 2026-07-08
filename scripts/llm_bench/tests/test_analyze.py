"""Unit tests for process()/analyze() using fixture JSON. No network calls."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.llm_bench import analyze

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def test_process_timings_source():
    raw = _load_fixture("llama_server_response.json")
    result = analyze.process(raw)

    assert result["metrics_source"] == analyze.METRIC_SOURCE_TIMINGS
    assert result["ok"] is True
    assert result["prompt_n"] == 81
    assert result["pp_tok_s"] == pytest.approx(346.6)
    assert result["predicted_n"] == 168
    assert result["gen_tok_s"] == pytest.approx(189.0)
    assert result["ttft_s"] == pytest.approx(0.2337, abs=1e-3)


def test_process_wall_clock_fallback():
    raw = _load_fixture("wall_clock_fallback_response.json")
    result = analyze.process(raw)

    assert result["metrics_source"] == analyze.METRIC_SOURCE_WALL_CLOCK
    assert result["prompt_n"] == 80
    assert result["predicted_n"] == 150
    # No phase-level timing available without streaming.
    assert result["pp_tok_s"] is None
    assert result["ttft_s"] is None
    assert result["gen_tok_s"] == pytest.approx(150 / 3.2)


def test_process_error_record_short_circuits():
    raw = _load_fixture("error_response.json")
    result = analyze.process(raw)

    assert result["ok"] is False
    assert result["error"] == "timeout: read timed out after 120.0s"
    assert result["prompt_n"] is None
    assert result["gen_tok_s"] is None


def test_analyze_aggregates_and_report_single_run():
    raw = _load_fixture("llama_server_response.json")
    processed = [analyze.process(raw)]

    aggregates, report = analyze.analyze(processed, pod_rate_usd_hr=0.25)

    short_stats = aggregates["short"]
    assert short_stats["gen_tok_s"]["mean"] == pytest.approx(189.0)
    assert short_stats["gen_tok_s"]["median"] == pytest.approx(189.0)
    assert short_stats["gen_tok_s"]["stdev"] is None  # single sample

    assert "## SHORT" in report
    assert "189.0" in report
    assert "346.6" in report
    # cost per M generated tokens = 0.25 * 1e6 / (189.0 * 3600)
    expected_cost = 0.25 * 1_000_000 / (189.0 * 3600)
    assert f"${expected_cost:.2f}" in report


def test_analyze_multiple_runs_computes_stdev():
    raw_a = _load_fixture("llama_server_response.json")
    raw_b = json.loads(json.dumps(raw_a))
    raw_b["run_index"] = 2
    raw_b["response"]["timings"]["predicted_per_second"] = 199.0

    processed = [analyze.process(raw_a), analyze.process(raw_b)]
    aggregates, report = analyze.analyze(processed)

    gen_stats = aggregates["short"]["gen_tok_s"]
    assert gen_stats["mean"] == pytest.approx((189.0 + 199.0) / 2)
    assert gen_stats["stdev"] is not None
    assert "| 1 |" in report
    assert "| 2 |" in report


def test_analyze_reports_errors_separately():
    error_raw = _load_fixture("error_response.json")
    processed = [analyze.process(error_raw)]

    aggregates, report = analyze.analyze(processed)

    assert "Errors:" in report
    assert "timeout" in report
    # No successful runs, so no numeric aggregate values.
    assert aggregates["long"]["gen_tok_s"]["mean"] is None
