"""Shared helpers for the repo-root .env file (gitignored).

All pod_ops scripts read/write live pod data through here so the parsing and
preservation rules live in exactly one place: comments, blank lines and
unrelated keys are never touched, and a key is updated in place if it already
exists.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"


def read_values(path: Path = ENV_FILE) -> dict[str, str]:
    """Parse KEY=VALUE lines. Comments/blank/malformed lines are skipped."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def upsert(updates: dict[str, str], path: Path = ENV_FILE) -> None:
    """Update or append KEY=VALUE lines, preserving everything else
    (comments, RUNPOD_API_KEY, unrelated keys)."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(updates)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"
    if remaining:
        if lines and lines[-1].strip():
            lines.append("")
        for key, value in remaining.items():
            lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def export_to_environ(path: Path = ENV_FILE) -> None:
    """Load .env into os.environ without overriding what the shell exported."""
    for key, value in read_values(path).items():
        os.environ.setdefault(key, value)


def is_placeholder(value: str | None) -> bool:
    """True for missing/empty values or the '<paste ...>' placeholders that
    deploy writes when it could not derive a real SSH command."""
    return not value or value.startswith("<")
