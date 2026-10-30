"""Generate or check the portable, hashed pip requirements with the pinned uv release."""

from __future__ import annotations

import argparse
import difflib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ("requirements.in", "requirements-dev.in")
OUTPUTS = ("requirements.txt", "requirements-dev.txt")
TOOL = ROOT / "tools" / "requirements-uv.txt"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="fail on drift without changing checked-in files")
    mode.add_argument("--upgrade", action="store_true", help="refresh all pins instead of retaining existing versions")
    args = parser.parse_args()
    uv = shutil.which("uv")
    if not uv:
        parser.error("Install the lock tool first: python -m pip install -r tools/requirements-uv.txt")
    version = subprocess.check_output([uv, "--version"], text=True).split()[1]
    expected = TOOL.read_text().strip().removeprefix("uv==")
    if version != expected:
        parser.error(f"Need uv {expected}, found {version}; install -r tools/requirements-uv.txt")
    # Keep a developer's private indexes and uv configuration out of the published locks.
    env = {key: value for key, value in os.environ.items() if not key.startswith(("UV_", "PIP_"))}
    if "UV_CACHE_DIR" in os.environ:
        env["UV_CACHE_DIR"] = os.environ["UV_CACHE_DIR"]
    with tempfile.TemporaryDirectory(prefix="synapse-locks-") as tmp:
        stage = Path(tmp)
        for name in (*INPUTS, *OUTPUTS):
            source = ROOT / name
            if source.exists():
                shutil.copyfile(source, stage / name)
        for source, output in zip(INPUTS, OUTPUTS, strict=True):
            command = [
                uv,
                "--no-config",
                "pip",
                "compile",
                source,
                "--universal",
                "--python-version",
                "3.10",
                "--generate-hashes",
                "--no-annotate",
                "--default-index",
                "https://pypi.org/simple",
                "--custom-compile-command",
                "python tools/lock_requirements.py",
                "--output-file",
                output,
                "--quiet",
            ]
            if args.upgrade:
                command.append("--upgrade")
            subprocess.run(command, cwd=stage, env=env, check=True)
        changed = []
        for name in OUTPUTS:
            path = ROOT / name
            old = path.read_text() if path.exists() else ""
            new = (stage / name).read_text()
            if old == new:
                continue
            changed.append(name)
            if args.check:
                sys.stdout.writelines(
                    difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True), name, name)
                )
        if args.check:
            if changed:
                print("Locks are stale; run python tools/lock_requirements.py", file=sys.stderr)
                return 1
            print("Python dependency locks are current.")
        else:
            # Both resolutions must succeed before either checked-in output is replaced.
            for name in OUTPUTS:
                (ROOT / name).write_text((stage / name).read_text())
            print("Updated " + ", ".join(changed) if changed else "Python dependency locks are unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
