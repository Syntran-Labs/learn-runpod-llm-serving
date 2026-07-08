"""Local HTTP relay to the pod's llama-server, standing in for `ssh -L`.

Why not a real tunnel: RunPod's ssh.runpod.io proxy rejects the
"direct-tcpip" channel type that `-L` needs, and the "SSH over exposed TCP"
alternative is unreachable on this host (connection refused). Worse, the
proxy IGNORES ssh exec commands entirely -- it always opens an interactive
login shell (observed 2026-07-03: exec'd commands never ran; stdin content
was executed line-by-line at the shell prompt instead). And that forced PTY
mangles raw bytes three separate ways (echoes input, drops >4KB lines in
canonical mode, rewrites \\n to \\r\\n on output), which killed the previous
byte-level relay.

So this relays at the HTTP level with base64 armoring in BOTH directions:
a local http.server receives the benchmark's request, ships the body to the
pod as short base64 lines (pure ASCII, newline-terminated -- immune to every
PTY transformation), the pod decodes and replays it against 127.0.0.1:8080
with curl, and the raw HTTP response comes back base64-encoded between
markers. Echoed input and banner noise fall outside the marker window, and
CRLF mangling of base64 text is harmless (stripped before decoding).

One ssh exec session per request (~2-5s overhead each). That inflates
wall-clock but NOT the benchmark numbers: tok/s and TTFT come from
llama-server's own `timings` block, measured server-side.

Requires an unlocked ssh-agent in this shell (`eval $(ssh-agent -s) &&
ssh-add ~/.ssh/id_ed25519`) BEFORE running, so each request doesn't prompt
for the key passphrase.

Usage: python -m scripts.pod_ops.relay_tunnel
"""

from __future__ import annotations

import base64
import re
import shlex
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"
LOCAL_PORT = 8080
REMOTE_PORT = 8080

# Markers delimiting the phases of the remote command's output. Everything
# before READY is login banner; anything between READY and BEGIN is echoed
# input (if the remote `stty -echo` didn't take); only the BEGIN..DONE window
# is the base64-encoded HTTP response.
_READY = "__RELAY_READY_9f2ab17c__"
_BEGIN = "__B64_BEGIN_9f2ab17c__"
_DONE = "__B64_DONE_9f2ab17c__"
_END_INPUT = "__END_INPUT_9f2ab17c__"

_CURL_MAX_TIME_S = 100          # remote curl gives up first...
_RELAY_DEADLINE_S = 110         # ...then the relay (must stay under the
                                # benchmark client's 120s timeout so errors
                                # surface as clean 502s, not client timeouts)
_B64_JUNK = re.compile(rb"[^A-Za-z0-9+/=]")
_SAFE_PATH = re.compile(r"^/[A-Za-z0-9/_.\-]*$")

# Prefer Git's own ssh.exe over whatever "ssh" resolves to on Windows' PATH:
# when Python spawns ssh as a subprocess, PATH lookup can land on Windows'
# built-in OpenSSH client, which doesn't understand the ssh-agent socket
# created in Git Bash and falls back to a GUI passphrase popup.
_GIT_SSH_CANDIDATES = [
    r"D:\Program Files\Git\usr\bin\ssh.exe",
    r"D:\Program Files\Git\bin\ssh.exe",
    r"C:\Program Files\Git\usr\bin\ssh.exe",
    r"C:\Program Files\Git\bin\ssh.exe",
]


def _resolve_ssh_binary() -> str:
    for candidate in _GIT_SSH_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return "ssh"  # fall back to PATH resolution


def _load_ssh_cmd() -> list[str]:
    if not ENV_FILE.is_file():
        raise SystemExit(f"error: {ENV_FILE} not found - deploy a pod first.")
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("POD_SSH_CMD="):
            value = line.split("=", 1)[1].strip()
            if not value or value.startswith("<paste"):
                raise SystemExit("error: POD_SSH_CMD in .env is missing/a placeholder.")
            parts = shlex.split(value)
            if parts and parts[0] == "ssh":
                parts[0] = _resolve_ssh_binary()
            return parts
    raise SystemExit("error: POD_SSH_CMD not found in .env")


