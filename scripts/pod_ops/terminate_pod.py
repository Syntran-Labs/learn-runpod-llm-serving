"""Gated pod termination: requires --yes + a typed confirmation.

Terminate wipes the pod's volume - irreversible. Per RUNBOOK.md section 7,
Terminate (not Stop) is the default at session end. This script only ever
performs Terminate; it does not implement Stop.

Usage:
    python -m scripts.pod_ops.terminate_pod --pod-id <ID>          # dry run
    python -m scripts.pod_ops.terminate_pod --pod-id <ID> --yes    # real terminate
"""

from __future__ import annotations

import argparse
import sys

from scripts.pod_ops import runpod_api


def _confirm_interactively(pod_id: str) -> bool:
    if not sys.stdin.isatty():
        print("error: stdin is not interactive - refusing to terminate without a typed confirmation.",
              file=sys.stderr)
        return False
    typed = input(f"Type TERMINATE to permanently wipe pod {pod_id} (anything else cancels): ")
    return typed.strip() == "TERMINATE"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pod-id", required=True)
    parser.add_argument("--yes", action="store_true",
                         help="actually call the API (omit for a dry run / no-op preview)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print(f"Will terminate pod {args.pod_id} - this wipes its volume and stops all billing on it.")

    if not args.yes:
        print("Dry run only (no --yes passed). Nothing was terminated.")
        return 0

    if not _confirm_interactively(args.pod_id):
        print("Cancelled - pod left running.")
        return 1

    try:
        runpod_api.terminate_pod(args.pod_id)
    except runpod_api.MissingApiKeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except runpod_api.GraphQLError as exc:
        print(f"error: RunPod API rejected the request (schema may have changed - "
              f"check https://docs.runpod.io/api-reference): {exc}", file=sys.stderr)
        return 2

    print(f"pod {args.pod_id} terminated. Verify in console.runpod.io -> Billing that "
          f"it is no longer accruing charges (RUNBOOK.md section 7).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
