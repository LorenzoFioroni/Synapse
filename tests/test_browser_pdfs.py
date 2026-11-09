"""Live rendezvous: unavailable browsers never leave work for a future connection."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.online.browser_pdfs import BrowserPdfs


def listening(broker, client="desktop", timeout=1):
    with broker._condition:
        assert broker._condition.wait_for(lambda: client in broker._listeners, timeout=timeout)


def test_offline_request_is_not_saved_for_a_browser_that_connects_later():
    broker = BrowserPdfs()
    assert not broker.request(["paper"])
    assert broker.poll("desktop", timeout=0) is None
    assert not broker._offers


@pytest.mark.parametrize("accepted", [True, False])
def test_live_request_requires_its_assigned_browsers_acknowledgement(accepted):
    broker = BrowserPdfs()
    with ThreadPoolExecutor(2) as pool:
        receiver = pool.submit(broker.poll, "desktop", timeout=1)
        listening(broker)
        request = pool.submit(broker.request, ["paper"], timeout=1)
        job = receiver.result(timeout=1)
        assert job["ids"] == ["paper"]
        assert broker.claim("desktop", job["id"]) > 0
        assert broker.claim("another-browser", job["id"]) == 0
        assert not broker.acknowledge("another-browser", job["id"], True)
        assert broker.acknowledge("desktop", job["id"], accepted)
        assert request.result(timeout=1) is accepted
        assert broker.claim("desktop", job["id"]) == 0
        assert not broker.acknowledge("desktop", job["id"], True)


def test_unacknowledged_request_expires_and_cannot_start_later():
    broker = BrowserPdfs()
    with ThreadPoolExecutor(2) as pool:
        receiver = pool.submit(broker.poll, "desktop", timeout=1)
        listening(broker)
        request = pool.submit(broker.request, ["paper"], timeout=1)
        job = receiver.result(timeout=1)
        with broker._condition:
            broker._offers[job["id"]].deadline = 0
            broker._condition.notify_all()
        assert not request.result(timeout=1)
        assert broker.claim("desktop", job["id"]) == 0
        assert not broker.acknowledge("desktop", job["id"], True)
        assert broker.poll("desktop", timeout=0) is None


def test_disconnect_removes_listener_and_cancels_its_unaccepted_offer():
    broker = BrowserPdfs()
    with ThreadPoolExecutor(2) as pool:
        receiver = pool.submit(broker.poll, "desktop", timeout=1)
        listening(broker)
        broker.leave("desktop")
        assert receiver.result(timeout=1) is None
        assert not broker.available
        receiver = pool.submit(broker.poll, "desktop", timeout=1)
        listening(broker)
        request = pool.submit(broker.request, ["paper"], timeout=1)
        receiver.result(timeout=1)
        broker.leave("desktop")
        assert not request.result(timeout=1)
        assert not broker._offers


def test_receiver_capacity_is_bounded_without_a_fast_poll_loop(client, svc, monkeypatch):
    monkeypatch.setattr("backend.online.browser_pdfs.MAX_BROWSERS", 1)
    broker = svc.browser_pdfs
    with ThreadPoolExecutor(1) as pool:
        receiver = pool.submit(broker.poll, "desktop", timeout=1)
        try:
            listening(broker)
            busy = client.get("/api/connector/browser-pdfs?client=desktop")
            assert busy.status_code == 400
            assert busy.get_json() == {"error": "This browser is already listening or accepting a request."}
            full = client.get("/api/connector/browser-pdfs?client=another")
            assert full.status_code == 400
            assert full.get_json() == {"error": "All browser receiver slots are occupied."}
        finally:
            broker.leave("desktop")
        assert receiver.result(timeout=1) is None


def test_poll_does_not_expose_unexpected_value_error(app, client, svc, monkeypatch, caplog):
    def fail(client_id):
        raise ValueError("Internal receiver details")

    monkeypatch.setitem(app.config, "PROPAGATE_EXCEPTIONS", False)
    monkeypatch.setattr(svc.browser_pdfs, "poll", fail)
    response = client.get("/api/connector/browser-pdfs?client=desktop")

    assert response.status_code == 500
    assert "Internal receiver details" not in response.get_data(as_text=True)
    assert "Internal receiver details" in caplog.text


def test_each_instance_has_its_own_receiver_and_busy_browsers_cannot_accumulate_jobs():
    broker = BrowserPdfs()
    with ThreadPoolExecutor(2) as pool:
        receiver = pool.submit(broker.poll, "desktop", timeout=1)
        listening(broker)
        assert not BrowserPdfs().request(["other-library"])
        request = pool.submit(broker.request, ["first"], timeout=1)
        job = receiver.result(timeout=1)
        assert not broker.request(["second"])
        broker.acknowledge("desktop", job["id"], True)
        assert request.result(timeout=1)


def test_app_to_connector_handoff_preserves_selection_and_filters_ineligible_items(app, client):
    publisher = client.post("/api/items", json={"title": "Publisher", "doi": "10.1234/paper"}).get_json()["id"]
    arxiv = client.post("/api/items", json={"title": "arXiv", "doi": "10.48550/arXiv.2401.12345"}).get_json()["id"]
    broker = app.extensions["synapse"].browser_pdfs

    def poll():
        with app.test_client() as browser:
            response = browser.get("/api/connector/browser-pdfs?client=desktop")
            assert response.headers["Cache-Control"] == "no-store"
            return response.get_json()["job"]

    def request():
        with app.test_client() as phone:
            return phone.post("/api/items/find-publisher-pdfs", json={"ids": [publisher, arxiv]}).get_json()

    with ThreadPoolExecutor(2) as pool:
        receiver = pool.submit(poll)
        listening(broker)
        requester = pool.submit(request)
        job = receiver.result(timeout=2)
        assert job["ids"] == [publisher]
        assert (
            client.post("/api/connector/browser-pdfs/claim", json={"client": "desktop", "id": job["id"]}).get_json()[
                "remaining"
            ]
            > 0
        )
        assert client.post(
            "/api/connector/browser-pdfs/ack", json={"client": "desktop", "id": job["id"], "accepted": True}
        ).get_json()["active"]
        assert requester.result(timeout=2) == {"accepted": True}


@pytest.mark.parametrize("ids", [None, [], [1], ["a,b"], ["x"] * 201, ["/arbitrary/url"]])
def test_invalid_app_requests_are_refused(client, ids):
    assert client.post("/api/items/find-publisher-pdfs", json={"ids": ids}).status_code == 400


def test_non_object_app_request_is_refused(client):
    assert client.post("/api/items/find-publisher-pdfs", json=["paper"]).status_code == 400


def test_unavailable_browser_falls_back_without_changing_library(client, svc):
    paper = svc.papers.create({"title": "Publisher", "doi": "10.1234/paper"})
    assert client.post("/api/items/find-publisher-pdfs", json={"ids": [paper["id"]]}).get_json() == {"accepted": False}
    assert svc.papers.get(paper["id"])["attachments"] == []
    assert not svc.browser_pdfs._offers


def test_handoff_endpoints_preserve_origin_and_scope_boundaries(client):
    assert (
        client.post(
            "/api/items/find-publisher-pdfs", json={"ids": ["paper"]}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/items/find-publisher-pdfs", json={"ids": ["paper"]}, headers={"Origin": "chrome-extension://test"}
        ).status_code
        == 403
    )
    for path in ("claim", "ack"):
        assert client.post(f"/api/connector/browser-pdfs/{path}", json=[]).status_code == 400
        assert client.post(f"/api/connector/browser-pdfs/{path}", json={"id": [], "client": {}}).status_code == 400
    invalid = client.get("/api/connector/browser-pdfs?client=/invalid")
    assert invalid.status_code == 400
    assert invalid.get_json() == {"error": "Invalid browser client ID."}


def test_remote_connector_tokens_can_receive_but_cannot_dispatch(client, svc):
    _, token = svc.tokens.create("Desktop browser", scope="connector")
    remote = {"REMOTE_ADDR": "198.51.100.8"}
    headers = {"Authorization": f"Bearer {token}"}
    assert client.post(
        "/api/connector/browser-pdfs/claim",
        json={"client": "desktop", "id": "unknown"},
        headers=headers,
        environ_base=remote,
    ).get_json() == {"remaining": 0}
    assert (
        client.post(
            "/api/items/find-publisher-pdfs",
            json={"ids": ["paper"]},
            headers={**headers, "Origin": "http://localhost"},
            environ_base=remote,
        ).status_code
        == 401
    )
    _, ai_token = svc.tokens.create("AI app", scope="mcp")
    assert (
        client.post(
            "/api/connector/browser-pdfs/claim",
            json={"client": "desktop", "id": "unknown"},
            headers={"Authorization": f"Bearer {ai_token}"},
            environ_base=remote,
        ).status_code
        == 401
    )
