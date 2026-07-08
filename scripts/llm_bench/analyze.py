"""Turn raw per-run records into normalized metrics and a Markdown report.

process(): one raw JSONL record -> one normalized metrics dict.
analyze(): a list of normalized records -> (aggregate stats, Markdown report).
"""

from __future__ import annotations

import statistics
from typing import Any

METRIC_SOURCE_TIMINGS = "timings"
METRIC_SOURCE_WALL_CLOCK = "wall_clock"

_NUMERIC_METRICS = ("prompt_n", "pp_tok_s", "predicted_n", "gen_tok_s", "ttft_s")


def process(raw_record: dict[str, Any]) -> dict[str, Any]:
    """Normalize one raw run record into a flat metrics dict.

    Prefers llama-server's `timings` block; falls back to client-measured
    wall_clock_s + OpenAI-standard `usage` counts when timings is absent
    (e.g. OpenRouter). In the fallback path pp_tok_s and ttft_s are not
    derivable from a non-streamed response and are left as None.
    """
    base: dict[str, Any] = {
        "scenario": raw_record.get("scenario"),
        "run_index": raw_record.get("run_index"),
        "timestamp": raw_record.get("timestamp"),
        "ok": raw_record.get("ok", False),
        "error": raw_record.get("error"),
        "metrics_source": raw_record.get("metrics_source"),
        "prompt_n": None,
        "pp_tok_s": None,
        "predicted_n": None,
        "gen_tok_s": None,
        "ttft_s": None,
    }

    if not raw_record.get("ok"):
        return base

    response = raw_record.get("response") or {}
    timings = response.get("timings")
    usage = response.get("usage") or {}

    if timings:
        base["metrics_source"] = METRIC_SOURCE_TIMINGS
        base["prompt_n"] = timings.get("prompt_n")
        base["pp_tok_s"] = timings.get("prompt_per_second")
        base["predicted_n"] = timings.get("predicted_n")
        base["gen_tok_s"] = timings.get("predicted_per_second")
        prompt_ms = timings.get("prompt_ms")
        base["ttft_s"] = prompt_ms / 1000 if prompt_ms is not None else None
        return base

    base["metrics_source"] = METRIC_SOURCE_WALL_CLOCK
    wall_clock_s = raw_record.get("wall_clock_s")
    prompt_n = usage.get("prompt_tokens")
    predicted_n = usage.get("completion_tokens")
    base["prompt_n"] = prompt_n
    base["predicted_n"] = predicted_n
    if predicted_n is not None and wall_clock_s:
        # Approximation: attributes the full request wall time to generation
        # since a non-streamed response can't separate prompt vs. gen phases.
        base["gen_tok_s"] = predicted_n / wall_clock_s
    return base


def _stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "median": None, "stdev": None}
    return {
        "mean": statistics.mean(values),
        "median": statistics.median(values),
        "stdev": statistics.stdev(values) if len(values) > 1 else None,
    }


def _fmt(value: float | None, digits: int = 1) -> str:
    return f"{value:.{digits}f}" if value is not None else "N/A"


def analyze(
    records: list[dict[str, Any]], pod_rate_usd_hr: float | None = None
) -> tuple[dict[str, Any], str]:
    """Aggregate normalized records per scenario and render a Markdown report.

    Returns (aggregates_by_scenario, markdown_report_text).
    """
    scenarios = sorted({r["scenario"] for r in records if r.get("scenario")})
    aggregates: dict[str, Any] = {}
    lines: list[str] = ["# Benchmark report", ""]

    for scenario in scenarios:
        scenario_records = [r for r in records if r.get("scenario") == scenario]
        ok_records = [r for r in scenario_records if r.get("ok")]
        error_records = [r for r in scenario_records if not r.get("ok")]

        metric_values = {
            metric: [r[metric] for r in ok_records if r.get(metric) is not None]
            for metric in _NUMERIC_METRICS
        }
        metric_stats = {metric: _stats(values) for metric, values in metric_values.items()}
        aggregates[scenario] = metric_stats

        lines.append(f"## {scenario.upper()}")
        lines.append("")
        lines.append("| Run | prompt_n | pp (tok/s) | gen (tok/s) | TTFT (s) |")
        lines.append("|---|---|---|---|---|")
        for r in ok_records:
            lines.append(
                f"| {r['run_index']} | {r['prompt_n'] if r['prompt_n'] is not None else 'N/A'} "
                f"| {_fmt(r['pp_tok_s'])} | {_fmt(r['gen_tok_s'])} | {_fmt(r['ttft_s'], 2)} |"
            )
        lines.append("")
        lines.append("| Aggregate | pp (tok/s) | gen (tok/s) | TTFT (s) |")
        lines.append("|---|---|---|---|")
        lines.append(
            "| mean | "
            f"{_fmt(metric_stats['pp_tok_s']['mean'])} | {_fmt(metric_stats['gen_tok_s']['mean'])} "
            f"| {_fmt(metric_stats['ttft_s']['mean'], 2)} |"
        )
        lines.append(
            "| median | "
            f"{_fmt(metric_stats['pp_tok_s']['median'])} | {_fmt(metric_stats['gen_tok_s']['median'])} "
            f"| {_fmt(metric_stats['ttft_s']['median'], 2)} |"
        )
        lines.append(
            "| stdev | "
            f"{_fmt(metric_stats['pp_tok_s']['stdev'])} | {_fmt(metric_stats['gen_tok_s']['stdev'])} "
            f"| {_fmt(metric_stats['ttft_s']['stdev'], 2)} |"
        )
        lines.append("")

        if pod_rate_usd_hr:
            mean_gen_tok_s = metric_stats["gen_tok_s"]["mean"]
            if mean_gen_tok_s:
                cost_per_m = pod_rate_usd_hr * 1_000_000 / (mean_gen_tok_s * 3600)
                lines.append(f"Cost per M generated tokens: **${cost_per_m:.2f}**")
                lines.append("")

        if error_records:
            lines.append("Errors:")
            for r in error_records:
                lines.append(f"- run {r['run_index']}: {r.get('error')}")
            lines.append("")

    return aggregates, "\n".join(lines)
