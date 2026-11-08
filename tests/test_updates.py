"""Updates of Synapse itself (backend/online/updates.py, backend/api/updates.py): the GitHub check,
installing through the updater, and who may start that."""

import http.server
import json
import threading

import pytest

from backend import create_app
from backend.config import Config
from backend.online import updates as U
from backend.online.updates import Source, UpdateError, Updates, parse_source
from tests.test_public_url import PUBLIC_URL, local, tunnel

SOURCE = "https://github.com/owner/Synapse.git#main"
BUILT = "a" * 40
NEW = ["b" * 40, "c" * 40, "d" * 40]  # oldest first, as GitHub lists them


def gh_commit(sha, title, day):
    return {
        "sha": sha,
        "html_url": f"https://github.com/owner/Synapse/commit/{sha}",
        "commit": {"message": f"{title}\n\nMore words.", "committer": {"date": f"2026-11-0{day}T10:00:00Z"}},
    }


class FakeGitHub:
    def __init__(self, status="ahead", commits=NEW):
        self.status, self.commits, self.asked, self.fail = status, list(commits), [], None

    def __call__(self, url):
        self.asked.append(url)
        if self.fail:
            raise UpdateError(self.fail)
        if "/compare/" in url:
            return {
                "status": self.status,
                "ahead_by": len(self.commits),
                "html_url": "https://github.com/owner/Synapse/compare/x...main",
                "commits": [gh_commit(sha, f"Change {i}", i + 1) for i, sha in enumerate(self.commits)],
            }
        return gh_commit(NEW[-1], "Newest", 5)


def updates(github=None, **kw):
    kw.setdefault("before_install", lambda: None)
    return Updates(kw.pop("source", SOURCE), kw.pop("commit", BUILT), None, None, github=github or FakeGitHub(), **kw)


# ------------------------------------------------------------------ where the code comes from


@pytest.mark.parametrize(
    "text,source",
    [
        ("https://github.com/owner/Synapse.git#main", Source("owner", "Synapse", "main")),
        ("https://github.com/owner/Synapse", Source("owner", "Synapse", "main")),
        ("https://github.com/o-1/s.y_n#feature/x", Source("o-1", "s.y_n", "feature/x")),
    ],
)
def test_github_sources(text, source):
    assert parse_source(text) == source


@pytest.mark.parametrize(
    "text", ["https://gitlab.com/o/S.git#main", "git@github.com:o/S.git", ".", "https://github.com/o"]
)
def test_other_sources_are_reported(text):
    with pytest.raises(UpdateError, match="GitHub"):
        parse_source(text)
    st = updates(source=text).status()
    assert not st["available"] and "GitHub" in st["error"]


def test_no_source_means_no_checks():
    u = updates(source=None)
    st = u.status(refresh=True)
    assert not st["available"] and st["source"] is None and st["behind"] is None


# ------------------------------------------------------------------ the check


def test_new_commits_on_the_branch():
    gh = FakeGitHub()
    st = updates(gh).status(refresh=True)
    assert gh.asked == [f"https://api.github.com/repos/owner/Synapse/compare/{BUILT}...main"]
    assert st["available"] and st["source"] == {
        "repo": "owner/Synapse",
        "branch": "main",
        "url": "https://github.com/owner/Synapse/tree/main",
    }
    assert st["current"] == {"commit": BUILT, "short": "aaaaaaa"}
    assert st["behind"] == 3 and st["error"] is None and st["checked"]
    assert [c["commit"] for c in st["commits"]] == list(reversed(NEW))  # newest first
    assert st["latest"]["title"] == "Change 2" and st["latest"]["date"] == "2026-11-03T10:00:00Z"
    assert not st["can_install"] and st["install"] is None


def test_up_to_date_and_diverged():
    st = updates(FakeGitHub(status="identical", commits=[])).status(refresh=True)
    assert st["behind"] == 0 and st["commits"] == [] and st["latest"]["commit"] == BUILT
    st = updates(FakeGitHub(status="diverged", commits=NEW[:1])).status(refresh=True)
    assert st["behind"] == 1 and st["diverged"]


