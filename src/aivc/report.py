"""Render the daily Markdown digest of papers (fixed template, Chinese labels)."""
from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import Path

from .models import LLMAnnotation, NewsItem, Paper
from .news_report import render_major_section
from .topics import Topic, match_topics

_CIRCLE_CN = {"core": "核心圈", "peripheral": "外围圈", "none": "范围外"}
_SECTION_MUST_READ = "值得精读"
_SECTION_OTHER = "其他已打分"
_SECTION_UNSCORED = "未打分（按关键词分排序）"


def _is_scored(p: Paper) -> bool:
    return p.llm_status == "scored" and p.llm is not None


def _circle_cn(p: Paper) -> str:
    circle = p.llm.circle if (_is_scored(p) and p.llm is not None) else p.circle
    return _CIRCLE_CN.get(circle, circle)


def _one_line(text: str) -> str:
    return " ".join(text.splitlines()).strip()


def _date_str(p: Paper) -> str:
    return (p.published or p.first_seen).isoformat()


def _authors(p: Paper) -> str:
    if not p.authors:
        return "（未提供）"
    head = ", ".join(p.authors[:3])
    if len(p.authors) > 3:
        head += f" 等（共 {len(p.authors)} 人）"
    return head


def _join_or_dash(items: list[str]) -> str:
    return ", ".join(items) if items else "—"


def title_with_topics(p: Paper, topics: list[Topic] | None) -> str:
    title = _one_line(p.title)
    if topics:
        names = [t.name for t in match_topics(p, topics)]
        if names:
            title += f" 【相关课题：{', '.join(names)}】"
    return title


def _must_read_heading(threshold: float | None) -> str:
    if threshold is None:
        return _SECTION_MUST_READ
    return f"{_SECTION_MUST_READ}（≥{threshold:g} 分）"


def _links_line(p: Paper) -> str | None:
    parts: list[str] = []
    if p.url:
        parts.append(f"[原文]({p.url})")
    if p.pdf_url:
        parts.append(f"[PDF]({p.pdf_url})")
    if p.code_url:
        parts.append(f"[代码]({p.code_url})")
    if not parts:
        return None
    return "- 链接：" + " · ".join(parts)


def _scored_entry(n: int, p: Paper, topics: list[Topic] | None = None) -> list[str]:
    llm: LLMAnnotation | None = p.llm
    assert llm is not None  # guaranteed by _is_scored
    score = p.final_score if p.final_score is not None else 0.0
    if llm.releases_new_dataset:
        names = ", ".join(llm.new_dataset_names)
        new_ds = f"是（{names}）" if names else "是"
    else:
        new_ds = "否"
    lines = [
        f"### {n}. {title_with_topics(p, topics)}",
        "",
        f"- 分数：{score:.1f}（{_circle_cn(p)}）｜ 来源：{p.source} ｜ 日期：{_date_str(p)}",
        f"- 作者：{_authors(p)}",
        f"- 任务：{_join_or_dash(llm.tasks)} ｜ 范式：{_join_or_dash(llm.paradigms)}",
        f"- 数据：{_join_or_dash(llm.datasets_used)} ｜ **新数据集/基准**：{new_ds}",
        f"- 新意：{llm.novelty_zh}",
        f"- 摘要：{llm.summary_zh}",
    ]
    links = _links_line(p)
    if links:
        lines.append(links)
    return lines


def _unscored_line(p: Paper, topics: list[Topic] | None = None) -> str:
    line = (
        f"- **{title_with_topics(p, topics)}** — {p.source} · {_date_str(p)} · "
        f"关键词分 {p.keyword_score:.1f}（{_circle_cn(p)}）"
        f"· 命中：{_join_or_dash(p.matched_keywords)}"
    )
    if p.url:
        line += f" · [原文]({p.url})"
    return line


def _scored_key(p: Paper) -> tuple:
    return (-(p.final_score if p.final_score is not None else 0.0), -p.keyword_score, p.title)


def _unscored_key(p: Paper) -> tuple:
    return (-p.keyword_score, p.title)


