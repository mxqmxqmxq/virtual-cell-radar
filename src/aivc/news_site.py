"""新闻与公司档案的静态站导出（site/data/news.json、companies.json）。"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from .config import AppConfig
from .models import NewsItem, Paper
from .news import event_date

EVENT_TYPE_LABELS: list[tuple[str, str]] = [
    ("funding", "融资"), ("partnership", "合作"), ("m_and_a", "并购"),
    ("model_or_data_release", "模型或数据发布"), ("clinical", "临床进展"),
    ("people", "重要人事"), ("other", "其他"),
]
COMPANY_CATEGORY_LABELS: dict[str, str] = {
    "biotech": "生物科技公司", "pharma": "制药公司", "big_tech": "科技巨头",
    "philanthropy": "公益/基金会", "research_institute": "研究机构", "other": "其他",
}
NEWS_ITEM_KEYS = (
    "id", "title", "url", "source", "sources", "published", "date", "week", "quarter",
    "companies", "company_ids", "event_type", "amount_usd", "round", "investors",
    "summary_zh", "significance_zh", "relevance", "scored", "major", "major_reasons", "first_seen",
)


def _week_id(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _quarter(d: date) -> str:
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


def _is_scored(it: NewsItem) -> bool:
    return it.llm_status == "scored" and it.relevance is not None


def _included(items: list[NewsItem], cfg: AppConfig) -> list[NewsItem]:
    """被 news.json 收录的条目：已打分且 relevance 达标，或未打分（前端默认隐藏）。"""
    from .news import cap_per_company_rolling, cap_per_company_week, publishable
    # 发布日期不明的条目不展示、不进融资统计（与周报共用 publishable）
    kept = publishable(items, cfg.pipeline.news.site_min_relevance)
    ncfg = cfg.pipeline.news
    kept = cap_per_company_week(kept, getattr(ncfg, "max_per_company_per_week", 0))
    return cap_per_company_rolling(kept, getattr(ncfg, "max_per_company_per_30d", 0))


def _norm(s: str) -> str:
    return s.strip().casefold()


def _company_index(cfg: AppConfig) -> dict[str, str]:
    idx: dict[str, str] = {}
    for c in cfg.companies.companies:
        for n in [c.name, *c.aliases]:
            if n.strip():
                idx.setdefault(_norm(n), c.id)
    return idx


def _company_ids(it: NewsItem, idx: dict[str, str]) -> list[str]:
    out: list[str] = []
    for n in it.companies:
        cid = idx.get(_norm(n))
        if cid and cid not in out:
            out.append(cid)
    return out


def export_news(items: list[NewsItem], cfg: AppConfig, *, generated_at: str) -> dict[str, Any]:
    idx = _company_index(cfg)
    kept = _included(items, cfg)
    rows: list[dict[str, Any]] = []
    for it in kept:
        d = event_date(it)
        rows.append({
            "id": it.id, "title": it.title, "url": it.url, "source": it.source,
            "sources": [{"name": s.name, "url": s.url} for s in it.sources],
            "published": it.published.isoformat() if it.published else None,
            "date": d.isoformat(), "week": _week_id(d), "quarter": _quarter(d),
            "companies": list(it.companies), "company_ids": _company_ids(it, idx),
            "event_type": it.event_type, "amount_usd": it.amount_usd, "round": it.round,
            "investors": list(it.investors), "summary_zh": it.summary_zh,
            "significance_zh": it.significance_zh, "relevance": it.relevance,
            "scored": _is_scored(it), "major": bool(it.major), "major_reasons": list(it.major_reasons),
            "first_seen": it.first_seen.isoformat(),
        })
    rows.sort(key=lambda r: (_neg_date(r["date"]), -(r["relevance"] if r["relevance"] is not None else -1), r["id"]))

    wk: dict[str, int] = {}
    for r in rows:
        wk[r["week"]] = wk.get(r["week"], 0) + 1
    weeks = []
    for wid in sorted(wk, reverse=True):
        y, w = wid.split("-W")
        start = date.fromisocalendar(int(y), int(w), 1)
        weeks.append({"id": wid, "start": start.isoformat(), "end": (start + timedelta(days=6)).isoformat(),
                      "count": wk[wid]})

    byq: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r["event_type"] == "funding" and r["scored"]:
            byq.setdefault(r["quarter"], []).append(r)
    fq = []
    for q in sorted(byq):
        evs = byq[q]
        disclosed = [e for e in evs if e["amount_usd"] is not None]
        top = sorted(disclosed, key=lambda e: (-e["amount_usd"], e["id"]))[:3]
        fq.append({
            "quarter": q,
            "total_usd": sum(e["amount_usd"] for e in disclosed),
            "n_events": len(evs),
            "n_undisclosed": len(evs) - len(disclosed),
            "top": [{"id": e["id"], "title": e["title"], "amount_usd": e["amount_usd"],
                     "companies": list(e["companies"])} for e in top],
        })
    return {
        "generated_at": generated_at, "count": len(rows),
        "event_types": [{"id": i, "label": l} for i, l in EVENT_TYPE_LABELS],
        "items": rows, "weeks": weeks, "funding_quarters": fq,
    }


def _neg_date(s: str) -> int:
    return -date.fromisoformat(s).toordinal()


def _term_re(term: str) -> re.Pattern[str] | None:
    term = term.strip()
    if not term:
        return None
    return re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)")  # 大小写敏感


def export_companies(items: list[NewsItem], papers: list[Paper], cfg: AppConfig, *,
                     generated_at: str) -> dict[str, Any]:
    idx = _company_index(cfg)
    kept = [it for it in _included(items, cfg) if _is_scored(it)]
    by_co: dict[str, list[NewsItem]] = {}
    for it in kept:
        for cid in _company_ids(it, idx):
            by_co.setdefault(cid, []).append(it)

    texts: list[tuple[Paper, str]] = []
    for p in sorted(papers, key=lambda p: ((p.published or p.first_seen).toordinal(), p.id), reverse=True):
        texts.append((p, "\n".join([p.title or "", p.abstract or "", *p.affiliations])))

    companies: list[dict[str, Any]] = []
    paper_companies: dict[str, list[str]] = {}
    for c in cfg.companies.companies:
        news = sorted(by_co.get(c.id, []), key=lambda x: (-event_date(x).toordinal(), x.id))
        funding = [x for x in news if x.event_type == "funding" and x.amount_usd is not None]
        pats = [r for r in (_term_re(t) for t in [*c.paper_terms, *c.models]) if r]
        pids: list[str] = []
        if pats:
            for p, txt in texts:
                if any(r.search(txt) for r in pats):
                    pids.append(p.id)
                    paper_companies.setdefault(p.id, []).append(c.id)
        companies.append({
            "id": c.id, "name": c.name, "aliases": list(c.aliases), "url": c.url,
            "category": c.category, "tier": c.tier, "models": list(c.models),
            "n_news": len(news), "news_ids": [x.id for x in news],
            "funding_total_usd": sum(x.amount_usd for x in funding),
            "funding_events": [{"id": x.id, "date": event_date(x).isoformat(),
                                "amount_usd": x.amount_usd, "round": x.round} for x in funding],
            "last_event_date": event_date(news[0]).isoformat() if news else None,
            "paper_ids": pids, "n_papers": len(pids),
        })
    return {
        "generated_at": generated_at,
        "categories": dict(COMPANY_CATEGORY_LABELS),
        "companies": companies,
        "paper_companies": paper_companies,
    }