def _split_marker(marker: str) -> str:
    """Render a printf that assembles the marker from two halves at runtime.

    The RunPod proxy ignores ssh exec commands and always opens an
    interactive shell, so the command must be TYPED via stdin -- and an
    interactive PTY echoes typed lines back. Building markers as
    printf '%s%s' <half> <half> guarantees the echoed command text never
    contains a complete marker, so marker matching only fires on the real
    printf output."""
    half = len(marker) // 2
    return f"printf '%s%s\\n' {marker[:half]} {marker[half:]}"


def _remote_cmd(path: str, has_body: bool) -> str:
    """Build the one-liner typed into the pod's interactive shell: collect
    base64 input first, THEN emit BEGIN -- so echoed input lines land between
    READY and BEGIN, outside the extraction window."""
    url = f"http://127.0.0.1:{REMOTE_PORT}{path}"
    # -H 'Expect:' disables curl's 100-continue on large bodies, so the
    # response contains exactly one header block.
    curl_post = (f"curl -s -i --max-time {_CURL_MAX_TIME_S} -H 'Expect:' "
                 f"-H 'Content-Type: application/json' --data-binary @- '{url}'")
    curl_get = f"curl -s -i --max-time {_CURL_MAX_TIME_S} '{url}'"
    if has_body:
        collect = (
            't=$(mktemp); '
            f'while IFS= read -r l; do [ "$l" = "{_END_INPUT}" ] && break; '
            'printf \'%s\\n\' "$l" >> "$t"; done; '
        )
        pipeline = f'base64 -d < "$t" | {curl_post} | base64; rm -f "$t"'
    else:
        collect = ""
        pipeline = f"{curl_get} | base64"
    return (
        f"{_split_marker(_READY)}; "
        f"{collect}"
        f"{_split_marker(_BEGIN)}; "
        f"{pipeline}; "
        f"echo; {_split_marker(_DONE)}"
    )


class _StreamCollector:
    """Drains a subprocess stdout continuously on a background thread.

    Draining must never stop, even while the handler is busy writing stdin:
    if the remote PTY echoes our (large) input back and nobody reads it, the
    stdout pipe fills, the remote blocks writing, stops reading stdin, and
    both sides deadlock. A dedicated pump thread makes that impossible.
    """

    def __init__(self, stream) -> None:
        self._stream = stream
        self._buf = bytearray()
        self._cond = threading.Condition()
        self._eof = False
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        while True:
            try:
                chunk = self._stream.read1(65536)
            except (OSError, ValueError):
                chunk = b""
            with self._cond:
                if chunk:
                    self._buf.extend(chunk)
                else:
                    self._eof = True
                self._cond.notify_all()
            if not chunk:
                return

    def wait_for(self, marker: bytes, deadline: float) -> bytes:
        """Block until marker appears in the collected output (returning a
        snapshot of everything so far) or raise on EOF/deadline."""
        with self._cond:
            while True:
                if self._buf.find(marker) != -1:
                    return bytes(self._buf)
                if self._eof:
                    raise ConnectionError(f"remote closed before {marker!r} "
                                           f"(got {len(self._buf)} bytes)")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"marker {marker!r} not seen before deadline "
                                        f"(got {len(self._buf)} bytes)")
                self._cond.wait(min(remaining, 1.0))


_req_counter = 0
_req_lock = threading.Lock()


def _next_req_id() -> int:
    global _req_counter
    with _req_lock:
        _req_counter += 1
        return _req_counter


