"""Offline responses at plugin.http's HTTP transport boundary, shared by provider tests."""

import json
from urllib.parse import parse_qsl, urlsplit

from backend.plugins import http


class Response:
    def __init__(self, data=None, status=200, *, content=None):
        self.status_code = status
        self.headers = {"Content-Type": "application/json"}
        self.encoding, self.reason = "utf-8", ""
        self.content = json.dumps(data).encode() if content is None else content

    def iter_content(self, size):
        return [self.content]

    def close(self):
        pass


def install_transport(monkeypatch, get):
    """Keep permissions, credential injection, parsing and spacing real; only HTTP/DNS are faked."""

    def request(method, url, **kwargs):
        assert method == "GET"
        parts = urlsplit(url)
        params = dict(parse_qsl(parts.query)) or None
        return get(parts._replace(query="").geturl(), params=params, **kwargs)

    monkeypatch.setattr(http.online_http, "public_request", request)
    monkeypatch.setattr(http, "DEFAULT_GAP", 0.0)
