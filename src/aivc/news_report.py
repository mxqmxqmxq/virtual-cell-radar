"""新闻在日报/周报里的 Markdown 渲染（纯函数，返回行列表）。只含标题、来源、链接与 LLM 自写的中文字段。"""
from __future__ import annotations

from datetime import date

from .models import NewsItem
from .news import event_date

EVENT_CN = {
    "funding": "融资", "partnership": "合作", "m_and_a": "并购",
    "model_or_data_release": "模型/数据发布", "clinical": "临床进展", "people": "人事", "other": "其他",
}


def fmt_usd(x: float | None) -> str:
    if x is None:
        return "未披露"
    if x >= 1e9:
        return f"${x / 1e9:.1f}B"
    if x >= 1e6:
        return f"${x / 1e6:.0f}M"
    return f"${x / 1e3:.0f}K"


def _one(text: str | None) -> str:
    return " ".join((text or "").split())


def _cell(text: str | None) -> str:
    return _one(text).replace("|", "/")


def _type_cn(it: NewsItem) -> str:
    return EVENT_CN.get(it.event_type or "other", "其他")


def _links(it: NewsItem) -> str:
    srcs = it.sources or []
    if len(srcs) <= 1:
        name = srcs[0].name if srcs else it.source
        return f"{name} · [原文]({srcs[0].url if srcs else it.url})"
    return " · ".join(f"[{_one(s.name)}]({s.url})" for s in srcs)


def render_major_section(items: list[NewsItem]) -> list[str]:
    if not items:
        return []
    out = ["## 重大行业事件", ""]
    for it in items:
        parts = [_type_cn(it)]
        if it.companies:
            parts.append("、".join(it.companies))
        if it.amount_usd is not None:
            parts.append(fmt_usd(it.amount_usd))
        parts.append(it.source)
        parts.append(event_date(it).isoformat())
        parts.append(f"[原文]({it.url})")
        out.append(f"- **[重大] {_one(it.title)}** — " + " · ".join(parts))
        if it.significance_zh:
            out.append(f"  {_one(it.significance_zh)}")
    out.append("")
    return out


def render_weekly_news(items: list[NewsItem], start: date, end: date, cfg) -> list[str]:
    ncfg = cfg.pipeline.news
    from .news import cap_per_company_rolling, cap_per_company_week, publishable
    scored = publishable(items, ncfg.site_min_relevance, scored_only=True)
    scored = cap_per_company_week(scored, getattr(ncfg, "max_per_company_per_week", 0))
    # 滚动上限要看完整历史（与网站一致），再截取本周
    scored = cap_per_company_rolling(scored, getattr(ncfg, "max_per_company_per_30d", 0))
    week = [x for x in scored if start <= event_date(x) <= end]
    out = ["## 行业动态", ""]
    if not week:
        out += ["本周暂无已收录的行业动态。", ""]
        return out
    major = sorted((x for x in week if x.major), key=lambda x: (-(x.relevance or 0), x.id))
    if major:
        out += ["### 重大事件", ""]
        for it in major:
            tag = f"（{'、'.join(it.major_reasons)}）" if it.major_reasons else ""
            out.append(f"- **[重大] {_one(it.title)}**{tag} — {_type_cn(it)} · "
                       f"{'、'.join(it.companies) or '—'} · {event_date(it).isoformat()}")
            if it.significance_zh:
                out.append(f"  {_one(it.significance_zh)}")
        out.append("")
    top = sorted(week, key=lambda x: (-(x.relevance or 0), -event_date(x).toordinal(), x.id))
    top = top[: max(ncfg.weekly_top_n, 0)]
    out += ["### 本周重要事件", ""]
    for it in top:
        out.append(f"- **{_one(it.title)}** — {_type_cn(it)} · {'、'.join(it.companies) or '—'}"
                   f" · {event_date(it).isoformat()}")
        if it.summary_zh:
            out.append(f"  {_one(it.summary_zh)}")
        out.append(f"  来源：{_links(it)}")
    out.append("")
    fund = sorted((x for x in week if x.event_type == "funding"),
                  key=lambda x: (-(x.amount_usd or 0), x.id))
    out += ["### 融资汇总", ""]
    if not fund:
        out += ["本周无已收录的融资事件。", ""]
        return out
    out += ["| 公司 | 轮次 | 金额 | 投资方 | 来源 |", "| --- | --- | ---: | --- | --- |"]
    for it in fund:
        out.append("| " + " | ".join([
            _cell("、".join(it.companies)) or "—", _cell(it.round) or "—", fmt_usd(it.amount_usd),
            _cell("、".join(it.investors)) or "—", _cell(_links(it)),
        ]) + " |")
    known = [x.amount_usd for x in fund if x.amount_usd is not None]
    undisclosed = len(fund) - len(known)
    out.append(f"| **合计** | | **{fmt_usd(sum(known)) if known else '未披露'}** | "
               f"共 {len(fund)} 笔，未披露金额 {undisclosed} 笔 | |")
    out.append("")
    return out
