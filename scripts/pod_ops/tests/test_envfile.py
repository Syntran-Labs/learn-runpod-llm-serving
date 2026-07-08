"""envfile.py: parsing/preservation rules every pod_ops script relies on."""

from __future__ import annotations

import os
from pathlib import Path

from scripts.pod_ops import envfile


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(content, encoding="utf-8")
    return path


def test_read_values_skips_comments_and_blank_lines(tmp_path: Path) -> None:
    path = _write(tmp_path, "# comment\n\nA=1\nB = spaced \nmalformed line\n")
    assert envfile.read_values(path) == {"A": "1", "B": "spaced"}


def test_read_values_missing_file_is_empty(tmp_path: Path) -> None:
    assert envfile.read_values(tmp_path / "nope.env") == {}


def test_upsert_updates_in_place_and_appends(tmp_path: Path) -> None:
    path = _write(tmp_path, "# keep me\nRUNPOD_API_KEY=secret\nPOD_ID=old\n")
    envfile.upsert({"POD_ID": "new", "POD_SSH_CMD": "ssh x@y"}, path)
    text = path.read_text(encoding="utf-8")
    assert "# keep me" in text
    assert "RUNPOD_API_KEY=secret" in text
    assert "POD_ID=new" in text
    assert "POD_ID=old" not in text
    assert text.rstrip().endswith("POD_SSH_CMD=ssh x@y")


def test_upsert_creates_file(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    envfile.upsert({"A": "1"}, path)
    assert envfile.read_values(path) == {"A": "1"}


def test_export_to_environ_does_not_override_shell(tmp_path: Path, monkeypatch) -> None:
    path = _write(tmp_path, "FROM_FILE=file\nALREADY_SET=file\n")
    monkeypatch.delenv("FROM_FILE", raising=False)
    monkeypatch.setenv("ALREADY_SET", "shell")
    envfile.export_to_environ(path)
    try:
        assert os.environ["FROM_FILE"] == "file"
        assert os.environ["ALREADY_SET"] == "shell"
    finally:
        os.environ.pop("FROM_FILE", None)


def test_is_placeholder() -> None:
    assert envfile.is_placeholder(None)
    assert envfile.is_placeholder("")
    assert envfile.is_placeholder("<paste the top 'SSH' command ...>")
    assert not envfile.is_placeholder("ssh abc-123@ssh.runpod.io -i ~/.ssh/id_ed25519")
