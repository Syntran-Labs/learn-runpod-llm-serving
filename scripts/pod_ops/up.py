"""One-command session bring-up: deploy pod -> install stack -> benchmark.

Collapses the four-terminal flow of 2026-07-03 into a single command:

    python -m scripts.pod_ops.up            # dry run - prints the plan only
    python -m scripts.pod_ops.up --yes      # real run - gated by typed DEPLOY

Phases (everything BETWEEN the human gates is automated; the gates stay):
  1. deploy      - plan + typed DEPLOY confirmation (governance: Critical),
                   then create the pod and poll until RUNNING.
  2. ssh derive  - POD_SSH_CMD comes from the API's machine.podHostId
                   (no more manual Connect-tab paste).
  3. ssh agent   - make sure an unlocked ssh-agent exists (at most one
                   passphrase prompt per session).
  4. ssh ready   - probe the ssh.runpod.io proxy until the pod's shell answers.
  5. setup       - feed setup.sh to the pod's interactive shell (idempotent,
                   ~10-15 min on a fresh pod, output streams live).
  6. relay       - start relay_tunnel's HTTP relay on a background thread.
  7. benchmark   - run scripts.llm_bench.runner through the relay, print report.

The pod is left RUNNING (and billing!) so you can keep using the endpoint.
Shut everything down with: python -m scripts.pod_ops.down --yes

Hard-won constraints honored here (see relay_tunnel.py's docstring): the
RunPod proxy needs a PTY, IGNORES exec commands (stdin only), and offers no
scp/-L; ssh must be Git's own ssh.exe on this machine, not Windows' native one.
"""

from __future__ import annotations

import argparse
import atexit
import os
import re
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx

from scripts.llm_bench import runner
from scripts.pod_ops import deploy_pod, envfile, progress, runpod_api
from scripts.pod_ops.relay_tunnel import (
    LOCAL_PORT,
    _load_ssh_cmd,
    _RelayHandler,
    _resolve_ssh_binary,
    _split_marker,
    _StreamCollector,
)

REPO_ROOT = envfile.REPO_ROOT
SETUP_SCRIPT = REPO_ROOT / "setup.sh"
RESULTS_DIR = REPO_ROOT / "results"

DEFAULT_GPU_TYPE = "NVIDIA GeForce RTX 3090"
SSH_READY_TIMEOUT_S = 420.0     # sshd + proxy registration after RUNNING
SSH_PROBE_WINDOW_S = 30.0       # one probe attempt
SETUP_TIMEOUT_S = 120 * 60.0    # fresh setup is ~10-15 min on a good host, but
                                # Community Cloud CPU quotas vary wildly: a
                                # cgroup-throttled host took >45 min on the
                                # CUDA build alone (2026-07-03)
HEALTH_TIMEOUT_S = 300.0        # llama-server answers 503 until the ~12GB
                                # model is loaded, so the budget is a load
                                # window, not a handful of ssh round trips
                                # (each attempt already costs one ssh session)

_PROBE_MARKER = "__SSH_PROBE_OK_5c41d9__"


# ---------------------------------------------------------------- ssh helpers

def _ssh_base_cmd() -> list[str]:
    """POD_SSH_CMD from .env with Git's ssh.exe substituted, plus the flags
    every proxy session needs (PTY mandatory, first-connect host key)."""
    return [*_load_ssh_cmd(), "-tt", "-o", "StrictHostKeyChecking=accept-new"]


def _sibling_tool(name: str) -> str:
    """ssh-add/ssh-agent living next to the resolved ssh binary, so agent and
    client are the same OpenSSH build (mixing Git's and Windows' breaks the
    agent socket)."""
    ssh_bin = _resolve_ssh_binary()
    if ssh_bin == "ssh":
        return name
    sibling = Path(ssh_bin).with_name(f"{name}.exe")
    return str(sibling) if sibling.is_file() else name


