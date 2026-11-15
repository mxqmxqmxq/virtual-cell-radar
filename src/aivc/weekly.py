"""Weekly Markdown report (ISO weeks, Monday-Sunday, grouped by first_seen)."""
from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .config import AppConfig
from .models import NewsItem, Paper
from .news_report import render_weekly_news
from .report import (
    _CIRCLE_CN,
    _circle_cn,
    _date_str,
    _is_scored,
    _links_line,
    _one_line,
    _scored_key,
    _unscored_key,
    title_with_topics,
)
from .storage import PaperStore
from .topics import Topic, match_topics, report_topics

log = logging.getLogger(__name__)

_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")
OVERVIEW_TOOL_NAME = "record_weekly_overview"


# ---------------------------------------------------------------- week math
def week_id(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y:04d}-W{w:02d}"


def parse_week(s: str) -> tuple[date, date]:
    m = _WEEK_RE.match((s or "").strip())
    if not m:
        raise ValueError(f"周格式无效: {s!r}（应为 YYYY-Www，例如 2026-W45）")
    y, w = int(m.group(1)), int(m.group(2))
    try:
        monday = date.fromisocalendar(y, w, 1)
    except ValueError as e:
        raise ValueError(f"周号不存在: {s!r}（{e}）") from e
    return monday, monday + timedelta(days=6)


def week_of(d: date) -> tuple[date, date]:
    monday = d - timedelta(days=d.isoweekday() - 1)
    return monday, monday + timedelta(days=6)


def last_complete_week(today: date) -> tuple[date, date]:
    sunday = today - timedelta(days=today.isoweekday())
    return sunday - timedelta(days=6), sunday


# ---------------------------------------------------------------- helpers
def _cell(text: str) -> str:
    return _one_line(text).replace("|", "/")


def _line(text: str) -> str:
    return _one_line(text)


def _in_week(p: Paper, start: date, end: date) -> bool:
    return start <= p.first_seen <= end


def _circle_of(p: Paper) -> str:
    return p.llm.circle if (_is_scored(p) and p.llm is not None) else p.circle


def _labels(cfg: AppConfig) -> tuple[dict[str, str], dict[str, str]]:
    tax = cfg.scope.taxonomy
    return ({t.id: t.label for t in tax.tasks}, {t.id: t.label for t in tax.paradigms})


def _label_list(ids: list[str], mapping: dict[str, str]) -> str:
    return ", ".join(mapping.get(i, i) for i in ids) if ids else "—"


def _sort_key_any(p: Paper) -> tuple:
    if _is_scored(p):
        return (0, *_scored_key(p))
    return (1, -p.keyword_score, 0.0, p.title)


def _top_entry(n: int, p: Paper, tasks_l: dict, par_l: dict, topics: list[Topic] | None = None) -> list[str]:
    llm = p.llm
    assert llm is not None
    title = title_with_topics(p, topics)
    score = p.final_score if p.final_score is not None else 0.0
    lines = [
        f"### {n}. {title}",
        "",
        f"- 分数：{score:.1f}（{_circle_cn(p)}）｜ 来源：{p.source} ｜ 日期：{_date_str(p)}",
        f"- 任务：{_label_list(llm.tasks, tasks_l)} ｜ 范式：{_label_list(llm.paradigms, par_l)}",
    ]
    if llm.novelty_zh:
        lines.append(f"- 新意：{_line(llm.novelty_zh)}")
    links = _links_line(p)
    if links:
        lines.append(links)
    return lines


def _unscored_line(p: Paper) -> str:
    line = (
        f"- **{_line(p.title)}** — {p.source} · {_date_str(p)} · "
        f"关键词分 {p.keyword_score:.1f}（{_circle_cn(p)}）"
    )
    if p.url:
        line += f" · [原文]({p.url})"
    return line


def _change(cur: int, prev: int) -> str:
    d = cur - prev
    return f"+{d}" if d > 0 else str(d)


