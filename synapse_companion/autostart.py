"""Starting the companion when the user logs in, and an entry to open its window: an XDG autostart
entry and an applications-menu entry on Linux, a LaunchAgent on macOS, a Run value on Windows.
All are per user; none needs administrator rights."""

from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path

from .common import command

LABEL = "org.synapse.companion"


def _desktop_exec(args: list[str]) -> str:
    # The Desktop Entry spec: arguments in double quotes, with \ " ` $ escaped.
    def quote(a: str) -> str:
        return '"' + "".join("\\" + c if c in '\\"`$' else c for c in a) + '"'

    return " ".join(quote(a) for a in args)


def _xdg(kind: str) -> Path:
    if kind == "autostart":
        return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "applications"


def _paths(profile: str) -> dict[str, Path]:
    suffix = "" if profile == "default" else f"-{profile}"
    if sys.platform == "darwin":
        return {"agent": Path.home() / "Library" / "LaunchAgents" / f"{LABEL}{suffix.replace('-', '.')}.plist"}
    return {
        "autostart": _xdg("autostart") / f"synapse-companion{suffix}.desktop",
        "menu": _xdg("applications") / f"synapse-companion{suffix}.desktop",
    }


def _run_value(profile: str) -> str:
    return "Synapse Companion" + ("" if profile == "default" else f" ({profile})")


def enabled(profile: str) -> bool:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
                winreg.QueryValueEx(k, _run_value(profile))
            return True
        except OSError:
            return False
    key = "agent" if sys.platform == "darwin" else "autostart"
    return _paths(profile)[key].exists()


def _entry(name: str, args: list[str], icon: Path | None, *, hidden: bool) -> str:
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={name}",
        "Comment=Ask AI and the browser extension for your Synapse library",
        f"Exec={_desktop_exec(args)}",
        "Terminal=false",
        "Categories=Office;",
    ]
    if icon:
        lines.append(f"Icon={icon}")
    if hidden:
        lines += ["NoDisplay=true", "X-GNOME-Autostart-enabled=true"]
    return "\n".join(lines) + "\n"


def set_enabled(profile: str, on: bool, icon: Path | None = None) -> None:
    """Start the companion at login (``run``), or stop doing so. On Linux the applications menu
    also gets "Synapse Companion", which opens the window."""
    run = command("run", "--profile", profile, windowless=True)
    if sys.platform == "win32":
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
            if on:
                winreg.SetValueEx(k, _run_value(profile), 0, winreg.REG_SZ, " ".join(f'"{a}"' for a in run))
            else:
                try:
                    winreg.DeleteValue(k, _run_value(profile))
                except OSError:
                    pass
        return
    paths = _paths(profile)
    if sys.platform == "darwin":
        agent = paths["agent"]
        if on:
            agent.parent.mkdir(parents=True, exist_ok=True)
            agent.write_bytes(
                plistlib.dumps(
                    {
                        "Label": agent.stem,
                        "ProgramArguments": run,
                        "RunAtLoad": True,
                        "StandardOutPath": "/dev/null",
                        "StandardErrorPath": "/dev/null",
                    }
                )
            )
        else:
            agent.unlink(missing_ok=True)
        return
    if on:
        for key, args, hidden in (("autostart", run, True), ("menu", command("open", "--profile", profile), False)):
            paths[key].parent.mkdir(parents=True, exist_ok=True)
            paths[key].write_text(_entry("Synapse Companion", args, icon, hidden=hidden), encoding="utf-8")
    else:
        paths["autostart"].unlink(missing_ok=True)


def describe(profile: str) -> str:
    """Where the start-at-login entry is, for the window."""
    if sys.platform == "win32":
        return r"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run"
    return str(_paths(profile)["agent" if sys.platform == "darwin" else "autostart"])