def test_only_the_newest_commits_are_listed():
    many = [f"{i:040x}" for i in range(30)]
    st = updates(FakeGitHub(commits=many)).status(refresh=True)
    assert st["behind"] == 30 and len(st["commits"]) == U.MAX_COMMITS and st["commits"][0]["commit"] == many[-1]


def test_unknown_build_asks_for_the_newest_commit(tmp_path):
    gh = FakeGitHub()
    st = updates(gh, commit=None, app_root=tmp_path).status(refresh=True)  # no .git there either
    assert gh.asked == ["https://api.github.com/repos/owner/Synapse/commits/main"]
    assert st["current"] is None and st["behind"] is None and st["latest"]["title"] == "Newest"


def test_a_failed_check_keeps_the_last_answer():
    gh = FakeGitHub()
    u = updates(gh)
    u.check()
    gh.fail = "GitHub could not be reached (ConnectionError)."
    st = u.check()
    assert st["error"] == gh.fail and st["behind"] == 3


def test_checking_again_moments_later_answers_the_last_result():
    gh = FakeGitHub()
    u = updates(gh)
    u.status(refresh=True)
    u.status(refresh=True)
    assert len(gh.asked) == 1


def test_github_answers_become_readable_errors(monkeypatch):
    class Answer:
        def __init__(self, status, body=b"{}"):
            self.status_code, self.body = status, body

        def json(self):
            return json.loads(self.body)

        def close(self):
            pass

    for status, message in ((403, "rate limit"), (404, "does not know"), (500, "answered 500")):
        monkeypatch.setattr(U.online_http, "public_request", lambda *a, s=status, **kw: Answer(s))
        with pytest.raises(UpdateError, match=message):
            U._github_json("https://api.github.com/repos/o/r/commits/main")


# ------------------------------------------------------------------ installing


class FakeUpdater:
    """The updater's HTTP API (docker/updater/updater.py) on a free port of 127.0.0.1."""

    def __init__(self, state="idle", answer=(202, {"state": "running", "commit": NEW[-1], "message": "Building"})):
        self.state, self.answer, self.requests = state, answer, []
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, data):
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                fake.requests.append(("GET", self.path, self.headers.get("Authorization")))
                self._send(200, {"state": fake.state, "message": ""})

            def do_POST(self):
                fake.requests.append(("POST", self.path, self.headers.get("Authorization")))
                self._send(*fake.answer)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def test_install_backs_up_then_asks_the_updater():
    done = []
    with FakeUpdater() as fake:
        u = Updates(
            SOURCE, BUILT, fake.url, "t" * 32, before_install=lambda: done.append("backup"), github=FakeGitHub()
        )
        assert u.install()["state"] == "running"
        assert u.status()["install"]["state"] == "idle" and u.status()["can_install"]
    assert done == ["backup"]
    assert [(m, p) for m, p, _ in fake.requests][:2] == [("GET", "/status"), ("POST", "/update")]
    assert {auth for _, _, auth in fake.requests} == {"Bearer " + "t" * 32}


def test_no_install_without_an_updater_or_while_one_runs():
    with pytest.raises(UpdateError, match="no updater"):
        updates().install()
    with pytest.raises(UpdateError, match="token"):
        Updates(SOURCE, BUILT, "http://127.0.0.1:9", None, before_install=lambda: None).install()
    with FakeUpdater(state="running") as fake:
        u = Updates(SOURCE, BUILT, fake.url, "t" * 32, before_install=lambda: pytest.fail("no backup"))
        with pytest.raises(UpdateError, match="already"):
            u.install()
    assert [m for m, _, _ in fake.requests] == ["GET"]