def _dist_table(
    title: str, header: str, cur: list[Paper], prev: list[Paper], field_name: str, labels: dict[str, str]
) -> list[str]:
    out = [f"## {title}", ""]
    c_cur: Counter = Counter()
    c_prev: Counter = Counter()
    for p in cur:
        if p.llm:
            c_cur.update(set(getattr(p.llm, field_name)))
    for p in prev:
        if p.llm:
            c_prev.update(set(getattr(p.llm, field_name)))
    if not cur and not prev:
        out.append("本周与上周均无已打分论文，暂无分布数据。")
        out.append("")
        return out
    ids = [i for i in set(c_cur) | set(c_prev) if c_cur[i] or c_prev[i]]
    ids.sort(key=lambda i: (-c_cur[i], -c_prev[i], i))
    out.append(f"| {header} | 本周 | 上周 | 变化 |")
    out.append("| --- | ---: | ---: | ---: |")
    for i in ids:
        out.append(f"| {_cell(labels.get(i, i))} | {c_cur[i]} | {c_prev[i]} | {_change(c_cur[i], c_prev[i])} |")
    if not ids:
        out.append("| （已打分论文未标注） | 0 | 0 | 0 |")
    out.append("")
    return out


# ---------------------------------------------------------------- render
def render_weekly(
    all_papers: list[Paper],
    start: date,
    end: date,
    cfg: AppConfig,
    *,
    today: date | None = None,
    overview: str | None = None,
    topics: list[Topic] | None = None,
    news: list[NewsItem] | None = None,
) -> str:
    if topics is None:
        topics = report_topics(cfg.topics.topics)
    wcfg = cfg.pipeline.weekly
    wid = week_id(start)
    prev_start, prev_end = start - timedelta(days=7), end - timedelta(days=7)
    papers = [p for p in all_papers if _in_week(p, start, end)]
    prev = [p for p in all_papers if _in_week(p, prev_start, prev_end)]
    scored = [p for p in papers if _is_scored(p)]
    unscored = [p for p in papers if not _is_scored(p)]
    tasks_l, par_l = _labels(cfg)

    out: list[str] = [f"# AIVC 周报 · {wid}（{start.isoformat()} ~ {end.isoformat()}）", ""]
    quote = ["> 统计口径：按入库日期 first_seen 划分，本周 = 入库日期落在上述区间内的论文。"]
    if today is not None and end > today:
        quote.append(f"> 本周尚未结束，数据截至 {today.isoformat()}。")
    if unscored:
        quote.append(
            f"> 有 {len(unscored)} 篇未打分，运行 `uv run python -m aivc score` 后重新生成周报。"
        )
    out.extend(quote)
    out.append("")

    if not papers:
        out.append("本周无新增论文。")
        out.append("")
        out.append("可用 `--week YYYY-Www` 指定其他周，或用 `--end YYYY-MM-DD` 生成包含该日期的那一周（例如传今天的日期，生成进行中的本周）。")
        if news:
            out.append("")
            out.extend(render_weekly_news(news, start, end, cfg))
        return "\n".join(out) + "\n"

    # overview numbers
    circ = Counter(_circle_of(p) for p in papers)
    src = Counter(p.source for p in papers)
    src_str = "，".join(f"{k} {v}" for k, v in sorted(src.items(), key=lambda kv: (-kv[1], kv[0])))
    n_must = sum(1 for p in scored if p.must_read)
    out += [
        "## 本周概览",
        "",
        f"- 新增论文：{len(papers)} 篇（上周 {len(prev)} 篇）",
        f"- 圈层：{_CIRCLE_CN['core']} {circ['core']} ｜ {_CIRCLE_CN['peripheral']} {circ['peripheral']}"
        f" ｜ {_CIRCLE_CN['none']} {circ['none']}",
        f"- 打分：已打分 {len(scored)} ｜ 未打分 {len(unscored)}",
        f"- 必读（≥{cfg.llm.must_read_threshold:g} 分）：{n_must} 篇",
        f"- 来源：{src_str}",
        "",
    ]

    if overview and overview.strip():
        out += ["## 本周综述", "", _line(overview), ""]

    # Top
    out += ["## Top 论文", ""]
    top = sorted(scored, key=_scored_key)[: max(wcfg.top_n, 0)]
    if not top:
        out += ["本周暂无已打分论文，无法生成 Top 榜单。", ""]
    else:
        for n, p in enumerate(top, 1):
            out.extend(_top_entry(n, p, tasks_l, par_l, topics))
            out.append("")

    # Unscored
    if unscored:
        out += ["## 未打分论文（按关键词分排序）", ""]
        us = sorted(unscored, key=_unscored_key)
        shown = us[: max(wcfg.max_unscored, 0)]
        out.extend(_unscored_line(p) for p in shown)
        if len(us) > len(shown):
            out.append("")
            out.append(f"> 另有 {len(us) - len(shown)} 篇未列出。")
        out.append("")

    # New datasets
    out += ["## 新数据集 / 基准", ""]
    newds = [p for p in scored if p.llm and p.llm.releases_new_dataset]
    if not newds:
        out += ["本周无（或尚未打分）。", ""]
    else:
        for p in sorted(newds, key=_scored_key):
            names = ", ".join(p.llm.new_dataset_names) if p.llm and p.llm.new_dataset_names else "（未给出名称）"
            line = f"- **{_line(names)}** — {_line(p.title)} · {_date_str(p)}"
            if p.url:
                line += f" · [原文]({p.url})"
            out.append(line)
        out.append("")

    if news:
        out += render_weekly_news(news, start, end, cfg)

    # distributions
    prev_scored = [p for p in prev if _is_scored(p)]
    out += _dist_table("范式分布（对比上周）", "范式", scored, prev_scored, "paradigms", par_l)
    out += _dist_table("任务分布（对比上周）", "任务", scored, prev_scored, "tasks", tasks_l)

    # topic tracking
    if topics:
        out += ["## 主题追踪", ""]
        any_hit = False
        for topic in topics:
            hits = [p for p in papers if topic.id in {t.id for t in match_topics(p, [topic])}]
            if not hits:
                continue
            any_hit = True
            items = sorted(hits, key=_sort_key_any)
            shown = items[: max(wcfg.max_per_topic, 0)]
            out += [f"### {_line(topic.name)}", ""]
            for p in shown:
                if _is_scored(p):
                    tag = f"{p.final_score if p.final_score is not None else 0.0:.1f} 分"
                else:
                    tag = f"未打分，关键词分 {p.keyword_score:.1f}"
                line = f"- {_line(p.title)} — {tag} · {p.source} · {_date_str(p)}"
                if p.url:
                    line += f" · [原文]({p.url})"
                out.append(line)
            if len(items) > len(shown):
                out.append("")
                out.append(f"> 另有 {len(items) - len(shown)} 篇。")
            out.append("")
        if not any_hit:
            out += ["本周无命中主题的论文。", ""]

    return "\n".join(out).rstrip("\n") + "\n"


