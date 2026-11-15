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

    return paper.model_copy(
        update={
            "keyword_score": round(score, 2),
            "matched_keywords": matched,
            "circle": circle,
        }
    )


def filter_relevant(papers: list[Paper], cfg: AppConfig) -> list[Paper]:
    out = [apply_keywords(p, cfg) for p in papers]
    out = [
        p for p in out
        if p.circle != "none" and p.keyword_score >= cfg.keywords.min_keyword_score
    ]
    out.sort(key=lambda p: p.keyword_score, reverse=True)
    return out
