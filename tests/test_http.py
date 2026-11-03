from __future__ import annotations

import httpx
import pytest

from scrapers import http as http_util
from scrapers.errors import BlockedError


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://test")


def test_returns_body_on_first_success():
    def handler(request):
        return httpx.Response(200, text="ok")

    with _client(handler) as client:
        assert http_util.get_with_retry(client, "https://test/x").text == "ok"


def test_retries_on_500_then_succeeds():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(500) if len(calls) < 3 else httpx.Response(200, text="ok")

    with _client(handler) as client:
        resp = http_util.get_with_retry(client, "https://test/x", sleep=lambda s: None)
    assert resp.text == "ok"
    assert len(calls) == 3


def test_retries_on_transport_error():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 2:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, text="ok")

    with _client(handler) as client:
        resp = http_util.get_with_retry(client, "https://test/x", sleep=lambda s: None)
    assert resp.text == "ok"


def test_gives_up_after_attempts():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503)

    with _client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError):
            http_util.get_with_retry(client, "https://test/x", attempts=3, sleep=lambda s: None)
    assert len(calls) == 3


def test_403_fails_fast_without_retrying():
    """A block is a real answer — burning three attempts on it wastes time.

    It also arrives typed, so the health report can name the cause instead of
    blaming the page markup for a response that never carried a page.
    """
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(403)

    with _client(handler) as client:
        with pytest.raises(BlockedError) as excinfo:
            http_util.get_with_retry(client, "https://test/x", sleep=lambda s: None)
    assert len(calls) == 1
    assert excinfo.value.status_code == 403


def test_honours_numeric_retry_after():
    slept = []
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, text="ok")

    with _client(handler) as client:
        http_util.get_with_retry(client, "https://test/x", sleep=slept.append)
    assert slept == [7.0]


def test_snapshot_writes_body(tmp_path):
    path = http_util.snapshot("mostaql", "<html>changed</html>", tmp_path)
    assert path is not None and path.exists()
    assert "changed" in path.read_text(encoding="utf-8")
    assert path.name.startswith("mostaql_empty_")


def test_snapshot_never_raises(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("i am a file, not a directory", encoding="utf-8")
    assert http_util.snapshot("x", "body", blocker) is None
