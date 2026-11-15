"""refresh-openreview：用 OpenReview 官方公开 API 核验并覆盖公开库里的 ICLR 条目。

公开库里 source=openreview 的条目，标题/摘要/关键词来自作者本地整理的汇总而非官方接口。
本模块逐条向官方 API 取回，成功则覆盖；明确取不回则移到 <local-dir>/data/papers；
临时性失败（超时、5xx、429、解析异常）绝不动数据，只计入失败清单。

依据的官方文档（访问日期 2026-11-14，只读取了 docs.openreview.net 的文档页，
没有向 api.openreview.net / api2.openreview.net 发过任何请求）：

- https://docs.openreview.net/reference/api-v2  （API V2 总览，指向 OpenAPI 定义
  https://api2.openreview.net/docs/api.yml ；此处未去读，需要时由作者对照）
- https://docs.openreview.net/reference/api-v2/entities/note/fields  （Note 字段）
- https://docs.openreview.net/how-to-guides/data-retrieval-and-modification/
  how-to-get-all-notes-for-submissions-reviews-rebuttals-etc  （按 invitation/forum/content 过滤）
- https://docs.openreview.net/getting-started/using-the-api  （使用 API 概述）

结论：
- 基地址：文档页只出现了 OpenAPI 定义所在的主机 api2.openreview.net；常用做法是 API v2
  的基地址 https://api2.openreview.net，端点 GET /notes。文档本身未直接写明基地址，待核实
  （已做成配置项 sources.yaml 的 openreview.api_base_url）。
- 按 note id 取单篇：文档页未写明 GET /notes 的参数列表，待核实；这里按 OpenReview 官方客户端
  的约定使用查询参数 id=<note id>（id 即 openreview_id），返回 {"notes": [...]}，该形态待核实。
  文档确认存在的过滤参数有 invitation、forum、content（如 {'venueid': ...}）、details。
- content 结构：文档确认「content 是 JSON 对象，每个字段含 value，可选 readers」，
  即 {"title": {"value": ...}, "abstract": {"value": ...}}。本模块同时容忍直接给值的形式。
- 匿名访问：文档未写明公开稿件能否免登录读取（只说「需要 OpenReview 账号使用 API」，
  且「API 的权限与网站一致」），待核实。若匿名不可读，本命令会得到 401/403；为避免误把全部数据
  移走，一次运行里「0 篇成功且 ≥3 篇被判为取不回」时不执行任何移动，并提示可能是接口访问问题。
- 速率限制：文档未写明，待核实。本模块保守地把请求间隔固定在不小于 1 秒，429/5xx 走
  fetchers.base.get_with_retry 的指数退避，仍失败则计为临时失败。
- 时间字段（文档原义，毫秒级 unix 时间戳）：cdate 创建时间（可被设置）、tcdate 真实创建时间、
  mdate 修改时间、odate 首次公开（Online Date）、pdate 被标记为录用的日期（Publication Date）、
  ddate 软删除时间。本模块 published 取值优先级 pdate → odate → cdate，且只在原值为空时填。
- venue / venueid：文档确认 venueid 是 content 里的字段，表示投稿状态（在审、录用、撤稿、
  desk reject 等）；文档页未定义 venue 字段，待核实。本模块若 content.venue.value 存在就用它更新
  venue，否则保持原值。
- 匿名作者：在审稿件的 authors 通常不公开；值为空或为 "Anonymous" 时保持 authors 为空。
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from . import prefilter
from .config import AppConfig
from .fetchers.base import FetchError, build_client, get_with_retry
from .models import Paper
from .storage import PaperStore

log = logging.getLogger("aivc.openreview_refresh")

MIN_DELAY = 1.0
GONE_STATUS = {403, 404, 410}
_STATUS_RE = re.compile(r"failed: HTTP (\d{3})")


class NotAvailable(Exception):
    """官方明确取不回（404/403/已删除/不可见）。"""


class TransientError(Exception):
    """临时性失败，不得改动记录。"""


@dataclass
class Official:
    title: str | None = None
    abstract: str | None = None
    keywords: list[str] = field(default_factory=list)
    authors: list[str] = field(default_factory=list)
    venue: str | None = None
    published: date | None = None


@dataclass
class RefreshSummary:
    total: int = 0
    overwritten: int = 0
    abstract_changed: int = 0
    moved: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    dry_run: bool = False
    guard_tripped: bool = False


def _val(content: dict, key: str) -> Any:
    v = content.get(key)
    if isinstance(v, dict) and "value" in v:
        return v["value"]
    return v


def _ms_to_date(ms: Any) -> date | None:
    if isinstance(ms, bool) or not isinstance(ms, (int, float)):
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()


def parse_note(note: dict) -> Official:
    content = note.get("content")
    if not isinstance(content, dict):
        raise ValueError("note.content 不是对象")
    title = _val(content, "title")
    abstract = _val(content, "abstract")
    kws = _val(content, "keywords") or []
    authors = _val(content, "authors") or []
    venue = _val(content, "venue")
    if isinstance(kws, str):
        kws = [k.strip() for k in re.split(r"[;,]", kws) if k.strip()]
    if isinstance(authors, str):
        authors = [authors]
    if not isinstance(kws, list) or not isinstance(authors, list):
        raise ValueError("keywords/authors 类型异常")
    authors = [a.strip() for a in authors if isinstance(a, str) and a.strip()
               and a.strip().lower() != "anonymous"]
    pub = None
    for k in ("pdate", "odate", "cdate"):
        pub = _ms_to_date(note.get(k))
        if pub:
            break
    return Official(
        title=title.strip() if isinstance(title, str) and title.strip() else None,
        abstract=abstract.strip() if isinstance(abstract, str) and abstract.strip() else None,
        keywords=[k.strip() for k in kws if isinstance(k, str) and k.strip()],
        authors=authors,
        venue=venue.strip() if isinstance(venue, str) and venue.strip() else None,
        published=pub,
    )


def fetch_official(client: httpx.Client, base_url: str, note_id: str, *,
                   sleep: Callable[[float], None], max_retries: int) -> Official:
    url = base_url.rstrip("/") + "/notes"
    try:
        resp = get_with_retry(client, url, params={"id": note_id}, max_retries=max_retries, sleep=sleep)
    except FetchError as e:
        m = _STATUS_RE.search(str(e))
        if m and int(m.group(1)) in GONE_STATUS:
            raise NotAvailable(f"HTTP {m.group(1)}") from e
        raise TransientError(str(e)) from e
    except httpx.HTTPError as e:
        raise TransientError(f"{type(e).__name__}: {e}") from e
    try:
        data = resp.json()
        notes = data["notes"]
        if not isinstance(notes, list):
            raise ValueError("notes 不是列表")
    except Exception as e:  # noqa: BLE001
        raise TransientError(f"响应解析失败：{type(e).__name__}: {e}") from e
    notes = [n for n in notes if isinstance(n, dict) and n.get("id") == note_id]
    if not notes:
        raise NotAvailable("返回空列表（条目不存在或不可见）")
    note = notes[0]
    if note.get("ddate"):
        raise NotAvailable("条目已删除（ddate）")
    try:
        return parse_note(note)
    except Exception as e:  # noqa: BLE001
        raise TransientError(f"解析失败：{type(e).__name__}: {e}") from e


def _norm(s: str | None) -> str:
    return " ".join((s or "").split())


def apply_official(paper: Paper, off: Official, cfg: AppConfig, today: date) -> tuple[Paper, bool]:
    """返回 (新记录, 摘要是否变化)。"""
    changed = bool(off.abstract) and _norm(off.abstract) != _norm(paper.abstract)
    upd: dict[str, Any] = {"updated": today}
    if off.title:
        upd["title"] = off.title
    if off.abstract:
        upd["abstract"] = off.abstract
    if off.keywords:
        upd["keywords"] = off.keywords
    if off.authors:
        upd["authors"] = off.authors
    if off.venue:
        upd["venue"] = off.venue
    if paper.published is None and off.published:
        upd["published"] = off.published
    if changed:
        upd.update(llm_status="unscored", llm=None, final_score=None, must_read=False,
                   llm_skip_reason="abstract_changed")
    new = paper.model_copy(update=upd)
    return prefilter.apply_keywords(new, cfg), changed


def _move_to_local(paper: Paper, public: PaperStore, local: PaperStore) -> None:
    existing = {p.id: p for p in local.load_all()}.get(paper.id)
    if existing is not None:
        tags = list(existing.tags) + [t for t in paper.tags if t not in existing.tags]
        paper = paper.model_copy(update={"local_file": existing.local_file or paper.local_file,
                                         "tags": tags})
    local.upsert([paper])  # 先写 local
    public.delete([paper.id])  # 再删公开


def run_refresh(cfg: AppConfig, *, data_dir: Path, local_dir: Path, dry_run: bool = False,
                limit: int | None = None, client: httpx.Client | None = None,
                sleep: Callable[[float], None] = time.sleep, today: date | None = None,
                out: Callable[[str], None] = print) -> RefreshSummary:
    today = today or date.today()
    orc = cfg.sources.openreview
    delay = max(MIN_DELAY, float(orc.delay_seconds))
    public = PaperStore(data_dir)
    local = PaperStore(Path(local_dir) / "data")
    targets = [p for p in public.load_all() if p.source == "openreview" and p.openreview_id]
    targets.sort(key=lambda p: p.id)
    if limit is not None:
        targets = targets[:limit]
    s = RefreshSummary(total=len(targets), dry_run=dry_run)
    prefix = "[dry-run] " if dry_run else ""

    def emit(msg: str) -> None:
        s.lines.append(msg)
        out(msg)

    own_client = client is None
    client = client or build_client(cfg.sources.http)
    pending_moves: list[Paper] = []
    n_success = 0
    try:
        for i, p in enumerate(targets):
            if i > 0:
                sleep(delay)
            try:
                off = fetch_official(client, orc.api_base_url, p.openreview_id,
                                     sleep=sleep, max_retries=cfg.sources.http.max_retries)
            except NotAvailable as e:
                pending_moves.append(p)
                emit(f"{prefix}{p.id}: 取不回（{e}），将移到 local")
                continue
            except TransientError as e:
                s.failed.append((p.id, str(e)))
                emit(f"{prefix}{p.id}: 临时失败，保持不动（{e}）")
                continue
            n_success += 1
            new, changed = apply_official(p, off, cfg, today)
            s.overwritten += 1
            s.abstract_changed += int(changed)
            emit(f"{prefix}{p.id}: 用官方数据覆盖" + ("，摘要已变化，重置打分" if changed else "，摘要未变，保留打分"))
            if not dry_run:
                public.upsert([new])
        if pending_moves and n_success == 0 and len(pending_moves) >= 3:
            s.guard_tripped = True
            for p in pending_moves:
                s.failed.append((p.id, "全部请求都被判为取不回，疑似接口访问问题，未移动"))
            emit("警告：0 篇成功且大量条目取不回，疑似接口需要登录或不可用；本次不移动任何记录")
            pending_moves = []
        for p in pending_moves:
            s.moved += 1
            if not dry_run:
                _move_to_local(p, public, local)
    finally:
        if own_client:
            client.close()

    emit(f"{prefix}汇总：处理 {s.total} 条；覆盖 {s.overwritten}（其中摘要变化 {s.abstract_changed}）；"
         f"移到 local {s.moved}；失败 {len(s.failed)}")
    if s.abstract_changed and not dry_run:
        emit("提示：摘要有变化的条目已置为未打分，运行 `python -m aivc score` 重新打分")
    for pid, why in s.failed:
        emit(f"  失败 {pid}: {why}")
    return s
