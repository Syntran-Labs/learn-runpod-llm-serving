"""Terminal progress display: a discrete step bar plus an indeterminate spinner.

No third-party dependency (no tqdm/rich) - this project keeps its dependency
footprint minimal (see client.py's rationale for avoiding the openai SDK).
"""

from __future__ import annotations

import sys
import time

_SPINNER_FRAMES = "|/-\\"
_BAR_WIDTH = 30


def _fmt_elapsed(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


class StepBar:
    """Renders '[#####-----] 2/5 Building llama.cpp (elapsed 0:32)' on one line.

    Steps are discrete and named up front. Use for stages with a known count
    but unknown/variable per-stage duration (apt install, model download,
    compiling, etc.) - fabricating a byte-level percentage for a `make` build
    would be dishonest, so this shows stage progress + elapsed time instead.
    """

    def __init__(self, step_labels: list[str]) -> None:
        self._labels = step_labels
        self._total = len(step_labels)
        self._start = time.monotonic()
        self._current = 0

    def start_step(self, index: int) -> None:
        """index is 1-based; call once when a step begins."""
        self._current = index
        self._render(done=False)

    def finish_step(self, index: int) -> None:
        self._current = index
        self._render(done=True)

    def _render(self, done: bool) -> None:
        filled = int(_BAR_WIDTH * self._current / self._total)
        bar = "#" * filled + "-" * (_BAR_WIDTH - filled)
        label = self._labels[self._current - 1]
        suffix = "done" if done else "..."
        elapsed = _fmt_elapsed(time.monotonic() - self._start)
        line = f"\r[{bar}] {self._current}/{self._total} {label} ({suffix}, elapsed {elapsed})"
        sys.stdout.write(line.ljust(100))
        sys.stdout.flush()
        if done and self._current == self._total:
            sys.stdout.write("\n")
            sys.stdout.flush()

    def fail_step(self, index: int, reason: str) -> None:
        elapsed = _fmt_elapsed(time.monotonic() - self._start)
        sys.stdout.write("\n")
        sys.stdout.write(f"[FAILED] step {index}/{self._total} '{self._labels[index - 1]}' "
                          f"after {elapsed}: {reason}\n")
        sys.stdout.flush()


class Spinner:
    """Indeterminate spinner for polling loops (e.g. waiting for a pod to boot).

    Usage:
        spinner = Spinner("waiting for pod to reach RUNNING")
        while not ready:
            spinner.tick()
            time.sleep(2)
        spinner.stop("pod is running")
    """

    def __init__(self, message: str) -> None:
        self._message = message
        self._start = time.monotonic()
        self._frame = 0

    def tick(self, extra: str = "") -> None:
        frame = _SPINNER_FRAMES[self._frame % len(_SPINNER_FRAMES)]
        self._frame += 1
        elapsed = _fmt_elapsed(time.monotonic() - self._start)
        suffix = f" - {extra}" if extra else ""
        line = f"\r{frame} {self._message} (elapsed {elapsed}){suffix}"
        sys.stdout.write(line.ljust(100))
        sys.stdout.flush()

    def stop(self, final_message: str) -> None:
        elapsed = _fmt_elapsed(time.monotonic() - self._start)
        sys.stdout.write(f"\r{final_message} (took {elapsed})".ljust(100) + "\n")
        sys.stdout.flush()
