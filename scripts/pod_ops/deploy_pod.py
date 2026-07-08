"""Gated pod creation: prints the plan, requires --yes + a typed confirmation,
then polls until the pod is RUNNING and records connection details.

This intentionally does NOT run on its own. Per this project's governance
(profile.md, Safety Notes): pod deploy is a Critical action - a human must
read the plan and explicitly confirm before any money is spent.

Usage:
    # Dry run - prints the exact plan, spends nothing, always safe to run.
    python -m scripts.pod_ops.deploy_pod --gpu-type "NVIDIA GeForce RTX 3090"

    # Real deploy - spends money the moment it succeeds.
    python -m scripts.pod_ops.deploy_pod --gpu-type "NVIDIA GeForce RTX 3090" --yes
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from scripts.pod_ops import envfile, progress, runpod_api

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = envfile.ENV_FILE
SSH_PROXY_HOST = "ssh.runpod.io"
SSH_IDENTITY_FILE = "~/.ssh/id_ed25519"

DEFAULT_IMAGE = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"
DEFAULT_CLOUD_TYPE = "COMMUNITY"
DEFAULT_CONTAINER_DISK_GB = 15
DEFAULT_VOLUME_GB = 40
DEFAULT_VOLUME_MOUNT = "/workspace"
DEFAULT_PORTS = "22/tcp"  # SSH only - no public HTTP port; llama-server stays on 127.0.0.1
POLL_INTERVAL_S = 5.0
POLL_TIMEOUT_S = 600.0  # 10 min

# Approximate rates seen at RUNBOOK-writing time (2026-07-02) - NOT a live quote.
# Always cross-check the number shown in console.runpod.io before confirming.
_RATE_HINTS_USD_HR = {
    "NVIDIA GeForce RTX 4090": 0.34,
    "NVIDIA RTX A5000": 0.27,
    "NVIDIA GeForce RTX 3090": 0.27,
}


def _pod_name(gpu_type_id: str) -> str:
    slug = gpu_type_id.lower().replace("nvidia", "").replace("geforce", "")
    slug = "-".join(part for part in slug.split() if part)
    return f"syntran-llm-{slug}"


def _print_plan(spec: runpod_api.PodSpec) -> None:
    rate = _RATE_HINTS_USD_HR.get(spec.gpu_type_id)
    rate_line = f"~${rate:.2f}/hr (reference only, verify in console)" if rate else "unknown - check console"
    print("Deploy plan")
    print("-----------")
    print(f"  name:            {spec.name}")
    print(f"  gpu_type:        {spec.gpu_type_id}")
    print(f"  cloud_type:      {spec.cloud_type}")
    print(f"  image:           {spec.image_name}")
    print(f"  container_disk:  {spec.container_disk_gb} GB")
    print(f"  volume:          {spec.volume_gb} GB @ {spec.volume_mount_path}")
    print(f"  ports:           {spec.ports}  (SSH only - no public HTTP port)")
    print(f"  est. rate:       {rate_line}")
    print()


def _confirm_interactively() -> bool:
    if not sys.stdin.isatty():
        print("error: stdin is not interactive - refusing to deploy without a typed confirmation.",
              file=sys.stderr)
        return False
    typed = input("Type DEPLOY to spend money and create this pod (anything else cancels): ")
    return typed.strip() == "DEPLOY"


def proxy_ssh_cmd(pod_host_id: str) -> str:
    """The exact command shape the console's Connect tab shows for 'Basic SSH'.

    The username is machine.podHostId from the API (`<pod_id>-<8hex>`), which
    matched the Connect tab on the pods observed 2026-07-03. The proxy is the
    ONLY working path on Community Cloud hosts seen so far (direct TCP was
    connection-refused), so this is the command every other script consumes.
    """
    return f"ssh {pod_host_id}@{SSH_PROXY_HOST} -i {SSH_IDENTITY_FILE}"


def record_pod_env(pod: runpod_api.PodInfo, spec: runpod_api.PodSpec) -> bool:
    """Record what the API confirmed, deriving POD_SSH_CMD from the API's
    machine.podHostId when available.

    Returns True when a usable POD_SSH_CMD was written; False when it could
    not be derived and the user must paste it from the Connect tab (the
    GraphQL runtime.ports data has proven unreliable, so nothing is guessed).
    """
    connect = runpod_api.get_pod_connect(pod.pod_id)
    updates = {
        "POD_ID": pod.pod_id,
        "POD_NAME": spec.name,
        "POD_GPU_TYPE": spec.gpu_type_id,
        "POD_IMAGE": spec.image_name,
    }
    if connect.cost_per_hr:
        updates["POD_RATE_USD_HR"] = f"{connect.cost_per_hr:.3f}"
    derived = connect.pod_host_id is not None
    if derived:
        updates["POD_SSH_CMD"] = proxy_ssh_cmd(connect.pod_host_id)
    else:
        updates["POD_SSH_CMD"] = ("<paste the top 'SSH' command from "
                                   "console.runpod.io -> your pod -> Connect tab>")
    envfile.upsert(updates)
    print(f"pod info written to {ENV_FILE.name} ({'/'.join(updates)}).")
    if derived:
        print(f"POD_SSH_CMD derived from the API: {updates['POD_SSH_CMD']}")
        print("(cross-check against the console's Connect tab if SSH fails)")
    else:
        print("IMPORTANT: the API did not return machine.podHostId - open "
              "console.runpod.io -> your pod -> Connect tab and paste the top "
              "'SSH' command into .env as POD_SSH_CMD.")
    return derived


def _wait_until_running(pod_id: str) -> runpod_api.PodInfo:
    """Poll until desiredStatus == RUNNING.

    Deliberately does NOT gate on info.ip/info.ssh_port: that data has proven
    unreliable against what the Connect tab actually needs (see _update_env),
    so it's no longer treated as a readiness signal here.
    """
    spinner = progress.Spinner("waiting for pod to reach RUNNING")
    start = time.monotonic()
    while True:
        info = runpod_api.get_pod(pod_id)
        if info.desired_status == "RUNNING":
            spinner.stop(f"pod {pod_id} is RUNNING")
            return info
        if time.monotonic() - start > POLL_TIMEOUT_S:
            spinner.stop(f"timed out waiting for {pod_id} - check console.runpod.io directly")
            return info
        spinner.tick(extra=f"status={info.desired_status}")
        time.sleep(POLL_INTERVAL_S)


def build_spec(
    gpu_type: str,
    *,
    cloud_type: str = DEFAULT_CLOUD_TYPE,
    image: str = DEFAULT_IMAGE,
    container_disk_gb: int = DEFAULT_CONTAINER_DISK_GB,
    volume_gb: int = DEFAULT_VOLUME_GB,
    name: str | None = None,
) -> runpod_api.PodSpec:
    return runpod_api.PodSpec(
        name=name or _pod_name(gpu_type),
        gpu_type_id=gpu_type,
        cloud_type=cloud_type,
        image_name=image,
        container_disk_gb=container_disk_gb,
        volume_gb=volume_gb,
        volume_mount_path=DEFAULT_VOLUME_MOUNT,
        ports=DEFAULT_PORTS,
    )


def deploy_confirmed(spec: runpod_api.PodSpec) -> runpod_api.PodInfo | None:
    """Create the pod, wait for RUNNING, record .env. Returns the PodInfo, or
    None if the pod never reached RUNNING in time.

    Callers MUST have already shown the plan and obtained the typed DEPLOY
    confirmation - this function spends money immediately.
    """
    pod_id = runpod_api.create_pod(spec)
    # Record the ID BEFORE polling: from this moment the pod exists and bills,
    # and if it never reaches RUNNING (timeout below returns None) .env must
    # still point at it so `down --yes` can terminate it and callers can warn.
    envfile.upsert({"POD_ID": pod_id, "POD_NAME": spec.name,
                    "POD_GPU_TYPE": spec.gpu_type_id})
    print(f"pod created: {pod_id} - polling until it's RUNNING")
    info = _wait_until_running(pod_id)
    if info.desired_status != "RUNNING":
        print(f"pod {pod_id} did not reach RUNNING in time - check console.runpod.io directly.")
        return None
    record_pod_env(info, spec)
    print("Note: the container's sshd can take ~30-60s longer to accept connections "
          "after RUNNING - if the first SSH attempt says 'connection refused', wait "
          "a bit and retry.")
    return info


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpu-type", required=True,
                         help='e.g. "NVIDIA GeForce RTX 3090" - must match RunPod\'s gpuTypeId exactly')
    parser.add_argument("--cloud-type", default=DEFAULT_CLOUD_TYPE, choices=["COMMUNITY", "SECURE"])
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--container-disk-gb", type=int, default=DEFAULT_CONTAINER_DISK_GB)
    parser.add_argument("--volume-gb", type=int, default=DEFAULT_VOLUME_GB)
    parser.add_argument("--name", default=None, help="defaults to syntran-llm-<gpu-slug>")
    parser.add_argument("--yes", action="store_true",
                         help="actually call the API (omit for a dry run / plan preview only)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    spec = build_spec(
        args.gpu_type,
        cloud_type=args.cloud_type,
        image=args.image,
        container_disk_gb=args.container_disk_gb,
        volume_gb=args.volume_gb,
        name=args.name,
    )
    _print_plan(spec)

    if not args.yes:
        print("Dry run only (no --yes passed). Nothing was created.")
        return 0

    if not _confirm_interactively():
        print("Cancelled - no pod created.")
        return 1

    try:
        deploy_confirmed(spec)
    except runpod_api.MissingApiKeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except runpod_api.GraphQLError as exc:
        print(f"error: RunPod API rejected the request (schema may have changed - "
              f"check https://docs.runpod.io/api-reference): {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
