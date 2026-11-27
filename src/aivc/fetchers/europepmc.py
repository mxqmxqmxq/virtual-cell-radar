"""Europe PMC REST search fetcher (journal articles and preprints)."""
from __future__ import annotations

import re
import time
from datetime import date, datetime
from typing import Any, Callable

import httpx

from ..models import Paper, make_paper_id
from ..textutil import biorxiv_id_from_doi, normalize_doi
from .base import FetchError, build_client, get_with_retry
from .links import extract_links

_TAG_RE = re.compile(r"<[^>]+>")


def _clean_text(s: Any) -> str:
    return " ".join(_TAG_RE.sub(" ", str(s or "")).split())


def _parse_date(s: Any) -> date | None:
    try:
        return datetime.strptime(str(s).strip()[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _as_list(x: Any) -> list:
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out


def result_to_paper(r: dict, today: date) -> Paper | None:
    title = _clean_text(r.get("title"))
    if not title:
        return None
    pmid = str(r.get("pmid") or "").strip() or None
    pmcid = str(r.get("pmcid") or "").strip() or None
    doi_raw = (r.get("doi") or "").strip()
    doi = normalize_doi(doi_raw) if doi_raw else None
    abstract = _clean_text(r.get("abstractText")) or None

    authors_raw = _as_list((r.get("authorList") or {}).get("author"))
    authors = [str(a.get("fullName")).strip() for a in authors_raw if a.get("fullName")]
    if not authors:
        authors = [a.strip().rstrip(".") for a in (r.get("authorString") or "").split(", ") if a.strip()]
    affs: list[str] = []
    for a in authors_raw:
        if a.get("affiliation"):
            affs.append(" ".join(str(a["affiliation"]).split()))
        det = (a.get("authorAffiliationDetailsList") or {}).get("authorAffiliation")
        for d in _as_list(det):
            if d.get("affiliation"):
                affs.append(" ".join(str(d["affiliation"]).split()))
    affs = _dedup(affs)

    venue = (((r.get("journalInfo") or {}).get("journal") or {}).get("title") or "").strip() or None
    cc = r.get("citedByCount")
    citation_count = int(cc) if isinstance(cc, (int, float)) or (isinstance(cc, str) and cc.isdigit()) else None
    src = r.get("source") or "MED"
    rid = r.get("id") or pmid or ""
    pdf_url = None
    for u in _as_list((r.get("fullTextUrlList") or {}).get("fullTextUrl")):
        if u.get("documentStyle") == "pdf" and u.get("url"):
            pdf_url = u["url"]
            break
    pub_types = [str(t) for t in _as_list((r.get("pubTypeList") or {}).get("pubType"))]
    is_preprint = src == "PPR" or "Preprint" in pub_types
    bid = biorxiv_id_from_doi(doi) if doi else None
    links = extract_links(abstract or "")
    lic = (r.get("license") or "").strip() or None
    return Paper(
        id=make_paper_id(biorxiv_id=bid, doi=doi, pmid=pmid, title=title),
        title=title,
        abstract=abstract,
        authors=authors,
        affiliations=affs,
        source="europepmc",
        sources=["europepmc"],
        venue=venue,
        published=_parse_date(r.get("firstPublicationDate")),
        url=f"https://europepmc.org/article/{src}/{rid}",
        pdf_url=pdf_url,
        doi=doi,
        pmid=pmid,
        pmcid=pmcid,
        biorxiv_id=bid,
        license=lic,
        citation_count=citation_count,
        published_venue=venue if (not is_preprint and venue) else None,
        published_doi=doi if (not is_preprint and venue and doi) else None,
        code_url=links.code_url,
        weights_url=links.weights_url,
        has_code=True if links.has_code else None,
        has_weights=True if links.has_weights else None,
        first_seen=today,
    )


class EuropePMCFetcher:
    name = "europepmc"

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

    def _get_json(self, params: dict) -> dict:
        url = self.cfg.base_url
        resp = get_with_retry(
            self.client, url, params,
            max_retries=self.http_cfg.max_retries, sleep=self.sleep,
        )
        try:
            data = resp.json()
        except ValueError as e:
            raise FetchError(f"invalid JSON from {url}") from e
        if not isinstance(data, dict):
            raise FetchError(f"unexpected JSON shape from {url}")
        return data

    def fetch(self, since: date, until: date) -> list[Paper]:
        today = self.today or date.today()
        out: dict[str, Paper] = {}
        first = True
        for q in self.cfg.queries:
            query = f"({q}) AND FIRST_PDATE:[{since.isoformat()} TO {until.isoformat()}]"
            cursor = "*"
            for _ in range(self.cfg.max_pages):
                if not first:
                    self.sleep(self.cfg.delay_seconds)
                first = False
                data = self._get_json({
                    "query": query, "format": "json", "resultType": "core",
                    "pageSize": self.cfg.page_size, "cursorMark": cursor,
                })
                results = (data.get("resultList") or {}).get("result") or []
                if not results:
                    break
                for r in results:
                    p = result_to_paper(r, today)
                    if p and p.id not in out:
                        out[p.id] = p
                nxt = data.get("nextCursorMark")
                if not nxt or nxt == cursor:
                    break
                cursor = nxt
        return list(out.values())