def write_weekly(
    all_papers: list[Paper],
    start: date,
    end: date,
    cfg: AppConfig,
    reports_dir: Path,
    *,
    today: date | None = None,
    overview: str | None = None,
    topics: list[Topic] | None = None,
    news: list[NewsItem] | None = None,
) -> Path:
    d = Path(reports_dir) / "weekly"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{week_id(start)}.md"
    path.write_text(
        render_weekly(all_papers, start, end, cfg, today=today, overview=overview, topics=topics, news=news),
        encoding="utf-8"
    )
    return path


# ---------------------------------------------------------------- overview
def _overview_prompt(papers: list[Paper], cfg: AppConfig) -> tuple[str, str]:
    tasks_l, par_l = _labels(cfg)
    scored = [p for p in papers if _is_scored(p)]
    top = sorted(scored, key=_scored_key)[: max(cfg.pipeline.weekly.top_n, 0)]
    pc: Counter = Counter()
    tc: Counter = Counter()
    for p in scored:
        if p.llm:
            pc.update(set(p.llm.paradigms))
            tc.update(set(p.llm.tasks))
    lines = [f"本周新增论文 {len(papers)} 篇，其中已打分 {len(scored)} 篇。", "", "Top 论文："]
    if top:
        for p in top:
            lines.append(f"- {_line(p.title)} | {_line(p.llm.novelty_zh) if p.llm else ''}")
    else:
        for p in sorted(papers, key=_unscored_key)[:5]:
            lines.append(f"- {_line(p.title)}")
    lines.append("")
    lines.append("范式计数：" + ("，".join(f"{par_l.get(k, k)} {v}" for k, v in pc.most_common()) or "无"))
    lines.append("任务计数：" + ("，".join(f"{tasks_l.get(k, k)} {v}" for k, v in tc.most_common()) or "无"))
    system = (
        "你是虚拟细胞与 AI 药物发现领域的文献周报编辑。请用中文写一段 3-5 句的「本周综述」，"
        "概括本周的主要方向、值得关注的论文与趋势；不要编造输入中没有的信息；论文标题与术语保留英文。"
        f"请通过 {OVERVIEW_TOOL_NAME} 工具返回。"
    )
    return system, "\n".join(lines)