class _RelayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    ssh_cmd: list[str] = []

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass  # we do our own per-request logging with timings

    def do_GET(self) -> None:
        self._relay(has_body=False)

    def do_POST(self) -> None:
        self._relay(has_body=True)

    def _fail(self, req_id: int, status: int, message: str) -> None:
        print(f"[relay] #{req_id} FAILED: {message}", file=sys.stderr)
        body = message.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _relay(self, has_body: bool) -> None:
        req_id = _next_req_id()
        start = time.monotonic()
        if not _SAFE_PATH.match(self.path):
            self._fail(req_id, 400, f"refusing to relay suspicious path {self.path!r}")
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        print(f"[relay] #{req_id} {self.command} {self.path} ({length} bytes) -> ssh exec")

        deadline = time.monotonic() + _RELAY_DEADLINE_S
        # NO exec command: the RunPod proxy ignores it and opens an
        # interactive shell regardless, so the command is typed via stdin.
        proc = subprocess.Popen(
            [*self.ssh_cmd, "-tt", "-o", "StrictHostKeyChecking=accept-new"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        collector = _StreamCollector(proc.stdout)
        try:
            # Turn echo off first on its own line (its own echo is harmless
            # noise); if it doesn't take, split-built markers keep echoed
            # text out of the extraction anyway.
            proc.stdin.write(b"stty -echo 2>/dev/null\n")
            proc.stdin.write(_remote_cmd(self.path, has_body).encode() + b"\n")
            proc.stdin.flush()
            collector.wait_for(_READY.encode(), deadline)
            if has_body:
                proc.stdin.write(base64.encodebytes(body))
                proc.stdin.write(_END_INPUT.encode() + b"\n")
                proc.stdin.flush()
            print(f"[relay] #{req_id} shell ready after {time.monotonic() - start:.1f}s, "
                  f"waiting for llama-server response")
            output = collector.wait_for(_DONE.encode(), deadline)
        except (TimeoutError, ConnectionError, OSError) as exc:
            proc.kill()
            self._fail(req_id, 502, f"relay transport failed: {exc}")
            return
        finally:
            try:
                proc.stdin.write(b"exit\n")
                proc.stdin.flush()
            except OSError:
                pass

        done = output.find(_DONE.encode())
        begin = output.rfind(_BEGIN.encode(), 0, done)
        if begin == -1:
            proc.kill()
            self._fail(req_id, 502, "BEGIN marker missing before DONE")
            return
        segment = output[begin + len(_BEGIN):done]
        raw_response = base64.b64decode(_B64_JUNK.sub(b"", segment))
        if not raw_response:
            proc.kill()
            self._fail(req_id, 502, "pod-side curl produced no response "
                                     "(is llama-server running? tmux attach -t serve)")
            return

        # raw_response is curl -i output: status line + headers + body.
        # curl already de-chunked the body, so re-frame with Content-Length.
        head, _, payload = raw_response.partition(b"\r\n\r\n")
        while head.split(b" ", 2)[1:2] and head.split(b" ", 2)[1].startswith(b"1"):
            head, _, payload = payload.partition(b"\r\n\r\n")  # skip 1xx blocks
        status_line, *header_lines = head.split(b"\r\n")
        try:
            status = int(status_line.split()[1])
        except (IndexError, ValueError):
            proc.kill()
            self._fail(req_id, 502, f"unparseable status line {status_line!r}")
            return
        content_type = "application/json"
        for header in header_lines:
            name, _, value = header.partition(b":")
            if name.strip().lower() == b"content-type":
                content_type = value.strip().decode(errors="replace")

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        print(f"[relay] #{req_id} done: HTTP {status}, {len(payload)} bytes, "
              f"total {time.monotonic() - start:.1f}s")


def main() -> int:
    _RelayHandler.ssh_cmd = _load_ssh_cmd()
    server = ThreadingHTTPServer(("127.0.0.1", LOCAL_PORT), _RelayHandler)
    # Without a timeout, Ctrl+C on Windows can't interrupt the blocking
    # accept loop until the next incoming connection.
    server.timeout = 1.0
    print(f"HTTP relay listening on http://127.0.0.1:{LOCAL_PORT} -> pod's "
          f"127.0.0.1:{REMOTE_PORT} (one ssh exec per request). Ctrl+C to stop.")
    print("Reminder: run `eval $(ssh-agent -s) && ssh-add ~/.ssh/id_ed25519` "
          "in THIS shell first, or every request will prompt for the passphrase.")
    try:
        while True:
            server.handle_request()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
