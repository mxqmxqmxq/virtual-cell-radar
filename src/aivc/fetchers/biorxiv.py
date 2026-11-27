"""bioRxiv / medRxiv fetcher using the api.biorxiv.org ``details`` endpoint."""
from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime
from typing import Any, Callable

import httpx

from ..models import Paper, make_paper_id
from ..textutil import biorxiv_id_from_doi, normalize_doi
from .base import FetchError, build_client, get_with_retry
from .links import extract_links

log = logging.getLogger("aivc.fetchers.biorxiv")

_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
_NO_RESULT_HINTS = ("no posts", "no result", "no articles", "not found")


def _parse_date(s: Any) -> date | None:
    try:
        return datetime.strptime(str(s).strip()[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _first_submitted(biorxiv_id: str | None) -> date | None:
    if not biorxiv_id:
        return None
    try:
        return datetime.strptime(biorxiv_id[:10], "%Y.%m.%d").date()
    except ValueError:
        return None


def _version_num(p: Paper) -> int:
    try:
        return int((p.version or "v0").lstrip("v"))
    except ValueError:
        return 0


def _entry_to_paper(e: dict, server: str, today: date) -> Paper | None:
    doi = normalize_doi(e.get("doi") or "")
    title = " ".join((e.get("title") or "").split())
    if not doi or not title:
        return None
    bid = biorxiv_id_from_doi(doi)
    ver = str(e.get("version") or "1").strip()
    date_v = _parse_date(e.get("date"))
    first = _first_submitted(bid)
    published = first or date_v
    host = "www.medrxiv.org" if server == "medrxiv" else "www.biorxiv.org"
    url = f"https://{host}/content/{doi}v{ver}"
    abstract = " ".join((e.get("abstract") or "").split()) or None
    links = extract_links(abstract)
    inst = (e.get("author_corresponding_institution") or "").strip()
    pub = (e.get("published") or "").strip()
    pub_doi: str | None = None
    venue: str | None = None
    if pub and _DOI_RE.match(pub):
        pub_doi = normalize_doi(pub) or None
    elif pub and pub.upper() != "NA":
        venue = pub
    cat = (e.get("category") or "").strip()
    lic = (e.get("license") or "").strip() or None  # raw source value, not rewritten
    return Paper(
        id=make_paper_id(biorxiv_id=bid, doi=doi, title=title),
        title=title,
        abstract=abstract,
        authors=[a.strip() for a in (e.get("authors") or "").split(";") if a.strip()],
        affiliations=[inst] if inst else [],
        source="medrxiv" if server == "medrxiv" else "biorxiv",
        venue=venue,
        published_doi=pub_doi,
        published=published,
        updated=date_v,
        version=f"v{ver}",
        url=url,
        pdf_url=url + ".full.pdf",
        doi=doi,
        license=lic,
        biorxiv_id=bid,
        categories=[cat] if cat else [],
        code_url=links.code_url,
        has_code=True if links.has_code else None,
        weights_url=links.weights_url,
        has_weights=True if links.has_weights else None,
        sources=[server if server == "medrxiv" else "biorxiv"],
        first_seen=today,
    )


def parse_biorxiv_page(payload: dict, server: str, today: date) -> tuple[list[Paper], int]:
    """Return (papers, total) for one API page. Raises FetchError on error status."""
    msgs = payload.get("messages") or [{}]
    msg = msgs[0] if isinstance(msgs, list) and msgs else {}
    status = str(msg.get("status", "ok")).lower()
    collection = payload.get("collection") or []
    if status != "ok":
        if any(h in status for h in _NO_RESULT_HINTS):
            return [], 0
        raise FetchError(f"bioRxiv API returned status {msg.get('status')!r}")
    try:
        total = int(msg.get("total") or 0)
    except (TypeError, ValueError):
        total = 0
    papers = [p for e in collection if (p := _entry_to_paper(e, server, today))]
    return papers, total


class BiorxivFetcher:
    def __init__(
        self,
        cfg: Any,
        http_cfg: Any,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        today: date | None = None,
    ):
        self.cfg = cfg
        self.http_cfg = http_cfg
        self.client = client or build_client(http_cfg)
        self.sleep = sleep
        self.today = today
        self.name = cfg.server
        self.last_published_links: dict[str, tuple[str, str]] = {}
        self.last_incomplete = False
        self.last_warning: str | None = None

    def _get_json(self, url: str, params: dict) -> dict:
        resp = get_with_retry(
            self.client, url, params,
            max_retries=self.http_cfg.max_retries, sleep=self.sleep,
        )
        try:
            return resp.json()
        except ValueError as e:
            raise FetchError(f"invalid JSON from {url}") from e

    def fetch(self, since: date, until: date) -> list[Paper]:
        self.last_published_links = {}
        self.last_incomplete = False
        self.last_warning = None
        today = self.today or date.today()
        base = self.cfg.base_url.rstrip("/")
        best: dict[str, Paper] = {}
        first_request = True
        for category in self.cfg.categories:
            cat_param = category.strip().replace(" ", "_")
            cursor = 0
            for _ in range(self.cfg.max_pages_per_category):
                if not first_request:
                    self.sleep(self.cfg.delay_seconds)
                first_request = False
                url = f"{base}/details/{self.cfg.server}/{since.isoformat()}/{until.isoformat()}/{cursor}"
                payload = self._get_json(url, {"category": cat_param})
                n = len(payload.get("collection") or [])
                papers, total = parse_biorxiv_page(payload, self.cfg.server, today)
                for p in papers:
                    cur = best.get(p.id)
                    if cur is None or _version_num(p) > _version_num(cur):
                        best[p.id] = p
                cursor += n
                if n == 0 or cursor >= total:
                    break
        result = list(best.values())
        if getattr(self.cfg, "fetch_published_links", False):
            try:
                self.sleep(self.cfg.delay_seconds)
                links = self.fetch_published_links(since, until)
            except Exception as e:  # noqa: BLE001 - enrichment must not break the main fetch
                log.warning("bioRxiv pubs 回填失败，已跳过: %s", e)
                links = {}
            self.last_published_links = dict(links)
            if links:
                for i, p in enumerate(result):
                    hit = links.get(normalize_doi(p.doi or ""))
                    if not hit:
                        continue
                    upd = {"published_doi": hit[0], "published_venue": hit[1]}
                    if not p.venue:
                        upd["venue"] = hit[1]
                    result[i] = p.model_copy(update=upd)
        return result

    def fetch_published_links(self, since: date, until: date) -> dict[str, tuple[str, str]]:
        """biorxiv_doi -> (published_doi, published_journal) via the /pubs endpoint."""
        base = self.cfg.base_url.rstrip("/")
        out: dict[str, tuple[str, str]] = {}
        cursor = 0
        for page in range(self.cfg.max_pages_per_category):
            if page:
                self.sleep(self.cfg.delay_seconds)
            url = f"{base}/pubs/{self.cfg.server}/{since.isoformat()}/{until.isoformat()}/{cursor}"
            payload = self._get_json(url, {})
            msgs = payload.get("messages") or [{}]
            msg = msgs[0] if isinstance(msgs, list) and msgs else {}
            status = str(msg.get("status", "ok")).lower()
            if status != "ok":
                if any(h in status for h in _NO_RESULT_HINTS):
                    break  # page 0: nothing at all (out is empty); later page: keep accumulated
                raise FetchError(f"bioRxiv pubs API returned status {msg.get('status')!r}")
            coll = payload.get("collection") or []
            for e in coll:
                b = normalize_doi(e.get("biorxiv_doi") or "")
                pd = normalize_doi(e.get("published_doi") or "")
                j = (e.get("published_journal") or "").strip()
                if b and pd and j:
                    out[b] = (pd, j)
            try:
                total = int(msg.get("total") or 0)
            except (TypeError, ValueError):
                total = 0
            cursor += len(coll)
            if not coll or cursor >= total:
                break
        return out
