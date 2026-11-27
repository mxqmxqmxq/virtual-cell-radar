"""Keyword pre-filter: decides circle (core/peripheral/none) and keyword_score."""
from __future__ import annotations

import re

from .config import AppConfig, KeywordsConfig
from .models import Paper
from .termmatch import compile_term

_compile_term = compile_term


class _Compiled:
    def __init__(self, kw: KeywordsConfig):
        self.kw = kw  # keep a reference so id() stays unique
        self.groups = [
            (
                g,
                [(t, p) for t in g.terms if (p := _compile_term(t))],
                [
                    [(t, p) for t in sub if (p := _compile_term(t))]
                    for sub in g.all_of
                ],
            )
            for g in kw.groups
        ]
        self.exclude = [(t, p) for t in kw.exclude_terms if (p := _compile_term(t))]
        self.soft = [(t, p) for t in kw.soft_exclude_terms if (p := _compile_term(t))]


_CACHE: dict[int, _Compiled] = {}


def _compiled(cfg: AppConfig) -> _Compiled:
    key = id(cfg.keywords)
    c = _CACHE.get(key)
    if c is None or c.kw is not cfg.keywords:
        c = _Compiled(cfg.keywords)
        _CACHE[key] = c
    return c


def apply_keywords(paper: Paper, cfg: AppConfig) -> Paper:
    c = _compiled(cfg)
    title = paper.title or ""
    body = " \n ".join([title, paper.abstract or "", " ; ".join(paper.keywords)])

    score = 0.0
    matched: list[str] = []
    core_hit = peripheral_hit = False
    for group, terms, combos in c.groups:
        hit_any = False
        in_title = False
        for term, pat in terms:
            if pat.search(body):
                hit_any = True
                if term not in matched:
                    matched.append(term)
                if pat.search(title):
                    in_title = True
        if combos and all(sub for sub in combos):
            hits = [[(t, p) for t, p in sub if p.search(body)] for sub in combos]
            if all(hits):
                hit_any = True
                for sub in hits:
                    t, p = sub[0]
                    label = f"{group.name}:{t}"
                    if label not in matched:
                        matched.append(label)
                    if any(p2.search(title) for _, p2 in sub):
                        in_title = True
        if hit_any:
            score += group.weight * (1.5 if in_title else 1.0)
            if group.circle == "core":
                core_hit = True
            else:
                peripheral_hit = True

    circle = "core" if core_hit else "peripheral" if peripheral_hit else "none"
    if not core_hit and any(p.search(body) for _, p in c.exclude):
        circle, score = "none", 0.0
    elif circle != "core" and c.soft:
        hits = [t for t, p in c.soft if p.search(body)]
        if hits:
            score = max(0.0, score - c.kw.soft_exclude_penalty)
            matched.extend(f"soft_exclude:{t}" for t in hits)
            if score <= 0:
                circle, score = "none", 0.0

    return paper.model_copy(
        update={
            "keyword_score": round(score, 2),
            "matched_keywords": matched,
            "circle": circle,
        }
    )


def rank_score(p: Paper) -> float:
    return (
        p.keyword_score
        + p.semantic_score * 5.0
        + (0.5 if p.hf_upvotes else 0.0)
        + (0.5 if p.published_venue else 0.0)
    )


def classify(p: Paper, cfg: AppConfig) -> Paper | None:
    """混合判定（输入已带 keyword_score / circle / semantic_score）。

    关键词达标 -> 原样保留；否则语义分达标且未命中硬排除词 -> 以 peripheral 捞回并追加
    "semantic:rescue"；其余返回 None。filter_relevant 与 run_reindex 共用。
    """
    c = _compiled(cfg)
    if p.circle != "none" and p.keyword_score >= cfg.keywords.min_keyword_score:
        return p
    if p.semantic_score < cfg.keywords.semantic_rescue_threshold:
        return None
    body = " \n ".join([p.title or "", p.abstract or "", " ; ".join(p.keywords)])
    if any(pat.search(body) for _, pat in c.exclude):
        return None
    matched = list(p.matched_keywords)
    if "semantic:rescue" not in matched:
        matched.append("semantic:rescue")
    return p.model_copy(update={"circle": "peripheral", "matched_keywords": matched})


def filter_relevant(
    papers: list[Paper], cfg: AppConfig, exemplars: list[Paper] | None = None
) -> list[Paper]:
    from .semantic import score_semantic

    annotated = [apply_keywords(p, cfg) for p in papers]
    scored = score_semantic(annotated, cfg, exemplars)
    out = [r for p in scored if (r := classify(p, cfg)) is not None]
    out.sort(key=lambda p: (-rank_score(p), p.id))
    return out
