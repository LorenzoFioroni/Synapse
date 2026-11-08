"""Select browser checklists for a PR; unknown application changes fall back to full coverage."""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORTABLE = {"notes", "portable_touch", "passkey_ui"}
GROUPS = [
    (
        ("frontend/src/views/settings/backups.ts", "frontend/src/views/settings/remote*.ts"),
        {"settings_organization", "backups", "remote_backups"},
    ),
    (
        ("frontend/src/views/notes/history*.ts", "backend/notes/history/*"),
        {"notes", "notes_history", "claude_edits"},
    ),
    (("frontend/src/views/notes/attachments*.ts", "backend/notes/attachments.py"), {"notes", "attachments", "gallery"}),
    (("frontend/src/views/notes/equations.ts",), {"notes", "equations", "render", "writing", "export"}),
    (("frontend/src/views/notes/commands.ts",), {"notes", "note_commands"}),
    (
        ("frontend/src/viewer/*", "frontend/viewer.html"),
        {"annotations", "viewer_links", "pdf_tabs", "multipdf", "lazy_viewer", "zoom", "touch_app", "links"},
    ),
    (("browser_extension/*", "backend/connector.py", "backend/api/connector.py"), {"extension", "find_pdfs"}),
    (("backend/backup/*", "backend/api/backups.py"), {"backups", "remote_backups"}),
    (
        ("backend/access/*", "backend/api/devices.py", "backend/api/passkeys.py"),
        {"devices", "passkeys", "passkey_ui", "csp"},
    ),
    (("plugins/autocompletion/*",), {"assist", "notes"}),
]


def select(paths: list[str], known: set[str]) -> set[str]:
    chosen: set[str] = set()
    for path in paths:
        if path.startswith("tests/e2e/") and path.endswith("_checklist.py"):
            name = Path(path).name.removesuffix("_checklist.py")
            chosen.update({name} if name in known else known)
            continue
        if (
            path in {".github/workflows/browser-checks.yml", "tools/browser_scope.py", "server.py"}
            or path.startswith(("tests/e2e/", "tests/fake_", "requirements"))
            or path == "tools/requirements-e2e.txt"
        ):
            return known.copy()
        groups = [names for patterns, names in GROUPS if any(fnmatch.fnmatchcase(path, p) for p in patterns)]
        if groups:
            for names in groups:
                chosen.update(names)
            if path.startswith(("frontend/", "backend/", "plugins/")):
                chosen.update(PORTABLE)
        elif path.startswith(("frontend/", "backend/", "browser_extension/", "plugins/")):
            return known.copy()
    return chosen


def matrix(paths: list[str] | None, known: set[str], browser: str = "both") -> dict:
    chosen = known.copy() if paths is None else select(paths, known)
    missing = chosen - known
    if missing:
        raise ValueError("Unknown checklists in browser scope: " + ", ".join(sorted(missing)))
    rows = []
    for name in ("chromium", "webkit"):
        if browser not in ("both", name):
            continue
        names = chosen if name == "chromium" else chosen & PORTABLE
        if names:
            rows.append({"browser": name, "checklists": sorted(names)})
    return {"include": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", choices=["pull_request", "schedule", "workflow_dispatch"], required=True)
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="")
    parser.add_argument("--browser", choices=["both", "chromium", "webkit"], default="both")
    args = parser.parse_args()
    paths = None
    if args.event == "pull_request":
        if not all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in (args.base, args.head)):
            parser.error("PR base and head must be full commit SHAs")
        result = subprocess.run(
            ["git", "diff", "--name-only", "--no-renames", "-z", f"{args.base}...{args.head}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        paths = result.stdout.decode().rstrip("\0").split("\0") if result.stdout else []
    known = {p.name.removesuffix("_checklist.py") for p in (ROOT / "tests/e2e").glob("*_checklist.py")}
    selected = matrix(paths, known, args.browser)
    print("needed=" + str(bool(selected["include"])).lower())
    print("matrix=" + json.dumps(selected, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