def _generate_overview(
    cfg: AppConfig, papers: list[Paper], data_dir: Path, scorer, notes: list[str]
) -> str | None:
    if scorer is None:
        from .llm import LLMCache, LLMScorer

        scorer = LLMScorer(cfg, LLMCache(Path(data_dir) / cfg.llm.cache_file))
    if not scorer.available:
        notes.append("已开启 llm_overview，但没有可用的 LLM 后端，已跳过本周综述")
        return None
    system, user = _overview_prompt(papers, cfg)
    tool = {
        "name": OVERVIEW_TOOL_NAME,
        "description": "记录本周综述（3-5 句中文）",
        "input_schema": {
            "type": "object",
            "properties": {"overview_zh": {"type": "string", "description": "3-5 句中文综述"}},
            "required": ["overview_zh"],
        },
    }
    res = scorer.complete_structured(
        system=system,
        user=user,
        tool=tool,
        model=cfg.pipeline.weekly.overview_model,
        max_tokens=cfg.pipeline.weekly.overview_max_tokens,
    )
    text = res.get("overview_zh") if isinstance(res, dict) else None
    if not isinstance(text, str) or not text.strip():
        notes.append("本周综述生成失败或为空，已跳过")
        return None
    return text.strip()


# ---------------------------------------------------------------- run
@dataclass
class WeeklyResult:
    path: Path
    week_id: str
    start: date
    end: date
    n_total: int
    n_scored: int
    n_unscored: int
    overview_written: bool = False
    notes: list[str] = field(default_factory=list)


def run_weekly(
    cfg: AppConfig,
    *,
    data_dir: Path,
    reports_dir: Path,
    week: str | None = None,
    end: date | None = None,
    today: date | None = None,
    scorer=None,
    topics: list[Topic] | None = None,
    papers: list[Paper] | None = None,
    news: list[NewsItem] | None = None,
) -> WeeklyResult:
    if week is not None and end is not None:
        raise ValueError("--week 与 --end 不能同时指定")
    today = today or date.today()
    if week is not None:
        start, stop = parse_week(week)
    elif end is not None:
        start, stop = week_of(end)
    else:
        start, stop = last_complete_week(today)

    if papers is None:
        papers = PaperStore(Path(data_dir)).load_all()
    if news is None:
        from .news import NewsStore
        news = NewsStore(Path(data_dir)).load_all()
    in_week = [p for p in papers if _in_week(p, start, stop)]
    n_scored = sum(1 for p in in_week if _is_scored(p))
    notes: list[str] = []
    overview: str | None = None
    if cfg.pipeline.weekly.llm_overview and in_week:
        overview = _generate_overview(cfg, in_week, Path(data_dir), scorer, notes)
    path = write_weekly(
        papers, start, stop, cfg, Path(reports_dir), today=today, overview=overview, topics=topics, news=news
    )
    return WeeklyResult(
        path=path,
        week_id=week_id(start),
        start=start,
        end=stop,
        n_total=len(in_week),
        n_scored=n_scored,
        n_unscored=len(in_week) - n_scored,
        overview_written=overview is not None,
        notes=notes,
    )
