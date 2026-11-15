"""Export the paper store to a static site directory (JSON data + copied web assets) and serve it locally."""
from __future__ import annotations

import json
import os
import re
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .config import AppConfig
from .mdhtml import render as render_md
from .models import NewsItem, Paper
from .news import NewsStore, interleave_by_company
from .news_site import export_companies, export_news
from .resources import MetricsStore, ResourceStore
from .resources_site import export_resources
from .storage import PaperStore
from .topics import Topic

CIRCLE_LABELS = {"core": "核心圈", "peripheral": "外围圈", "none": "范围外"}
_WEEK_ID_RE = re.compile(r"^(\d{4})-W(\d{2})$")


@dataclass
class SiteBuildResult:
    site_dir: Path
    n_papers: int
    n_weekly: int
    n_news: int = 0
    n_resources: int = 0
    files: list[Path] = field(default_factory=list)


class ServerStartError(OSError):
    """本地服务启动失败（通常是端口被占用）。"""


# ---------- week helpers ----------

def week_id(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_range(d: date) -> tuple[date, date]:
    start = d - timedelta(days=d.weekday())
    return start, start + timedelta(days=6)


def week_range_from_id(wid: str) -> tuple[date, date] | None:
    m = _WEEK_ID_RE.match(wid)
    if not m:
        return None
    try:
        start = date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
    except ValueError:
        return None
    return start, start + timedelta(days=6)


# ---------- export ----------

def _is_scored(p: Paper) -> bool:
    return p.llm_status == "scored" and p.llm is not None


def _paper_date(p: Paper) -> date:
    return p.published or p.first_seen


def export_papers(papers: list[Paper], *, include_local: bool = False) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in papers:
        scored = _is_scored(p)
        llm = p.llm if scored else None
        d = _paper_date(p)
        row = {
            "id": p.id,
            "title": p.title,
            "abstract": p.abstract,
            "authors": list(p.authors),
            "source": p.source,
            "venue": p.venue,
            "published": p.published.isoformat() if p.published else None,
            "first_seen": p.first_seen.isoformat(),
            "date": d.isoformat(),
            "month": d.strftime("%Y-%m"),
            "week": week_id(p.first_seen),
            "url": p.url,
            "pdf_url": p.pdf_url,
            "code_url": p.code_url,
            "doi": p.doi,
            "license": p.license,
            "categories": list(p.categories),
            "keywords": list(p.keywords),
            "tags": list(p.tags),
            "matched_keywords": list(p.matched_keywords),
            "keyword_score": p.keyword_score,
            "circle": llm.circle if llm else p.circle,
            "scored": scored,
            "llm_status": p.llm_status,
            "final_score": p.final_score if scored else None,
            "must_read": bool(p.must_read),
            "relevance": llm.relevance if llm else None,
            "tasks": list(llm.tasks) if llm else [],
            "paradigms": list(llm.paradigms) if llm else [],
            "datasets_used": list(llm.datasets_used) if llm else [],
            "releases_new_dataset": bool(llm.releases_new_dataset) if llm else False,
            "new_dataset_names": list(llm.new_dataset_names) if llm else [],
            "novelty_zh": llm.novelty_zh if llm else "",
            "summary_zh": llm.summary_zh if llm else "",
        }
        if include_local:
            row["local_file"] = p.local_file
        out.append(row)
    return out


def collect_weekly(reports_dir: Path) -> list[dict[str, Any]]:
    wdir = Path(reports_dir) / "weekly"
    if not wdir.is_dir():
        return []
    reports = []
    for f in wdir.glob("*.md"):
        rng = week_range_from_id(f.stem)
        if rng is None:
            continue
        text = f.read_text(encoding="utf-8")
        title = f.stem
        for line in text.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
            if line.strip():
                break
        reports.append({
            "id": f.stem,
            "start": rng[0].isoformat(),
            "end": rng[1].isoformat(),
            "title": title,
            "html": render_md(text),
        })
    reports.sort(key=lambda r: r["id"], reverse=True)
    return reports


def build_datasets(papers: list[Paper], top_used: int = 30) -> dict[str, Any]:
    new_rows: list[dict[str, Any]] = []
    for p in papers:
        if not (_is_scored(p) and p.llm and p.llm.releases_new_dataset):
            continue
        names = [n.strip() for n in p.llm.new_dataset_names if n and n.strip()] or [None]
        for name in names:
            new_rows.append({
                "name": name, "paper_id": p.id, "paper_title": p.title,
                "date": _paper_date(p).isoformat(), "url": p.url, "pdf_url": p.pdf_url,
                "final_score": p.final_score,
            })
    new_rows.sort(key=lambda r: r["date"], reverse=True)

    counts: dict[str, Counter] = defaultdict(Counter)
    pids: dict[str, list[str]] = defaultdict(list)
    for p in papers:
        if not (_is_scored(p) and p.llm):
            continue
        seen: set[str] = set()
        for raw in p.llm.datasets_used:
            name = raw.strip()
            if not name:
                continue
            key = name.casefold()
            counts[key][name] += 1
            if key not in seen:
                seen.add(key)
                pids[key].append(p.id)
    used = [
        {"name": counts[k].most_common(1)[0][0], "count": len(pids[k]), "paper_ids": pids[k]}
        for k in counts
    ]
    used.sort(key=lambda r: (-r["count"], r["name"].casefold()))
    return {"new_datasets": new_rows, "used_top": used[:top_used]}


def build_meta(
    cfg: AppConfig, papers: list[Paper], weekly: list[dict[str, Any]], today: date, generated_at: str,
    news_items: list[dict[str, Any]] | None = None, resources_total: int = 0,
) -> dict[str, Any]:
    news_items = [n for n in (news_items or []) if n.get("scored")]  # 未打分的前端默认隐藏，不计入
    since = (today - timedelta(days=6)).isoformat()
    start, end = week_range(today)
    cur = [p for p in papers if start <= p.first_seen <= end]
    months = sorted({_paper_date(p).strftime("%Y-%m") for p in papers})
    latest = None
    if weekly:
        w = weekly[0]
        latest = {k: w[k] for k in ("id", "title", "start", "end")}
    return {
        "generated_at": generated_at,
        "today": today.isoformat(),
        "current_week": {"id": week_id(today), "start": start.isoformat(), "end": end.isoformat()},
        "stats": {
            "total": len(papers),
            "scored": sum(1 for p in papers if _is_scored(p)),
            "unscored": sum(1 for p in papers if not _is_scored(p)),
            "must_read": sum(1 for p in papers if p.must_read),
            "new_datasets": sum(1 for p in papers if _is_scored(p) and p.llm and p.llm.releases_new_dataset),
            "this_week_new": len(cur),
            "this_week_must_read": sum(1 for p in cur if p.must_read),
            "news_total": len(news_items),
            "resources_total": resources_total,
            "news_7d": sum(1 for n in news_items if since <= n["date"] <= today.isoformat()),
            "by_source": dict(Counter(p.source for p in papers)),
            "by_circle": dict(Counter((p.llm.circle if _is_scored(p) and p.llm else p.circle) for p in papers)),
        },
        "taxonomy": {
            "tasks": [{"id": t.id, "label": t.label} for t in cfg.scope.taxonomy.tasks],
            "paradigms": [{"id": t.id, "label": t.label} for t in cfg.scope.taxonomy.paradigms],
        },
        "circle_labels": dict(CIRCLE_LABELS),
        "months": months,
        "must_read_threshold": cfg.llm.must_read_threshold,
        "latest_weekly": latest,
    }


# ---------- home page data ----------

SOURCE_LABELS = {
    "arxiv": "arXiv", "biorxiv": "bioRxiv", "medrxiv": "medRxiv", "openreview": "OpenReview",
    "journal": "期刊", "seed_pdf": "本地种子 PDF", "other": "其他",
}
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_LIST_RE = re.compile(r"^(?:[-*+]\s+|\d+[.)]\s+)")


def _plain_lines(md: str, limit: int = 8) -> list[str]:
    """Markdown -> up to `limit` plain-text lines (skips the first H1, blanks and blockquotes)."""
    out: list[str] = []
    skipped_title = False
    for raw in md.splitlines():
        line = raw.strip()
        if not line or line.startswith(">"):
            continue
        if not skipped_title and line.startswith("# "):
            skipped_title = True
            continue
        line = line.lstrip("#").strip()
        line = _MD_LIST_RE.sub("", line)
        line = _MD_LINK_RE.sub(r"\1", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"`([^`]*)`", r"\1", line)
        line = re.sub(r"<[^>]*>", "", line).strip()
        if not line:
            continue
        out.append(line[:120])
        if len(out) >= limit:
            break
    return out


def build_home(
    cfg: AppConfig, papers: list[Paper], weekly: list[dict[str, Any]], today: date, generated_at: str,
    reports_dir: Path, news_export: dict[str, Any] | None = None,
) -> dict[str, Any]:
    wstart, wend = week_range(today)
    scored = [p for p in papers if _is_scored(p)]

    def rank(p: Paper, score: float) -> tuple:
        # score desc, date desc, id asc
        return (-score, -_paper_date(p).toordinal(), p.id)

    items: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(cands: list[Paper], reason: str) -> None:
        for p in cands:
            if len(items) >= 6:
                return
            if p.id not in seen:
                seen.add(p.id)
                items.append({"id": p.id, "reason": reason})

    add(sorted((p for p in scored if p.must_read and wstart <= p.first_seen <= wend),
               key=lambda p: rank(p, p.final_score or 0.0)), "week_must")
    add(sorted(scored, key=lambda p: rank(p, p.final_score or 0.0)), "top_final")
    add(sorted((p for p in papers if not _is_scored(p)), key=lambda p: rank(p, p.keyword_score)), "top_keyword")

    circles = Counter((p.llm.circle if _is_scored(p) and p.llm else p.circle) for p in papers)
    progress = {
        "total": len(papers), "scored": len(scored), "unscored": len(papers) - len(scored),
        "must_read": sum(1 for p in papers if p.must_read),
        "new_datasets": sum(1 for p in scored if p.llm and p.llm.releases_new_dataset),
        "core": circles.get("core", 0), "peripheral": circles.get("peripheral", 0), "none": circles.get("none", 0),
        "this_week_new": sum(1 for p in papers if wstart <= p.first_seen <= wend),
    }
    digest = _build_digest(cfg, papers, weekly, today, news_export)
    progress["news_7d"] = digest["n_news_7d"]

    # sources
    src_counts = Counter(p.source for p in papers)
    for k in ("arxiv", "biorxiv", "medrxiv", "openreview"):
        src_counts.setdefault(k, 0)
    cfg_enabled = {
        "arxiv": cfg.sources.arxiv.enabled, "biorxiv": cfg.sources.biorxiv.enabled,
        "medrxiv": cfg.sources.medrxiv.enabled, "openreview": cfg.sources.openreview.enabled,
    }
    active: list[dict[str, Any]] = []
    planned: list[dict[str, Any]] = []
    for sid, n in src_counts.items():
        on = n > 0 or cfg_enabled.get(sid, False)
        (active if on else planned).append(
            {"id": sid, "label": SOURCE_LABELS.get(sid, sid), "kind": "source",
             "status": "active" if on else "planned", "count": n})
    active.sort(key=lambda r: -r["count"])  # stable: ties keep insertion order
    for f in cfg.sources.journals_rss.feeds:
        (active if cfg.sources.journals_rss.enabled else planned).append(
            {"id": f.name, "label": f.name, "kind": "journal",
             "status": "active" if cfg.sources.journals_rss.enabled else "planned", "count": 0})
    for v in cfg.sources.openreview.venues:
        (active if cfg.sources.openreview.enabled else planned).append(
            {"id": v, "label": v, "kind": "venue",
             "status": "active" if cfg.sources.openreview.enabled else "planned", "count": 0})
    # keep count-ordered sources first, then enabled journals/venues, then planned in config order
    sources = active + planned

    # heat
    tax = cfg.scope.taxonomy
    pc: Counter = Counter()
    tc: Counter = Counter()
    cells: Counter = Counter()
    pids = {t.id for t in tax.paradigms}
    tids = {t.id for t in tax.tasks}
    for p in scored:
        assert p.llm
        ps = {x for x in p.llm.paradigms if x in pids}
        ts = {x for x in p.llm.tasks if x in tids}
        pc.update(ps)
        tc.update(ts)
        for a in ps:
            for b in ts:
                cells[(a, b)] += 1
    heat = {
        "paradigms": [{"id": t.id, "label": t.label, "count": pc.get(t.id, 0)} for t in tax.paradigms],
        "tasks": [{"id": t.id, "label": t.label, "count": tc.get(t.id, 0)} for t in tax.tasks],
        "cells": [{"p": a.id, "t": b.id, "n": cells[(a.id, b.id)]}
                  for a in tax.paradigms for b in tax.tasks if cells.get((a.id, b.id), 0) > 0],
        "max": max(cells.values(), default=0),
        "n_scored": len(scored),
    }

    kw = Counter(k for p in papers for k in set(p.matched_keywords))
    keywords_top = [{"name": k, "count": n} for k, n in sorted(kw.items(), key=lambda kv: (-kv[1], kv[0]))[:12]]

    cur_monday = wstart
    weeks = []
    for i in range(11, -1, -1):
        ws = cur_monday - timedelta(days=7 * i)
        we = ws + timedelta(days=6)
        inw = [p for p in papers if ws <= p.first_seen <= we]
        weeks.append({"id": week_id(ws), "start": ws.isoformat(), "count": len(inw),
                      "scored": sum(1 for p in inw if _is_scored(p))})

    # demo
    with_kw = sorted((p for p in papers if p.matched_keywords),
                     key=lambda p: (-p.first_seen.toordinal(), -p.keyword_score, p.id))
    rest = sorted((p for p in papers if not p.matched_keywords),
                  key=lambda p: (-p.first_seen.toordinal(), -p.keyword_score, p.id))
    pipeline_ids = [p.id for p in (with_kw + rest)[:6]]
    tagged = [p for p in scored if p.llm and p.llm.tasks and p.llm.paradigms]
    tag_id: str | None = None
    tag_scored = False
    if tagged:
        tag_id = min(tagged, key=lambda p: rank(p, p.final_score or 0.0)).id
        tag_scored = True
    elif papers:
        tag_id = min(papers, key=lambda p: rank(p, p.keyword_score)).id

    wk = None
    if weekly:
        w = weekly[0]
        try:
            md = (Path(reports_dir) / "weekly" / f"{w['id']}.md").read_text(encoding="utf-8")
            lines = _plain_lines(md)
        except OSError:
            lines = []
        wk = {"id": w["id"], "title": w["title"], "start": w["start"], "end": w["end"],
              "lines": lines, "count": len(weekly)}

    return {
        "generated_at": generated_at,
        "featured": {"mode": items[0]["reason"] if items else "empty", "week": week_id(today), "items": items},
        "progress": progress,
        "sources": sources,
        "heat": heat,
        "keywords_top": keywords_top,
        "weeks": weeks,
        "demo": {"pipeline_ids": pipeline_ids, "tag_id": tag_id, "tag_scored": tag_scored},
        "weekly": wk,
        "digest": digest,
    }


def _build_digest(
    cfg: AppConfig, papers: list[Paper], weekly: list[dict[str, Any]], today: date,
    news_export: dict[str, Any] | None,
) -> dict[str, Any]:
    start = today - timedelta(days=6)
    pstart, pend = today - timedelta(days=13), today - timedelta(days=7)
    in_win = [p for p in papers if start <= p.first_seen <= today]

    def prank(p: Paper) -> tuple:
        # scored first (final_score desc), then unscored (keyword_score desc); date desc, id asc
        if _is_scored(p):
            return (0, -(p.final_score or 0.0), -_paper_date(p).toordinal(), p.id)
        return (1, -p.keyword_score, -_paper_date(p).toordinal(), p.id)

    fallback = len(in_win) < 3
    if fallback:
        pool = sorted((p for p in papers if p.first_seen <= today), key=lambda p: (-p.first_seen.toordinal(), p.id))[:30]
        label = "最近入库"
    else:
        pool = in_win
        label = f"近 7 天（{start.strftime('%m-%d')} ~ {today.strftime('%m-%d')}）"
    paper_ids = [p.id for p in sorted(pool, key=prank)[:30]]

    # new datasets (scored papers in window; no fallback)
    ds: list[dict[str, str]] = []
    seen_ds: set[str] = set()
    cands = sorted((p for p in in_win if _is_scored(p) and p.llm and p.llm.releases_new_dataset),
                   key=lambda p: (-_paper_date(p).toordinal(), p.id))
    for p in cands:
        assert p.llm
        for name in p.llm.new_dataset_names:
            nm = name.strip()
            if nm and nm.lower() not in seen_ds and len(ds) < 6:
                seen_ds.add(nm.lower())
                ds.append({"name": nm, "paper_id": p.id, "paper_title": p.title, "date": _paper_date(p).isoformat()})

    # movers: paradigm counts of scored papers, this window vs previous 7 days
    pids = {t.id: t.label for t in cfg.scope.taxonomy.paradigms}
    cur: Counter = Counter()
    prev: Counter = Counter()
    for p in papers:
        if not _is_scored(p):
            continue
        assert p.llm
        tgt = cur if start <= p.first_seen <= today else prev if pstart <= p.first_seen <= pend else None
        if tgt is not None:
            tgt.update({x for x in p.llm.paradigms if x in pids})
    n_scored_two = sum(1 for p in papers if _is_scored(p) and pstart <= p.first_seen <= today)
    movers: list[dict[str, Any]] = []
    if n_scored_two >= 5:
        rows = [{"id": i, "label": pids[i], "cur": cur.get(i, 0), "prev": prev.get(i, 0),
                 "delta": cur.get(i, 0) - prev.get(i, 0)} for i in pids if cur.get(i, 0) or prev.get(i, 0)]
        rows.sort(key=lambda r: (-abs(r["delta"]), -r["cur"], r["id"]))
        movers = rows[:6]

    # news
    nstart14 = today - timedelta(days=13)
    n_items = [r for r in (news_export or {}).get("items", []) if r.get("scored")]
    recent = [r for r in n_items if nstart14 <= date.fromisoformat(r["date"]) <= today]
    recent.sort(key=lambda r: (not r["major"], -(r["relevance"] or 0), -date.fromisoformat(r["date"]).toordinal(), r["id"]))
    # 同（重大、相关度）区段内按公司轮转，避免单一公司占满速览
    recent = interleave_by_company(recent, key=lambda r: (not r["major"], -(r["relevance"] or 0)))
    n7 = sum(1 for r in n_items if start <= date.fromisoformat(r["date"]) <= today)

    return {
        "window": {"start": start.isoformat(), "end": today.isoformat(), "days": 7, "label": label},
        "fallback": fallback,
        "paper_ids": paper_ids,
        "n_papers": len(in_win),
        "n_must_read": sum(1 for p in in_win if p.must_read),
        "new_datasets": ds,
        "movers": movers,
        "news_ids": [r["id"] for r in recent[:8]],
        "n_news_7d": n7,
        "weekly": {"id": weekly[0]["id"], "title": weekly[0]["title"]} if weekly else None,
    }


# ---------- writing ----------

def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def build_site(
    cfg: AppConfig,
    *,
    data_dir: Path,
    reports_dir: Path,
    site_dir: Path,
    today: date | None = None,
    assets_dir: Path | None = None,
    top_used: int = 30,
    papers: list[Paper] | None = None,
    topics: list[Topic] | None = None,
    include_local: bool = False,
) -> SiteBuildResult:
    today = today or date.today()
    assets = Path(assets_dir) if assets_dir is not None else Path(__file__).parent / "web"
    if not assets.is_dir():
        raise FileNotFoundError(f"静态资源目录不存在: {assets}（请确认 src/aivc/web/ 已创建）")
    site_dir = Path(site_dir)
    site_dir.mkdir(parents=True, exist_ok=True)

    papers = list(papers) if papers is not None else PaperStore(Path(data_dir)).load_all()
    topics = list(topics) if topics is not None else list(cfg.topics.topics)
    papers.sort(key=lambda p: (_paper_date(p), p.id), reverse=True)
    weekly = collect_weekly(Path(reports_dir))
    now = datetime.now().replace(microsecond=0).isoformat()

    news_all: list[NewsItem] = NewsStore(Path(data_dir)).load_all()
    news_json = export_news(news_all, cfg, generated_at=now)

    res_store = ResourceStore(Path(data_dir))
    resources_json = export_resources(
        res_store.load_curated(), MetricsStore(Path(data_dir)).load(), [p.id for p in papers], cfg,
        today=today, generated_at=now, n_candidates=sum(1 for r in res_store.load_candidates() if r.status == "candidate"))

    # copy assets first; data files are written afterwards so they always win
    shutil.copytree(assets, site_dir, dirs_exist_ok=True)

    exported = export_papers(papers, include_local=include_local)
    data = site_dir / "data"
    targets = {
        data / "papers.json": {"generated_at": now, "count": len(exported), "papers": exported},
        data / "meta.json": build_meta(cfg, papers, weekly, today, now, news_json["items"],
                                    resources_total=resources_json["count"]),
        data / "datasets.json": build_datasets(papers, top_used),
        data / "weekly.json": {"reports": weekly},
        data / "topics.json": {"version": 1, "presets": [
            {"id": t.id, "name": t.name, "terms": list(t.terms), "default_enabled": t.default_enabled} for t in topics]},
        data / "home.json": build_home(cfg, papers, weekly, today, now, Path(reports_dir), news_json),
        data / "news.json": news_json,
        data / "companies.json": export_companies(news_all, papers, cfg, generated_at=now),
        data / "resources.json": resources_json,
    }
    for path, obj in targets.items():
        _write_json(path, obj)

    files = sorted(p for p in site_dir.rglob("*") if p.is_file())
    return SiteBuildResult(site_dir=site_dir, n_papers=len(exported), n_weekly=len(weekly),
                           n_news=news_json["count"], n_resources=resources_json["count"], files=files)


# ---------- serving ----------

class _Handler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - quiet by default
        pass


def make_server(site_dir: Path, port: int = 8000, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    handler = partial(_Handler, directory=str(site_dir))
    try:
        return ThreadingHTTPServer((host, port), handler)
    except OSError as e:
        raise ServerStartError(f"无法在 {host}:{port} 启动服务（{e.strerror or e}）。端口可能被占用，请用 --port 换一个端口。") from e


def serve(site_dir: Path, port: int = 8000) -> None:
    server = make_server(site_dir, port)
    actual = server.server_address[1]
    print(f"虚拟细胞雷达已启动: http://127.0.0.1:{actual}/  （Ctrl-C 退出）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
