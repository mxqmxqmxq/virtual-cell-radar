"""Shared fetcher plumbing: client factory, retry helper, Protocol."""
from __future__ import annotations

import time
from datetime import date
from typing import Any, Callable, Protocol

import httpx

from ..models import Paper


class FetchError(Exception):
    pass


def build_client(http_cfg: Any) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": http_cfg.user_agent},
        timeout=http_cfg.timeout,
        follow_redirects=True,
    )


def get_with_retry(
    client: httpx.Client,
    url: str,
    params: dict | None = None,
    *,
    max_retries: int = 3,
    backoff: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> httpx.Response:
    last: str = ""
    for attempt in range(max_retries + 1):
        try:
            resp = client.get(url, params=params)
            if resp.status_code == 429 or resp.status_code >= 500:
                last = f"HTTP {resp.status_code}"
            else:
                resp.raise_for_status()
                return resp
        except httpx.TransportError as e:
            last = f"{type(e).__name__}: {e}"
        except httpx.HTTPStatusError as e:
            raise FetchError(f"GET {url} failed: HTTP {e.response.status_code}") from e
        if attempt < max_retries:
            sleep(backoff * (2 ** attempt))
    raise FetchError(f"GET {url} failed after {max_retries + 1} attempts: {last}")


class Fetcher(Protocol):
    name: str

    def fetch(self, since: date, until: date) -> list[Paper]: ...
