"""新闻 HTML 列表页抓取器：只取标题、链接与日期，不请求条目链接、不保存正文。

仅用标准库 html.parser。适用于没有 RSS、但 robots 与条款允许自动读取列表页的站点；
每个站点由 `NewsHtmlSource` 配置（URL 过滤规则、日期取值范围等）。
"""
from __future__ import annotations

import html
import logging
import re
import time
from datetime import date
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urldefrag, urljoin, urlsplit

import httpx

from ..config import NewsHtmlSource, NewsSourceConfig
from ..models import NewsEntry
from .base import FetchError, get_with_retry
from .news_rss import NewsFetchResult, NewsHTTPBase, NewsRSSFetcher

log = logging.getLogger("aivc.fetchers.news_html")

_WS = re.compile(r"\s+")
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MON = (r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
        r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)")
_DATE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)"), "ymd"),
    (re.compile(r"(?<!\d)(\d{4})[./](\d{1,2})[./](\d{1,2})(?!\d)"), "ymd"),
    (re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"), "ymd"),
    (re.compile(rf"\b{_MON}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I), "mdy"),
    (re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MON}\.?,?\s+(\d{{4}})\b", re.I), "dmy"),
]
_URL_DATE = re.compile(r"/(\d{4})/(\d{1,2})/(\d{1,2})(?:/|$)")
_SKIP_TAGS = {"script", "style", "noscript", "template"}
_HEADINGS = {"h1", "h2", "h3", "h4"}
_AFTER_MAX_CHARS = 400
_BEFORE_MAX_CHARS = 200


def _mk(y: int, m: int, d: int) -> date | None:
    if not 1990 <= y <= 2100:
        return None
    try:
        return date(y, m, d)
    except ValueError:
        return None


def find_date(text: str, *, last: bool = False) -> date | None:
    """返回文本里位置最靠前的合法日期；last=True 时返回位置最靠后的合法日期。"""
    best: tuple[int, date] | None = None
    for pat, kind in _DATE_PATTERNS:
        for m in pat.finditer(text):
            g = m.groups()
            if kind == "ymd":
                d = _mk(int(g[0]), int(g[1]), int(g[2]))
            elif kind == "mdy":
                d = _mk(int(g[2]), _MONTHS[g[0][:3].lower()], int(g[1]))
            else:
                d = _mk(int(g[2]), _MONTHS[g[1][:3].lower()], int(g[0]))
            if d is not None:
                if last:
                    # 该模式下的所有匹配里取最靠后的合法日期
                    for m2 in pat.finditer(text, m.start()):
                        g2 = m2.groups()
                        if kind == "ymd":
                            d2 = _mk(int(g2[0]), int(g2[1]), int(g2[2]))
                        elif kind == "mdy":
                            d2 = _mk(int(g2[2]), _MONTHS[g2[0][:3].lower()], int(g2[1]))
                        else:
                            d2 = _mk(int(g2[2]), _MONTHS[g2[1][:3].lower()], int(g2[0]))
                        if d2 is not None and (best is None or m2.start() >= best[0]):
                            best = (m2.start(), d2)
                elif best is None or m.start() < best[0]:
                    best = (m.start(), d)
                break
    return best[1] if best else None


def _url_date(url: str) -> date | None:
    m = _URL_DATE.search(urlsplit(url).path)
    return _mk(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _clean(s: str) -> str:
    return _WS.sub(" ", html.unescape(s or "")).strip()


class _Tokenizer(HTMLParser):
    """把 HTML 压平成 token 序列：("start", tag, attrs) / ("end", tag) / ("text", s, kind)。

    kind 为 "t"（普通文本）或 "time"（<time> 内文本及其 datetime 属性，不计入标题）。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[tuple] = []
        self._skip = 0
        self._time = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if self._skip:
            return
        a = {k: (v or "") for k, v in attrs}
        self.tokens.append(("start", tag, a))
        if tag == "time":
            self._time += 1
            if a.get("datetime"):
                self.tokens.append(("text", a["datetime"], "time"))
        elif tag == "br":
            self.tokens.append(("text", " ", "t"))

    def handle_startendtag(self, tag, attrs):
        if tag in _SKIP_TAGS or self._skip:
            return
        a = {k: (v or "") for k, v in attrs}
        self.tokens.append(("start", tag, a))
        self.tokens.append(("end", tag))

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "time":
            self._time = max(0, self._time - 1)
        self.tokens.append(("end", tag))

    def handle_data(self, data):
        if self._skip or not data:
            return
        self.tokens.append(("text", data, "time" if self._time else "t"))


class _Anchor:
    __slots__ = ("start", "end", "url", "attrs")

    def __init__(self, start: int, url: str, attrs: dict[str, str]):
        self.start, self.end, self.url, self.attrs = start, start, url, attrs


def _anchors(tokens: list[tuple], base_url: str) -> list[_Anchor]:
    out: list[_Anchor] = []
    cur: _Anchor | None = None
    for i, t in enumerate(tokens):
        if t[0] == "start" and t[1] == "a":
            if cur is not None:
                cur.end = i - 1
                out.append(cur)
                cur = None
            href = t[2].get("href", "").strip()
            if href:
                cur = _Anchor(i, urldefrag(urljoin(base_url, href))[0], t[2])
        elif t[0] == "end" and t[1] == "a" and cur is not None:
            cur.end = i
            out.append(cur)
            cur = None
    if cur is not None:
        cur.end = len(tokens) - 1
        out.append(cur)
    return out


def _anchor_titles(tokens: list[tuple], a: _Anchor) -> list[tuple[int, str]]:
    """返回 (优先级, 标题) 候选：标题标签 2 > 锚点文本 1 > aria-label/title 属性 0。"""
    cands: list[tuple[int, str]] = []
    inner = tokens[a.start + 1 : a.end + 1]
    plain: list[str] = []
    head: list[str] = []
    in_head = 0
    cur_head: list[str] = []
    for t in inner:
        if t[0] == "start" and t[1] in _HEADINGS:
            in_head += 1
            if in_head == 1:
                cur_head = []
        elif t[0] == "end" and t[1] in _HEADINGS and in_head:
            in_head -= 1
            if in_head == 0:
                head.append(_clean(" ".join(cur_head)))
        elif t[0] == "text" and t[2] == "t":
            plain.append(t[1])
            if in_head:
                cur_head.append(t[1])
    for h in head:
        if h:
            cands.append((2, h))
    cands.append((1, _clean(" ".join(plain))))
    for k in ("aria-label", "title"):
        if a.attrs.get(k):
            cands.append((0, _clean(a.attrs[k])))
    return cands


def _texts(tokens: list[tuple], lo: int, hi: int) -> str:
    return " ".join(t[1] for t in tokens[lo:hi] if t[0] == "text")


def _same_host(a: str, b: str) -> bool:
    ha, hb = urlsplit(a).netloc.lower(), urlsplit(b).netloc.lower()
    strip = lambda h: h[4:] if h.startswith("www.") else h  # noqa: E731
    return strip(ha) == strip(hb)


def parse_list_page(html_text: str, source: NewsHtmlSource) -> list[NewsEntry]:
    """解析列表页，返回只含标题/链接/日期的条目（snippet 恒为空）。"""
    tk = _Tokenizer()
    try:
        tk.feed(html_text)
        tk.close()
    except Exception as e:  # 残缺 HTML：用已解析的部分
        log.info("html parse error for %s: %s", source.name, e)
    tokens = tk.tokens
    link_re = re.compile(source.link_pattern) if source.link_pattern else None
    excl_re = re.compile(source.exclude_pattern) if source.exclude_pattern else None
    strip_res = [re.compile(p) for p in source.title_strip_patterns]
    list_norm = urldefrag(source.list_url)[0].rstrip("/")

    # 1) 通过 URL 规则的锚点
    keep: list[_Anchor] = []
    for a in _anchors(tokens, source.list_url):
        parts = urlsplit(a.url)
        if parts.scheme not in ("http", "https"):
            continue
        if a.url.rstrip("/") == list_norm:
            continue
        if source.same_host_only and not _same_host(a.url, source.list_url):
            continue
        if link_re and not link_re.search(a.url):
            continue
        if excl_re and excl_re.search(a.url):
            continue
        keep.append(a)

    # 2) 同一 URL 合并，标题择优
    order: list[str] = []
    groups: dict[str, list[int]] = {}
    for i, a in enumerate(keep):
        if a.url not in groups:
            groups[a.url] = []
            order.append(a.url)
        groups[a.url].append(i)

    def pick_title(idxs: list[int]) -> str | None:
        best: tuple[int, int, str] | None = None
        for i in idxs:
            for pri, title in _anchor_titles(tokens, keep[i]):
                for rx in strip_res:
                    title = rx.sub(" ", title)
                if strip_res:
                    title = _clean(title)
                if len(title) < source.min_title_chars:
                    continue
                key = (pri, len(title), title)
                if best is None or key[:2] > best[:2]:
                    best = key
        return best[2] if best else None

    # 3) 日期候选（每个锚点位置）
    def inside(i: int) -> date | None:
        a = keep[i]
        return find_date(_texts(tokens, a.start, a.end + 1))

    def after(i: int) -> date | None:
        a = keep[i]
        hi = keep[i + 1].start if i + 1 < len(keep) else len(tokens)
        return find_date(_texts(tokens, a.end + 1, hi)[:_AFTER_MAX_CHARS])

    def before(i: int) -> date | None:
        a = keep[i]
        lo = keep[i - 1].end + 1 if i > 0 else 0
        return find_date(_texts(tokens, lo, a.start)[-_BEFORE_MAX_CHARS:])

    def before_nearest(i: int) -> date | None:
        a = keep[i]
        lo = keep[i - 1].end + 1 if i > 0 else 0
        txt = _texts(tokens, lo, a.start)
        n = source.date_lookback_chars
        return find_date(txt[-n:] if n > 0 else "", last=True)

    scope = source.date_scope
    if scope == "auto":
        # 取覆盖最多条目的方向；并列时 after 优先于 before
        n_after = sum(1 for i in range(len(keep)) if after(i))
        n_before = sum(1 for i in range(len(keep)) if before(i))
        scope = "before" if n_before > n_after else "after"
    scoped = {"after": after, "before": before, "before_nearest": before_nearest}.get(scope)

    entries: list[NewsEntry] = []
    for url in order:
        idxs = groups[url]
        title = pick_title(idxs)
        if not title:
            continue
        d = _url_date(url)
        if d is None:
            for i in idxs:
                d = inside(i)
                if d:
                    break
        if d is None and scope != "inside" and scoped is not None:
            for i in idxs:
                d = scoped(i)
                if d:
                    break
        if d is None and source.require_date:
            continue
        entries.append(
            NewsEntry(
                title=title,
                url=url,
                source=source.name,
                published=d,
                snippet="",
                feed_kind=source.kind,
                feed_company=source.company,
                feed_prefilter=source.prefilter,
                feed_strict_terms=list(source.strict_terms),
            )
        )
        if len(entries) >= source.max_items:
            break
    return entries


class NewsHTMLFetcher(NewsHTTPBase):
    name = "news_html"

    def fetch(self, since: date, until: date) -> NewsFetchResult:
        res = NewsFetchResult()
        start = self._requests
        for src in self.cfg.html_sources:
            if not src.enabled:
                continue
            try:
                if not self._allowed(src.list_url):
                    res.errors[src.name] = "robots disallow"
                    continue
                self._pace()
                resp = get_with_retry(
                    self._client, src.list_url, max_retries=self.http.max_retries, sleep=self._sleep
                )
                entries = parse_list_page(resp.text, src)
            except FetchError as e:
                res.errors[src.name] = str(e)
                continue
            except Exception as e:  # 单源异常不影响其他源
                res.errors[src.name] = f"{type(e).__name__}: {e}"
                continue
            kept = [x for x in entries if x.published is None or since <= x.published <= until]
            res.entries.extend(kept)
            res.per_feed[src.name] = len(kept)
        res.requests = self._requests - start
        return res


class CompositeNewsFetcher:
    """依次运行 RSS 与 HTML 抓取器；共用同一个 client、间隔与 robots 缓存。"""

    name = "news_composite"

    def __init__(
        self,
        news_cfg: NewsSourceConfig,
        http_cfg: Any,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        date_cache: Any = None,
    ):
        self.rss = NewsRSSFetcher(news_cfg, http_cfg, client=client, sleep=sleep, date_cache=date_cache)
        self.html = NewsHTMLFetcher(news_cfg, http_cfg, sleep=sleep, share_with=self.rss)

    def fetch(self, since: date, until: date) -> NewsFetchResult:
        out = NewsFetchResult()
        for f in (self.rss, self.html):
            try:
                r = f.fetch(since, until)
            except Exception as e:  # 理论上不会发生，兜底
                out.errors[f.name] = f"{type(e).__name__}: {e}"
                continue
            out.entries.extend(r.entries)
            out.per_feed.update(r.per_feed)
            out.errors.update(r.errors)
            out.requests += r.requests
            out.date_lookups += r.date_lookups
        return out
