import json
from pathlib import Path

import httpx
import pytest

from aivc.fetchers.github import GitHubClient, GitHubError, RateLimited

FIX = Path(__file__).parent / "fixtures" / "github"


def fx(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def make_client(handler, token=None, **kw) -> GitHubClient:
    c = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    return GitHubClient(token=token, client=c, **kw)


def test_get_repo_ok_fields_and_headers(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["headers"] = req.headers
        return httpx.Response(200, json=fx("repo_ok.json"),
                              headers={"X-RateLimit-Remaining": "58", "X-RateLimit-Reset": "1790000000"})

    c = make_client(handler)
    info = c.get_repo("example-org/demo-repo")
    assert seen["url"] == "https://api.github.com/repos/example-org/demo-repo"
    assert seen["headers"]["accept"] == "application/vnd.github+json"
    assert seen["headers"]["user-agent"].startswith("virtual-cell-radar")
    assert "authorization" not in seen["headers"]
    assert (info.status, info.full_name, info.stars, info.archived) == ("ok", "example-org/demo-repo", 1234, False)
    assert info.pushed_at == "2026-10-20T08:30:00Z" and info.license == "MIT" and info.language == "Python"
    assert info.html_url == "https://github.com/example-org/demo-repo"
    assert c.remaining == 58 and not c.exhausted and c.requests == 1
    assert c.reset_at is not None


def test_token_adds_bearer_header_and_env_is_read(monkeypatch):
    seen = []

    def handler(req):
        seen.append(req.headers.get("authorization"))
        return httpx.Response(200, json=fx("repo_ok.json"))

    make_client(handler, token="tkn").get_repo("a/b")
    monkeypatch.setenv("GITHUB_TOKEN", "from-env")
    make_client(handler).get_repo("a/b")
    assert seen == ["Bearer tkn", "Bearer from-env"]


def test_404_is_not_found():
    c = make_client(lambda req: httpx.Response(404, json=fx("not_found.json")))
    info = c.get_repo("a/gone")
    assert info.status == "not_found" and info.stars is None


def test_rename_redirect_returns_new_full_name():
    def handler(req: httpx.Request):
        if req.url.path == "/repos/example-org/old-name":
            return httpx.Response(301, headers={"Location": "https://api.github.com/repositories/1002"})
        assert req.url.path == "/repositories/1002"
        return httpx.Response(200, json=fx("repo_renamed.json"))

    info = make_client(handler).get_repo("example-org/old-name")
    assert info.status == "ok" and info.full_name == "example-org/new-name"
    assert info.archived is True and info.license is None and info.language is None


def test_rate_limit_403_with_remaining_zero():
    def handler(req):
        return httpx.Response(403, json=fx("rate_limited.json"),
                              headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1790000000"})

    c = make_client(handler)
    with pytest.raises(RateLimited) as ei:
        c.get_repo("a/b")
    assert ei.value.reset_at is not None and ei.value.reset_at.timestamp() == 1790000000
    assert c.exhausted


def test_rate_limit_429_and_retry_after():
    c = make_client(lambda req: httpx.Response(429, headers={"Retry-After": "60"}))
    with pytest.raises(RateLimited) as ei:
        c.get_repo("a/b")
    assert ei.value.reset_at is not None


def test_plain_403_is_not_rate_limit():
    c = make_client(lambda req: httpx.Response(403, json={"message": "Repository access blocked"},
                                               headers={"X-RateLimit-Remaining": "40"}))
    with pytest.raises(GitHubError) as ei:
        c.get_repo("a/b")
    assert not isinstance(ei.value, RateLimited)


def test_remaining_zero_after_success_marks_exhausted():
    c = make_client(lambda req: httpx.Response(200, json=fx("repo_ok.json"),
                                               headers={"X-RateLimit-Remaining": "0"}))
    c.get_repo("a/b")
    assert c.exhausted


def test_server_error_and_transport_error_and_bad_json():
    with pytest.raises(GitHubError):
        make_client(lambda req: httpx.Response(500)).get_repo("a/b")

    def boom(req):
        raise httpx.ConnectError("down")

    with pytest.raises(GitHubError):
        make_client(boom).get_repo("a/b")
    with pytest.raises(GitHubError):
        make_client(lambda req: httpx.Response(200, json={"full_name": "a/b"})).get_repo("a/b")


def test_custom_base_url():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        return httpx.Response(200, json=fx("repo_ok.json"))

    make_client(handler, base_url="https://ghe.example.org/api/v3/").get_repo("a/b")
    assert seen["url"] == "https://ghe.example.org/api/v3/repos/a/b"
