"""Type-check each bundled Python plugin without shadowing the host's packages."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_plugin(folder: Path) -> int:
    """Give relative plugin imports their own namespace and absolute host imports the real API."""
    with tempfile.TemporaryDirectory(prefix="synapse-plugin-mypy-") as temporary:
        stage = Path(temporary)
        namespace = stage / "synapse_bundled"
        package = namespace / folder.name.replace("-", "_")
        package.mkdir(parents=True)
        (namespace / "__init__.py").touch()
        (package / "__init__.py").touch()
        sources = sorted(folder.rglob("*.py"))
        for source in sources:
            target = package / source.relative_to(folder)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        env = {**os.environ, "MYPYPATH": os.pathsep.join((str(stage), str(ROOT)))}
        command = [sys.executable, "-m", "mypy", "--config-file", str(ROOT / "pyproject.toml"), str(package)]
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
        # Mypy may report a cached or canonicalized temporary path, including macOS /var aliases.
        staged_prefix = r"(?m)^.*?[/\\]synapse_bundled[/\\]" + re.escape(package.name) + r"[/\\]"
        output = re.sub(staged_prefix, lambda _: os.path.relpath(folder, ROOT) + os.sep, result.stdout + result.stderr)
        print(f"\n{folder.name} ({len(sources)} Python files)", flush=True)
        print(output, end="", flush=True)
        return result.returncode


def main() -> int:
    folders = sorted(p for p in (ROOT / "plugins").iterdir() if p.is_dir() and any(p.rglob("*.py")))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plugins", nargs="*", help="Plugin folder names; omitted runs all Python plugins")
    args = parser.parse_args()
    unknown = set(args.plugins) - {p.name for p in folders}
    if unknown:
        parser.error("Unknown Python plugins: " + ", ".join(sorted(unknown)))
    selected = [p for p in folders if not args.plugins or p.name in args.plugins]
    failures = sum(check_plugin(p) != 0 for p in selected)
    print(f"\n{len(selected) - failures}/{len(selected)} plugins passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