def _kill_started_agent() -> None:
    subprocess.run([_sibling_tool("ssh-agent"), "-k"],
                   capture_output=True, text=True)


def ensure_ssh_agent() -> None:
    """Make sure an agent with at least one key is reachable, starting one and
    running ssh-add (one interactive passphrase prompt) if needed.

    Best-effort: on failure it warns and continues - every ssh connection will
    then prompt for the passphrase, which is annoying but functional.
    """
    ssh_add = _sibling_tool("ssh-add")
    check = subprocess.run([ssh_add, "-l"], capture_output=True, text=True)
    if check.returncode == 0:
        print("ssh-agent: already running with key(s) loaded.")
        return

    try:
        out = subprocess.run([_sibling_tool("ssh-agent"), "-s"],
                             capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"warning: could not start ssh-agent ({exc}) - each ssh "
              f"connection may prompt for the key passphrase.", file=sys.stderr)
        return
    for match in re.finditer(r"(SSH_AUTH_SOCK|SSH_AGENT_PID)=([^;\s]+);", out):
        os.environ[match.group(1)] = match.group(2)
    atexit.register(_kill_started_agent)

    print("ssh-agent: started for this session. Loading your key "
          "(passphrase prompt may follow):")
    added = subprocess.run([ssh_add])  # inherits the console for the prompt
    if added.returncode != 0:
        print("warning: ssh-add failed - each ssh connection may prompt for "
              "the key passphrase.", file=sys.stderr)


