"""Hugging Face Daily Papers fetcher.

Uses the unofficial public JSON endpoint ``/api/daily_papers?date=YYYY-MM-DD``;
field shapes are unverified (待核实). Results are broad (all ML papers) and
must still go through the prefilter.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Callable

import httpx

from ..models import Paper, make_paper_id
from ..textutil import strip_arxiv_version
from .base import FetchError, build_client, get_with_retry
from .links import clean_url, extract_links

log = logging.getLogger("aivc.fetchers.hf_papers")


def _int(v) -> int | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return int(v)


def _author_names(raw) -> list[str]:
    out = []
    for a in raw or []:
        name = a.get("name") if isinstance(a, dict) else a
        if isinstance(name, str) and name.strip():
            out.append(name.strip())
    return out


def item_to_paper(item: dict, today: date) -> Paper | None:
    paper = item.get("paper") or {}
    arxiv_id = strip_arxiv_version(paper.get("id") or item.get("id"))
    if not arxiv_id:
        return None
    title = (paper.get("title") or item.get("title") or "").strip()
    if not title:
        return None
    abstract = (paper.get("summary") or "").strip() or None
    links = extract_links(abstract)
    repo = paper.get("githubRepo")
    repo_url = clean_url(repo) if isinstance(repo, str) and repo.strip() else None
    code_url = repo_url or links.code_url
    ts = paper.get("publishedAt") or item.get("publishedAt")
    published = None
    if isinstance(ts, str) and len(ts) >= 10:
        try:
            published = date.fromisoformat(ts[:10])
        except ValueError:
            published = None
    return Paper(
        id=make_paper_id(arxiv_id=arxiv_id),
        title=title,
        abstract=abstract,
        authors=_author_names(paper.get("authors")),
        source="hf_papers",
        sources=["hf_papers"],
        published=published,
        url=f"https://arxiv.org/abs/{arxiv_id}",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
        alt_urls=[f"https://huggingface.co/papers/{arxiv_id}"],
        arxiv_id=arxiv_id,
        code_url=code_url,
        # githubRepo 是作者显式登记的仓库，视为发布证据；摘要里的链接需有发布语境
        has_code=True if repo_url else (True if links.has_code else None),
        weights_url=links.weights_url,
        has_weights=True if links.has_weights else None,
        hf_upvotes=_int(paper.get("upvotes")),
        github_stars=_int(paper.get("githubStars")),
        first_seen=today,
    )


class HFPapersFetcher:
    name = "hf_papers"

    def __init__(
        self,
        cfg,
        http_cfg,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        today: date | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        self.cfg = cfg
        self.http_cfg = http_cfg
        self.client = client or build_client(http_cfg)
        self.sleep = sleep
        self.today = today  # 测试注入的「UTC 今天」
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.last_incomplete = False
        self.last_warning: str | None = None

    def fetch(self, since: date, until: date) -> list[Paper]:
        self.last_incomplete = False
        self.last_warning = None
        # 未来日期按 UTC 判断（HF 以 UTC 为准），不使用本机时区的 date.today()
        today = self.today or self.now().astimezone(timezone.utc).date()
        days: list[date] = []
        d = until
        while d >= since:
            days.append(d)
            d -= timedelta(days=1)
        max_days = self.cfg.max_days
        if len(days) > max_days:
            log.info("hf_papers 窗口 %d 天超过 max_days=%d，跳过更早的 %d 天",
                     len(days), max_days, len(days) - max_days)
            self.last_incomplete = True
            self.last_warning = (
                f"hf_papers 窗口 {len(days)} 天超过 max_days={max_days}，"
                f"跳过更早的 {len(days) - max_days} 天"
            )
            days = days[:max_days]
        merged: dict[str, Paper] = {}
        for i, day in enumerate(days):
            if i:
                self.sleep(self.cfg.delay_seconds)
            try:
                resp = get_with_retry(
                    self.client,
                    self.cfg.base_url,
                    {"date": day.isoformat()},
                    max_retries=getattr(self.http_cfg, "max_retries", 3),
                    sleep=self.sleep,
                )
            except FetchError as e:
                if "HTTP 404" in str(e):
                    log.info("hf_papers %s 无数据，已跳过 (%s)", day, e)
                    continue
                if "HTTP 400" in str(e):
                    if day > today:
                        log.info("hf_papers %s 为未来 UTC 日期，已跳过 (%s)", day, e)
                        continue
                    raise FetchError(f"hf_papers {day}: {e}") from e
                raise
            try:
                data = resp.json()
            except ValueError as e:
                raise FetchError(f"hf_papers {day} invalid JSON: {e}") from e
            if not isinstance(data, list):
                raise FetchError(f"hf_papers {day}: 响应不是列表")
            for item in data:
                if not isinstance(item, dict):
                    continue
                p = item_to_paper(item, today)
                if p is not None:
                    merged.setdefault(p.id, p)
        return list(merged.values())
