"""离线统计：库内论文概况、LLM 积压、关键词误报率、水位线健康（只读，零网络）。"""
from __future__ import annotations

from collections import Counter
from datetime import date

from .config import AppConfig
from .llm import _is_top_venue
from .models import Paper
from .semantic import build_prototype, score_text, select_exemplars, _paper_text
from .state import FetchState

MIN_FP_HITS = 3
_SKIP_KW_PREFIX = ("semantic:", "soft_exclude:")


SOURCE_ORDER = ("arxiv", "biorxiv", "medrxiv", "openalex", "europepmc", "hf_papers", "openreview")
DIRECT_JOURNAL_SOURCES = frozenset({"openalex", "europepmc", "journal", "pubmed"})


def enabled_source_ids(cfg: AppConfig) -> list[str]:
    """按固定顺序返回已启用的论文源 id（site/stats 共用，0 篇也要列出）。"""
    out = []
    for sid in SOURCE_ORDER:
        sc = getattr(cfg.sources, sid, None)
        if sc is not None and getattr(sc, "enabled", False):
            out.append(sid)
    return out


def _ratio(a: int, b: int) -> float:
    return round(a / b, 4) if b else 0.0


def _quantile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    pos = q * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (pos - lo), 4)


def _is_scored(p: Paper) -> bool:
    return p.llm_status == "scored" and p.llm is not None


def _semantic_calibration(papers: list[Paper], cfg: AppConfig) -> dict:
    """现场计算（不落盘）：按 LLM circle 分组的语义分分位数，用于核对 semantic_rescue_threshold。"""
    groups: dict[str, list[float]] = {"core": [], "peripheral": [], "none": [], "unscored": []}
    thr = cfg.keywords.semantic_rescue_threshold
    if papers:
        proto = build_prototype(cfg, select_exemplars(papers))
        for p in papers:
            sc = score_text(_paper_text(p), proto, frozenset({p.id}))
            g = p.llm.circle if _is_scored(p) and p.llm.circle in groups else "unscored"
            groups[g].append(sc)
    out: dict = {"threshold": thr}
    for g, xs in groups.items():
        out[g] = {"n": len(xs), "p10": _quantile(xs, 0.1), "p50": _quantile(xs, 0.5),
                  "p90": _quantile(xs, 0.9)}
    out["none_at_or_above_threshold"] = sum(1 for x in groups["none"] if x >= thr)
    return out


def compute_stats(papers: list[Paper], state: FetchState | None, cfg: AppConfig, today: date) -> dict:
    n = len(papers)
    by_source = Counter(p.source for p in papers)
    for sid in enabled_source_ids(cfg):
        by_source.setdefault(sid, 0)
    by_any: Counter[str] = Counter()
    for p in papers:
        for s in set(p.sources) | {p.source}:
            by_any[s] += 1

    yield_by_source: dict[str, dict] = {}
    for src in by_source:
        scored = [p for p in papers if p.source == src and _is_scored(p)]
        rel = sum(1 for p in scored if p.llm.circle != "none")
        yield_by_source[src] = {"total": by_source[src], "scored": len(scored), "relevant": rel,
                                "yield": _ratio(rel, len(scored))}

    llm_status = Counter(p.llm_status for p in papers)
    backlog = sum(1 for p in papers
                  if p.llm_status in ("error", "unscored") and (p.abstract or "").strip())
    pv = Counter(p.llm.prompt_version for p in papers if p.llm is not None)
    cur = cfg.llm.prompt_version
    stale = sum(c for v, c in pv.items() if v != cur)

    hist = [0] * 10
    for p in papers:
        if p.final_score is not None:
            hist[min(int(max(p.final_score, 0.0)), 9)] += 1
    score_hist = {f"{i}-{i + 1}": hist[i] for i in range(10)}

    kw_n: Counter[str] = Counter()
    kw_total: Counter[str] = Counter()
    kw_fp: Counter[str] = Counter()
    rescued = 0
    for p in papers:
        fp = _is_scored(p) and p.llm.circle == "none"
        for k in set(p.matched_keywords):
            if k == "semantic:rescue":
                rescued += 1
                continue
            if k.startswith(_SKIP_KW_PREFIX):
                continue
            kw_total[k] += 1
            if _is_scored(p):
                kw_n[k] += 1
            if fp:
                kw_fp[k] += 1
    fps = [{"keyword": k, "n": c, "n_total": kw_total[k], "fp": kw_fp[k], "fp_rate": _ratio(kw_fp[k], c)}
           for k, c in kw_n.items() if c >= MIN_FP_HITS]
    fps.sort(key=lambda r: (-r["fp_rate"], -r["n"], r["keyword"]))

    orgs = Counter(o for p in papers for o in set(p.matched_orgs))
    sem = [p.semantic_score for p in papers if p.semantic_score and p.semantic_score > 0]

    d: dict = {
        "total": n,
        "by_source": dict(by_source),
        "by_sources_any": dict(by_any),
        "yield_by_source": yield_by_source,
        "llm_status": dict(llm_status),
        "llm_backlog": backlog,
        "prompt_versions": dict(pv),
        "stale_prompt_count": stale,
        "score_hist": score_hist,
        "keyword_false_positive_top10": fps[:10],
        "semantic_rescued": rescued,
        "promoted_top_venue": sum(1 for p in papers
                                  if p.published_venue and _is_top_venue(p.published_venue, cfg.llm.top_venues)),
        "promoted_any": sum(1 for p in papers if p.published_venue),
        "published_direct": sum(1 for p in papers
                                if p.published_venue and p.source in DIRECT_JOURNAL_SOURCES),
        "published_promoted": sum(1 for p in papers
                                  if p.published_venue and p.source not in DIRECT_JOURNAL_SOURCES),
        "semantic_calibration": _semantic_calibration(papers, cfg),
        "code_ratio": _ratio(sum(1 for p in papers if p.has_code or p.code_url), n),
        "weights_ratio": _ratio(sum(1 for p in papers if p.has_weights or p.weights_url), n),
        "matched_orgs_top10": [{"org": o, "n": c} for o, c in
                               sorted(orgs.items(), key=lambda kv: (-kv[1], kv[0]))[:10]],
        "semantic_score_quantiles": {"n": len(sem), "p10": _quantile(sem, 0.1),
                                     "p50": _quantile(sem, 0.5), "p90": _quantile(sem, 0.9)},
        "fetch_health": {},
    }
    if state is not None:
        srcs = {}
        for name, s in sorted(state.sources.items()):
            lsd = s.last_success_date
            srcs[name] = {
                "last_success_date": lsd.isoformat() if lsd else None,
                "consecutive_failures": s.consecutive_failures,
                "last_fetched": s.last_fetched,
                "days_since_success": (today - lsd).days if lsd else None,
                "last_error": s.last_error,
            }
        d["fetch_health"] = {"sources": srcs, "recent_runs": list(state.runs[:5])}
    return d


