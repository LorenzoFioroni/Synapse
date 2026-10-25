"""Keep the authoring guide's runnable example in step with the public API."""

import shutil
from pathlib import Path


def test_hello_library_example(client, svc):
    example = Path(__file__).resolve().parents[2] / "docs/examples/hello-library"
    shutil.copytree(example, svc.config.plugins_dir / "hello-library", ignore=shutil.ignore_patterns("__pycache__"))
    enabled = client.post("/api/plugin-host/hello-library/enable")
    assert enabled.status_code == 200
    assert enabled.get_json()["plugin"]["status"] == "on"
    route = "/api/plugins/hello-library/message"
    assert client.get(route).get_json() == {"message": "Hello from your plugin."}
    changed = client.put("/api/plugin-host/hello-library/settings", json={"message": "A new greeting"})
    assert changed.status_code == 200
    assert client.get(route).get_json() == {"message": "A new greeting"}
    client.post("/api/plugin-host/hello-library/disable")
    assert client.get(route).status_code == 404
    client.post("/api/plugin-host/hello-library/enable")
    assert client.get(route).get_json() == {"message": "A new greeting"}
