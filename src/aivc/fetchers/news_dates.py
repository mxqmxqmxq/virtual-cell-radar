"""从文章页 HTML 中只提取「发布日期」，以及仅存 URL 与日期的持久缓存。

正文、标题等一律不读取、不保存；调用方应在取到日期后丢弃页面文本。
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

from ..models import normalize_news_url

log = logging.getLogger("aivc.fetchers.news_dates")

_ISO = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})")
_JSON_LD = re.compile(r'"datePublished"\s*:\s*"([^"]+)"')
_NAME_KEYS = {"date", "pubdate", "publishdate", "dc.date.issued", "parsely-pub-date"}
RETRY_NULL_AFTER_DAYS = 7


def _parse(value: str | None, today: date | None) -> date | None:
    m = _ISO.match(value or "")
    if not m:
        return None
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    if today is not None and d > today + timedelta(days=1):
        return None
    return d


class _Scan(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.og: list[str] = []
        self.itemprop: list[str] = []
        self.named: list[str] = []
        self.time: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            content = a.get("content", "")
            if a.get("property", "").lower() == "article:published_time" \
                    or a.get("name", "").lower() == "article:published_time":
                self.og.append(content)
            if a.get("itemprop", "").lower() == "datepublished":
                self.itemprop.append(content)
            if a.get("name", "").lower() in _NAME_KEYS:
                self.named.append(content)
        elif tag == "time" and a.get("datetime"):
            self.time.append(a["datetime"])

    handle_startendtag = handle_starttag


def extract_published_date(html_text: str, *, today: date | None = None) -> date | None:
    """按优先级返回第一个可解析的发布日期；从不使用 modified 类字段。"""
    if not html_text:
        return None
    scan = _Scan()
    try:
        scan.feed(html_text)
        scan.close()
    except Exception as e:  # noqa: BLE001 解析器对坏 HTML 通常容错，兜底
        log.debug("html parse issue: %s", e)
    groups = [scan.og, _JSON_LD.findall(html_text), scan.itemprop, scan.named, scan.time]
    for vals in groups:
        for v in vals:
            d = _parse(v, today)
            if d is not None:
                return d
    return None


class ArticleDateCache:
    """JSON 缓存：规范化 URL -> {"date": "YYYY-MM-DD"|null, "checked": "YYYY-MM-DD"}。只含 URL 与日期。"""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._data: dict[str, dict] = {}
        self._dirty = False
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("not an object")
                for k, v in raw.items():
                    if isinstance(v, dict) and "checked" in v:
                        self._data[k] = {"date": v.get("date"), "checked": v["checked"]}
        except (OSError, ValueError) as e:
            log.warning("文章日期缓存损坏，按空缓存处理: %s", e)
            self._data = {}

    def get(self, url: str, *, today: date | None = None) -> tuple[bool, date | None]:
        rec = self._data.get(normalize_news_url(url))
        if rec is None:
            return False, None
        d = _parse(rec.get("date"), None)
        if d is not None:
            return True, d
        chk = _parse(rec.get("checked"), None)
        now = today or date.today()
        if chk is not None and (now - chk).days < RETRY_NULL_AFTER_DAYS:
            return True, None
        return False, None

    def put(self, url: str, d: date | None, *, today: date | None = None) -> None:
        rec = {"date": d.isoformat() if d else None, "checked": (today or date.today()).isoformat()}
        key = normalize_news_url(url)
        if self._data.get(key) != rec:
            self._data[key] = rec
            self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                       encoding="utf-8")
        os.replace(tmp, self.path)
        self._dirty = False