def wait_ssh_ready(ssh_cmd: list[str]) -> None:
    """Probe the proxy until the pod's shell executes a command for us.

    A probe types a split-built printf into the forced interactive shell (the
    proxy ignores exec commands) and waits for the marker. Echo of the typed
    command can't false-positive: the marker never appears whole in the input.
    """
    spinner = progress.Spinner("waiting for the pod's SSH to answer")
    deadline = time.monotonic() + SSH_READY_TIMEOUT_S
    attempt = 0
    while True:
        attempt += 1
        proc = subprocess.Popen(
            [*ssh_cmd, "-o", "ConnectTimeout=10"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        collector = _StreamCollector(proc.stdout)
        try:
            proc.stdin.write(b"stty -echo 2>/dev/null\n")
            proc.stdin.write(f"{_split_marker(_PROBE_MARKER)}; exit\n".encode())
            proc.stdin.flush()
            collector.wait_for(
                _PROBE_MARKER.encode(),
                min(deadline, time.monotonic() + SSH_PROBE_WINDOW_S),
            )
            proc.kill()
            spinner.stop(f"pod SSH is ready (attempt {attempt})")
            return
        except (TimeoutError, ConnectionError, OSError):
            proc.kill()
            if time.monotonic() >= deadline:
                spinner.stop("pod SSH never answered")
                raise SystemExit(
                    "error: SSH to the pod did not come up within "
                    f"{SSH_READY_TIMEOUT_S / 60:.0f} min. Check the pod in "
                    "console.runpod.io (it IS running and billing)."
                )
            spinner.tick(extra=f"attempt {attempt} failed, retrying")
            time.sleep(10)


def run_setup(ssh_cmd: list[str]) -> None:
    """Feed setup.sh to the pod's interactive shell, streaming output live.

    Same mechanism as run_setup.sh: no upload exists on this proxy (scp/sftp
    rejected), the script's lines are typed one by one and EOF closes the
    shell. setup.sh itself is idempotent, so re-running is safe.
    """
    print(f"\nFeeding {SETUP_SCRIPT.name} to the pod (~10-15 min on a fresh pod; "
          "output streams below)...")
    with SETUP_SCRIPT.open("rb") as script:
        proc = subprocess.Popen(ssh_cmd, stdin=script)
        try:
            rc = proc.wait(timeout=SETUP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise SystemExit(f"error: setup.sh exceeded {SETUP_TIMEOUT_S / 60:.0f} min "
                             "- inspect the pod via SSH (it IS running and billing).")
    if rc != 0:
        # The proxy shell's exit status is not fully trustworthy; the health
        # check below is the real verdict, so this is a warning, not an abort.
        print(f"warning: setup ssh session exited {rc} - relying on the "
              f"health check to decide.", file=sys.stderr)


# -------------------------------------------------------------- relay + bench

class Relay:
    """relay_tunnel's HTTP relay on a background thread (context manager)."""

    def __enter__(self) -> "Relay":
        _RelayHandler.ssh_cmd = _load_ssh_cmd()
        try:
            self._server = ThreadingHTTPServer(("127.0.0.1", LOCAL_PORT), _RelayHandler)
        except OSError as exc:
            raise SystemExit(f"error: cannot bind 127.0.0.1:{LOCAL_PORT} ({exc}) - "
                             "is another relay_tunnel already running?")
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        print(f"relay: listening on http://127.0.0.1:{LOCAL_PORT} -> pod's "
              f"127.0.0.1:8080 (one ssh session per request)")
        return self

    def __exit__(self, *exc_info) -> None:
        self._server.shutdown()
        self._server.server_close()
        print("relay: stopped")


def wait_server_healthy() -> None:
    """GET /v1/models through the relay until llama-server answers 200."""
    print("health check: asking llama-server for /v1/models through the relay "
          f"(up to {HEALTH_TIMEOUT_S / 60:.0f} min - a fresh server is still "
          "loading the model)...")
    last_error = "no attempt made"
    deadline = time.monotonic() + HEALTH_TIMEOUT_S
    attempt = 0
    while True:
        attempt += 1
        try:
            response = httpx.get(f"http://127.0.0.1:{LOCAL_PORT}/v1/models", timeout=60.0)
            if response.status_code == 200:
                print(f"health check: llama-server is up (attempt {attempt}).")
                return
            last_error = f"HTTP {response.status_code}: {response.text[:200]}"
        except httpx.HTTPError as exc:
            last_error = str(exc)
        if time.monotonic() >= deadline:
            raise SystemExit(
                f"error: llama-server did not answer 200 through the relay "
                f"within {HEALTH_TIMEOUT_S / 60:.0f} min ({attempt} attempts, "
                f"last: {last_error}). Inspect the pod: tmux attach -t serve. "
                "The pod IS running and billing.")
        time.sleep(5)


def run_benchmark(runs: int, scenarios: str) -> int:
    envfile.export_to_environ()  # POD_RATE_USD_HR etc. for the report's cost math
    rc = runner.main(["--runs", str(runs), "--scenarios", scenarios])
    if rc == 0:
        reports = sorted(RESULTS_DIR.glob("report-*.md"))
        if reports:
            print("\n" + "=" * 60)
            print(reports[-1].read_text(encoding="utf-8"))
            print("=" * 60)
    return rc


# ----------------------------------------------------------------------- main

def _print_plan(args: argparse.Namespace, reuse_pod_id: str | None) -> None:
    deploy_line = (f"reuse pod {reuse_pod_id} from .env (no deploy)" if reuse_pod_id
                   else f"deploy {args.gpu_type} (gate: typed DEPLOY - spends money)")
    print("Up plan")
    print("-------")
    print(f"  1. {deploy_line}")
    print("  2. derive POD_SSH_CMD from the API (machine.podHostId)")
    print("  3. ensure ssh-agent (at most one passphrase prompt)")
    print("  4. wait for the pod's SSH proxy to answer")
    step5 = "skipped (--skip-setup)" if args.skip_setup else \
        "feed setup.sh (apt -> 12GB model -> CUDA build -> start server; ~10-15 min fresh)"
    print(f"  5. {step5}")
    print(f"  6. start local HTTP relay on 127.0.0.1:{LOCAL_PORT}")
    print(f"  7. benchmark: runs={args.runs} scenarios={args.scenarios}")
    print("  8. leave the pod RUNNING (billing!) - stop it with: "
          "python -m scripts.pod_ops.down --yes")
    print()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One command: deploy pod + install LLM stack + benchmark.")
    parser.add_argument("--gpu-type", default=DEFAULT_GPU_TYPE)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--scenarios", default="short,long")
    parser.add_argument("--reuse-pod", action="store_true",
                        help="skip deployment and use the POD_ID/POD_SSH_CMD already in .env")
    parser.add_argument("--skip-setup", action="store_true",
                        help="skip feeding setup.sh (server already installed and running)")
    parser.add_argument("--yes", action="store_true",
                        help="actually run (omit for a dry run / plan preview only)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    env = envfile.read_values()
    reuse_pod_id = env.get("POD_ID") if args.reuse_pod else None
    if args.reuse_pod and not reuse_pod_id:
        print("error: --reuse-pod but no POD_ID in .env", file=sys.stderr)
        return 2
    _print_plan(args, reuse_pod_id)

    if not args.yes:
        print("Dry run only (no --yes passed). Nothing was created or contacted.")
        return 0

    pod_id: str | None = reuse_pod_id
    try:
        # Phase 1+2: deploy (human-gated) or validate the reused pod.
        if reuse_pod_id:
            info = runpod_api.get_pod(reuse_pod_id)
            if info.desired_status != "RUNNING":
                print(f"error: pod {reuse_pod_id} is {info.desired_status}, not RUNNING.",
                      file=sys.stderr)
                return 2
            if envfile.is_placeholder(env.get("POD_SSH_CMD")):
                connect = runpod_api.get_pod_connect(reuse_pod_id)
                if connect.pod_host_id is None:
                    print("error: no usable POD_SSH_CMD in .env and the API did not "
                          "return machine.podHostId - paste the Connect tab command "
                          "into .env first.", file=sys.stderr)
                    return 2
                envfile.upsert({"POD_SSH_CMD": deploy_pod.proxy_ssh_cmd(connect.pod_host_id)})
        else:
            spec = deploy_pod.build_spec(args.gpu_type)
            deploy_pod._print_plan(spec)
            if not deploy_pod._confirm_interactively():
                print("Cancelled - no pod created.")
                return 1
            info = deploy_pod.deploy_confirmed(spec)
            # deploy_confirmed records POD_ID in .env the moment the pod is
            # created; read it back so the billing reminder in `finally` fires
            # even when the pod never reached RUNNING (info is None).
            pod_id = envfile.read_values().get("POD_ID") or None
            if info is None:
                return 2
            if envfile.is_placeholder(envfile.read_values().get("POD_SSH_CMD")):
                print("error: POD_SSH_CMD could not be derived - fill it in from the "
                      "Connect tab, then rerun with --reuse-pod --yes.", file=sys.stderr)
                return 2

        # Phase 3+4: ssh-agent + reachability.
        ensure_ssh_agent()
        ssh_cmd = _ssh_base_cmd()
        wait_ssh_ready(ssh_cmd)

        # Phase 5: in-pod stack setup.
        if not args.skip_setup:
            run_setup(ssh_cmd)

        # Phase 6+7: relay + health + benchmark.
        with Relay():
            wait_server_healthy()
            rc = run_benchmark(args.runs, args.scenarios)

        print("\nDone. The OpenAI-compatible endpoint stays reachable by rerunning "
              "the relay:\n  python -m scripts.pod_ops.relay_tunnel")
        return rc
    except KeyboardInterrupt:
        print("\ninterrupted.", file=sys.stderr)
        return 130
    except (runpod_api.MissingApiKeyError, runpod_api.GraphQLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        if pod_id:
            print(f"\nREMINDER: pod {pod_id} is RUNNING and BILLING. "
                  f"Terminate with: python -m scripts.pod_ops.down --yes")


if __name__ == "__main__":
    raise SystemExit(main())
