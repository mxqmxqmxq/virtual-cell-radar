"""OpenAlex works fetcher (https://api.openalex.org/works, CC0 metadata).

Two modes, both run when configured (results are id-deduplicated):
  A) ``journal_issns`` set: filter by journal ISSNs + date window, optionally
     narrowed by ``domain_terms`` via the ``search`` parameter.
  B) one request series per entry of ``queries``.

NOTE (to verify with a real smoke run): boolean ``search`` syntax
(``"a" OR "b"``), the ``primary_location.source.issn`` filter key, and
``cursor=*`` / ``meta.next_cursor`` paging are assumed from the public docs.
"""
from __future__ import annotations

import logging
import os
import re
import time
from urllib.parse import urlsplit
from datetime import date
from typing import Any, Callable

import httpx

from ..models import Paper, make_paper_id
from ..textutil import biorxiv_id_from_doi, is_valid_doi, normalize_doi, strip_arxiv_version
from .base import FetchError, build_client, get_with_retry
from .links import extract_links

log = logging.getLogger("aivc.fetchers.openalex")

_ARXIV_PATH_RE = re.compile(r"^/(?:abs|pdf)/([A-Za-z0-9.\-/]+?)(?:v\d+)?(?:\.pdf)?/?$", re.IGNORECASE)
_ARXIV_HOSTS = {"arxiv.org"}


def reconstruct_abstract(inv: dict | None) -> str | None:
    if not inv or not isinstance(inv, dict):
        return None
    pairs: list[tuple[int, str]] = []
    for word, positions in inv.items():
        for pos in positions or []:
            try:
                pairs.append((int(pos), str(word)))
            except (TypeError, ValueError):
                continue
    if not pairs:
        return None
    pairs.sort(key=lambda x: x[0])
    text = " ".join(w for _, w in pairs).strip()
    return text or None


def short_openalex_id(url_or_id: str | None) -> str | None:
    if not url_or_id:
        return None
    s = str(url_or_id).strip().rstrip("/")
    s = s.rsplit("/", 1)[-1]
    return s or None


def _source(work: dict) -> dict:
    loc = work.get("primary_location") or {}
    return loc.get("source") or {}


def _arxiv_from_locations(work: dict) -> str | None:
    locs = list(work.get("locations") or [])
    if work.get("primary_location"):
        locs.insert(0, work["primary_location"])
    for loc in locs:
        for key in ("landing_page_url", "pdf_url"):
            url = ((loc or {}).get(key) or "").strip()
            if not url:
                continue
            try:
                parts = urlsplit(url)
            except ValueError:
                continue
            host = (parts.hostname or "").lower()
            for pre in ("www.", "export."):
                if host.startswith(pre):
                    host = host[len(pre):]
            if parts.scheme not in ("http", "https") or host not in _ARXIV_HOSTS:
                continue
            m = _ARXIV_PATH_RE.match(parts.path)
            if m:
                return strip_arxiv_version(m.group(1))
    return None


def work_signals(work: dict) -> dict:
    src = _source(work)
    loc = work.get("primary_location") or {}
    venue_type = src.get("type")
    ids = work.get("ids") or {}
    pmid_raw = ids.get("pmid") or ""
    pmid_digits = "".join(re.findall(r"\d+", str(pmid_raw).rstrip("/").rsplit("/", 1)[-1])) or None
    doi_raw = work.get("doi") or ids.get("doi")
    doi_norm = normalize_doi(doi_raw) if doi_raw else None
    doi = doi_norm if is_valid_doi(doi_norm) else None
    affs: list[str] = []
    for a in work.get("authorships") or []:
        for inst in (a or {}).get("institutions") or []:
            n = (inst or {}).get("display_name")
            if n and n not in affs:
                affs.append(n)
    return {
        "openalex_id": short_openalex_id(work.get("id")),
        "doi": doi or None,
        "pmid": pmid_digits,
        "venue": src.get("display_name"),
        "venue_type": venue_type,
        "citation_count": work.get("cited_by_count"),
        "affiliations": affs,
        "license": loc.get("license"),
        "is_journal_article": work.get("type") == "article" and venue_type == "journal",
        "arxiv_id": _arxiv_from_locations(work),
    }


