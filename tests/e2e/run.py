"""Run the browser checklists and summarise them.

    python tests/e2e/run.py                    every checklist, one after another
    python tests/e2e/run.py notes render       only these (names as in tests/e2e/<name>_checklist.py)
    python tests/e2e/run.py --browser webkit       portable WebKit subset
    python tests/e2e/run.py --output-dir results  retain all attempt logs/screenshots
    python tests/e2e/run.py --list             the names
    python tests/e2e/run.py --retries 1        run a failed checklist once more (timing-sensitive checks)

Each checklist starts its own server with a throw-away data folder, so they run one at a time.
The output of a failed checklist is printed in full; the exit status is 1 if any failed.
Needs Playwright (pip install playwright) and the frontend built (python server.py builds it).
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TIMEOUT = 600  # seconds per checklist


def checklists() -> dict[str, Path]:
    return {p.name.removesuffix("_checklist.py"): p for p in sorted(HERE.glob("*_checklist.py"))}


def run_one(path: Path) -> tuple[bool, str, str]:
    """(passed, "n/m", output) for one checklist."""
    process = subprocess.Popen(
        [sys.executable, str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=HERE.parents[1],
        start_new_session=os.name == "posix",
    )
    try:
        text, _ = process.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        # Stop the checklist's test server too, so a later checklist cannot hit stale data.
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        text, _ = process.communicate()
        return False, "timeout", text
    counts = re.findall(r"(\d+)/(\d+) (?:checks )?passed", text)
    score = f"{counts[-1][0]}/{counts[-1][1]}" if counts else "?"
    return process.returncode == 0 and bool(counts) and counts[-1][0] == counts[-1][1], score, text


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the browser checklists in tests/e2e.")
    ap.add_argument("names", nargs="*", help="checklists to run (default: all)")
    ap.add_argument("--list", action="store_true", help="list the checklists and stop")
    ap.add_argument("--browser", choices=["chromium", "webkit"], default="chromium")
    ap.add_argument("--output-dir", type=Path, help="save complete logs and available screenshots for every attempt")
    ap.add_argument("--retries", type=int, default=0, help="run a failed checklist again up to N times")
    args = ap.parse_args()

    known = checklists()
    if args.list:
        print("\n".join(known))
        return 0
    unknown = [n for n in args.names if n not in known]
    if unknown:
        print(f"Unknown checklist(s): {', '.join(unknown)}. Known: {', '.join(known)}", file=sys.stderr)
        return 2
    webkit = ["notes", "portable_touch", "passkey_ui"]
    chosen = args.names or (webkit if args.browser == "webkit" else list(known))
    if args.browser == "webkit" and any(name not in webkit for name in chosen):
        print(
            "WebKit supports: " + ", ".join(webkit) + "; CDP gesture/authenticator checklists require Chromium.",
            file=sys.stderr,
        )
        return 2
    os.environ["SYNAPSE_E2E_BROWSER"] = args.browser

    rows = []
    for name in chosen:
        start = time.time()
        tries = 0
        while True:
            tries += 1
            if args.output_dir:
                folder = args.output_dir.resolve() / name / f"attempt-{tries}"
                folder.mkdir(parents=True, exist_ok=True)
                shots = folder / "screenshots"
                shots.mkdir(exist_ok=True)
                os.environ["SHOTS"] = str(shots)
            ok, score, output = run_one(known[name])
            if args.output_dir:
                (folder / "output.log").write_text(output, encoding="utf-8")
            if ok or tries > args.retries:
                break
        took = time.time() - start
        note = f" (passed on try {tries})" if ok and tries > 1 else ""
        print(f"{'PASS' if ok else 'FAIL'}  {name:<16} {score:>8}  {took:5.0f} s{note}", flush=True)
        if not ok:
            print("\n".join(f"    | {line}" for line in output.rstrip().splitlines()[-40:]), flush=True)
        rows.append((name, ok))

    if args.output_dir:
        summary = f"# Browser checklists: {args.browser}\n\n| Checklist | Result |\n| --- | --- |\n"
        summary += "".join(f"| {name} | {'PASS' if ok else 'FAIL'} |\n" for name, ok in rows)
        (args.output_dir / "summary.md").write_text(summary, encoding="utf-8")
    failed = [n for n, ok in rows if not ok]
    print(
        f"\n{len(rows) - len(failed)}/{len(rows)} checklists passed"
        + (f"; failed: {', '.join(failed)}" if failed else "")
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
