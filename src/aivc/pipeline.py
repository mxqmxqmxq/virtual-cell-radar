"""Pipeline orchestration: fetch -> prefilter -> dedup -> LLM score -> store -> report."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from . import prefilter, report
from .config import AppConfig
from .dedup import dedup_batch
from .fetchers.arxiv import ArxivFetcher
from .fetchers.base import Fetcher
from .fetchers.biorxiv import BiorxivFetcher
from .llm import LLMCache, LLMScorer, ScoreStats
from .models import Paper
from .seed import collect_seed
from .storage import PaperStore, load_overlay
from .topics import Topic, report_topics

log = logging.getLogger("aivc.pipeline")

NO_KEY_NOTE = "没有可用的打分后端（未配置 ANTHROPIC_API_KEY，也找不到 claude 命令），本次新增论文未打分"
NO_LLM_NOTE = "使用了 --no-llm，本次新增论文未打分"


class PipelineError(Exception):
    """配置或参数错误（CLI 以退出码 2 结束）。"""


@dataclass
class RunSummary:
    window: tuple[date, date] | None = None
    fetched: dict[str, int] = field(default_factory=dict)
    fetch_errors: dict[str, str] = field(default_factory=dict)
    kept_after_filter: int = 0
    n_new: int = 0
    n_updated: int = 0
    n_duplicates: int = 0
    llm: ScoreStats | None = None
    report_path: Path | None = None
    notes: list[str] = field(default_factory=list)
    attempted: list[str] = field(default_factory=list)  # 本次尝试的源名

    @property
    def all_sources_failed(self) -> bool:
        return bool(self.attempted) and all(n in self.fetch_errors for n in self.attempted)


def build_fetchers(cfg: AppConfig) -> list[Fetcher]:
    s = cfg.sources
    out: list[Fetcher] = []
    if s.arxiv.enabled:
        out.append(ArxivFetcher(s.arxiv, s.http))
    if s.biorxiv.enabled:
        out.append(BiorxivFetcher(s.biorxiv, s.http))
    if s.medrxiv.enabled:
        out.append(BiorxivFetcher(s.medrxiv, s.http))
    return out


def _make_scorer(cfg: AppConfig, data_dir: Path) -> LLMScorer:
    return LLMScorer(cfg, LLMCache(Path(data_dir) / cfg.llm.cache_file))


def _add_stats(a: ScoreStats | None, b: ScoreStats) -> ScoreStats:
    if a is None:
        return b
    for k in ("scored", "cache_hits", "api_calls", "errors", "skipped_no_key",
              "skipped_no_abstract", "skipped_budget", "skipped_already"):
        setattr(a, k, getattr(a, k) + getattr(b, k))
    a.disabled_reason = b.disabled_reason or a.disabled_reason
    return a


def _llm_notes(scorer: LLMScorer, stats: ScoreStats | None) -> list[str]:
    notes: list[str] = []
    if not scorer.available:
        notes.append(NO_KEY_NOTE)
    if stats is not None and stats.disabled_reason:
        notes.append(f"LLM 打分已停用：{stats.disabled_reason}")
    if stats is not None and stats.skipped_budget:
        notes.append(f"达到单次调用预算上限，{stats.skipped_budget} 篇未打分")
    return notes


def _score(cfg: AppConfig, scorer: LLMScorer, papers: list[Paper], used_calls: int = 0):
    budget = max(0, cfg.llm.max_calls_per_run - used_calls)
    return scorer.score_papers(papers, max_calls=budget)


def _run_window(
    cfg: AppConfig,
    store: PaperStore,
    fetchers: list[Fetcher],
    scorer: LLMScorer | None,
    since: date,
    until: date,
    used_calls: int = 0,
) -> tuple[RunSummary, list[Paper]]:
    """Single window: fetch -> prefilter -> dedup -> score -> upsert. Returns summary and new papers."""
    s = RunSummary(window=(since, until))
    incoming: list[Paper] = []
    for f in fetchers:
        s.attempted.append(f.name)
        try:
            got = f.fetch(since, until)
        except Exception as e:  # noqa: BLE001  one failing source must not stop the others
            log.warning("源 %s 抓取失败: %s", f.name, e)
            s.fetch_errors[f.name] = f"{type(e).__name__}: {e}"
            s.fetched[f.name] = 0
            continue
        s.fetched[f.name] = len(got)
        incoming.extend(got)
    kept = prefilter.filter_relevant(incoming, cfg)
    s.kept_after_filter = len(kept)
    result = dedup_batch(kept, store.load_all(), cfg.pipeline.dedup.title_threshold)
    s.n_duplicates = result.n_duplicates
    new = result.new
    if scorer is None:
        new = [p.model_copy(update={"llm_status": "unscored", "llm_skip_reason": "no_llm_flag"})
               for p in new]
        if new:
            s.notes.append(NO_LLM_NOTE)
    else:
        # 合并后摘要有变化的已有条目会被重置为 unscored，这里一并补打分
        rescore = [p for p in result.updated
                   if p.llm_status != "scored" and (p.abstract or "").strip()]
        scored, stats = _score(cfg, scorer, new + rescore, used_calls)
        new = scored[:len(new)]
        by_id = {p.id: p for p in scored[len(new):]}
        result.updated = [by_id.get(p.id, p) for p in result.updated]
        s.llm = stats
        s.notes.extend(_llm_notes(scorer, stats))
    store.upsert(new + result.updated)
    s.n_new = len(new)
    s.n_updated = len(result.updated)
    return s, new


def _dedup_notes(notes: list[str]) -> list[str]:
    seen: set[str] = set()
    return [n for n in notes if not (n in seen or seen.add(n))]


def run_update(
    cfg: AppConfig,
    *,
    data_dir: Path,
    reports_dir: Path,
    days: int | None = None,
    use_llm: bool = True,
    today: date | None = None,
    fetchers: list[Fetcher] | None = None,
    scorer: LLMScorer | None = None,
    write_report: bool = True,
    report_subdir: str = "daily",
    report_filename: str | None = None,
    report_title: str | None = None,
    topics: list[Topic] | None = None,
    pinned_news: list | None = None,
) -> RunSummary:
    until = today or date.today()
    # --days N 表示含今天在内共 N 天
    since = until - timedelta(days=(days or cfg.pipeline.update.default_days) - 1)
    fetchers = build_fetchers(cfg) if fetchers is None else fetchers
    store = PaperStore(data_dir)
    sc = None
    if use_llm:
        sc = scorer or _make_scorer(cfg, data_dir)
    s, new = _run_window(cfg, store, fetchers, sc, since, until)
    s.notes = _dedup_notes(s.notes)
    if s.fetch_errors:
        s.notes.append("抓取失败的源：" + "、".join(sorted(s.fetch_errors)))
    if write_report:
        # 同一天多次运行：日报包含当天所有新入库论文，而不只是本次新增
        same_day = {p.id: p for p in store.load_all() if p.first_seen == until}
        same_day.update({p.id: p for p in new})
        s.report_path = report.write_daily(
            list(same_day.values()), until, Path(reports_dir), window=(since, until), notes=s.notes,
            max_items=cfg.pipeline.report.max_items, subdir=report_subdir,
            filename=report_filename, title=report_title,
            topics=report_topics(cfg.topics.topics) if topics is None else topics,
            must_read_threshold=cfg.llm.must_read_threshold,
            pinned_news=pinned_news,
        )
    return s


def _windows(since: date, until: date, chunk: int) -> list[tuple[date, date]]:
    """从最近往最早切窗，相邻窗口不重叠，整体覆盖 [since, until]。"""
    chunk = max(1, chunk)
    out: list[tuple[date, date]] = []
    hi = until
    while hi >= since:
        lo = max(since, hi - timedelta(days=chunk - 1))
        out.append((lo, hi))
        hi = lo - timedelta(days=1)
    return out


def run_backfill(
    cfg: AppConfig,
    *,
    data_dir: Path,
    reports_dir: Path,
    days: int,
    use_llm: bool = True,
    today: date | None = None,
    fetchers: list[Fetcher] | None = None,
    scorer: LLMScorer | None = None,
    topics: list[Topic] | None = None,
) -> RunSummary:
    if days < 1:
        raise PipelineError("--days 必须为正整数")
    until = today or date.today()
    since = until - timedelta(days=days - 1)  # 含今天共 N 天
    fetchers = build_fetchers(cfg) if fetchers is None else fetchers
    store = PaperStore(data_dir)
    sc = (scorer or _make_scorer(cfg, data_dir)) if use_llm else None

    total = RunSummary(window=(since, until))
    total.attempted = [f.name for f in fetchers]
    all_new: list[Paper] = []
    err_windows: dict[str, list[str]] = {}
    n_win = 0
    for lo, hi in _windows(since, until, cfg.pipeline.update.backfill_chunk_days):
        n_win += 1
        log.info("回填窗口 %s ~ %s", lo, hi)
        w, new = _run_window(cfg, store, fetchers, sc, lo, hi,
                             used_calls=total.llm.api_calls if total.llm else 0)
        for k, v in w.fetched.items():
            total.fetched[k] = total.fetched.get(k, 0) + v
        for k, v in w.fetch_errors.items():
            err_windows.setdefault(k, []).append(f"{lo}~{hi}: {v}")
        total.kept_after_filter += w.kept_after_filter
        total.n_new += w.n_new
        total.n_updated += w.n_updated
        total.n_duplicates += w.n_duplicates
        if w.llm is not None:
            total.llm = _add_stats(total.llm, w.llm)
        total.notes.extend(w.notes)
        all_new.extend(new)
    total.notes = _dedup_notes(total.notes)
    for k, errs in err_windows.items():
        if len(errs) == n_win:
            total.fetch_errors[k] = errs[0]
        total.notes.append(f"源 {k} 有 {len(errs)}/{n_win} 个窗口抓取失败（可重跑回填，操作幂等）")
    total.notes.append(f"回填共 {n_win} 个窗口")
    total.report_path = report.write_daily(
        all_new, until, Path(reports_dir), window=(since, until), notes=total.notes,
        max_items=cfg.pipeline.report.max_items, subdir="backfill",
        filename=f"{until.isoformat()}_{days}d.md", title=f"AIVC 回填 · 最近 {days} 天",
        topics=report_topics(cfg.topics.topics) if topics is None else topics,
        must_read_threshold=cfg.llm.must_read_threshold,
    )
    return total


def run_seed(
    cfg: AppConfig,
    *,
    data_dir: Path,
    source_dir: Path,
    iclr_md: Path | None = None,
    use_llm: bool = True,
    today: date | None = None,
    scorer: LLMScorer | None = None,
    public_data_dir: Path | None = None,
) -> RunSummary:
    """导入种子。data_dir 是写入目标；给了 public_data_dir 时，查重以「公开库 + data_dir」的叠加结果
    为已有集合，公开库本身只读，不会被写。"""
    source_dir = Path(source_dir)
    if not source_dir.is_dir():
        raise PipelineError(f"种子目录不存在或不是目录: {source_dir}")
    if iclr_md is not None and not Path(iclr_md).is_file():
        raise PipelineError(f"ICLR 摘要文件不存在: {iclr_md}")
    today = today or date.today()
    s = RunSummary()
    papers = collect_seed(source_dir, iclr_md, cfg.sources.seed, today)
    papers = [prefilter.apply_keywords(p, cfg) for p in papers]  # 只标注，不过滤
    store = PaperStore(data_dir)
    existing = load_overlay(public_data_dir, data_dir) if public_data_dir is not None else store.load_all()
    result = dedup_batch(papers, existing, cfg.pipeline.dedup.title_threshold)
    new = result.new
    n_abs = sum(1 for p in papers if (p.abstract or "").strip())
    s.kept_after_filter = len(papers)
    s.n_duplicates = result.n_duplicates
    if use_llm:
        sc = scorer or _make_scorer(cfg, data_dir)
        with_abs = [p for p in new if (p.abstract or "").strip()]
        scored, stats = _score(cfg, sc, with_abs)
        s.llm = stats
        by_id = {p.id: p for p in scored}
        new = [by_id.get(p.id, p) for p in new]
        s.notes.extend(_llm_notes(sc, stats))
    else:
        s.notes.append(NO_LLM_NOTE)
    store.upsert(new + result.updated)
    s.n_new = len(new)
    s.n_updated = len(result.updated)
    s.notes.append(
        f"种子共 {len(papers)} 条，其中有摘要 {n_abs} 条；新增 {s.n_new} 条，合并/重复 {s.n_duplicates} 条"
    )
    return s


def run_reindex(cfg: AppConfig, *, data_dir: Path, prune: bool = False) -> RunSummary:
    """按当前 keywords.yaml 重算库内所有条目的 circle / keyword_score / matched_keywords。

    prune=True 时删除重算后 circle 为 none 的抓取条目（种子条目是人工挑选的，始终保留）。
    """
    s = RunSummary()
    store = PaperStore(data_dir)
    papers = store.load_all()
    changed = []
    to_drop: list[str] = []
    for p in papers:
        q = prefilter.apply_keywords(p, cfg)
        if prune and q.circle == "none" and q.source not in ("seed_pdf", "openreview"):
            to_drop.append(q.id)
        elif q != p:
            changed.append(q)
    _, n_upd = store.upsert(changed)
    n_drop = store.delete(to_drop) if to_drop else 0
    s.n_updated = n_upd
    s.notes.append(f"共 {len(papers)} 条，重算后变化 {n_upd} 条" + (f"，删除 {n_drop} 条不再相关的抓取条目" if prune else ""))
    return s


def run_score(
    cfg: AppConfig,
    *,
    data_dir: Path,
    limit: int | None = None,
    scorer: LLMScorer | None = None,
) -> RunSummary:
    s = RunSummary()
    sc = scorer or _make_scorer(cfg, data_dir)
    if not sc.available:
        s.notes.append("没有可用的打分后端：配置 ANTHROPIC_API_KEY，或安装并登录 Claude Code 后重跑 `python -m aivc score`")
        return s
    store = PaperStore(data_dir)
    pv = cfg.llm.prompt_version
    todo = [p for p in store.load_all()
            if (p.abstract or "").strip()
            and (p.llm_status != "scored" or p.llm is None or p.llm.prompt_version != pv)]
    todo.sort(key=lambda p: (-p.keyword_score, p.id))
    if limit is not None:
        todo = todo[:max(0, limit)]
    scored, stats = _score(cfg, sc, todo)
    s.llm = stats
    s.notes.extend(_llm_notes(sc, stats))
    changed = [q for p, q in zip(todo, scored) if p != q]
    n_new, n_upd = store.upsert(changed)
    s.n_updated = n_upd
    s.notes.append(f"待打分 {len(todo)} 篇，已写回 {n_upd} 篇")
    return s