def _parse_date(s: Any) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def work_to_paper(work: dict, today: date) -> Paper | None:
    title = " ".join((work.get("display_name") or work.get("title") or "").split())
    if not title:
        return None
    sig = work_signals(work)
    doi = sig["doi"]
    oid = sig["openalex_id"]
    abstract = reconstruct_abstract(work.get("abstract_inverted_index"))
    links = extract_links(abstract)
    loc = work.get("primary_location") or {}
    oa = work.get("best_oa_location") or {}
    url = loc.get("landing_page_url") or (f"https://doi.org/{doi}" if doi else None) or work.get("id")
    pdf = oa.get("pdf_url") or (work.get("open_access") or {}).get("oa_url")
    kws: list[str] = []
    for item in (work.get("keywords") or []) + (work.get("concepts") or []):
        n = (item or {}).get("display_name")
        if n and n not in kws:
            kws.append(n)
    authors = [
        n for a in work.get("authorships") or []
        if (n := ((a or {}).get("author") or {}).get("display_name"))
    ]
    journal = sig["is_journal_article"]
    return Paper(
        id=make_paper_id(
            arxiv_id=sig["arxiv_id"], biorxiv_id=biorxiv_id_from_doi(doi), doi=doi,
            pmid=sig["pmid"], openalex_id=oid, title=title,
        ),
        title=title,
        abstract=abstract,
        authors=authors,
        affiliations=sig["affiliations"],
        source="openalex",
        sources=["openalex"],
        venue=sig["venue"],
        published=_parse_date(work.get("publication_date")),
        url=url,
        pdf_url=pdf,
        doi=doi,
        pmid=sig["pmid"],
        openalex_id=oid,
        arxiv_id=sig["arxiv_id"],
        biorxiv_id=biorxiv_id_from_doi(doi),
        license=sig["license"],
        citation_count=sig["citation_count"],
        published_venue=sig["venue"] if journal else None,
        published_doi=doi if journal else None,
        code_url=links.code_url,
        weights_url=links.weights_url,
        has_code=True if links.has_code else None,
        has_weights=True if links.has_weights else None,
        keywords=kws[:10],
        first_seen=today,
    )


def _richness(p: Paper) -> tuple:
    return (bool(p.abstract), bool(p.doi), p.citation_count or 0, len(p.authors), len(p.keywords))


def _prefer(cur: Paper, new: Paper) -> Paper:
    """Pick the better of two records sharing one id (journal version first)."""
    if bool(new.published_venue) != bool(cur.published_venue):
        return new if new.published_venue else cur
    return new if _richness(new) > _richness(cur) else cur


MAX_ISSNS_PER_QUERY = 15


class OpenAlexFetcher:
    name = "openalex"

    def __init__(
        self,
        cfg: Any,
        http_cfg: Any,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        today: date | None = None,
        api_key: str | None = None,
    ):
        self.cfg = cfg
        self.http_cfg = http_cfg
        self.client = client or build_client(http_cfg)
        self.sleep = sleep
        self.today = today
        if api_key is None:
            env = getattr(cfg, "api_key_env", "") or ""
            api_key = os.environ.get(env) if env else None
        self.api_key = api_key or None

    def _get_json(self, params: dict) -> dict:
        resp = get_with_retry(
            self.client, self.cfg.base_url, params,
            max_retries=self.http_cfg.max_retries, sleep=self.sleep,
        )
        try:
            data = resp.json()
        except ValueError as e:
            raise FetchError(f"invalid JSON from {self.cfg.base_url}") from e
        if not isinstance(data, dict):
            raise FetchError(f"unexpected JSON shape from {self.cfg.base_url}")
        return data

    def _request_specs(self, since: date, until: date) -> list[dict]:
        window = f"from_publication_date:{since.isoformat()},to_publication_date:{until.isoformat()}"
        specs: list[dict] = []
        issns: list[str] = []
        for j in self.cfg.journal_issns:
            for i in j.issn:
                if i not in issns:
                    issns.append(i)
        if issns:
            search = " OR ".join(f'"{t}"' for t in self.cfg.domain_terms) if self.cfg.domain_terms else None
            for i in range(0, len(issns), MAX_ISSNS_PER_QUERY):
                chunk = issns[i : i + MAX_ISSNS_PER_QUERY]
                p = {"filter": f"primary_location.source.issn:{'|'.join(chunk)},{window}"}
                if search:
                    p["search"] = search
                specs.append(p)
        for q in self.cfg.queries:
            specs.append({"search": q, "filter": window})
        return specs

    def fetch(self, since: date, until: date) -> list[Paper]:
        self.last_incomplete = False
        self.last_warning: str | None = None
        today = self.today or date.today()
        best: dict[str, Paper] = {}
        first = True
        for spec in self._request_specs(since, until):
            cursor: str | None = "*"
            for _ in range(self.cfg.max_pages):
                if not first:
                    self.sleep(self.cfg.delay_seconds)
                first = False
                params = dict(spec)
                params["per-page"] = self.cfg.per_page
                params["cursor"] = cursor
                if self.cfg.mailto:
                    params["mailto"] = self.cfg.mailto
                if self.api_key:
                    params["api_key"] = self.api_key
                try:
                    data = self._get_json(params)
                except FetchError as e:
                    if cursor != "*" and not self.api_key and "HTTP 401" in str(e):
                        log.warning("OpenAlex 游标翻页需要免费的 OPENALEX_API_KEY，保留当前已获取结果")
                        self.last_incomplete = True
                        self.last_warning = "OpenAlex 游标翻页需要 OPENALEX_API_KEY，仅保留首页结果"
                        break
                    raise
                results = data.get("results") or []
                for w in results:
                    p = work_to_paper(w, today)
                    if p:
                        best[p.id] = _prefer(best[p.id], p) if p.id in best else p
                cursor = (data.get("meta") or {}).get("next_cursor")
                if not results or not cursor:
                    break
            else:
                if cursor:
                    self.last_incomplete = True
                    self.last_warning = self.last_warning or f"OpenAlex 达到 max_pages={self.cfg.max_pages} 仍有后续页，结果不完整"
        return list(best.values())
