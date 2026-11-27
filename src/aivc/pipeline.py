"""Pipeline orchestration: fetch -> prefilter -> dedup -> LLM score -> store -> report."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import enrich, prefilter, report, semantic
from .config import AppConfig
from .dedup import _SUBMISSION_RE, dedup_batch
from .fetchers.arxiv import ArxivFetcher
from .fetchers.base import Fetcher
from .fetchers.biorxiv import BiorxivFetcher
from .fetchers.europepmc import EuropePMCFetcher
from .fetchers.hf_papers import HFPapersFetcher
from .fetchers.openalex import OpenAlexFetcher
from .fetchers.openreview import OpenReviewFetcher
from .llm import LLMCache, LLMScorer, ScoreStats, recompute_final_score
from .models import Paper
from .seed import collect_seed
from .state import FetchState
from .storage import PaperStore, load_overlay
from .textutil import normalize_doi
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
    windows_by_source: dict[str, tuple[date, date]] = field(default_factory=dict)
    n_semantic_rescued: int = 0
    n_backlog_scored: int = 0
    touched_ids: set[str] = field(default_factory=set)  # 本窗口 new/updated 的 id
    incomplete: dict[str, str] = field(default_factory=dict)  # 抓取不完整的源 -> 原因

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
    if s.openalex.enabled:
        out.append(OpenAlexFetcher(s.openalex, s.http))
    if s.europepmc.enabled:
        out.append(EuropePMCFetcher(s.europepmc, s.http))
    if s.hf_papers.enabled:
        out.append(HFPapersFetcher(s.hf_papers, s.http))
    if s.openreview.enabled:
        out.append(OpenReviewFetcher(s.openreview, s.http))
    return out


def _make_scorer(cfg: AppConfig, data_dir: Path) -> LLMScorer:
    return LLMScorer(cfg, LLMCache(Path(data_dir) / cfg.llm.cache_file))


def _add_stats(a: ScoreStats | None, b: ScoreStats) -> ScoreStats:
    if a is None:
        return b
    for k in ("scored", "cache_hits", "api_calls", "errors", "skipped_no_key",
              "skipped_no_abstract", "skipped_budget", "skipped_already", "papers_attempted"):
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


def _overlay_pubs(base: Paper, pub: Paper) -> Paper:
    upd = {"published_doi": pub.published_doi, "published_venue": pub.published_venue}
    if pub.venue != base.venue and (not base.venue or _SUBMISSION_RE.search(base.venue)):
        upd["venue"] = pub.venue
    return base.model_copy(update=upd)


def _apply_pubs(cfg: AppConfig, fetchers: list[Fetcher], existing: list[Paper],
                s: RunSummary) -> dict[str, Paper]:
    """把抓取器本轮拿到的 bioRxiv /pubs 映射应用于库内存量预印本，返回 id -> 更新后的论文。"""
    links: dict[str, tuple[str, str]] = {}
    for f in fetchers:
        got = getattr(f, "last_published_links", None)
        if got:
            links.update(got)
    if not links:
        return {}
    out: dict[str, Paper] = {}
    for p in existing:
        hit = links.get(normalize_doi(p.doi or ""))
        if not hit:
            continue
        pd, pv = normalize_doi(hit[0]), hit[1]
        if p.published_doi and normalize_doi(p.published_doi) == pd and p.published_venue == pv:
            continue
        upd = {"published_doi": pd, "published_venue": pv}
        if not p.venue or _SUBMISSION_RE.search(p.venue):
            upd["venue"] = pv
        out[p.id] = recompute_final_score(p.model_copy(update=upd), cfg)
    if out:
        s.notes.append(f"/pubs 回填库内 {len(out)} 篇已发表预印本")
    return out


def _run_window(
    cfg: AppConfig,
    store: PaperStore,
    fetchers: list[Fetcher],
    scorer: LLMScorer | None,
    since: date,
    until: date,
    used_calls: int = 0,
    *,
    since_by_source: dict[str, date] | None = None,
    labs: list[str] | None = None,
    state: FetchState | None = None,
    now: datetime | None = None,
) -> tuple[RunSummary, list[Paper]]:
    """Single window: fetch -> enrich -> prefilter -> dedup -> score -> upsert. Returns summary and new papers."""
    s = RunSummary(window=(since, until))
    now = now or datetime.now(timezone.utc)
    incoming: list[Paper] = []
    for f in fetchers:
        s.attempted.append(f.name)
        since_i = (since_by_source or {}).get(f.name, since)
        s.windows_by_source[f.name] = (since_i, until)
        try:
            got = f.fetch(since_i, until)
        except Exception as e:  # noqa: BLE001  one failing source must not stop the others
            log.warning("源 %s 抓取失败: %s", f.name, e)
            s.fetch_errors[f.name] = f"{type(e).__name__}: {e}"
            s.fetched[f.name] = 0
            if state is not None:
                state.record_failure(f.name, s.fetch_errors[f.name], now)
            continue
        s.fetched[f.name] = len(got)
        inc = getattr(f, "last_incomplete", False)
        warn = getattr(f, "last_warning", None)
        if inc:
            s.incomplete[f.name] = warn or "抓取不完整"
            s.notes.append(f"源 {f.name} 抓取不完整，未推进水位线：{warn or '抓取不完整'}")
            if state is not None:
                state.record_incomplete(f.name, len(got), warn or "抓取不完整", now)
        elif state is not None:
            state.record_success(f.name, until, len(got), now)
        incoming.extend(got)
    existing = store.load_all()
    pubs_updated = _apply_pubs(cfg, fetchers, existing, s)
    incoming = enrich.enrich_offline(incoming, cfg, labs)
    # 本批已在库内的论文不充当自己的正样本，否则重复运行时语义分会漂移、造成无意义的更新
    in_ids = {p.id for p in incoming}
    exemplars = [e for e in semantic.select_exemplars(existing) if e.id not in in_ids]
    kept = prefilter.filter_relevant(incoming, cfg, exemplars)
    s.kept_after_filter = len(kept)
    s.n_semantic_rescued = sum(1 for p in kept if "semantic:rescue" in p.matched_keywords)
    result = dedup_batch(kept, existing, cfg.pipeline.dedup.title_threshold)
    s.n_duplicates = result.n_duplicates
    result.updated = [recompute_final_score(p, cfg) for p in result.updated]
    new = [recompute_final_score(p, cfg) for p in result.new]
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
    if pubs_updated:
        # 同 id 以 dedup 结果为底，再套用 /pubs 的发表信息
        merged = {p.id: p for p in result.updated}
        for pid, q in pubs_updated.items():
            base = merged.get(pid)
            if base is not None:
                q = recompute_final_score(_overlay_pubs(base, q), cfg)
            merged[pid] = q
        result.updated = list(merged.values())
    store.upsert(new + result.updated)
    s.n_new = len(new)
    s.n_updated = len(result.updated)
    s.touched_ids = {p.id for p in new} | {p.id for p in result.updated}
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
    upd = cfg.pipeline.update
    fetchers = build_fetchers(cfg) if fetchers is None else fetchers
    store = PaperStore(data_dir)
    sc = None
    if use_llm:
        sc = scorer or _make_scorer(cfg, data_dir)
    now = datetime.now(timezone.utc)
    state = FetchState.load(Path(data_dir) / upd.state_file)
    catchup_notes: list[str] = []
    since_by_source: dict[str, date] | None = None
    if days is not None:
        # --days N 表示含今天在内共 N 天，所有源同一窗口，不看水位线
        since = until - timedelta(days=days - 1)
    else:
        default_since = until - timedelta(days=upd.default_days - 1)
        since_by_source = {
            f.name: state.window_for(f.name, until, upd.default_days, upd.overlap_days,
                                     upd.max_catchup_days)
            for f in fetchers
        }
        since = min(since_by_source.values(), default=default_since)
        for name, si in since_by_source.items():
            if si != default_since:
                catchup_notes.append(f"水位线追赶：{name} 自 {si}")
    labs = enrich.load_lab_names(Path(data_dir))
    s, new = _run_window(cfg, store, fetchers, sc, since, until, since_by_source=since_by_source,
                         labs=labs, state=state, now=now)
    s.window = (since, until)
    s.notes.extend(catchup_notes)
    if sc is not None and sc.available and upd.retry_backlog_per_run > 0:
        used = s.llm.api_calls if s.llm else 0
        if used < cfg.llm.max_calls_per_run:
            fresh = s.touched_ids
            cands = [
                p for p in store.load_all()
                if (p.abstract or "").strip()
                and p.id not in fresh
                and (p.llm_status == "error"
                     or (p.llm_status == "unscored"
                         and p.llm_skip_reason not in ("no_abstract", "no_llm_flag")))
            ]
            cands.sort(key=lambda p: (-prefilter.rank_score(p), p.id))
            cands = cands[:upd.retry_backlog_per_run]
            if cands:
                scored, st = _score(cfg, sc, cands, used_calls=used)
                changed = [q for p, q in zip(cands, scored) if p != q]
                store.upsert(changed)
                s.llm = _add_stats(s.llm, st)
                s.n_backlog_scored = sum(
                    1 for p, q in zip(cands, scored)
                    if p.llm_status != "scored" and q.llm_status == "scored")
                s.notes.append(f"补打积压 {s.n_backlog_scored} 篇")
    s.notes = _dedup_notes(s.notes)
    if s.fetch_errors:
        s.notes.append("抓取失败的源：" + "、".join(sorted(s.fetch_errors)))
    state.add_run({
        "run_at": now.isoformat(), "since": since.isoformat(), "until": until.isoformat(),
        "fetched": dict(s.fetched), "kept_after_filter": s.kept_after_filter,
        "n_new": s.n_new, "n_updated": s.n_updated, "n_duplicates": s.n_duplicates,
        "llm_api_calls": s.llm.api_calls if s.llm else 0,
        "llm_errors": s.llm.errors if s.llm else 0,
        "fetch_errors": dict(s.fetch_errors),
        "incomplete": dict(s.incomplete),
    })
    state.save()
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
    labs = enrich.load_lab_names(Path(data_dir))

    total = RunSummary(window=(since, until))
    total.attempted = [f.name for f in fetchers]
    all_new: list[Paper] = []
    err_windows: dict[str, list[str]] = {}
    n_win = 0
    for lo, hi in _windows(since, until, cfg.pipeline.update.backfill_chunk_days):
        n_win += 1
        log.info("回填窗口 %s ~ %s", lo, hi)
        w, new = _run_window(cfg, store, fetchers, sc, lo, hi, labs=labs,
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
    # 与 filter_relevant 一致的混合判定：以库内高分核心样本建一次冻结原型，评分时排除自身
    proto = semantic.build_prototype(cfg, semantic.select_exemplars(papers))
    changed = []
    to_drop: list[str] = []
    n_rescued = 0
    for p in papers:
        q = prefilter.apply_keywords(p, cfg)
        q = q.model_copy(update={"semantic_score": semantic.score_text(
            semantic._paper_text(q), proto, exclude_ids=frozenset({q.id}))})
        r = prefilter.classify(q, cfg)
        if r is None:
            if (prune and q.source not in ("seed_pdf", "openreview")
                    and "seed_pdf" not in q.sources):
                to_drop.append(q.id)
                continue
            r = q
        elif "semantic:rescue" in r.matched_keywords:
            n_rescued += 1
        r = recompute_final_score(r, cfg)
        if r != p:
            changed.append(r)
    _, n_upd = store.upsert(changed)
    n_drop = store.delete(to_drop) if to_drop else 0
    s.n_updated = n_upd
    s.n_semantic_rescued = n_rescued
    s.notes.append(f"共 {len(papers)} 条，重算后变化 {n_upd} 条，语义捞回 {n_rescued} 条"
                   + (f"，删除 {n_drop} 条不再相关的抓取条目" if prune else ""))
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
    todo.sort(key=lambda p: (0 if p.llm_status in ("unscored", "error") else 1,
                             -prefilter.rank_score(p), p.id))
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
