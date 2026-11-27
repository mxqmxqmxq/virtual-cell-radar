"""Paper enrichment: offline (zero network) and via OpenAlex (explicit CLI call)."""
from __future__ import annotations

import os
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx
import yaml

from .config import AppConfig
from .fetchers.base import FetchError, build_client, get_with_retry
from .fetchers.links import extract_links
from .fetchers.openalex import work_signals
from .models import Paper
from .termmatch import compile_term
from .textutil import normalize_doi

_PAREN = re.compile(r"[(（]([^()（）]*)[)）]")
BATCH_SIZE = 50
MAX_URL_LEN = 1800  # 保守的 URL 长度上限（编码后）


def load_lab_names(data_dir: Path) -> list[str]:
    """Lab names (and parenthesised institution aliases) from resources/labs.yaml."""
    path = Path(data_dir) / "resources" / "labs.yaml"
    if not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return []
    out: list[str] = []

    def add(s: str) -> None:
        s = s.strip()
        if s and s not in out:
            out.append(s)

    for r in data.get("resources") or []:
        if not isinstance(r, dict) or r.get("kind") != "lab":
            continue
        name = str(r.get("name") or "")
        add(_PAREN.sub("", name))
        for alias in _PAREN.findall(name):
            add(alias)
    return out


def _union(a: list[str], b: list[str]) -> list[str]:
    return sorted(set(a) | set(b))


def enrich_offline(papers: list[Paper], cfg: AppConfig, labs: list[str] | None = None) -> list[Paper]:
    """Zero-network enrichment. Returns a new list; unchanged papers are returned as-is."""
    aff_patterns: list[tuple[str, list[re.Pattern[str]]]] = []
    text_patterns: list[tuple[str, list[re.Pattern[str]]]] = []
    for c in cfg.companies.companies:
        pats = [p for t in [c.name, *c.aliases] if (p := compile_term(t))]
        if pats:
            aff_patterns.append((c.name, pats))
        tp = [p for t in c.paper_terms if (p := compile_term(t))]
        if tp:
            text_patterns.append((c.name, tp))
    lab_patterns = [(n, p) for n in (labs or []) if (p := compile_term(n))]

    out: list[Paper] = []
    for p in papers:
        update: dict[str, Any] = {}
        aff_text = " ; ".join(p.affiliations)
        body = "\n".join([p.title or "", p.abstract or ""])  # 作者名不参与匹配
        found: set[str] = set()
        if aff_text:
            for name, pats in aff_patterns:
                if any(x.search(aff_text) for x in pats):
                    found.add(name)
            for name, pat in lab_patterns:
                if pat.search(aff_text):
                    found.add(name)
        orgs = _union(p.matched_orgs, list(found))
        if orgs != p.matched_orgs:
            update["matched_orgs"] = orgs
        mentioned: set[str] = set()
        for name, pats in text_patterns:
            if name not in orgs and any(x.search(body) for x in pats):
                mentioned.add(name)
        ment = _union(p.mentioned_orgs, list(mentioned))
        if ment != p.mentioned_orgs:
            update["mentioned_orgs"] = ment

        if not p.code_url or not p.weights_url:
            ex = extract_links(p.abstract)
            if not p.code_url and ex.code_url:
                update["code_url"] = ex.code_url
                if ex.has_code:
                    update["has_code"] = True
            if not p.weights_url and ex.weights_url:
                update["weights_url"] = ex.weights_url
                if ex.has_weights:
                    update["has_weights"] = True

        if p.source not in p.sources:
            update["sources"] = [*p.sources, p.source]
        out.append(p.model_copy(update=update) if update else p)
    return out


@dataclass
class EnrichResult:
    papers: list[Paper]
    n_requests: int = 0
    n_matched: int = 0
    n_changed: int = 0
    n_batches: int = 0
    n_failed_batches: int = 0
    notes: list[str] = field(default_factory=list)


