"""One-command session teardown: terminate the pod recorded in .env.

    python -m scripts.pod_ops.down            # dry run - shows status + est. cost
    python -m scripts.pod_ops.down --yes      # real terminate - gated by typed TERMINATE

Terminate wipes the pod's volume and stops all billing - irreversible, which
is exactly what RUNBOOK.md section 7 prescribes at session end (Stop still
bills for disk). The typed TERMINATE gate stays per governance (Critical).

After a confirmed terminate, the POD_* values in .env are blanked so a stale
relay/benchmark fails loudly instead of targeting a dead pod.
"""

from __future__ import annotations

import argparse
import sys

from scripts.pod_ops import envfile, runpod_api
from scripts.pod_ops.terminate_pod import _confirm_interactively

_STALE_KEYS = ("POD_ID", "POD_NAME", "POD_GPU_TYPE", "POD_IMAGE",
               "POD_SSH_CMD", "POD_DIRECT_SSH_CMD")


def _fmt_uptime(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, _ = divmod(rem, 60)
    return f"{h}h{m:02d}m"


def _print_status(pod_id: str, env: dict[str, str]) -> None:
    name = env.get("POD_NAME", "?")
    gpu = env.get("POD_GPU_TYPE", "?")
    print(f"Pod {pod_id} ({name}, {gpu})")
    try:
        info = runpod_api.get_pod(pod_id)
    except (runpod_api.GraphQLError, KeyError, TypeError):
        print("  status: not found via the API - it may already be terminated. "
              "Verify in console.runpod.io.")
        return
    line = f"  status: {info.desired_status}"
    rate = env.get("POD_RATE_USD_HR")
    if info.uptime_s is not None:
        line += f", uptime {_fmt_uptime(info.uptime_s)}"
        if rate:
            line += f", est. GPU cost this boot ~${info.uptime_s / 3600 * float(rate):.2f}"
    print(line)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One command: terminate the current pod and clean up .env.")
    parser.add_argument("--pod-id", default=None,
                        help="defaults to POD_ID from .env")
    parser.add_argument("--yes", action="store_true",
                        help="actually terminate (omit for a dry run / status preview)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    env = envfile.read_values()
    pod_id = args.pod_id or env.get("POD_ID")
    if envfile.is_placeholder(pod_id):
        print("error: no POD_ID in .env and none passed via --pod-id - "
              "nothing to terminate.", file=sys.stderr)
        return 2

    _print_status(pod_id, env)
    print("Terminate wipes the pod's volume and stops all billing on it - irreversible.")

    if not args.yes:
        print("Dry run only (no --yes passed). Nothing was terminated.")
        return 0

    if not _confirm_interactively(pod_id):
        print("Cancelled - pod left running.")
        return 1

    try:
        runpod_api.terminate_pod(pod_id)
    except runpod_api.MissingApiKeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except runpod_api.GraphQLError as exc:
        print(f"error: RunPod API rejected the request (schema may have changed - "
              f"check https://docs.runpod.io/api-reference): {exc}", file=sys.stderr)
        return 2

    # Verify: after terminate the pod query should fail or stop saying RUNNING.
    try:
        info = runpod_api.get_pod(pod_id)
        verdict = f"API still reports status {info.desired_status}"
        gone = info.desired_status != "RUNNING"
    except (runpod_api.GraphQLError, KeyError, TypeError):
        verdict = "API no longer finds the pod"
        gone = True
    print(f"pod {pod_id} terminated ({verdict}).")
    if not gone:
        print("WARNING: the pod still reports RUNNING - check console.runpod.io "
              "-> Billing before walking away.", file=sys.stderr)

    if not args.pod_id or args.pod_id == env.get("POD_ID"):
        envfile.upsert({key: "" for key in _STALE_KEYS if key in env})
        print(f"cleared {', '.join(k for k in _STALE_KEYS if k in env)} in .env.")
    print("Verify in console.runpod.io -> Billing that the pod is no longer "
          "accruing charges (RUNBOOK.md section 7).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
