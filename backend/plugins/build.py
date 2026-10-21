"""A plugin frontend written in TypeScript (``"frontend": "frontend/main.ts"``) is bundled into one
JavaScript module with esbuild when the plugin starts: the copy Synapse builds its own frontend with
(``frontend/node_modules``), else one on the PATH. The bundle goes to ``plugin-data/<id>/.build/``
(not in backups) and is what the app loads."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .errors import PluginError

TS_SUFFIXES = (".ts", ".tsx", ".mts")
BUILD_DIR = ".build"
BUNDLE = "main.js"
BUNDLED = "__build__/main.js"  # the bundle's path in /api/plugin-files/<id>/…
TIMEOUT = 120


def needs_build(frontend: str | None) -> bool:
    return bool(frontend) and frontend.endswith(TS_SUFFIXES)  # type: ignore[union-attr]


def esbuild(app_frontend: Path) -> str | None:
    local = app_frontend / "node_modules" / ".bin" / ("esbuild.cmd" if os.name == "nt" else "esbuild")
    if local.exists():
        return str(local)
    return shutil.which("esbuild")


def bundle(entry: Path, out_dir: Path, app_frontend: Path) -> Path:
    """Bundle ``entry`` (and what it imports) into ``out_dir/main.js``; raises PluginError with
    esbuild's message when it fails."""
    tool = esbuild(app_frontend)
    if not tool:
        raise PluginError(
            "Its frontend is TypeScript, which needs esbuild: install Node.js and run npm ci in Synapse's frontend folder"
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / BUNDLE
    cmd = [tool, str(entry), "--bundle", "--format=esm", "--target=es2020", f"--outfile={out}", "--log-level=error"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise PluginError(f"Building its frontend failed: {e}") from e
    if r.returncode != 0:
        tail = "\n".join((r.stderr or r.stdout).strip().splitlines()[-8:])
        raise PluginError(f"Building its frontend failed:\n{tail}")
    return out
