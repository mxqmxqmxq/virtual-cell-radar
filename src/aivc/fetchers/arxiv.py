"""arXiv Atom API fetcher."""
from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta
from typing import Callable

import feedparser
import httpx

from ..models import Paper, make_paper_id
from ..textutil import normalize_doi, strip_arxiv_version
from .base import FetchError, build_client, get_with_retry

log = logging.getLogger("aivc.fetchers.arxiv")

_WS = re.compile(r"\s+")
_GH = re.compile(r"https?://(?:www\.)?github\.com/[^\s<>\"')\]]+", re.IGNORECASE)
_EMPTY_PAGE_RETRIES = 2


def _clean(s: str | None) -> str:
    return _WS.sub(" ", s or "").strip()


def _to_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _first_github(text: str) -> str | None:
    m = _GH.search(text or "")
    if not m:
        return None
    return m.group(0).rstrip(".,;:!?)]}'\"")


def parse_arxiv_feed(text: str, today: date) -> tuple[list[Paper], int]:
    feed = feedparser.parse(text)
    try:
        total = int(feed.feed.get("opensearch_totalresults", 0) or 0)
    except (TypeError, ValueError):
        total = 0
    papers: list[Paper] = []
    for e in feed.entries:
        raw_id = (e.get("id") or "").rsplit("/abs/", 1)[-1]
        m = re.match(r"^(.*?)(v\d+)?$", raw_id)
        arxiv_id = strip_arxiv_version(raw_id)
        version = m.group(2) if m else None
        if not arxiv_id:
            continue
        title = _clean(e.get("title"))
        abstract = _clean(e.get("summary")) or None
        primary = (e.get("arxiv_primary_category") or {}).get("term")
        cats = [t.get("term") for t in e.get("tags", []) if t.get("term")]
        if primary:
            cats = [primary] + [c for c in cats if c != primary]
        doi = e.get("arxiv_doi")
        # The arXiv Atom feed carries no per-paper license, so Paper.license stays None here.
        code_url = _first_github(abstract or "")
        papers.append(
            Paper(
                id=make_paper_id(arxiv_id=arxiv_id),
                title=title,
                abstract=abstract,
                authors=[a.get("name") for a in e.get("authors", []) if a.get("name")],
                source="arxiv",
                venue=_clean(e.get("arxiv_journal_ref")) or None,
                published=_to_date(e.get("published")),
                updated=_to_date(e.get("updated")),
                version=version,
                url=f"https://arxiv.org/abs/{arxiv_id}",
                pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
                doi=normalize_doi(doi) if doi else None,
                arxiv_id=arxiv_id,
                categories=cats,
                code_url=code_url,
                has_code=bool(code_url),
                first_seen=today,
            )
        )
    return papers, total


class ArxivFetcher:
    name = "arxiv"

    def __init__(
        self,
        cfg,
        http_cfg,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        today: date | None = None,
    ):
        self.cfg = cfg
        self.http_cfg = http_cfg
        self.client = client or build_client(http_cfg)
        self.sleep = sleep
        self.today = today

    def _queries(self, since: date, until: date) -> list[str]:
        win = f"submittedDate:[{since:%Y%m%d}0000 TO {until:%Y%m%d}2359]"
        bio = "(" + " OR ".join(f"cat:{c}" for c in self.cfg.bio_categories) + ")"
        ml = "(" + " OR ".join(f"cat:{c}" for c in self.cfg.ml_categories) + ")"
        terms = "(" + " OR ".join(f'abs:"{t}"' for t in self.cfg.query_terms) + ")"
        qs = []
        if self.cfg.bio_categories:
            qs.append(f"{bio} AND {win}")
        if self.cfg.ml_categories and self.cfg.query_terms:
            qs.append(f"{ml} AND {terms} AND {win}")
        return qs

    def _get_page(self, query: str, start: int) -> tuple[list[Paper], int]:
        params = {
            "search_query": query,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "start": start,
            "max_results": self.cfg.page_size,
        }
        today = self.today or date.today()
        for attempt in range(_EMPTY_PAGE_RETRIES + 1):
            resp = get_with_retry(
                self.client,
                self.cfg.base_url,
                params,
                max_retries=getattr(self.http_cfg, "max_retries", 3),
                sleep=self.sleep,
            )
            papers, total = parse_arxiv_feed(resp.text, today)
            if papers or total <= start:
                return papers, total
            # arXiv occasionally returns an empty page although more results exist
            if attempt < _EMPTY_PAGE_RETRIES:
                self.sleep(self.cfg.delay_seconds)
        return papers, total

    def _fetch_query(self, query: str) -> tuple[list[Paper], int]:
        out: list[Paper] = []
        start = 0
        total = 0
        limit = self.cfg.max_results_per_window
        while start < limit:
            papers, total = self._get_page(query, start)
            if start == 0 and total > limit:
                # 结果超过上限：交给调用方拆窗，不在这里抓前 limit 条
                return [], total
            out.extend(papers)
            start += self.cfg.page_size
            if len(papers) < self.cfg.page_size or start >= total or start >= limit:
                break
            self.sleep(self.cfg.delay_seconds)
        return out, total

    def _fetch_window(self, idx: int, since: date, until: date) -> list[Paper]:
        query = self._queries(since, until)[idx]
        papers, total = self._fetch_query(query)
        limit = self.cfg.max_results_per_window
        if total <= limit:
            return papers
        if since < until:
            mid = since + timedelta(days=(until - since).days // 2)
            log.info("arXiv 窗口 %s~%s 共 %d 条，超过上限 %d，拆成两半", since, until, total, limit)
            self.sleep(self.cfg.delay_seconds)
            left = self._fetch_window(idx, since, mid)
            self.sleep(self.cfg.delay_seconds)
            return left + self._fetch_window(idx, mid + timedelta(days=1), until)
        log.warning("arXiv 单日 %s 共 %d 条，超过上限 %d，只取最新的 %d 条；"
                    "可调大 sources.yaml 的 max_results_per_window", since, total, limit, limit)
        out: list[Paper] = []
        start = 0
        while start < limit:
            page, _ = self._get_page(query, start)
            out.extend(page)
            start += self.cfg.page_size
            if len(page) < self.cfg.page_size:
                break
            self.sleep(self.cfg.delay_seconds)
        return out

    def fetch(self, since: date, until: date) -> list[Paper]:
        merged: dict[str, Paper] = {}
        n_queries = len(self._queries(since, until))
        for i in range(n_queries):
            if i:
                self.sleep(self.cfg.delay_seconds)
            for p in self._fetch_window(i, since, until):
                merged.setdefault(p.arxiv_id, p)
        return list(merged.values())
