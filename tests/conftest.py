import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import create_app  # noqa: E402
from backend.config import Config  # noqa: E402


@pytest.fixture(autouse=True)
def _local_network(monkeypatch):
    from backend.access import lan

    # Access-control tests simulate remote devices, not the runner's network or Bonjour DNS.
    monkeypatch.setattr(lan, "own_addresses", lambda: [])
    monkeypatch.setattr(lan, "local_hostname", lambda: "synapse-test.local")


@pytest.fixture(autouse=True)
def _stop_plugin_hosts(monkeypatch):
    from backend.access.authdb import AuthTable
    from backend.plugins import PluginHost

    hosts = []
    auth_tables = []
    initialize = PluginHost.__init__
    initialize_auth = AuthTable.__init__

    def tracked(host, *args, **kwargs):
        initialize(host, *args, **kwargs)
        hosts.append(host)

    def tracked_auth(table, *args, **kwargs):
        initialize_auth(table, *args, **kwargs)
        auth_tables.append(table)

    # Include hosts made by module-specific fixtures and by tests that build their own apps.
    monkeypatch.setattr(PluginHost, "__init__", tracked)
    monkeypatch.setattr(AuthTable, "__init__", tracked_auth)
    yield
    for host in reversed(hosts):
        host.stop()
    for table in auth_tables:
        table.close()


@pytest.fixture(autouse=True)
def _no_arxiv_spacing(monkeypatch):
    from backend.online import http

    monkeypatch.setattr(http, "ARXIV_SPACING", 0)


@pytest.fixture
def app(tmp_path):
    app = create_app(Config.from_env(tmp_path / "data"), start_indexer=False)
    app.config["TESTING"] = True
    # no background reference fetching unless a test turns it on (it would outlive the test)
    app.extensions["synapse"].settings.update(
        {"references_auto": False, "add_fetch_references": "never", "add_find_pdf": "never"}
    )
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def svc(app):
    return app.extensions["synapse"]


def make_pdf(path: Path, text: str) -> Path:
    """Write a tiny one-page PDF containing ``text`` (no external deps)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
    return path


@pytest.fixture(scope="session")
def built_frontend():
    """frontend/js, built from frontend/src if needed (the JavaScript tests run the built code)."""
    import shutil

    from backend.frontend import BuildError, ensure_built

    if not shutil.which("node"):
        pytest.skip("Node.js not installed")
    frontend = Path(__file__).resolve().parent.parent / "frontend"
    try:
        ensure_built(frontend)
    except BuildError as e:
        pytest.skip(f"frontend not built: {e}")
    return frontend / "js"
