"""Stopping a provider cancels queued enrichment without waiting for its in-flight work."""

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from backend.plugins import PluginError, Response
from backend.plugins.loading import load_module


def test_stop_cancels_queued_author_enrichment():
    package, _ = load_module(
        "metadata_lifecycle", Path(__file__).resolve().parents[2] / "plugins/metadata-sources/server"
    )
    entered = threading.Barrier(5, timeout=2)
    release = threading.Event()
    crossref_started = threading.Event()
    urls = []

    class Http:
        def get(self, url, **kwargs):
            urls.append(url)
            crossref_started.set()
            return Response(200, url, {}, b'{"message":{"title":["Kept"],"DOI":"10.1234/queued"}}')

    provider = package.providers.Providers(Http())

    def occupy_worker():
        entered.wait()
        assert release.wait(3)

    occupied = [provider._pool.submit(occupy_worker) for _ in range(4)]
    try:
        entered.wait()
        with ThreadPoolExecutor(max_workers=1) as caller:
            future = caller.submit(provider.resolve, {"kind": "doi", "value": "10.1234/queued"})
            try:
                assert crossref_started.wait(2)
                provider.close()
                with pytest.raises(PluginError, match="stopped"):
                    future.result(timeout=2)
                assert len(urls) == 1 and "crossref" in urls[0]
            finally:
                release.set()
    finally:
        release.set()
        provider.close()
        provider._pool.shutdown(wait=True)
        for future in occupied:
            future.result(timeout=2)


def test_stop_discards_an_inflight_lookup_and_restart_has_fresh_state():
    package, _ = load_module(
        "metadata_restart", Path(__file__).resolve().parents[2] / "plugins/metadata-sources/server"
    )
    entered, release = threading.Event(), threading.Event()
    urls = []

    class Http:
        def get(self, url, **kwargs):
            urls.append(url)
            if len(urls) == 1:
                entered.set()
                assert release.wait(3)
            return Response(200, url, {}, b'{"ISBN:9780262033848":{"title":"A book"}}')

    old = package.providers.Providers(Http())
    query = {"kind": "isbn", "value": "9780262033848"}
    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(old.resolve, query)
        new = None
        try:
            assert entered.wait(2)
            old.close()
            new = package.providers.Providers(old.http)
            assert new.resolve(query)["title"] == "A book"
            release.set()
            with pytest.raises(PluginError, match="stopped"):
                future.result(timeout=2)
        finally:
            release.set()
            old.close()
            if new is not None:
                new.close()