def test_a_failed_backup_installs_nothing():
    def broken():
        raise OSError("disk full")

    with FakeUpdater() as fake:
        u = Updates(SOURCE, BUILT, fake.url, "t" * 32, before_install=broken)
        with pytest.raises(UpdateError, match="disk full"):
            u.install()
    assert [m for m, _, _ in fake.requests] == ["GET"]


def test_an_unreachable_updater_is_shown():
    u = Updates(SOURCE, BUILT, "http://127.0.0.1:9", "t" * 32, before_install=lambda: None, github=FakeGitHub())
    st = u.status()
    assert (
        st["can_install"] and st["install"]["state"] == "unknown" and "could not be reached" in st["install"]["message"]
    )


# ------------------------------------------------------------------ the HTTP API


@pytest.fixture
def server(tmp_path):
    """A server behind a tunnel, with an updater, as in Docker (docs/updates.md)."""
    fake = FakeUpdater().__enter__()
    cfg = Config.from_env(tmp_path / "data")
    cfg.public_url = PUBLIC_URL
    cfg.update_source, cfg.commit, cfg.updater_url, cfg.updater_token = SOURCE, BUILT, fake.url, "t" * 32
    app = create_app(cfg, start_indexer=False)
    app.extensions["synapse"].updates._github = FakeGitHub()
    yield app, fake
    fake.__exit__()


def signed_in_device(app):
    c = app.test_client()
    code = local(c, "POST", "/api/devices/pairing").get_json()["links"][0]["url"].split("code=")[1]
    phone = app.test_client()
    assert tunnel(phone, "POST", "/api/devices/pair", json={"code": code, "name": "iPhone"}).status_code == 201
    return phone


def test_settings_show_the_update_status(server):
    app, _ = server
    phone = signed_in_device(app)
    st = tunnel(phone, "GET", "/api/updates?refresh=1").get_json()
    assert st["behind"] == 3 and st["can_install"]
    assert tunnel(app.test_client(), "GET", "/api/updates").status_code == 401  # not signed in


def test_installing_from_a_device_needs_a_passkey_confirmation(server):
    app, fake = server
    phone = signed_in_device(app)
    r = tunnel(phone, "POST", "/api/updates/install")
    assert r.status_code == 403 and "computer running" in r.get_json()["error"]
    assert not [m for m, _, _ in fake.requests if m == "POST"]


def test_installing_on_this_computer(server):
    app, fake = server
    r = local(app.test_client(), "POST", "/api/updates/install")
    assert r.status_code == 202, r.get_json()
    assert r.get_json()["started"]["state"] == "running"
    assert ("POST", "/update", "Bearer " + "t" * 32) in fake.requests
    backups = app.extensions["synapse"].backups.list()
    assert backups and backups[0]["reason"] == "before updating Synapse"


def test_an_install_the_updater_refuses_is_a_conflict(server):
    app, fake = server
    fake.answer = (409, {"error": "An update is being installed already."})
    r = local(app.test_client(), "POST", "/api/updates/install")
    assert r.status_code == 409 and "already" in r.get_json()["error"]


def test_the_environment_configures_updates(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNAPSE_SOURCE", SOURCE)
    monkeypatch.setenv("SYNAPSE_COMMIT", BUILT)
    monkeypatch.setenv("SYNAPSE_UPDATER", "http://updater:9000")
    monkeypatch.setenv("SYNAPSE_UPDATER_TOKEN", "secret-token-value")
    cfg = Config.from_env(tmp_path)
    assert (cfg.update_source, cfg.commit, cfg.updater_url, cfg.updater_token) == (
        SOURCE,
        BUILT,
        "http://updater:9000",
        "secret-token-value",
    )


def test_an_unusable_source_is_named_without_its_credentials():
    from backend.online.updates import UpdateError, parse_source

    with pytest.raises(UpdateError) as refused:
        parse_source("https://owner:ghp_secret@github.com/owner/Synapse.git#main")
    assert "ghp_secret" not in str(refused.value)
