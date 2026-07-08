"""Orchestration + CLI: run scenarios against the endpoint, write raw JSONL and a report.

Usage:
    python -m scripts.llm_bench.runner --runs 3 --scenarios short,long
    python -m scripts.llm_bench.runner --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.llm_bench import analyze, client, scenarios

DEFAULT_RUNS = 3
DEFAULT_SCENARIOS = "short,long"
REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_RAW_DIR = REPO_ROOT / "results" / "raw"
RESULTS_DIR = REPO_ROOT / "results"


def _run_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def collect(
    scenario_names: list[str],
    runs: int,
    config: client.ClientConfig,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Run every (scenario, run index) combination and return raw records.

    In --dry-run mode, prints payloads and returns an empty list (nothing is
    sent, so there is nothing meaningful to persist).
    """
    records: list[dict[str, Any]] = []

    for scenario in scenario_names:
        for run_index in range(1, runs + 1):
            payload = scenarios.build_payload(scenario, config.model)

            if dry_run:
                print(f"# {scenario} run {run_index} -> {config.chat_completions_url}")
                print(json.dumps(payload, indent=2))
                continue

            result = client.send(payload, config)
            metrics_source = None
            if result["ok"]:
                response = result.get("response") or {}
                metrics_source = (
                    analyze.METRIC_SOURCE_TIMINGS
                    if response.get("timings")
                    else analyze.METRIC_SOURCE_WALL_CLOCK
                )

            records.append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "scenario": scenario,
                    "run_index": run_index,
                    "model": config.model,
                    "base_url_host": config.host_only,
                    "config_snapshot": config.snapshot(),
                    "ok": result["ok"],
                    "error": result.get("error"),
                    "metrics_source": metrics_source,
                    "response": result.get("response"),
                    "wall_clock_s": result["wall_clock_s"],
                }
            )

    return records


def _write_raw_jsonl(records: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")


def _write_report(records: list[dict[str, Any]], pod_rate_usd_hr: float | None, path: Path) -> None:
    processed = [analyze.process(r) for r in records]
    _, report_text = analyze.analyze(processed, pod_rate_usd_hr)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report_text, encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark an OpenAI-compatible chat endpoint.")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--scenarios", type=str, default=DEFAULT_SCENARIOS)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    scenario_names = [s.strip().lower() for s in args.scenarios.split(",") if s.strip()]
    unknown = [s for s in scenario_names if s not in scenarios.SCENARIOS]
    if unknown:
        print(f"error: unknown scenario(s) {unknown}, expected one of {sorted(scenarios.SCENARIOS)}", file=sys.stderr)
        return 2

    config = client.load_config_from_env()
    records = collect(scenario_names, args.runs, config, dry_run=args.dry_run)

    if args.dry_run:
        return 0

    ts = _run_timestamp()
    raw_path = RESULTS_RAW_DIR / f"{ts}.jsonl"
    report_path = RESULTS_DIR / f"report-{ts}.md"
    _write_raw_jsonl(records, raw_path)
    _write_report(records, config.pod_rate_usd_hr, report_path)

    print(f"raw records: {raw_path}")
    print(f"report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
