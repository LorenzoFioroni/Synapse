"""The desktop shortcut (``python server.py --make-shortcut``): an icon that starts Synapse in its
own window, with the server in the background, and stops the server when the window is closed.

* **macOS:** ``~/Applications/Synapse.app`` (so Spotlight and Launchpad find it, and it can be kept
  in the Dock) and a link to it on the Desktop. It is a small app bundle: a shell script that runs
  ``server.py --window --quit-when-closed`` with this checkout's Python, and the Synapse icon
  (``frontend/assets/Synapse.icns``). It shows no icon of its own in the Dock (``LSUIElement``);
  the window does. Its server's output goes to ``~/Library/Logs/Synapse/server.log``, and an alert
  shows the end of it when the server cannot start.
* **Linux:** a ``synapse.desktop`` launcher in ``~/.local/share/applications`` and on the Desktop.

The shortcut remembers where this checkout and its Python are, and the ``PATH`` (so that Node.js is
found when the frontend needs a rebuild after ``git pull``). Make it again after moving the folder.
"""

from __future__ import annotations

import plistlib
import shlex
import shutil
import sys
from pathlib import Path

BUNDLE_ID = "io.github.lorenzofioroni.synapse.launcher"
NAME = "Synapse"
LOG_LIMIT = 1_000_000  # bytes; a longer log is moved to server.log.1 at the next start


class ShortcutError(RuntimeError):
    pass


def _ours(bundle: Path) -> bool:
    try:
        with open(bundle / "Contents" / "Info.plist", "rb") as f:
            return plistlib.load(f).get("CFBundleIdentifier") == BUNDLE_ID
    except (OSError, plistlib.InvalidFileException, ValueError):
        return False


def launch_script(app_root: Path, python: str, args: list[str], path_env: str) -> str:
    """The macOS launcher. It starts the server in the background and returns once it runs (or
    has only opened the window of a server that was running already), so that a second
    double-click starts the launcher again instead of finding it still running. An alert shows the
    end of the log when the server stops with an error."""
    cmd = shlex.join([python, "server.py", "--window", "--quit-when-closed", *args])
    return f"""#!/bin/bash
# Synapse: opens the library in its own window, starting the server first when it is not running.
# Made by `python server.py --make-shortcut`; make it again after moving the Synapse folder.
export PATH={shlex.quote(path_env)}
LOG="$HOME/Library/Logs/Synapse/server.log"
mkdir -p "$(dirname "$LOG")"
if [ -f "$LOG" ] && [ "$(wc -c <"$LOG")" -gt {LOG_LIMIT} ]; then mv "$LOG" "$LOG.1"; fi
touch "$LOG"
from=$(($(wc -l <"$LOG") + 1))
cd {shlex.quote(str(app_root))} || exit 1
nohup {cmd} >>"$LOG" 2>&1 </dev/null &
pid=$!
for _ in $(seq 1 120); do   # up to a minute (the first start may build the frontend)
  sleep 0.5
  if ! kill -0 "$pid" 2>/dev/null; then
    wait "$pid"
    status=$?
    if [ "$status" -ne 0 ]; then
      osascript - "$(tail -n +"$from" "$LOG" | tail -n 6)" "$LOG" >/dev/null 2>&1 <<'END'
on run argv
  display alert "Synapse could not start" message (item 1 of argv) & return & return & "The whole log: " & (item 2 of argv) as critical
end run
END
    fi
    exit "$status"
  fi
  tail -n +"$from" "$LOG" | grep -q "Synapse running at" && exit 0
done
exit 0
"""


def info_plist() -> bytes:
    return plistlib.dumps(
        {
            "CFBundleName": NAME,
            "CFBundleDisplayName": NAME,
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleExecutable": "synapse",
            "CFBundleIconFile": "Synapse",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "1.0",
            "CFBundleVersion": "1",
            "LSUIElement": True,  # the launcher itself has no Dock icon: the window has one
            "NSHighResolutionCapable": True,
        }
    )


def make_mac(app_root: Path, python: str, args: list[str], path_env: str, home: Path) -> list[Path]:
    apps = home / "Applications"
    bundle = apps / f"{NAME}.app"
    if bundle.exists() and not _ours(bundle):
        # Safari's "Add to Dock" names its web apps after the site, so the name may be taken.
        bundle = apps / f"{NAME} Launcher.app"
        if bundle.exists() and not _ours(bundle):
            raise ShortcutError(f"{bundle} exists and was not made by Synapse; move it away and try again.")
    if bundle.exists():
        shutil.rmtree(bundle)
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    (bundle / "Contents" / "Resources").mkdir()
    (bundle / "Contents" / "Info.plist").write_bytes(info_plist())
    exe = bundle / "Contents" / "MacOS" / "synapse"
    exe.write_text(launch_script(app_root, python, args, path_env), encoding="utf-8")
    exe.chmod(0o755)
    shutil.copyfile(
        app_root / "frontend" / "assets" / "Synapse.icns", bundle / "Contents" / "Resources" / "Synapse.icns"
    )
    made = [bundle]
    desktop = home / "Desktop"
    if desktop.is_dir():
        link = desktop / bundle.name
        if link.is_symlink() or not link.exists():
            if link.is_symlink():
                link.unlink()
            link.symlink_to(bundle)
            made.append(link)
    return made


def desktop_entry(app_root: Path, python: str, args: list[str]) -> str:
    def q(a: str) -> str:  # quoting of the Exec key (Desktop Entry Specification)
        return '"' + a.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$") + '"'

    cmd = " ".join(q(a) for a in [python, str(app_root / "server.py"), "--window", "--quit-when-closed", *args])
    return f"""[Desktop Entry]
Type=Application
Name={NAME}
Comment=Your reference library, notes and PDFs
Exec={cmd.replace("%", "%%")}
Path={app_root}
Icon={app_root / "frontend" / "assets" / "icon-512.png"}
Terminal=false
Categories=Office;Education;
StartupWMClass={NAME}
"""


def make_linux(app_root: Path, python: str, args: list[str], home: Path) -> list[Path]:
    text = desktop_entry(app_root, python, args)
    made = []
    for folder in (home / ".local" / "share" / "applications", home / "Desktop"):
        if folder.name == "Desktop" and not folder.is_dir():
            continue
        folder.mkdir(parents=True, exist_ok=True)
        f = folder / "synapse.desktop"
        f.write_text(text, encoding="utf-8")
        f.chmod(0o755)
        made.append(f)
    return made


def make_shortcut(
    app_root: Path,
    args: list[str],
    *,
    python: str | None = None,
    platform: str = sys.platform,
    home: Path | None = None,
    path_env: str = "",
) -> list[Path]:
    """Make the shortcut for this checkout; ``args`` are server.py options to start with (port,
    data folder…). Returns the files made."""
    python = python or sys.executable
    home = home or Path.home()
    if platform == "darwin":
        return make_mac(app_root, python, args, path_env, home)
    if platform.startswith("linux"):
        return make_linux(app_root, python, args, home)
    raise ShortcutError(
        "A desktop shortcut can be made on macOS and Linux. Elsewhere, make one yourself that runs: "
        + shlex.join([python, str(app_root / "server.py"), "--window", "--quit-when-closed", *args])
    )
