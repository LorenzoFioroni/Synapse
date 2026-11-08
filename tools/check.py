"""Verify an already-set-up checkout without installing dependencies or changing source files."""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage", action="store_true", help="Include CI's coverage and slow-test diagnostics")
    args = parser.parse_args()
    python = sys.executable
    tests = [python, "-m", "pytest", "-q"]
    if args.coverage:
        tests += ["--cov", "--durations=20"]
    tests += ["-o", "faulthandler_timeout=60"]
    commands = [
        [python, "tools/agents.py", "--check"],
        [python, "-m", "ruff", "check", "."],
        [python, "-m", "ruff", "format", "--check", "."],
        [python, "-m", "mypy"],
        [python, "tools/typecheck_plugins.py"],
        ["npm", "--prefix", "frontend", "run", "check"],
        ["npm", "--prefix", "frontend", "run", "build"],
        tests,
    ]
    for command in commands:
        print(f"\n$ {shlex.join(command)}", flush=True)
        try:
            result = subprocess.run(command, cwd=ROOT, check=False)
        except FileNotFoundError:
            print(f"Missing tool: {command[0]}. See docs/development.md#setup.", file=sys.stderr)
            return 1
        if result.returncode:
            print("Verification stopped; see the failure above and docs/development.md.", file=sys.stderr)
            return result.returncode if result.returncode > 0 else 1
    print("\nAll local checks passed. Run affected browser checklists separately.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
