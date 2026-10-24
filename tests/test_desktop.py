"""Running Synapse like a desktop program (backend/desktop/, docs/desktop.md): the server that stops
with its last window, opening the window, the desktop shortcut, and the server.py options."""

import json
import os
import plistlib
import shutil
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from backend.desktop import presence as P
from backend.desktop import shortcut, window
from backend.desktop.presence import Presence
from server import app_url

ROOT = Path(__file__).resolve().parent.parent


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ---------------------------------------------------------------- presence


def watching(clock):
    p = Presence(clock)
    p.watching = True  # as watch() would, without its thread
    p._empty_since = clock()
    return p


def test_a_server_not_started_to_quit_does_not_watch():
    p = Presence(Clock())
    assert p.ping("a") is False
    assert p.open_windows() == 0


def test_stops_soon_after_the_last_window_says_goodbye():
    clock = Clock()
    p = watching(clock)
    assert p.ping("a") is True and p.ping("b") is True
    p.bye("a")
    clock.t += 100
    assert not p.idle(), "one window is still open"
    p.bye("b")
    clock.t += P.BYE_GRACE - 1
    assert not p.idle()
    clock.t += 2
    assert p.idle()


def test_a_reload_says_hello_again_in_time():
    clock = Clock()
    p = watching(clock)
    p.ping("page-1")
    p.bye("page-1")
    clock.t += 2
    p.ping("page-2")  # the reloaded page
    clock.t += P.BYE_GRACE * 3
    assert not p.idle()


def test_a_window_not_heard_from_for_long_is_gone():
    clock = Clock()
    p = watching(clock)
    p.ping("a")
    clock.t += P.SILENCE - 1
    assert not p.idle()
    p.ping("a")  # a hidden window still says hello once a minute
    clock.t += P.SILENCE - 1
    assert not p.idle()
    clock.t += 2
    assert not p.idle(), "gone just now: the grace starts"
    clock.t += P.BYE_GRACE
    assert p.idle()


def test_stops_when_no_window_ever_opens():
    clock = Clock()
    p = watching(clock)
    clock.t += P.FIRST - 1
    assert not p.idle()
    clock.t += 2
    assert p.idle()


def test_goodbye_from_an_unknown_window_changes_nothing():
    clock = Clock()
    p = watching(clock)
    p.ping("a")
    p.bye("someone-else")
    clock.t += P.BYE_GRACE * 2
    assert not p.idle()


def test_watch_calls_back_once_from_a_thread():
    clock = Clock()
    p = Presence(clock)
    done = threading.Event()
    p.watch(done.set, check_every=0.01)
    p.ping("a")
    p.bye("a")
    clock.t += P.BYE_GRACE + 1
    assert done.wait(2)


def test_ping_and_bye_endpoints(client, svc):
    assert client.post("/api/window/ping", json={"client": "x"}).get_json() == {"watch": False}
    svc.presence.watching = True
    assert client.post("/api/window/ping", json={"client": "x"}).get_json() == {"watch": True}
    assert svc.presence.open_windows() == 1
    assert client.post("/api/window/bye", json={"client": "x"}).status_code == 204
    assert svc.presence.open_windows() == 0


