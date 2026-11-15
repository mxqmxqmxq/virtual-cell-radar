"""Deduplication and merging of Paper records."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from rapidfuzz import fuzz, process

from .models import Paper
from .textutil import biorxiv_id_from_doi, normalize_doi, normalize_title, strip_arxiv_version

_STRONG = ("arxiv", "biorxiv", "openreview")


def _strong_id(p: Paper, kind: str) -> str | None:
    if kind == "arxiv":
        return strip_arxiv_version(p.arxiv_id) if p.arxiv_id else None
    if kind == "biorxiv":
        return p.biorxiv_id or biorxiv_id_from_doi(p.doi)
    return p.openreview_id or None


def id_keys(p: Paper) -> set[str]:
    keys = {p.id, *p.alt_ids}
    if p.arxiv_id:
        keys.add(f"arxiv:{strip_arxiv_version(p.arxiv_id)}")
    bid = _strong_id(p, "biorxiv")
    if bid:
        keys.add(f"biorxiv:{bid}")
    if p.doi:
        keys.add(f"doi:{normalize_doi(p.doi)}")
    if p.openreview_id:
        keys.add(f"openreview:{p.openreview_id}")
    return keys


def _conflict(a: Paper, b: Paper) -> bool:
    for kind in _STRONG:
        x, y = _strong_id(a, kind), _strong_id(b, kind)
        if x and y and x != y:
            return True
    return False


def _author_overlap(a: Paper, b: Paper) -> bool:
    sa = {normalize_title(x) for x in a.authors if x}
    sb = {normalize_title(x) for x in b.authors if x}
    return bool(sa & sb)


class DedupIndex:
    def __init__(self, papers: Iterable[Paper] = (), title_threshold: int = 93):
        self.title_threshold = title_threshold
        self._by_key: dict[str, Paper] = {}
        self._by_title: dict[str, list[Paper]] = {}
        self._titles: list[str] | None = None
        for p in papers:
            self.add(p)

    def add(self, paper: Paper) -> None:
        for k in id_keys(paper):
            self._by_key[k] = paper
        nt = normalize_title(paper.title)
        if nt:
            lst = self._by_title.setdefault(nt, [])
            lst[:] = [q for q in lst if q.id != paper.id]
            lst.append(paper)
            self._titles = None

    def _is_dup_by_title(self, paper: Paper, cand: Paper, exact: bool) -> bool:
        if _conflict(paper, cand):
            return exact and _author_overlap(paper, cand)
        return True

    def find(self, paper: Paper) -> Paper | None:
        for k in sorted(id_keys(paper)):
            hit = self._by_key.get(k)
            if hit is not None:
                return hit
        nt = normalize_title(paper.title)
        if not nt:
            return None
        for cand in self._by_title.get(nt, []):
            if self._is_dup_by_title(paper, cand, exact=True):
                return cand
        if len(nt) < 20:
            return None
        if self._titles is None:
            self._titles = list(self._by_title)
        for title, _score, _i in process.extract(
            nt, self._titles, scorer=fuzz.ratio, score_cutoff=self.title_threshold, limit=5
        ):
            for cand in self._by_title[title]:
                if self._is_dup_by_title(paper, cand, exact=False):
                    return cand
        return None


def _union(a: list, b: list) -> list:
    out = list(a)
    for x in b:
        if x not in out:
            out.append(x)
    return out


def _ver_num(v: str | None) -> int | None:
    if not v:
        return None
    m = re.search(r"\d+", v)
    return int(m.group()) if m else None


def _is_newer(existing: Paper, incoming: Paper) -> bool:
    ev, iv = _ver_num(existing.version), _ver_num(incoming.version)
    if ev is not None and iv is not None and iv != ev:
        return iv > ev
    if incoming.updated and (not existing.updated or incoming.updated > existing.updated):
        return True
    return False


_FILL = (
    "abstract", "doi", "license", "arxiv_id", "biorxiv_id", "openreview_id", "url", "pdf_url",
    "venue", "published", "updated", "version", "code_url", "has_code", "has_weights",
)
_LISTS = ("affiliations", "categories", "keywords", "tags")


def merge_papers(existing: Paper, incoming: Paper) -> Paper:
    upd: dict = {}
    for f in _FILL:
        if getattr(existing, f) in (None, "") and getattr(incoming, f) not in (None, ""):
            upd[f] = getattr(incoming, f)
    for f in _LISTS:
        merged = _union(getattr(existing, f), getattr(incoming, f))
        if merged != getattr(existing, f):
            upd[f] = merged
    if not existing.authors and incoming.authors:
        upd["authors"] = list(incoming.authors)

    if incoming.source == existing.source and _is_newer(existing, incoming):
        for f in ("title", "abstract", "updated", "version", "url", "pdf_url"):
            v = getattr(incoming, f)
            if v not in (None, ""):
                upd[f] = v

    alt_ids = _union(existing.alt_ids, incoming.alt_ids)
    if incoming.id != existing.id:
        alt_ids = _union(alt_ids, [incoming.id])
    alt_ids = [x for x in alt_ids if x != existing.id]
    if alt_ids != existing.alt_ids:
        upd["alt_ids"] = alt_ids
    alt_urls = _union(existing.alt_urls, incoming.alt_urls)
    if incoming.id != existing.id and incoming.url and incoming.url != existing.url:
        alt_urls = _union(alt_urls, [incoming.url])
    if alt_urls != existing.alt_urls:
        upd["alt_urls"] = alt_urls

    if incoming.keyword_score > 0:
        for f in ("keyword_score", "matched_keywords", "circle"):
            upd[f] = getattr(incoming, f)

    # 摘要新补上或被新版本替换：旧打分作废，交给流水线重新打分
    if "abstract" in upd and upd["abstract"] != existing.abstract and existing.llm_status != "unscored":
        upd.update(llm_status="unscored", llm=None, final_score=None, must_read=False,
                   llm_skip_reason="abstract_changed")
    elif "abstract" in upd and existing.llm_status == "unscored" and existing.llm_skip_reason == "no_abstract":
        upd["llm_skip_reason"] = None

    return existing.model_copy(update=upd)


@dataclass
class DedupResult:
    new: list[Paper] = field(default_factory=list)
    updated: list[Paper] = field(default_factory=list)
    n_duplicates: int = 0


def dedup_batch(
    incoming: list[Paper], existing: list[Paper], title_threshold: int = 93
) -> DedupResult:
    index = DedupIndex(existing, title_threshold)
    new: list[Paper] = []
    new_pos: dict[str, int] = {}
    touched: dict[str, Paper] = {}  # existing id -> latest merged
    originals: dict[str, dict] = {}
    n_dup = 0
    for p in incoming:
        hit = index.find(p)
        if hit is None:
            new_pos[p.id] = len(new)
            new.append(p)
            index.add(p)
            continue
        n_dup += 1
        merged = merge_papers(hit, p)
        if hit.id in new_pos:
            new[new_pos[hit.id]] = merged
        else:
            originals.setdefault(hit.id, hit.model_dump())
            touched[hit.id] = merged
        index.add(merged)
    updated = [m for i, m in touched.items() if m.model_dump() != originals[i]]
    return DedupResult(new=new, updated=updated, n_duplicates=n_dup)