def _refresh(p: Paper, sig: dict) -> Paper:
    update: dict[str, Any] = {}
    cc = sig.get("citation_count")
    if isinstance(cc, int) and cc > (p.citation_count or 0):
        update["citation_count"] = cc
    affs = list(p.affiliations)
    for a in sig.get("affiliations") or []:
        if a not in affs:
            affs.append(a)
    if affs != p.affiliations:
        update["affiliations"] = affs
    if not p.openalex_id and sig.get("openalex_id"):
        update["openalex_id"] = sig["openalex_id"]
    if not p.pmid and sig.get("pmid"):
        update["pmid"] = sig["pmid"]
    if sig.get("is_journal_article"):
        if not p.published_venue and sig.get("venue"):
            update["published_venue"] = sig["venue"]
        if not p.published_doi and sig.get("doi"):
            update["published_doi"] = sig["doi"]
    return p.model_copy(update=update) if update else p


def enrich_via_openalex(
    papers: list[Paper],
    cfg: AppConfig,
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    limit: int | None = None,
    dry_run: bool = False,
    api_key: str | None = None,
) -> EnrichResult:
    oa = cfg.sources.openalex
    res = EnrichResult(papers=list(papers))
    idx = [i for i, p in enumerate(papers) if p.doi]
    if limit is not None:
        idx = idx[: max(limit, 0)]
    if not idx:
        res.notes.append("没有可查询的 DOI")
        return res
    if api_key is None and oa.api_key_env:
        api_key = os.environ.get(oa.api_key_env) or None
    own_client = client is None
    client = client or build_client(cfg.sources.http)
    def build_params(by_doi: dict[str, list[int]]) -> dict[str, Any]:
        params: dict[str, Any] = {
            "filter": "doi:" + "|".join(f"https://doi.org/{d}" for d in by_doi),
            "per-page": BATCH_SIZE,
        }
        if oa.mailto:
            params["mailto"] = oa.mailto
        if api_key:
            params["api_key"] = api_key
        return params

    def url_len(by_doi: dict[str, list[int]]) -> int:
        return len(oa.base_url) + 1 + len(urllib.parse.urlencode(build_params(by_doi)))

    # 按编码后 URL 长度与条数切批
    batches: list[dict[str, list[int]]] = []
    cur: dict[str, list[int]] = {}
    for i in idx:
        d = normalize_doi(papers[i].doi or "")
        if d in cur:
            cur[d].append(i)
            continue
        trial = {**cur, d: [i]}
        if cur and (len(trial) > BATCH_SIZE or url_len(trial) > MAX_URL_LEN):
            batches.append(cur)
            cur = {d: [i]}
        else:
            cur = trial
    if cur:
        batches.append(cur)

    state = {"first": True}

    def run_batch(by_doi: dict[str, list[int]], label: str) -> bool:
        if not state["first"]:
            sleep(oa.delay_seconds)
        state["first"] = False
        res.n_requests += 1
        try:
            resp = get_with_retry(client, oa.base_url, build_params(by_doi),
                                  max_retries=cfg.sources.http.max_retries, sleep=sleep)
            data = resp.json()
        except (FetchError, ValueError) as e:
            if "HTTP 414" in str(e):
                keys = list(by_doi)
                if len(keys) > 1:
                    mid = len(keys) // 2
                    ok1 = run_batch({k: by_doi[k] for k in keys[:mid]}, label + "a")
                    ok2 = run_batch({k: by_doi[k] for k in keys[mid:]}, label + "b")
                    return ok1 and ok2
            res.notes.append(f"OpenAlex 批次 {label} 失败：{e}")
            return False
        for w in (data.get("results") or []) if isinstance(data, dict) else []:
            sig = work_signals(w)
            for i in by_doi.get(normalize_doi(sig.get("doi") or ""), []):
                res.n_matched += 1
                new = _refresh(res.papers[i], sig)
                if new is not res.papers[i]:
                    res.n_changed += 1
                    if not dry_run:
                        res.papers[i] = new
        return True

    try:
        for b, by_doi in enumerate(batches):
            res.n_batches += 1
            if not run_batch(by_doi, str(b + 1)):
                res.n_failed_batches += 1
    finally:
        if own_client:
            client.close()
    if dry_run:
        res.notes.append(f"dry-run：将变更 {res.n_changed} 篇（未写入）")
    return res
