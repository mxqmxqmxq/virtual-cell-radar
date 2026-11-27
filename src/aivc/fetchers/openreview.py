"""OpenReview API v2 batch fetcher (notes by venueid / invitation).

Query parameter names (``content.venueid``, ``invitation``, ``limit``,
``offset``, ``sort=tmdate:desc``) and the ``{"notes": [...], "count": N}``
response shape follow the OpenReview v2 docs as understood; 待核实 with a
real smoke run before relying on it.
"""
from __future__ import annotations

import os
import time
from datetime import date, datetime, timezone
from typing import Any, Callable

import httpx

from ..config import OpenReviewVenue
from ..models import Paper, make_paper_id
from ..openreview_refresh import _ms_to_date, _val
from .base import FetchError, build_client, get_with_retry
from .links import extract_links


def _is_public(readers: Any) -> bool:
    return isinstance(readers, list) and "everyone" in readers


def _public_content(content: dict) -> dict:
    """Drop content fields that carry their own non-public ``readers``.

    Fields without ``readers`` inherit the (already verified public) note level.
    """
    out = {}
    for k, v in content.items():
        if isinstance(v, dict) and "readers" in v and not _is_public(v.get("readers")):
            continue
        out[k] = v
    return out


def note_to_paper(note: dict, venue_name: str, since: date, until: date, today: date) -> Paper | None:
    nid = note.get("id")
    content = note.get("content")
    if not isinstance(nid, str) or not nid or not isinstance(content, dict):
        return None
    # Public-visibility gate: with a token the API also returns restricted notes.
    if not _is_public(note.get("readers")):
        return None
    content = _public_content(content)
    pub = None
    for k in ("pdate", "odate", "cdate"):
        pub = _ms_to_date(note.get(k))
        if pub:
            break
    if pub is None or not (since <= pub <= until):
        return None
    title = _val(content, "title")
    if not isinstance(title, str) or not " ".join(title.split()):
        return None
    title = " ".join(title.split())
    abstract = _val(content, "abstract")
    abstract = " ".join(abstract.split()) if isinstance(abstract, str) and abstract.strip() else None
    authors = _val(content, "authors") or []
    if isinstance(authors, str):
        authors = [authors]
    authors = [a.strip() for a in authors if isinstance(a, str) and a.strip()
               and a.strip().lower() != "anonymous"] if isinstance(authors, list) else []
    kws = _val(content, "keywords") or []
    if isinstance(kws, str):
        kws = [k.strip() for k in kws.replace(";", ",").split(",")]
    kws = [k.strip() for k in kws if isinstance(k, str) and k.strip()] if isinstance(kws, list) else []
    venue = _val(content, "venue")
    venue = venue.strip() if isinstance(venue, str) and venue.strip() else venue_name
    links = extract_links(abstract)
    return Paper(
        id=make_paper_id(openreview_id=nid),
        title=title,
        abstract=abstract,
        authors=authors,
        keywords=kws,
        source="openreview",
        sources=["openreview"],
        venue=venue,
        published=pub,
        url=f"https://openreview.net/forum?id={nid}",
        pdf_url=f"https://openreview.net/pdf?id={nid}",
        openreview_id=nid,
        code_url=links.code_url,
        has_code=True if links.has_code else None,
        weights_url=links.weights_url,
        has_weights=True if links.has_weights else None,
        first_seen=today,
    )


class OpenReviewFetcher:
    name = "openreview"

    def __init__(
        self,
        cfg: Any,
        http_cfg: Any,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        today: date | None = None,
        token: str | None = None,
    ):
        self.cfg = cfg
        self.http_cfg = http_cfg
        self.client = client or build_client(http_cfg)
        self.sleep = sleep
        self.today = today
        self.last_incomplete = False
        self.last_warning: str | None = None
        if token is None:
            token = os.environ.get(cfg.token_env)
        if token:
            self.client.headers["Authorization"] = f"Bearer {token}"

    def _page(self, url: str, params: dict) -> dict:
        resp = get_with_retry(
            self.client, url, params,
            max_retries=self.http_cfg.max_retries, sleep=self.sleep,
        )
        try:
            data = resp.json()
        except ValueError as e:
            raise FetchError(f"invalid JSON from {url}") from e
        if not isinstance(data, dict):
            raise FetchError(f"unexpected payload from {url}")
        return data

    def fetch(self, since: date, until: date) -> list[Paper]:
        self.last_incomplete = False
        self.last_warning = None
        today = self.today or date.today()
        url = f"{self.cfg.api_base_url.rstrip('/')}/notes"
        delay = max(1.0, float(self.cfg.delay_seconds))
        since_ms = int(datetime(since.year, since.month, since.day, tzinfo=timezone.utc).timestamp() * 1000)
        max_notes = self.cfg.max_notes_per_venue
        out: dict[str, Paper] = {}
        started = False
        for v in self.cfg.venues:
            if not isinstance(v, OpenReviewVenue) or not (v.venueid or v.invitation):
                continue
            if started:
                self.sleep(delay)  # rate-limit between venues
            started = True
            base = {"content.venueid": v.venueid} if v.venueid else {"invitation": v.invitation}
            offset = 0
            finished = False  # crossed since, or ran out of notes
            while offset < max_notes:
                # tmdate = last modification time; any publication/acceptance change
                # refreshes it, so paging newest-first until older than ``since``
                # also covers notes created early but made public recently.
                # Whether the API supports sort=tmdate:desc is 待核实.
                params = {**base, "limit": self.cfg.page_size, "offset": offset, "sort": "tmdate:desc"}
                data = self._page(url, params)
                notes = data.get("notes") or []
                if not isinstance(notes, list) or not notes:
                    finished = True
                    break
                times = []
                for n in notes:
                    if not isinstance(n, dict):
                        continue
                    t = n.get("tmdate")
                    if not isinstance(t, (int, float)) or isinstance(t, bool):
                        t = n.get("cdate")
                    if isinstance(t, (int, float)) and not isinstance(t, bool):
                        times.append(t)
                    p = note_to_paper(n, v.name, since, until, today)
                    if p and p.id not in out:
                        out[p.id] = p
                offset += len(notes)
                try:
                    count = int(data.get("count"))
                except (TypeError, ValueError):
                    count = None
                if times and min(times) < since_ms:
                    finished = True
                    break
                if count is not None and offset >= count:
                    finished = True
                    break
                self.sleep(delay)
            if not finished:
                self.last_incomplete = True
                self.last_warning = (
                    f"openreview {v.name}: 达到 max_notes_per_venue={max_notes} "
                    f"仍未翻到 {since} 之前，结果可能不完整"
                )
        return list(out.values())