def test_other_sites_cannot_say_goodbye(client, svc):
    svc.presence.watching = True
    client.post("/api/window/ping", json={"client": "x"})
    r = client.post("/api/window/bye", json={"client": "x"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert svc.presence.open_windows() == 1


# ------------------------------------------------------------- the setting


def test_pdfs_open_in_tabs_by_default(client):
    assert client.get("/api/settings").get_json()["settings"]["pdf_open_in"] == "tabs"
    assert client.put("/api/settings", json={"pdf_open_in": "browser"}).status_code == 200
    assert client.put("/api/settings", json={"pdf_open_in": "windows"}).status_code == 400


# ------------------------------------------------------------------ window


def chrome_app(home: Path, folder: str, name: str, url: str | None) -> Path:
    bundle = home / "Applications" / folder / f"{name}.app" if folder else home / "Applications" / f"{name}.app"
    (bundle / "Contents").mkdir(parents=True)
    info = {"CFBundleName": name}
    if url:
        info[window.CHROME_APP_KEY] = url
    (bundle / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
    return bundle


def test_finds_synapse_installed_as_an_app(tmp_path):
    chrome_app(tmp_path, "Chrome Apps.localized", "Gmail", "https://mail.google.com/")
    syn = chrome_app(tmp_path, "Chrome Apps.localized", "Synapse", "http://127.0.0.1:8000/")
    chrome_app(tmp_path, "", "Synapse", None)  # the shortcut's own launcher: not an installed app
    assert window.installed_app("http://127.0.0.1:8000/", tmp_path) == syn
    assert window.installed_app("http://localhost:8000/", tmp_path) == syn, "the same server"
    assert window.installed_app("http://127.0.0.1:5050/", tmp_path) is None, "another port"
    assert window.installed_app("https://127.0.0.1:8000/", tmp_path) is None
    assert window.installed_app("http://127.0.0.1:8000/", tmp_path / "nobody") is None


def test_finds_an_installed_app_for_the_same_local_name(tmp_path):
    syn = chrome_app(tmp_path, "Chrome Apps.localized", "Synapse", "https://macbook.local:8000/")
    assert window.installed_app("https://macbook.local:8000/", tmp_path) == syn
    assert window.installed_app("https://other.local:8000/", tmp_path) is None


def test_an_app_for_another_computer_is_not_this_server(tmp_path):
    chrome_app(tmp_path, "Chrome Apps.localized", "Synapse", "http://mac.local:8000/")
    assert window.installed_app("http://127.0.0.1:8000/", tmp_path) is None


def test_finds_a_chromium_browser_on_a_mac(tmp_path, monkeypatch):
    exe = tmp_path / "Applications" / "Brave Browser.app" / "Contents" / "MacOS" / "Brave Browser"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    real_exists = Path.exists
    monkeypatch.setattr(Path, "exists", lambda p: False if str(p).startswith("/Applications") else real_exists(p))
    assert window.chromium("darwin", tmp_path) == str(exe)
    exe.unlink()
    assert window.chromium("darwin", tmp_path) is None


def test_finds_a_chromium_browser_on_linux(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda n: f"/usr/bin/{n}" if n == "chromium" else None)
    assert window.chromium("linux") == "/usr/bin/chromium"
    monkeypatch.setattr(shutil, "which", lambda n: None)
    assert window.chromium("linux") is None


def test_open_window_prefers_the_installed_app_then_app_mode(monkeypatch, tmp_path):
    started = []
    monkeypatch.setattr(window, "_detached", started.append)
    monkeypatch.setattr(window.sys, "platform", "darwin")
    monkeypatch.setattr(window, "installed_app", lambda url: tmp_path / "Synapse.app")
    assert "installed" in window.open_window("http://127.0.0.1:8000/")
    assert started[-1] == ["open", str(tmp_path / "Synapse.app")]
    monkeypatch.setattr(window, "installed_app", lambda url: None)
    monkeypatch.setattr(window, "chromium", lambda: "/x/Google Chrome")
    msg = window.open_window("http://127.0.0.1:8000/")
    assert started[-1] == ["/x/Google Chrome", "--app=http://127.0.0.1:8000/"]
    assert "Install Page as App" in msg
    monkeypatch.setattr(window, "chromium", lambda: None)
    opened = []
    monkeypatch.setattr(window.webbrowser, "open_new", opened.append)
    assert "default browser" in window.open_window("http://127.0.0.1:8000/")
    assert opened == ["http://127.0.0.1:8000/"]


class _Answer(BaseHTTPRequestHandler):
    body = b""
    status = 200

    def do_GET(self):  # noqa: N802
        self.send_response(self.status)
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *a):
        pass


def serve(body: bytes, status: int = 200):
    handler = type("H", (_Answer,), {"body": body, "status": status})
    srv = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/"


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_tells_synapse_from_other_programs():
    srv, url = serve(json.dumps({"name": "Synapse"}).encode())
    try:
        assert window.synapse_at(url) is True
    finally:
        srv.shutdown()
    srv, url = serve(b"<html>not json</html>")
    try:
        assert window.synapse_at(url) is False
    finally:
        srv.shutdown()
    srv, url = serve(b"", status=404)
    try:
        assert window.synapse_at(url) is False
    finally:
        srv.shutdown()
    assert window.synapse_at(f"http://127.0.0.1:{free_port()}/") is None


# ---------------------------------------------------------------- shortcut


def test_mac_shortcut_is_an_app_with_the_icon(tmp_path):
    (tmp_path / "Desktop").mkdir()
    made = shortcut.make_shortcut(
        ROOT,
        ["--port", "5050"],
        python="/venv/bin/python",
        platform="darwin",
        home=tmp_path,
        path_env="/opt/homebrew/bin:/usr/bin",
    )
    bundle = tmp_path / "Applications" / "Synapse.app"
    assert made == [bundle, tmp_path / "Desktop" / "Synapse.app"]
    info = plistlib.loads((bundle / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == "synapse" and info["CFBundleIconFile"] == "Synapse"
    assert info["LSUIElement"] is True, "the window has the Dock icon, not the launcher"
    exe = bundle / "Contents" / "MacOS" / "synapse"
    assert os.access(exe, os.X_OK)
    script = exe.read_text()
    assert "/venv/bin/python server.py --window --quit-when-closed --port 5050" in script
    assert "export PATH=/opt/homebrew/bin:/usr/bin" in script, "Node.js is found for rebuilds"
    assert f"cd {ROOT}" in script or f"cd '{ROOT}'" in script
    icns = (bundle / "Contents" / "Resources" / "Synapse.icns").read_bytes()
    assert icns[:4] == b"icns" and int.from_bytes(icns[4:8], "big") == len(icns)
    assert (tmp_path / "Desktop" / "Synapse.app").resolve() == bundle.resolve()


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
def test_the_mac_launcher_is_valid_bash(tmp_path):
    script = tmp_path / "launcher"
    script.write_text(
        shortcut.launch_script(Path("/My Folder/syn apse"), "/py thon", ["--data-dir", "/a b/c"], "/usr/bin")
    )
    r = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "cd '/My Folder/syn apse'" in script.read_text()


def test_mac_shortcut_is_made_again_in_place(tmp_path):
    shortcut.make_shortcut(ROOT, [], python="py", platform="darwin", home=tmp_path)
    shortcut.make_shortcut(ROOT, ["--lan"], python="py", platform="darwin", home=tmp_path)
    script = (tmp_path / "Applications" / "Synapse.app" / "Contents" / "MacOS" / "synapse").read_text()
    assert "--quit-when-closed --lan" in script
    assert not (tmp_path / "Desktop").exists(), "no Desktop folder: no link made"


def test_mac_shortcut_leaves_another_synapse_app_alone(tmp_path):
    other = chrome_app(tmp_path, "", "Synapse", None)  # e.g. Safari's "Add to Dock"
    made = shortcut.make_shortcut(ROOT, [], python="py", platform="darwin", home=tmp_path)
    assert made[0].name == "Synapse Launcher.app"
    assert plistlib.loads((other / "Contents" / "Info.plist").read_bytes()) == {"CFBundleName": "Synapse"}
    shutil.rmtree(made[0])
    chrome_app(tmp_path, "", "Synapse Launcher", None)  # that name taken by something else too
    with pytest.raises(shortcut.ShortcutError):
        shortcut.make_shortcut(ROOT, [], python="py", platform="darwin", home=tmp_path)


def test_linux_shortcut(tmp_path):
    (tmp_path / "Desktop").mkdir()
    made = shortcut.make_shortcut(
        ROOT, ["--data-dir", "/home/me/My Library"], python="/usr/bin/python3", platform="linux", home=tmp_path
    )
    assert [p.parent.name for p in made] == ["applications", "Desktop"]
    text = made[0].read_text()
    assert (
        'Exec="/usr/bin/python3" "' in text
        and '"--window" "--quit-when-closed" "--data-dir" "/home/me/My Library"' in text
    )
    assert f"Icon={ROOT}/frontend/assets/icon-512.png" in text
    assert os.access(made[1], os.X_OK)


def test_no_shortcut_on_other_systems(tmp_path):
    with pytest.raises(shortcut.ShortcutError, match="--window --quit-when-closed"):
        shortcut.make_shortcut(ROOT, [], python="py", platform="win32", home=tmp_path)


# --------------------------------------------------------------- server.py


def test_https_lan_opens_the_certificate_friendly_local_name(monkeypatch):
    from backend.access import lan

    monkeypatch.setattr(lan, "local_hostname", lambda: "Lorenzos-MacBook.local")
    assert app_url("0.0.0.0", 8000, tls=True) == "https://Lorenzos-MacBook.local:8000/"
    assert app_url("::", 8443, tls=True) == "https://Lorenzos-MacBook.local:8443/"
    assert app_url("0.0.0.0", 8000, tls=False) == "http://127.0.0.1:8000/"
    assert app_url("::1", 8000, tls=True) == "https://[::1]:8000/"


def run_server(*args, home: Path, timeout=60):
    env = dict(os.environ, HOME=str(home), BROWSER="true")  # BROWSER: never open a real one
    env.pop("SYNAPSE_DATA_DIR", None)
    return subprocess.run(
        [sys.executable, "server.py", *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout
    )


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the Linux shortcut is checked here")
def test_make_shortcut_option_keeps_the_other_options(tmp_path):
    r = run_server("--make-shortcut", "--port", "5050", "--data-dir", str(tmp_path / "lib"), home=tmp_path)
    assert r.returncode == 0, r.stderr
    text = (tmp_path / ".local" / "share" / "applications" / "synapse.desktop").read_text()
    assert f'"--port" "5050" "--data-dir" "{tmp_path / "lib"}"' in text
    assert not (tmp_path / "lib").exists(), "making the shortcut starts nothing"


def test_window_option_with_synapse_running_only_opens_the_window(tmp_path):
    srv, url = serve(json.dumps({"name": "Synapse"}).encode())
    try:
        port = srv.server_address[1]
        r = run_server("--window", "--port", str(port), "--data-dir", str(tmp_path / "lib"), home=tmp_path)
    finally:
        srv.shutdown()
    assert r.returncode == 0, r.stderr
    assert "already running" in r.stdout
    assert not (tmp_path / "lib").exists()


def test_window_option_with_the_port_taken(tmp_path):
    srv, url = serve(b"<html>", status=200)
    try:
        r = run_server(
            "--window", "--port", str(srv.server_address[1]), "--data-dir", str(tmp_path / "lib"), home=tmp_path
        )
    finally:
        srv.shutdown()
    assert r.returncode != 0
    assert "used by another program" in r.stderr


# ------------------------------------------------------------- the Dock menu


def test_the_manifest_offers_a_new_window(client):
    """Right-clicking the installed app's icon (Dock, taskbar) offers "New Window" (manifest
    shortcuts); its address opens the library alone in a new window (windows.ts `readPlace`)."""
    m = client.get("/manifest.webmanifest").get_json(force=True)
    (sc,) = [s for s in m["shortcuts"] if s["name"] == "New Window"]
    assert sc["url"] == "/#window=new"
    assert all(i["src"].startswith("/assets/") for i in sc["icons"])


def test_the_manifest_is_installable(client):
    """Chromium needs standalone display and suitably sized PNG icons to offer installation."""
    m = client.get("/manifest.webmanifest").get_json(force=True)
    assert m["id"] == "/"
    assert m["start_url"] == "/"
    assert m["scope"] == "/"
    assert m["display"] == "standalone"
    assert {icon["sizes"] for icon in m["icons"]} >= {"192x192", "512x512"}
    assert all("any" in icon["purpose"] and "maskable" in icon["purpose"] for icon in m["icons"])