def render_daily(
    papers: list[Paper],
    run_date: date,
    *,
    window: tuple[date, date] | None = None,
    notes: list[str] | None = None,
    max_items: int = 200,
    title: str | None = None,
    topics: list[Topic] | None = None,
    must_read_threshold: float | None = None,
    pinned_news: list[NewsItem] | None = None,
) -> str:
    scored = [p for p in papers if _is_scored(p)]
    unscored = [p for p in papers if not _is_scored(p)]
    must = sorted((p for p in scored if p.must_read), key=_scored_key)
    others = sorted((p for p in scored if not p.must_read), key=_scored_key)
    unscored_sorted = sorted(unscored, key=_unscored_key)

    heading = title if title is not None else f"AIVC 每日论文 · {run_date.isoformat()}"
    out: list[str] = [f"# {_one_line(heading)}", ""]
    header: list[str] = []

    window_part = (
        f"抓取窗口：{window[0].isoformat()} ~ {window[1].isoformat()}" if window else None
    )
    if papers:
        src_counts = Counter(p.source for p in papers)
        src_str = "，".join(
            f"{k} {v}" for k, v in sorted(src_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        )
        stats = (
            f"新增 {len(papers)} 篇（已打分 {len(scored)}，未打分 {len(unscored)}）"
            f"｜ 来源：{src_str}"
        )
        header.append(f"> {window_part} ｜ {stats}" if window_part else f"> {stats}")
    elif window_part:
        header.append(f"> {window_part}")
    for note in notes or []:
        header.append(f"> 注意：{_one_line(note)}")
    out.extend(header)
    if header:
        out.append("")
    if pinned_news:
        out.extend(render_major_section(pinned_news))

    if not papers:
        out.append("> 今日无新增论文。")
        return "\n".join(out) + "\n"

    # Apply max_items across the ordered list: must-read, other scored, unscored.
    remaining = max(max_items, 0)

    def take(items: list[Paper]) -> list[Paper]:
        nonlocal remaining
        chunk = items[:remaining]
        remaining -= len(chunk)
        return chunk

    total_ordered = len(must) + len(others) + len(unscored_sorted)
    visible_must = take(must)
    visible_others = take(others)
    visible_unscored = take(unscored_sorted)
    shown = len(visible_must) + len(visible_others) + len(visible_unscored)

    body: list[str] = []
    n = 0
    for section, items in (
        (_must_read_heading(must_read_threshold), visible_must),
        (_SECTION_OTHER, visible_others),
    ):
        if not items:
            continue
        body.append(f"## {section}")
        body.append("")
        for p in items:
            n += 1
            body.extend(_scored_entry(n, p, topics))
            body.append("")

    if visible_unscored:
        body.append(f"## {_SECTION_UNSCORED}")
        body.append("")
        for p in visible_unscored:
            body.append(_unscored_line(p, topics))
        body.append("")

    out.extend(body)
    omitted = total_ordered - shown
    if omitted > 0:
        out.append(f"> 另有 {omitted} 篇未列出。")
    return "\n".join(out).rstrip("\n") + "\n"


def write_daily(
    papers: list[Paper],
    run_date: date,
    reports_dir: Path,
    *,
    window: tuple[date, date] | None = None,
    notes: list[str] | None = None,
    max_items: int = 200,
    subdir: str = "daily",
    filename: str | None = None,
    title: str | None = None,
    topics: list[Topic] | None = None,
    must_read_threshold: float | None = None,
    pinned_news: list[NewsItem] | None = None,
) -> Path:
    target_dir = Path(reports_dir) / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / (filename or f"{run_date.isoformat()}.md")
    if not papers and path.exists() and "今日无新增论文" not in path.read_text(encoding="utf-8"):
        # same-day rerun with nothing new: keep the earlier non-empty report
        return path
    content = render_daily(
        papers,
        run_date,
        window=window,
        notes=notes,
        max_items=max_items,
        title=title,
        topics=topics,
        must_read_threshold=must_read_threshold,
        pinned_news=pinned_news,
    )
    path.write_text(content, encoding="utf-8")
    return path
