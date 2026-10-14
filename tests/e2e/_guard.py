"""Shared safety for the browser checklists: refuse to run against a server that is already
listening on the port (it would be someone else's data folder), and never leave the test
server running when a checklist stops on an error."""

import atexit
import socket
import subprocess
import sys

_started = []
_Popen = subprocess.Popen


def _tracked(*args, **kwargs):
    p = _Popen(*args, **kwargs)
    _started.append(p)
    return p


def guard(port: int) -> None:
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            sys.exit(f"Port {port} is already in use (a server left from an earlier run?). Stop it and retry.")
    subprocess.Popen = _tracked
    atexit.register(lambda: [p.kill() for p in _started if p.poll() is None])
