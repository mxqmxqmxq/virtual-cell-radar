"""新闻 RSS/Atom 抓取器：只取标题、链接、发布时间与 feed 自带的摘要片段（内存中，不落盘）。"""
from __future__ import annotations

import html
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable
from urllib import robotparser
from urllib.parse import urlsplit

import feedparser
import httpx

from ..config import NewsFeed, NewsSourceConfig
from ..models import NewsEntry
from .base import FetchError, build_client, get_with_retry

log = logging.getLogger("aivc.fetchers.news_rss")

_BLOCKS = re.compile(r"<(script|style)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]*>")
_WS = re.compile(r"\s+")
_HTTP = re.compile(r"^https?://", re.IGNORECASE)


def _clean_text(s: str | None) -> str:
    return _WS.sub(" ", html.unescape(s or "")).strip()


def _clean_snippet(s: str | None, max_chars: int) -> str:
    """去 HTML、反转义、压缩空白并截断。"""
    t = _BLOCKS.sub(" ", s or "")
    t = _TAGS.sub(" ", t)
    t = _clean_text(t)
    # 反转义后可能又出现转义出来的标签，再清一遍
    t = _clean_text(_TAGS.sub(" ", t))
    if max_chars >= 0 and len(t) > max_chars:
        t = t[:max_chars].rstrip()
    return t


def _entry_date(e: Any) -> date | None:
    for key in ("published_parsed", "updated_parsed"):
        t = e.get(key)
        if t:
            try:
                return date(t[0], t[1], t[2])
            except (ValueError, TypeError):
                continue
    return None


def parse_feed(text: str, feed: NewsFeed, *, snippet_max_chars: int = 1200) -> list[NewsEntry]:
    """解析 RSS 2.0 / Atom。绝不读取 content:encoded / Atom content 全文。"""
    parsed = feedparser.parse(text)
    out: list[NewsEntry] = []
    for e in parsed.entries:
        title = _clean_text(_TAGS.sub(" ", e.get("title") or ""))
        link = (e.get("link") or "").strip()
        if not title or not _HTTP.match(link):
            continue
        # 只取 summary/description。feedparser 在缺 description 时会拿 content:encoded 顶替
        # summary，这里识别出来并丢弃，确保全文永远不被读取。
        summary = e.get("summary") or ""
        if any(summary == c.get("value") for c in (e.get("content") or [])):
            summary = ""
        snippet = _clean_snippet(summary, snippet_max_chars)
        out.append(
            NewsEntry(
                title=title,
                url=link,
                source=feed.name,
                published=_entry_date(e),
                snippet=snippet,
                feed_kind=feed.kind,
                feed_company=feed.company,
            )
        )
    return out


@dataclass
class NewsFetchResult:
    entries: list[NewsEntry] = field(default_factory=list)
    per_feed: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    requests: int = 0


class _Shared:
    """多个抓取器共用的节奏/robots/请求计数状态。"""

    def __init__(self) -> None:
        self.requests = 0
        self.made_request = False
        self.robots: dict[str, robotparser.RobotFileParser | None] = {}


class NewsHTTPBase:
    """robots 缓存、请求间隔与请求计数；RSS 与 HTML 抓取器共用。"""

    def __init__(
        self,
        news_cfg: NewsSourceConfig,
        http_cfg: Any,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        share_with: "NewsHTTPBase | None" = None,
    ):
        self.cfg = news_cfg
        self.http = http_cfg
        self._sleep = sleep
        if share_with is not None:
            self._client = share_with._client
            self._shared = share_with._shared
            return
        self._shared = _Shared()
        self._client = client or build_client(
            type("_H", (), {"user_agent": news_cfg.user_agent, "timeout": http_cfg.timeout})()
        )
        self._client.headers["User-Agent"] = news_cfg.user_agent
        self._client.event_hooks.setdefault("request", []).append(self._count)

    @property
    def _requests(self) -> int:
        return self._shared.requests

    def _count(self, request: httpx.Request) -> None:
        self._shared.requests += 1

    def _pace(self) -> None:
        """任意两次请求之间至少间隔 effective_delay 秒。"""
        if self._shared.made_request:
            self._sleep(self.cfg.effective_delay)
        self._shared.made_request = True

    def _robots_for(self, url: str) -> robotparser.RobotFileParser | None:
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host in self._shared.robots:
            return self._shared.robots[host]
        rp: robotparser.RobotFileParser | None = None
        self._pace()
        try:
            resp = self._client.get(f"{host}/robots.txt")
            if resp.status_code == 200:
                rp = robotparser.RobotFileParser()
                rp.parse(resp.text.splitlines())
        except httpx.HTTPError as e:
            log.info("robots.txt unavailable for %s: %s", host, e)
        self._shared.robots[host] = rp
        return rp

    def _allowed(self, url: str) -> bool:
        if not self.cfg.respect_robots:
            return True
        rp = self._robots_for(url)
        return rp is None or rp.can_fetch(self.cfg.user_agent, url)


class NewsRSSFetcher(NewsHTTPBase):
    name = "news_rss"

    def fetch(self, since: date, until: date) -> NewsFetchResult:
        res = NewsFetchResult()
        start = self._requests
        for feed in self.cfg.feeds:
            if not feed.enabled:
                continue
            if not self._allowed(feed.url):
                res.errors[feed.name] = "robots disallow"
                continue
            self._pace()
            try:
                resp = get_with_retry(
                    self._client, feed.url, max_retries=self.http.max_retries, sleep=self._sleep
                )
                entries = parse_feed(resp.text, feed, snippet_max_chars=self.cfg.snippet_max_chars)
            except FetchError as e:
                res.errors[feed.name] = str(e)
                continue
            except Exception as e:  # 单源异常不影响其他源
                res.errors[feed.name] = f"{type(e).__name__}: {e}"
                continue
            kept = [
                x for x in entries
                if (x.published is None and not feed.require_date)
                or (x.published is not None and since <= x.published <= until)
            ][: self.cfg.max_items_per_feed]
            res.entries.extend(kept)
            res.per_feed[feed.name] = len(kept)
        res.requests = self._requests - start
        return res
