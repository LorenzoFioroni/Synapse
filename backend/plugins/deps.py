"""Python packages a plugin needs (``"python": ["scirate>=0.0.7"]`` in its manifest), installed with
pip into the plugin's own folder of ``plugin-data`` (``.python/``, left out of backups) when it
starts, and on the import path only while it runs. Packages Synapse itself uses are refused, so a
plugin cannot replace a version the app relies on."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from .errors import PluginError

DIR = ".python"
MARKER = ".installed.json"
# a requirement: a name, optional extras, optional version specifiers — no URLs, paths or pip options
REQ_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9._,-]+\])?\s*((==|!=|<=|>=|~=|<|>)\s*[A-Za-z0-9.*+!-]+\s*,?\s*)*$"
)
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")
TIMEOUT = 600


def name_of(req: str) -> str:
    m = NAME_RE.match(req.strip())
    return re.sub(r"[-_.]+", "-", m.group(0)).lower() if m else ""


def core_packages(app_root: Path) -> set[str]:
    """The packages in Synapse's requirements files (and the ones they bring that matter)."""
    names = {"flask", "werkzeug", "jinja2", "requests", "urllib3", "pip", "setuptools"}
    for f in ("requirements.txt", "requirements-dev.txt"):
        try:
            for line in (app_root / f).read_text("utf-8").splitlines():
                line = line.split("#", 1)[0].strip()
                if line and not line.startswith("-"):
                    names.add(name_of(line))
        except OSError:
            continue
    return names


def check(reqs: Sequence[str], core: set[str]) -> list[str]:
    """The requirements, cleaned; raises PluginError for one that cannot be installed."""
    out = []
    for r in reqs:
        r = str(r).strip()
        if not REQ_RE.match(r):
            raise PluginError(f'"python": {r!r} is not a package requirement like "scirate>=0.0.7"')
        if name_of(r) in core:
            raise PluginError(f'"python": {name_of(r)} is one of Synapse\'s own packages; use the version it has')
        out.append(r)
    return out


def pip_install(reqs: list[str], target: Path) -> None:
    """pip, run by the Python that runs Synapse, installing into ``target``."""
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--quiet"]
    try:
        r = subprocess.run(
            [*cmd, "--target", str(target), *reqs], capture_output=True, text=True, timeout=TIMEOUT, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise PluginError(f"Installing {', '.join(reqs)} failed: {e}") from e
    if r.returncode != 0:
        tail = "\n".join((r.stderr or r.stdout or "").strip().splitlines()[-4:])
        raise PluginError(f"Installing {', '.join(reqs)} failed (is the computer online?): {tail}")


def ensure(reqs: list[str], folder: Path, installer: Callable[[list[str], Path], None] = pip_install) -> Path | None:
    """The folder holding ``reqs`` for a plugin whose data folder is ``folder``, installing them the
    first time and again when the list changes; None when it needs none."""
    target = folder / DIR
    if not reqs:
        return None
    marker = target / MARKER
    try:
        if json.loads(marker.read_text("utf-8")) == reqs:
            return target
    except (OSError, ValueError):
        pass
    fresh = folder / (DIR + ".new")
    shutil.rmtree(fresh, ignore_errors=True)
    fresh.mkdir(parents=True)
    try:
        installer(reqs, fresh)
        (fresh / MARKER).write_text(json.dumps(reqs), "utf-8")
    except BaseException:
        shutil.rmtree(fresh, ignore_errors=True)
        raise
    shutil.rmtree(target, ignore_errors=True)
    fresh.rename(target)
    return target
