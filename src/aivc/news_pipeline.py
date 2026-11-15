"""新闻流水线：抓取 -> 预筛 -> 去重 -> LLM 抽取 -> 事件合并 -> 重大事件 -> 入库。

只存标题、链接、来源、时间与 LLM 自写的中文字段；RSS 片段只在内存里流转。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol

from .config import AppConfig
from .fetchers.news_html import CompositeNewsFetcher
from .llm import LLMCache
from .models import NewsItem
from .news import (NewsStore, apply_major, canonical_companies, dedup_candidates, event_date,
                   merge_events, to_candidates)
from .news_llm import NewsExtractor, NewsLLMStats
from .pipeline import PipelineError

log = logging.getLogger("aivc.news_pipeline")


class Notifier(Protocol):
    """重大事件推送接口占位：本项目不实现任何外部推送。"""

    def notify(self, items: list[NewsItem]) -> None: ...


class NullNotifier:
    def notify(self, items: list[NewsItem]) -> None:
        if items:
            log.info("重大事件 %d 条（未配置推送，仅记录日志）", len(items))


@dataclass
class NewsRunSummary:
    window: tuple[date, date] | None = None
    per_feed: dict[str, int] = field(default_factory=dict)
    fetch_errors: dict[str, str] = field(default_factory=dict)
    requests: int = 0
    n_fetched: int = 0
    n_candidates: int = 0
    n_duplicates: int = 0
    n_new: int = 0
    n_updated: int = 0
    n_merged: int = 0
    n_skipped_limit: int = 0
    n_rescored: int = 0              # --rescore-stale：旧版本条目重打成功数
    n_rescore_failed: int = 0        # 重打失败（旧分保留）
    n_rescore_skipped_budget: int = 0
    llm: NewsLLMStats | None = None
    major: list[NewsItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    attempted: list[str] = field(default_factory=list)

    @property
    def all_feeds_failed(self) -> bool:
        return bool(self.attempted) and all(n in self.fetch_errors for n in self.attempted)


def _find_existing(item: NewsItem, existing: list[NewsItem], thr: float) -> NewsItem | None:
    from .news import _item_urls, _same_title  # 复用去重判定
    urls = _item_urls(item)
    for x in existing:
        if (urls & _item_urls(x)) or _same_title(item.title, x.title, thr):
            return x
    return None


def _merge_llm_stats(a: NewsLLMStats | None, b: NewsLLMStats | None) -> NewsLLMStats | None:
    if a is None or b is None:
        return a or b
    return NewsLLMStats(
        scored=a.scored + b.scored, cache_hits=a.cache_hits + b.cache_hits,
        api_calls=a.api_calls + b.api_calls, errors=a.errors + b.errors,
        skipped_no_backend=a.skipped_no_backend + b.skipped_no_backend,
        skipped_budget=a.skipped_budget + b.skipped_budget,
        disabled_reason=a.disabled_reason or b.disabled_reason)


def _todo_key(c: tuple[NewsItem, str]):
    return (-c[0].keyword_score, -event_date(c[0]).toordinal(), c[0].id)


def _rotate_by_source(todo: list[tuple[NewsItem, str]]) -> list[tuple[NewsItem, str]]:
    """按来源轮转排序：组内沿用 _todo_key，组按队首的键排序，逐轮各取一条。"""
    groups: dict[str, list[tuple[NewsItem, str]]] = {}
    for c in todo:
        groups.setdefault(c[0].source, []).append(c)
    for g in groups.values():
        g.sort(key=_todo_key)
    ordered = sorted(groups.values(), key=lambda g: _todo_key(g[0]))
    out: list[tuple[NewsItem, str]] = []
    depth = 0
    while len(out) < len(todo):
        for g in ordered:
            if depth < len(g):
                out.append(g[depth])
        depth += 1
    return out


def run_news(
    cfg: AppConfig,
    *,
    data_dir: Path,
    days: int | None = None,
    use_llm: bool = True,
    limit: int | None = None,
    rescore_stale: bool = False,
    today: date | None = None,
    fetcher=None,
    extractor=None,
    notifier: Notifier | None = None,
) -> NewsRunSummary:
    if days is not None and days < 1:
        raise PipelineError("--days 必须为正整数")
    if limit is not None and limit < 1:
        raise PipelineError("--limit 必须为正整数")
    ncfg = cfg.pipeline.news
    until = today or date.today()
    since = until - timedelta(days=(days or ncfg.default_days) - 1)
    s = NewsRunSummary(window=(since, until))
    src = cfg.sources.news
    feeds = [f for f in src.feeds if f.enabled] + [h for h in src.html_sources if h.enabled]
    if not src.enabled:
        s.notes.append("新闻源已在配置中关闭（sources.news.enabled=false），已跳过")
        return s
    if not feeds and fetcher is None:
        s.notes.append("没有启用的新闻 feed，已跳过")
        return s

    # 1) 抓取：任何异常都只记录，不抛出
    s.attempted = [f.name for f in feeds]
    fetcher = fetcher or CompositeNewsFetcher(src, cfg.sources.http)
    entries = []
    try:
        fr = fetcher.fetch(since, until)
        entries = fr.entries
        s.per_feed = dict(fr.per_feed)
        s.fetch_errors = dict(fr.errors)
        s.requests = fr.requests
    except Exception as e:  # noqa: BLE001
        log.warning("新闻抓取失败: %s", e)
        for n in s.attempted or ["news"]:
            s.fetch_errors[n] = f"{type(e).__name__}: {e}"
        if not s.attempted:
            s.attempted = ["news"]
    s.n_fetched = len(entries)
    if s.fetch_errors:
        s.notes.append("抓取失败的源：" + "、".join(sorted(s.fetch_errors)))

    # 2) 预筛 + 去重
    store = NewsStore(data_dir)
    existing = store.load_all()
    cands = to_candidates(entries, cfg, until)
    s.n_candidates = len(cands)
    dd = dedup_candidates(cands, existing, cfg)
    s.n_duplicates = dd.n_duplicates
    updated_by_id = {x.id: x for x in dd.updated}
    new_ids = {it.id for it, _ in dd.new}

    todo: list[tuple[NewsItem, str]] = list(dd.new)
    rescore_ids: set[str] = set()
    stale_todo: list[tuple[NewsItem, str]] = []
    stale_ids: set[str] = set()
    if use_llm:
        # 库内仍未打分、本次又出现的条目：用本次片段补打分
        thr = ncfg.title_threshold
        for item, snippet in cands:
            if item.id in new_ids:
                continue
            hit = _find_existing(item, existing, thr)
            if hit is None:
                continue
            if (rescore_stale and hit.llm_status == "scored" and hit.llm is not None
                    and hit.llm.prompt_version != ncfg.prompt_version
                    and hit.id not in stale_ids):
                stale_ids.add(hit.id)
                stale_todo.append((updated_by_id.get(hit.id, hit), snippet))
                continue
            if hit.llm_status == "scored" or hit.id in rescore_ids:
                continue
            rescore_ids.add(hit.id)
            todo.append((updated_by_id.get(hit.id, hit), snippet))

    todo.sort(key=_todo_key)
    if limit is not None and len(todo) > limit:
        s.n_skipped_limit = len(todo) - limit
        todo = _rotate_by_source(todo)[:limit]
        s.notes.append(f"--limit {limit}：另有 {s.n_skipped_limit} 条候选本次未处理也未入库")
    new_todo = [(it, sn) for it, sn in todo if it.id in new_ids]
    resc_todo = [(it, sn) for it, sn in todo if it.id in rescore_ids]

    # 3) LLM 抽取
    processed: list[NewsItem]
    stale_proc: dict[str, NewsItem] = {}
    if use_llm and (todo or stale_todo):
        if extractor is None:
            extractor = NewsExtractor(cfg, LLMCache(Path(data_dir) / ncfg.cache_file))
    if use_llm and todo:
        budget = ncfg.max_llm_calls_per_run
        if limit is not None:
            budget = min(budget, limit)
        processed, s.llm = extractor.extract(new_todo + resc_todo, max_calls=budget)
        if not extractor.available:
            s.notes.append("没有可用的 LLM 后端，本次新增新闻未打分")
        if s.llm.disabled_reason:
            s.notes.append(f"LLM 抽取已停用：{s.llm.disabled_reason}")
        if s.llm.skipped_budget:
            s.notes.append(f"达到单次调用预算上限，{s.llm.skipped_budget} 条未打分")
    else:
        processed = [it.model_copy(update={"llm_status": "unscored", "llm_skip_reason": "no_llm_flag"})
                     for it, _ in new_todo]
        if not use_llm and new_todo:
            s.notes.append("使用了 --no-llm，本次新增新闻未打分")
    if use_llm and stale_todo:
        # 旧版本重打：独立预算、不受 --limit 截断；非 scored 的结果一律丢弃，库内旧分保留
        res, st2 = extractor.extract(stale_todo, max_calls=ncfg.rescore_stale_max_calls)
        for (orig, _), r in zip(stale_todo, res):
            if r.llm_status == "scored" and r.llm is not None:
                stale_proc[orig.id] = _canon(r, cfg)
        s.n_rescored = len(stale_proc)
        s.n_rescore_skipped_budget = st2.skipped_budget
        s.n_rescore_failed = len(stale_todo) - s.n_rescored - st2.skipped_budget
        s.llm = _merge_llm_stats(s.llm, st2)
        if s.llm.disabled_reason and not any("LLM 抽取已停用" in n for n in s.notes):
            s.notes.append(f"LLM 抽取已停用：{s.llm.disabled_reason}")
        s.notes.append(f"--rescore-stale：旧版本条目 {len(stale_todo)} 条，重打成功 {s.n_rescored}，"
                       f"失败保留旧分 {s.n_rescore_failed}，预算跳过 {s.n_rescore_skipped_budget}")
    n_new_in = len(new_todo)
    new_proc = [_canon(x, cfg) for x in processed[:n_new_in]]
    resc_proc = {x.id: _canon(x, cfg) for x in processed[n_new_in:]}
    resc_proc.update(stale_proc)

    # 4) 事件合并：库内视图用补打分/合并来源后的版本
    view = [resc_proc.get(x.id) or updated_by_id.get(x.id) or x for x in existing]
    kept_new, mods = merge_events(new_proc, view, cfg)
    s.n_merged = len(new_proc) - len(kept_new)

    # 5) 重大事件、入库
    old = {x.id: x for x in existing}
    final: dict[str, NewsItem] = {}
    for x in dd.updated:
        final[x.id] = x
    final.update(resc_proc)
    for x in mods:
        final[x.id] = x
    for x in kept_new:
        final[x.id] = x
    final = {k: apply_major(v, cfg) for k, v in final.items()}
    n_new, n_upd = store.upsert(list(final.values()))
    s.n_new, s.n_updated = n_new, n_upd
    s.major = [v for k, v in final.items()
               if v.major and (k not in old or not old[k].major)]
    s.major.sort(key=lambda x: (-(x.relevance or 0), x.id))
    if s.major:
        try:
            (notifier or NullNotifier()).notify(s.major)
        except Exception as e:  # noqa: BLE001
            log.warning("重大事件通知失败: %s", e)
            s.notes.append(f"重大事件通知失败：{e}")
    return s


def _canon(item: NewsItem, cfg: AppConfig) -> NewsItem:
    if item.llm_status == "scored":
        return item.model_copy(update={"companies": canonical_companies(item.companies, cfg)})
    return item


# ---------------------------------------------------------------- 只处理库内条目的重打分
STORED_SNIPPET_PREFIX = "（原始片段已不可得；以下是此前版本模型自写的中文摘要，仅供参考）"


@dataclass
class RescoreSummary:
    n_targets: int = 0
    n_refetched: int = 0
    n_stored_summary: int = 0
    n_title_only: int = 0
    n_rescored: int = 0
    n_failed: int = 0
    n_skipped_budget: int = 0
    requests: int = 0               # LLM 真实请求数（含重试）
    cache_hits: int = 0
    dry_run: bool = False
    window: tuple[date, date] | None = None
    # (id, title, old_relevance, new_relevance, snippet_source)；未重打成功的 new_relevance 为 None
    rows: list[tuple[str, str, int | None, int | None, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    llm: NewsLLMStats | None = None


def rescore_stored(
    cfg: AppConfig,
    *,
    data_dir: Path,
    days: int | None = None,
    fetch: bool = True,
    ids: list[str] | None = None,
    include_unscored: bool = False,
    max_requests: int | None = None,
    dry_run: bool = False,
    today: date | None = None,
    fetcher=None,
    extractor=None,
) -> RescoreSummary:
    """只重打库内已有条目：抓取只用于找回片段，抓到的其它条目一律丢弃、不入库。"""
    if days is not None and days < 1:
        raise PipelineError("--days 必须为正整数")
    if max_requests is not None and max_requests < 0:
        raise PipelineError("--max-requests 不能为负数")
    ncfg = cfg.pipeline.news
    until = today or date.today()
    s = RescoreSummary(dry_run=dry_run)
    store = NewsStore(data_dir)
    existing = store.load_all()

    def is_target(x: NewsItem) -> bool:
        if x.published is None:
            return False
        if x.llm_status == "scored":
            return x.llm is None or x.llm.prompt_version != ncfg.prompt_version
        return include_unscored and x.llm_status != "scored"

    targets = [x for x in existing if is_target(x)]
    if ids:
        known = {x.id for x in existing}
        unknown = [i for i in ids if i not in known]
        if unknown:
            s.notes.append("不认识的 id：" + "、".join(unknown))
        want = set(ids)
        targets = [x for x in targets if x.id in want]
    targets.sort(key=lambda x: (event_date(x), x.id))
    s.n_targets = len(targets)
    if not targets:
        s.notes.append("没有需要重打的库内条目")
        return s

    # 1) 抓取只为找回片段
    snippets: dict[str, str] = {}
    if fetch:
        src = cfg.sources.news
        feeds = [f for f in src.feeds if f.enabled] + [h for h in src.html_sources if h.enabled]
        if src.enabled and (feeds or fetcher is not None):
            if days is not None:
                since = until - timedelta(days=days - 1)
            else:
                since = max(event_date(targets[0]), until - timedelta(days=89))
            s.window = (since, until)
            fetcher = fetcher or CompositeNewsFetcher(src, cfg.sources.http)
            try:
                fr = fetcher.fetch(since, until)
                thr = ncfg.title_threshold
                for cand, snip in to_candidates(fr.entries, cfg, until):
                    hit = _find_existing(cand, targets, thr)
                    if hit is not None and snip and hit.id not in snippets:
                        snippets[hit.id] = snip
                if fr.errors:
                    s.notes.append("抓取失败的源：" + "、".join(sorted(fr.errors)))
            except Exception as e:  # noqa: BLE001
                log.warning("重打分抓取失败: %s", e)
                s.notes.append(f"抓取失败：{type(e).__name__}: {e}")
        else:
            s.notes.append("新闻源关闭或没有启用的 feed，未抓取片段")

    cands: list[tuple[NewsItem, str]] = []
    source_of: dict[str, str] = {}
    for x in targets:
        if x.id in snippets:
            cands.append((x, snippets[x.id]))
            source_of[x.id] = "refetched"
        elif x.llm_status == "scored" and (x.summary_zh or x.significance_zh):
            text = "\n".join(p for p in (x.summary_zh, x.significance_zh) if p)
            cands.append((x, f"{STORED_SNIPPET_PREFIX}\n{text}"))
            source_of[x.id] = "stored_summary"
        else:
            cands.append((x, ""))
            source_of[x.id] = "title_only"
    s.n_refetched = sum(1 for v in source_of.values() if v == "refetched")
    s.n_stored_summary = sum(1 for v in source_of.values() if v == "stored_summary")
    s.n_title_only = sum(1 for v in source_of.values() if v == "title_only")

    if dry_run:
        s.rows = [(x.id, x.title, x.relevance, None, source_of[x.id]) for x in targets]
        return s

    # 2) 打分：只有 scored 的结果才写回
    limit = ncfg.rescore_stale_max_calls if max_requests is None else max_requests
    if extractor is None:
        extractor = NewsExtractor(cfg, LLMCache(Path(data_dir) / ncfg.cache_file))
    res, st = extractor.extract(cands, max_calls=len(cands), max_requests=limit)
    s.llm = st
    s.requests = st.requests
    s.cache_hits = st.cache_hits
    s.n_skipped_budget = st.skipped_budget
    if not extractor.available:
        s.notes.append("没有可用的 LLM 后端，未重打")
    if st.disabled_reason:
        s.notes.append(f"LLM 抽取已停用：{st.disabled_reason}")
    out: list[NewsItem] = []
    for (orig, _), r in zip(cands, res):
        if r.llm_status == "scored" and r.llm is not None:
            new = apply_major(_canon(r, cfg), cfg)
            out.append(new)
            s.rows.append((orig.id, orig.title, orig.relevance, new.relevance, source_of[orig.id]))
        else:
            s.rows.append((orig.id, orig.title, orig.relevance, None, source_of[orig.id]))
    s.n_rescored = len(out)
    s.n_failed = len(cands) - s.n_rescored - s.n_skipped_budget
    if out:
        store.upsert(out)
    return s