def render_stats(d: dict) -> str:
    L: list[str] = []
    L.append(f"总数：{d['total']}")
    L.append("各源分布：" + (", ".join(f"{k}={v}" for k, v in sorted(d["by_source"].items())) or "无"))
    L.append("各源（含合并来源）：" + (", ".join(f"{k}={v}" for k, v in sorted(d["by_sources_any"].items())) or "无"))
    L.append("各源产率（相关/已打分）：")
    for k, y in sorted(d["yield_by_source"].items()):
        L.append(f"  {k}: {y['relevant']}/{y['scored']} = {y['yield']:.1%}（总 {y['total']}）")
    L.append("LLM 状态：" + (", ".join(f"{k}={v}" for k, v in sorted(d["llm_status"].items())) or "无"))
    L.append(f"LLM 积压（error + 未打分且有摘要）：{d['llm_backlog']}")
    L.append("提示词版本：" + (", ".join(f"{k}={v}" for k, v in sorted(d["prompt_versions"].items())) or "无")
             + f"；旧版本 {d['stale_prompt_count']} 篇")
    L.append("分数直方图：" + " ".join(f"[{k}]{v}" for k, v in d["score_hist"].items()))
    L.append("关键词误报率 Top10（命中>=3 篇，LLM 判无关占比）：")
    for r in d["keyword_false_positive_top10"] or []:
        L.append(f"  {r['keyword']}: {r['fp']}/{r['n']} = {r['fp_rate']:.0%}（已判 {r['n']}/{r['n_total']}）")
    if not d["keyword_false_positive_top10"]:
        L.append("  （无）")
    L.append(f"语义捞回篇数：{d['semantic_rescued']}")
    L.append(f"正式发表：直接抓到的期刊论文 {d['published_direct']} 篇，"
             f"由预印本晋升 {d['published_promoted']} 篇（其中顶刊 {d['promoted_top_venue']} 篇）")
    L.append(f"有代码比例 {d['code_ratio']:.1%}，有权重比例 {d['weights_ratio']:.1%}")
    if d["matched_orgs_top10"]:
        L.append("机构 Top10：" + ", ".join(f"{o['org']}={o['n']}" for o in d["matched_orgs_top10"]))
    q = d["semantic_score_quantiles"]
    L.append(f"语义分（>0，n={q['n']}）：p10={q['p10']} p50={q['p50']} p90={q['p90']}")
    sc = d.get("semantic_calibration")
    if sc:
        L.append(f"语义校准（现场计算，不落盘；阈值 {sc['threshold']}）：")
        for g in ("core", "peripheral", "none", "unscored"):
            x = sc[g]
            L.append(f"  {g}: n={x['n']} p10={x['p10']} p50={x['p50']} p90={x['p90']}")
        L.append(f"  none 组 >= 阈值：{sc['none_at_or_above_threshold']} 篇")
    fh = d.get("fetch_health") or {}
    if not fh:
        L.append("水位线：无状态文件")
    else:
        L.append("水位线（各源健康）：")
        for k, s in fh["sources"].items():
            ds = s["days_since_success"]
            L.append(f"  {k}: 上次成功 {s['last_success_date'] or '-'}"
                     f"（距今 {ds if ds is not None else '-'} 天），连续失败 {s['consecutive_failures']}，"
                     f"上次抓取 {s['last_fetched']}")
        for r in fh["recent_runs"]:
            L.append(f"  运行 {r.get('run_at', '-')}: 抓取 {r.get('fetched', '-')} → 预筛后 "
                     f"{r.get('kept_after_filter', '-')} → 新增 {r.get('n_new', '-')}/更新 {r.get('n_updated', '-')}")
    return "\n".join(L)
