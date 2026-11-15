"""GitHub REST API 客户端：只读取公开仓库的元数据（star、最近推送、归档、许可证、主语言）。

- 只请求 `GET /repos/{owner}/{repo}`；token 来自环境变量 GITHUB_TOKEN（可选）。
- 限流（403/429 且剩余额度为 0 或带 Retry-After）抛 RateLimited；每次响应后读取
  X-RateLimit-Remaining，为 0 时记下 `exhausted`，调用方应主动停止。
- 404 返回 status="not_found"；改名/迁移的仓库跟随重定向，返回新的 full_name。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.github.com"
DEFAULT_UA = "virtual-cell-radar/0.1 (+https://github.com/mxqmxqmxq/virtual-cell-radar)"


class GitHubError(Exception):
    pass


class RateLimited(GitHubError):
    """被限流。reset_at 为额度恢复时间（UTC，未知为 None）。"""

    def __init__(self, message: str, reset_at: datetime | None = None):
        super().__init__(message)
        self.reset_at = reset_at


@dataclass
class RepoInfo:
    status: str  # ok | not_found
    requested: str
    full_name: str | None = None
    html_url: str | None = None
    stars: int | None = None
    pushed_at: str | None = None
    archived: bool | None = None
    license: str | None = None
    language: str | None = None


def build_github_client(user_agent: str = DEFAULT_UA, timeout: float = 30) -> httpx.Client:
    return httpx.Client(headers={"User-Agent": user_agent}, timeout=timeout, follow_redirects=True)


class GitHubClient:
    def __init__(self, token: str | None = None, client: httpx.Client | None = None,
                 base_url: str = DEFAULT_BASE_URL):
        self.token = token if token is not None else (os.environ.get("GITHUB_TOKEN") or None)
        self._client = client or build_github_client()
        self.base_url = base_url.rstrip("/")
        self.requests = 0
        self.remaining: int | None = None
        self.reset_at: datetime | None = None

    @property
    def exhausted(self) -> bool:
        """上一次响应表明剩余额度为 0：调用方应停止继续请求。"""
        return self.remaining is not None and self.remaining <= 0

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/vnd.github+json", "User-Agent": DEFAULT_UA,
             "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _note_limits(self, resp: httpx.Response) -> None:
        rem = resp.headers.get("X-RateLimit-Remaining")
        if rem is not None and rem.strip().lstrip("-").isdigit():
            self.remaining = int(rem)
        reset = resp.headers.get("X-RateLimit-Reset")
        if reset is not None and reset.strip().isdigit():
            self.reset_at = datetime.fromtimestamp(int(reset), tz=timezone.utc)

    def get_repo(self, full_name: str) -> RepoInfo:
        url = f"{self.base_url}/repos/{full_name}"
        try:
            resp = self._client.get(url, headers=self._headers())
        except httpx.TransportError as e:
            raise GitHubError(f"GET {url} 失败：{type(e).__name__}: {e}") from e
        self.requests += 1
        self._note_limits(resp)
        code = resp.status_code
        if code in (403, 429):
            retry_after = resp.headers.get("Retry-After")
            if self.remaining == 0 or retry_after is not None or code == 429:
                reset = self.reset_at
                if retry_after is not None and retry_after.strip().isdigit():
                    reset = datetime.fromtimestamp(time.time() + int(retry_after), tz=timezone.utc)
                    self.reset_at = reset
                raise RateLimited(f"GitHub API 限流（HTTP {code}）", reset)
            raise GitHubError(f"GET {url} 被拒绝：HTTP {code}")
        if code == 404:
            return RepoInfo(status="not_found", requested=full_name)
        if code != 200:
            raise GitHubError(f"GET {url} 失败：HTTP {code}")
        try:
            data: dict[str, Any] = resp.json()
            lic = data.get("license") or {}
            return RepoInfo(
                status="ok", requested=full_name,
                full_name=data["full_name"], html_url=data.get("html_url"),
                stars=int(data["stargazers_count"]), pushed_at=data.get("pushed_at"),
                archived=bool(data.get("archived", False)),
                license=lic.get("spdx_id") if isinstance(lic, dict) else None,
                language=data.get("language"),
            )
        except (ValueError, KeyError, TypeError) as e:
            raise GitHubError(f"GET {url} 响应无法解析：{e}") from e
