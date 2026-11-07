"""The updater container (docker/updater/updater.py, docs/updates.md), against a stand-in for Docker,
Compose and Git: what it builds, when it goes back to the previous image, and its HTTP API."""

import http.client
import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REPO = "https://github.com/owner/Synapse.git"
OLD, NEW = "a" * 40, "b" * 40
TOKEN = "t" * 32


@pytest.fixture(scope="module")
def U():
    spec = importlib.util.spec_from_file_location("synapse_updater", ROOT / "docker" / "updater" / "updater.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up there
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


class FakeDocker:
    """Answers the commands the updater runs, and records them with the environment they got."""

    def __init__(self, project: Path, *, branch_tip=NEW, running=OLD, build_ok=True, new_healthy=True, where=None):
        self.project, self.tip, self.running, self.build_ok = project, branch_tip, running, build_ok
        self.new_healthy, self.rolled_back = new_healthy, False
        self.where = where or str(project)
        self.image_exists = True
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, cmd, cwd, env):
        self.calls.append((cmd, env))
        assert cwd == self.project
        if cmd[:2] == ["git", "ls-remote"]:
            return 0, f"{self.tip}\trefs/heads/main\n"
        if cmd[:2] == ["docker", "compose"]:
            verb = cmd[4]
            if verb == "ps":
                return 0, "cid1\n"
            if verb == "build":
                return (0, "built") if self.build_ok else (1, "error: failed to solve")
            return 0, ""
        if cmd == ["docker", "image", "tag", "synapse:local-previous", "synapse:local"]:
            self.rolled_back = True
        if cmd[:3] == ["docker", "image", "inspect"]:
            return (0, "sha256:x\n") if self.image_exists else (1, "No such image")
        if cmd[:2] == ["docker", "inspect"]:
            fmt = cmd[3]
            if "working_dir" in fmt:
                return 0, self.where + "\n"
            if ".Config.Env" in fmt:
                # As a Docker that ignored the format would answer: the whole environment.
                return 0, f"PATH=/usr/bin\nSYNAPSE_UPDATER_TOKEN={TOKEN}\nSYNAPSE_COMMIT={self.running}\n"
            if "Health" in fmt:
                return 0, ("healthy" if self.new_healthy or self.rolled_back else "unhealthy") + "\n"
        return 0, ""

    def commands(self):
        return [" ".join(c) for c, _ in self.calls]


@pytest.fixture
def make(U, tmp_path):
    def build(**kw):
        settings = U.Settings(
            token=TOKEN,
            repo=REPO,
            branch="main",
            project_dir=tmp_path,
            services=["synapse"],
            image="synapse:local",
            health_timeout=kw.pop("health_timeout", 30),
        )
        docker = FakeDocker(tmp_path, **kw)
        updater = U.Updater(settings, run=docker, sleep=lambda s: None, spawn=lambda fn: fn())
        return updater, docker

    return build


def test_it_builds_exactly_the_branch_tip_and_restarts(make):
    updater, docker = make()
    status, answer = updater.start()
    assert status == 202 and answer["commit"] == NEW
    assert updater.state["state"] == "done" and NEW[:7] in updater.state["message"]
    builds = [env for cmd, env in docker.calls if cmd[4:5] == ["build"]]
    assert builds == [{"SYNAPSE_BUILD_SOURCE": f"{REPO}#{NEW}", "SYNAPSE_COMMIT": NEW}]
    cmds = docker.commands()
    assert "docker image tag synapse:local synapse:local-previous" in cmds
    assert any(c.endswith("up -d --no-deps --no-build synapse") for c in cmds)
    assert cmds.index("docker image tag synapse:local synapse:local-previous") < next(
        i for i, c in enumerate(cmds) if " build " in c
    )


def test_the_log_never_shows_the_containers_environment(make, U):
    for running in (OLD, NEW):  # an update, and nothing to do
        updater, docker = make(running=running)
        updater.start()
        log = "\n".join(updater.public()["log"])
        assert TOKEN not in log and "SYNAPSE_UPDATER_TOKEN" not in log
        asked = [c for c, _ in docker.calls if ".Config.Env" in " ".join(c)]
        assert asked and all(c[3] == U.COMMIT_FORMAT for c in asked)


def test_nothing_happens_when_it_runs_the_tip_already(make):
    updater, docker = make(running=NEW)
    status, answer = updater.start()
    assert status == 200 and answer["state"] == "current"
    assert not any(" build " in c or " up " in c for c in docker.commands())


def test_a_failed_build_changes_nothing(make):
    updater, docker = make(build_ok=False)
    updater.start()
    assert updater.state["state"] == "failed" and "nothing was changed" in updater.state["message"]
    assert not any(" up " in c for c in docker.commands())
    assert "failed to solve" in "\n".join(updater.state["log"])


def test_an_unhealthy_new_version_goes_back_to_the_previous_image(make):
    updater, docker = make(new_healthy=False, health_timeout=0)
    updater.start()
    cmds = docker.commands()
    assert "docker image tag synapse:local-previous synapse:local" in cmds
    assert sum(c.endswith("up -d --no-deps --no-build synapse") for c in cmds) == 2
    assert updater.state["state"] == "failed" and "previous version runs again" in updater.state["message"]


def test_it_refuses_another_project_folder(make, tmp_path):
    updater, docker = make(where="/somewhere/else")
    status, answer = updater.start()
    assert status == 502 and "/somewhere/else" in answer["error"]
    assert not any(" build " in c for c in docker.commands())


def test_one_update_at_a_time(make):
    updater, _ = make()
    updater.state["state"] = "running"
    status, answer = updater.start()
    assert status == 409 and "already" in answer["error"]


@pytest.mark.parametrize(
    "change,message",
    [
        ({"UPDATER_TOKEN": "short"}, "UPDATER_TOKEN"),
        ({"SYNAPSE_SOURCE": "not a url"}, "SYNAPSE_SOURCE"),
        ({"UPDATER_PROJECT_DIR": "relative/path"}, "UPDATER_PROJECT_DIR"),
    ],
)
def test_settings_are_checked(U, tmp_path, change, message):
    env = {"UPDATER_TOKEN": TOKEN, "SYNAPSE_SOURCE": f"{REPO}#main", "UPDATER_PROJECT_DIR": str(tmp_path), **change}
    with pytest.raises(U.UpdaterError, match=message):
        U.Settings.from_env(env)


def test_settings_from_the_environment(U, tmp_path):
    s = U.Settings.from_env(
        {"UPDATER_TOKEN": TOKEN, "SYNAPSE_SOURCE": f"{REPO}#stable", "UPDATER_PROJECT_DIR": str(tmp_path)}
    )
    assert (s.repo, s.branch, s.services, s.image) == (REPO, "stable", ["synapse"], "synapse:local")


def test_the_http_api_needs_the_token(U, make):
    updater, _ = make(running=NEW)
    server = U.ThreadingHTTPServer(("127.0.0.1", 0), U.handler(updater))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    def ask(method, path, token=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request(method, path, headers={"Authorization": f"Bearer {token}"} if token else {})
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")

    try:
        assert ask("GET", "/health")[0] == 200
        assert ask("GET", "/status")[0] == 401
        assert ask("GET", "/status", "wrong")[0] == 401
        assert ask("POST", "/update")[0] == 401
        status, body = ask("GET", "/status", TOKEN)
        assert status == 200 and body["state"] == "idle" and body["branch"] == "main"
        status, body = ask("POST", "/update", TOKEN)
        assert status == 200 and body["state"] == "current"
    finally:
        server.shutdown()
        server.server_close()
