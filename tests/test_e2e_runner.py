"""The CI runner must reject false success and retain diagnostic output."""

import subprocess
import sys
from pathlib import Path

import pytest

from tests.e2e import run


@pytest.mark.parametrize(
    "output,status,success",
    [
        ("3/3 passed", 0, True),
        ("3/3 passed", 1, False),
        ("2/3 passed", 0, False),
        ("all good", 0, False),
    ],
)
def test_checklist_exit_and_summary_contract(tmp_path, output, status, success):
    path = tmp_path / "checklist.py"
    path.write_text(f"print({output!r}); raise SystemExit({status})\n")
    ok, score, log = run.run_one(path)
    assert ok is success
    assert output in log
    assert score == ("?" if output == "all good" else output.split()[0])


def test_timeout_keeps_output(tmp_path, monkeypatch):
    path = tmp_path / "slow.py"
    path.write_text('import time\nprint("started", flush=True)\ntime.sleep(30)\n')
    monkeypatch.setattr(run, "TIMEOUT", 1.0)
    assert run.run_one(path) == (False, "timeout", "started\n")


def test_webkit_rejects_chromium_only_checklist():
    result = subprocess.run(
        [sys.executable, str(Path(run.__file__)), "--browser", "webkit", "touch"], capture_output=True, text=True
    )
    assert result.returncode == 2
    assert "CDP" in result.stderr


def test_runner_saves_success_and_failure_logs(tmp_path, monkeypatch):
    good, bad = tmp_path / "good.py", tmp_path / "bad.py"
    good.write_text('print("1/1 passed")\n')
    bad.write_text('print("0/1 passed")\n')
    monkeypatch.setattr(run, "checklists", lambda: {"good": good, "bad": bad})
    folder = tmp_path / "results"
    monkeypatch.setattr(sys, "argv", ["run.py", "--output-dir", str(folder)])
    monkeypatch.setenv("SHOTS", "")
    monkeypatch.setenv("SYNAPSE_E2E_BROWSER", "chromium")
    assert run.main() == 1
    assert "1/1 passed" in (folder / "good/attempt-1/output.log").read_text()
    assert "0/1 passed" in (folder / "bad/attempt-1/output.log").read_text()
    assert "| bad | FAIL |" in (folder / "summary.md").read_text()
