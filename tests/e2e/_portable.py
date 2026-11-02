"""Local server fixture for browser-portable checklists; no user's data or external services."""

import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import requests

from tests.e2e._guard import guard

APP = Path(__file__).resolve().parents[2]


def browser_type(playwright):
    name = os.environ.get("SYNAPSE_E2E_BROWSER", "chromium")
    if name not in {"chromium", "webkit"}:
        raise ValueError(f"Unsupported browser: {name}")
    return getattr(playwright, name)


@contextmanager
def server(port):
    guard(port)
    with tempfile.TemporaryDirectory(prefix="synapse-portable-") as data:
        process = subprocess.Popen(
            [sys.executable, "server.py", "--no-browser", "--port", str(port), "--data-dir", data],
            cwd=APP,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            for _ in range(240):
                try:
                    requests.get(base + "/api/settings", timeout=1).raise_for_status()
                    break
                except requests.RequestException as error:
                    if process.poll() is not None:
                        raise RuntimeError("Test server exited during startup") from error
                    time.sleep(0.25)
            else:
                raise RuntimeError("Test server did not start")
            yield base
        finally:
            process.terminate()
            process.wait(timeout=10)


async def screenshot(page, name):
    if folder := os.environ.get("SHOTS"):
        Path(folder).mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(Path(folder) / name))
