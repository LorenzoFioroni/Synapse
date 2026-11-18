"""Synapse Companion: connects a Synapse server to this computer.

It answers "Ask AI" questions from Synapse with your own AI command-line tool (Claude Code; Codex
if you turn it on), restricted to Synapse's tools, and it is how the Synapse browser extension
reaches your library. Python standard library only. See docs/companion.md.

    python synapse_companion.pyz            open the window (starting the companion if needed)
    python synapse_companion.pyz run        run in the foreground (what starting at login does)
"""

from __future__ import annotations

import argparse
import getpass
import os
import signal
import subprocess
import sys

from . import native, tray
from .common import ENV_TOKEN, Fail, check_profile_name, command, load_profile, log, log_to, make_home
from .providers import fetch_check
from .service import Service
from .ui import Window, open_window, running_ticket


def cmd_open(a) -> None:
    running = running_ticket(a.profile)
    if running:
        open_window(f"http://127.0.0.1:{running['port']}/?ticket={running['ticket']}", a.profile)
        return
    # Start the companion in the background; it opens the window when it is ready.
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command("run", "--profile", a.profile, "--open", windowless=True), **kwargs)
    print("Synapse Companion is starting; its window opens in a moment.")


def cmd_run(a) -> None:
    running = running_ticket(a.profile)
    if running:
        if a.open:
            open_window(f"http://127.0.0.1:{running['port']}/?ticket={running['ticket']}", a.profile)
        print("Synapse Companion is already running for this profile.")
        return
    log_to(make_home() / f"{a.profile}.log")
    service = Service(a.profile)
    window = Window(service)
    window.start()
    service.start()
    window.tray.start()
    log(f"Synapse Companion started. Window: {window.origin} (open it with: python {command()[1]})")
    if a.open or not load_profile(a.profile):
        window.open()

    def terminate(*_):
        service.quit()

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    while not service.stopped.wait(1):
        pass
    window.stop()
    if service.restart:
        os.execv(sys.executable, command("run", "--profile", a.profile))
    print("Stopped.")


def cmd_connect(a) -> None:
    token = a.token or os.environ.get(ENV_TOKEN) or getpass.getpass("Companion token (Settings → AI Access): ").strip()
    Service(a.profile).connect(a.url, token, a.name or "")
    print("Connected. Start the companion with:  python", command()[1])


def cmd_status(a) -> None:
    state = Service(a.profile).state()
    if not state["connected"]:
        print("Not connected. Open the window to connect:  python", command()[1])
        return
    print(f"Library: {state['server']} as '{state['name']}'")
    running = running_ticket(a.profile)
    print(f"Companion: {'running' if running else 'not running'}")
    for p in state["providers"]:
        print(f"{p['label']}: {'on' if p['enabled'] else 'off'}, {'found' if p['found'] else 'not found'}")
    ext = state["extension"]
    print(f"Extension: {ext['dir'] if ext.get('installed') else 'not installed'}")


def profile_name(value: str) -> str:
    try:
        return check_profile_name(value)
    except Fail as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def parser() -> argparse.ArgumentParser:
    doc = (__doc__ or "").split("\n\n")
    p = argparse.ArgumentParser(prog="synapse_companion", description=doc[0])
    sub = p.add_subparsers(dest="command")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--profile", default="default", type=profile_name, help="which connection (default: default)")
    sub.add_parser("open", parents=[common], help="open the window (the default)").set_defaults(fn=cmd_open)
    r = sub.add_parser("run", parents=[common], help="run the companion in the foreground")
    r.add_argument("--open", action="store_true", help="also open the window")
    r.set_defaults(fn=cmd_run)
    c = sub.add_parser("connect", parents=[common], help="connect without the window")
    c.add_argument("url", help="the server's address, such as https://synapse.example.com")
    c.add_argument("--token", help=f"companion token (else ${ENV_TOKEN} or asked for)")
    c.add_argument("--name", help="how this computer is shown in Synapse")
    c.set_defaults(fn=cmd_connect)
    sub.add_parser("status", parents=[common], help="show the connection").set_defaults(fn=cmd_status)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["native-host"]:
        # Started by the browser: `native-host --profile NAME <extension origin> [--parent-window=…]`
        profile = argv[2] if argv[1:2] == ["--profile"] and len(argv) > 2 else "default"
        return native.main(check_profile_name(profile))
    if argv[:1] == ["tray"]:  # started by the companion, with its window key on stdin
        return tray.main()
    if argv[:1] == ["fetch-check"]:  # Claude Code's hook before each WebFetch, with the call on stdin
        return fetch_check(argv[1] if len(argv) > 1 else "", sys.stdin)
    args = parser().parse_args(argv)
    if args.command is None:
        args = parser().parse_args(["open", *argv])
    try:
        args.fn(args)
    except Fail as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0
