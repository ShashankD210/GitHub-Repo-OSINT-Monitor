#!/usr/bin/env python3
"""Basic smoke tests for GitHub OSINT Monitor."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MONITOR = PROJECT_ROOT / "monitor.py"


def test_monitor_help():
    result = subprocess.run(
        [sys.executable, str(MONITOR), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Monitor a GitHub repo" in result.stdout
    assert "--repos-file" in result.stdout
    assert "--login" in result.stdout
    assert "--logout" in result.stdout


def test_webgui_help():
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "webgui.py"), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "GitHub OSINT Monitor Web GUI" in result.stdout
    assert "--port" in result.stdout
    assert "--repos-file" in result.stdout


def test_state_file_roundtrip(tmp_path: Path):
    state_path = tmp_path / "test_state.json"
    state = {
        "last_sha": "abc123",
        "metrics": {"stars": 1, "forks": 2},
    }
    state_path.write_text(json.dumps(state))
    loaded = json.loads(state_path.read_text())
    assert loaded["last_sha"] == "abc123"
    assert loaded["metrics"]["stars"] == 1


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        test_monitor_help()
        test_webgui_help()
        test_state_file_roundtrip(Path(tmp))
    print("tests passed")
