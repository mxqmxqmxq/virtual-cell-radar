import httpx
import pytest

from aivc.fetchers.base import FetchError, build_client, get_with_retry


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_retry_then_success():
    codes = iter([429, 503, 200])
    sleeps = []

    def h(req):
        return httpx.Response(next(codes), text="ok")

    r = get_with_retry(client(h), "http://x/", sleep=sleeps.append)
    assert r.text == "ok" and sleeps == [2.0, 4.0]


def test_exhausted():
    n = []

    def h(req):
        n.append(1)
        return httpx.Response(500)

    with pytest.raises(FetchError):
        get_with_retry(client(h), "http://x/", max_retries=2, sleep=lambda s: None)
    assert len(n) == 3


def test_transport_error_retried():
    calls = []

    def h(req):
        calls.append(1)
        if len(calls) < 2:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, text="ok")

    assert get_with_retry(client(h), "http://x/", sleep=lambda s: None).text == "ok"


def test_404_not_retried():
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(404)

    with pytest.raises(FetchError):
        get_with_retry(client(h), "http://x/", sleep=lambda s: None)
    assert len(calls) == 1


def test_build_client():
    from aivc.config import HttpConfig

    c = build_client(HttpConfig())
    assert c.headers["user-agent"] == "virtual-cell-radar/0.1 (+https://github.com/mxqmxqmxq/virtual-cell-radar)" and c.follow_redirects
