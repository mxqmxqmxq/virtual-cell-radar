"""新闻的纯逻辑核心：存储、预筛、公司匹配、去重合并、重大事件规则。

不联网、不调 LLM。候选统一为 ``tuple[NewsItem, str]``，第二项是只在内存里流转的 RSS 片段，不落盘。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from rapidfuzz import fuzz

from .models import NewsEntry, NewsItem, NewsSourceRef, make_news_id, normalize_news_url
from .termmatch import compile_term
from .textutil import normalize_title

_CJK = re.compile(r"[一-鿿]")
_MERGEABLE = {"funding", "m_and_a", "partnership", "model_or_data_release", "clinical"}
_AMOUNT_TYPES = {"funding", "m_and_a"}


# ---------------------------------------------------------------- 存储

class NewsStore:
    """data_dir/news/YYYY-MM.jsonl 月分片（按 published，缺则 first_seen）。"""

    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir) / "news"

    def _files(self) -> list[Path]:
        return sorted(self.dir.glob("*.jsonl")) if self.dir.exists() else []

    def _read_file(self, p: Path) -> list[NewsItem]:
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(NewsItem.model_validate_json(line))
        return out

    def load_all(self) -> list[NewsItem]:
        res: list[NewsItem] = []
        for p in self._files():
            res.extend(self._read_file(p))
        return res

    def _write_file(self, p: Path, items: list[NewsItem]) -> None:
        items = sorted(items, key=lambda x: x.id)
        text = "".join(json.dumps(x.model_dump(mode="json"), ensure_ascii=False) + "\n" for x in items)
        tmp = p.with_suffix(".jsonl.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)

    def upsert(self, items: list[NewsItem]) -> tuple[int, int]:
        self.dir.mkdir(parents=True, exist_ok=True)
        files: dict[Path, dict[str, NewsItem]] = {
            p: {x.id: x for x in self._read_file(p)} for p in self._files()
        }
        where: dict[str, Path] = {i: p for p, d in files.items() for i in d}
        n_new = n_upd = 0
        dirty: set[Path] = set()
        for it in items:
            if it.id in where:
                f = where[it.id]
                if files[f][it.id] != it:
                    n_upd += 1
                    dirty.add(f)
                files[f][it.id] = it
            else:
                d = it.published or it.first_seen
                f = self.dir / (d.strftime("%Y-%m") + ".jsonl")
                files.setdefault(f, {})[it.id] = it
                where[it.id] = f
                n_new += 1
                dirty.add(f)
        for f in dirty:
            self._write_file(f, list(files[f].values()))
        return n_new, n_upd

    def delete(self, ids: list[str]) -> int:
        drop = set(ids)
        n = 0
        for f in self._files():
            items = self._read_file(f)
            keep = [x for x in items if x.id not in drop]
            if len(keep) != len(items):
                n += len(items) - len(keep)
                self._write_file(f, keep)
        return n


# ---------------------------------------------------------------- 公司匹配

def _companies(cfg):
    return cfg.companies.companies


def _name_pattern(term: str) -> re.Pattern[str] | None:
    term = term.strip()
    if not term:
        return None
    if _CJK.search(term):
        return re.compile(re.escape(term))
    flags = 0
    letters = [c for c in term if c.isalpha()]
    if len(term) > 5 and not (letters and all(c.isupper() for c in letters)):
        flags = re.IGNORECASE
    body = r"\s+".join(re.escape(p) for p in term.split())
    return re.compile(rf"(?<![\w\-]){body}(?!\w)", flags)


def match_companies(text: str, cfg) -> list[str]:
    """返回命中的公司规范名（按名单顺序）。"""
    out: list[str] = []
    for co in _companies(cfg):
        for term in [co.name, *co.aliases]:
            pat = _name_pattern(term)
            if pat and pat.search(text or ""):
                out.append(co.name)
                break
    return out


def canonical_companies(names: list[str], cfg) -> list[str]:
    lookup: dict[str, str] = {}
    for co in _companies(cfg):
        for term in [co.name, *co.aliases]:
            lookup.setdefault(term.strip().lower(), co.name)
    out: list[str] = []
    for n in names:
        raw = (n or "").strip()
        c = lookup.get(raw.lower())
        if c is None:
            # LLM 常把括号内外的中英文名倒置或拼接（如 "XtalPi (晶泰科技)"）：拆开逐段回查
            parts = [x.strip().lower() for x in re.split(r"[()（）/、]", raw) if x.strip()]
            hits = [lookup[x] for x in parts if x in lookup]
            c = hits[0] if hits else raw
        if c and c not in out:
            out.append(c)
    return out


# ---------------------------------------------------------------- 预筛

def _compiled_combos(ncfg) -> list[tuple[str, list[list]]]:
    out = []
    for c in getattr(ncfg, "combos", []) or []:
        groups = [[p for t in sub if (p := compile_term(t))] for sub in c.all_of]
        if groups and all(groups):
            out.append((c.name, groups))
    return out


def match_combos(text: str, combos) -> list[str]:
    return [name for name, groups in combos
            if all(any(p.search(text) for p in g) for g in groups)]


def to_candidates(entries: list[NewsEntry], cfg, today: date) -> list[tuple[NewsItem, str]]:
    ncfg = cfg.pipeline.news
    kw_pats = [(k, compile_term(k)) for k in ncfg.keywords]
    combos = _compiled_combos(ncfg)
    need_match = getattr(ncfg, "company_feed_requires_match", True)
    out: list[tuple[NewsItem, str]] = []
    for e in entries:
        text = f"{e.title}\n{e.snippet}"
        kws = [k for k, p in kw_pats if p and p.search(text)]
        kws += [f"combo:{n}" for n in match_combos(text, combos)]
        cos = match_companies(text, cfg)
        is_company_feed = e.feed_kind == "company"
        if is_company_feed:
            # 公司自家 feed 里出现自家公司名不说明相关；必须命中领域词或组合规则（或点名其他公司）
            own = set(canonical_companies([e.feed_company], cfg)) if e.feed_company else set()
            others = [c for c in cos if c not in own]
            if need_match and not (kws or others):
                continue
        elif not (cos or kws):
            continue
        companies = list(cos)
        if is_company_feed and e.feed_company:
            for c in reversed(canonical_companies([e.feed_company], cfg)):
                if c not in companies:
                    companies.insert(0, c)
        item = NewsItem(
            id=make_news_id(e.url),
            title=e.title.strip(),
            url=e.url,
            source=e.source,
            sources=[NewsSourceRef(name=e.source, url=e.url)],
            published=e.published,
            companies=companies,
            keyword_score=float(len(kws) + 2 * len(cos)),
            matched_terms=[*kws, *cos],
            first_seen=today,
            llm_status="unscored",
        )
        out.append((item, e.snippet))
    return out


def cap_per_company_week(items: list[NewsItem], n: int) -> list[NewsItem]:
    """同一主公司在同一 ISO 周内最多保留 n 条（按相关度、日期取前 n），保持原有顺序。n<=0 表示不限。"""
    if n <= 0:
        return list(items)
    groups: dict[tuple, list[NewsItem]] = {}
    for it in items:
        if not it.companies:
            continue
        y, w, _ = event_date(it).isocalendar()
        groups.setdefault((it.companies[0], y, w), []).append(it)
    drop: set[str] = set()
    for g in groups.values():
        g.sort(key=lambda x: (-(x.relevance or 0), -event_date(x).toordinal(), x.id))
        drop.update(x.id for x in g[n:])
    return [it for it in items if it.id not in drop]


def cap_per_company_rolling(items: list[NewsItem], n: int, window_days: int = 30) -> list[NewsItem]:
    """同一主公司（companies[0]）在任意连续 window_days 天内最多保留 n 条，返回值保持输入顺序。

    组内按（相关度降序、事件日期降序、id）依次尝试保留；加入后任一窗口超过 n 条则丢弃。
    无公司的条目不受限；n<=0 表示不限。结果只取决于条目自身日期，与今天无关。
    """
    if n <= 0:
        return list(items)
    groups: dict[str, list[NewsItem]] = {}
    for it in items:
        if it.companies:
            groups.setdefault(it.companies[0], []).append(it)
    span = max(window_days, 1)
    drop: set[str] = set()
    for g in groups.values():
        g.sort(key=lambda x: (-(x.relevance or 0), -event_date(x).toordinal(), x.id))
        kept: list[int] = []  # 已保留条目的日期序数（升序）
        for it in g:
            d = event_date(it).toordinal()
            trial = sorted([*kept, d])
            ok = True
            j = 0
            for i in range(len(trial)):  # 双指针：以每个日期为窗口起点
                while trial[i] - trial[j] >= span:
                    j += 1
                if i - j + 1 > n:
                    ok = False
                    break
            if ok:
                kept = trial
            else:
                drop.add(it.id)
    return [it for it in items if it.id not in drop]


def _primary_company(it) -> str | None:
    """条目主公司：对象取 companies[0]，字典优先取 company_ids[0] 再取 companies[0]。"""
    if isinstance(it, dict):
        for k in ("company_ids", "companies"):
            v = it.get(k) or []
            if v:
                return v[0]
        return None
    cos = getattr(it, "companies", None) or []
    return cos[0] if cos else None


def interleave_by_company(items: list, key) -> list:
    """对已排序列表，在 key(item) 相同的连续区段内按主公司轮转重排；区段之间的顺序不变。

    公司按其在区段内首次出现的顺序，逐轮各取一条（公司自身条目保持原相对顺序）；
    无公司的条目各自视为独立公司。
    """
    out: list = []
    i = 0
    while i < len(items):
        k = key(items[i])
        j = i + 1
        while j < len(items) and key(items[j]) == k:
            j += 1
        seg = items[i:j]
        buckets: dict = {}
        for idx, it in enumerate(seg):
            c = _primary_company(it)
            buckets.setdefault(("c", c) if c else ("u", idx), []).append(it)
        queues = list(buckets.values())
        while queues:
            nxt = []
            for q in queues:
                out.append(q.pop(0))
                if q:
                    nxt.append(q)
            queues = nxt
        i = j
    return out


# ---------------------------------------------------------------- 去重

@dataclass
class NewsDedupResult:
    new: list[tuple[NewsItem, str]] = field(default_factory=list)
    updated: list[NewsItem] = field(default_factory=list)
    n_duplicates: int = 0


def event_date(item: NewsItem) -> date:
    return item.published or item.first_seen


def _item_urls(it: NewsItem) -> set[str]:
    urls = {normalize_news_url(it.url)}
    urls.update(normalize_news_url(s.url) for s in it.sources)
    return urls


def _same_title(a: str, b: str, threshold: float) -> bool:
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if len(na) < 20 or len(nb) < 20:
        return False
    return fuzz.ratio(na, nb) >= threshold


def _merge_sources(target: NewsItem, incoming: NewsItem) -> NewsItem:
    """把 incoming 的来源并入 target（按 URL 去重保序），published 取较早者。"""
    seen = {normalize_news_url(s.url) for s in target.sources}
    srcs = list(target.sources)
    extra = list(incoming.sources) or [NewsSourceRef(name=incoming.source, url=incoming.url)]
    for s in extra:
        k = normalize_news_url(s.url)
        if k not in seen:
            seen.add(k)
            srcs.append(s)
    pubs = [d for d in (target.published, incoming.published) if d]
    return target.model_copy(update={"sources": srcs, "published": min(pubs) if pubs else None})


def dedup_candidates(cands, existing: list[NewsItem], cfg) -> NewsDedupResult:
    thr = cfg.pipeline.news.title_threshold
    res = NewsDedupResult()
    ex: dict[str, NewsItem] = {x.id: x for x in existing}
    ex_orig = dict(ex)
    batch: list[tuple[NewsItem, str]] = []
    changed: dict[str, NewsItem] = {}

    for item, snippet in cands:
        urls = _item_urls(item)
        hit_ex = next(
            (x for x in ex.values() if (urls & _item_urls(x)) or _same_title(item.title, x.title, thr)),
            None,
        )
        if hit_ex is not None:
            merged = _merge_sources(hit_ex, item)
            ex[hit_ex.id] = merged
            if merged != ex_orig[hit_ex.id]:
                changed[hit_ex.id] = merged
            res.n_duplicates += 1
            continue
        hit_idx = next(
            (i for i, (b, _) in enumerate(batch)
             if (urls & _item_urls(b)) or _same_title(item.title, b.title, thr)),
            None,
        )
        if hit_idx is not None:
            b, s = batch[hit_idx]
            batch[hit_idx] = (_merge_sources(b, item), s)
            res.n_duplicates += 1
            continue
        batch.append((item, snippet))

    res.new = batch
    res.updated = list(changed.values())
    return res


# ---------------------------------------------------------------- 事件合并

def _scored(it: NewsItem) -> bool:
    return it.llm_status == "scored"


def _amount_ok(a: NewsItem, b: NewsItem) -> bool:
    if a.amount_usd is None or b.amount_usd is None:
        return True
    hi = max(a.amount_usd, b.amount_usd)
    return hi == 0 or abs(a.amount_usd - b.amount_usd) <= 0.1 * hi


def _same_event(a: NewsItem, b: NewsItem, window: int) -> bool:
    if not (_scored(a) and _scored(b)):
        return False
    if a.event_type != b.event_type or a.event_type not in _MERGEABLE:
        return False
    if not ({c.lower() for c in a.companies} & {c.lower() for c in b.companies}):
        return False
    if abs((event_date(a) - event_date(b)).days) > window:
        return False
    if a.event_type in _AMOUNT_TYPES:
        return _amount_ok(a, b)
    return fuzz.token_set_ratio(normalize_title(a.title), normalize_title(b.title)) >= 55


def _absorb(primary: NewsItem, other: NewsItem) -> NewsItem:
    merged = _merge_sources(primary, other)
    inv = list(merged.investors)
    for i in other.investors:
        if i not in inv:
            inv.append(i)
    return merged.model_copy(update={
        "investors": inv,
        "amount_usd": merged.amount_usd if merged.amount_usd is not None else other.amount_usd,
        "round": merged.round or other.round,
    })


def merge_events(new_items: list[NewsItem], existing: list[NewsItem], cfg):
    """抽取后合并同一事件的多家报道。库内已有条目不会被丢弃：匹配到已有条目时已有条目作主条目。
    返回 (保留下来的新条目, 被修改的已有条目)。"""
    window = cfg.pipeline.news.merge_window_days
    ex = {x.id: x for x in existing}
    ex_orig = dict(ex)
    modified: set[str] = set()
    kept: list[NewsItem] = []
    for it in new_items:
        if not _scored(it):
            kept.append(it)
            continue
        hit_ex = next((x for x in ex.values() if _same_event(it, x, window)), None)
        if hit_ex is not None:
            ex[hit_ex.id] = _absorb(hit_ex, it)
            modified.add(hit_ex.id)
            continue
        idx = next((i for i, k in enumerate(kept) if _same_event(it, k, window)), None)
        if idx is None:
            kept.append(it)
            continue
        k = kept[idx]
        it_rel, k_rel = it.relevance or 0, k.relevance or 0
        if it_rel > k_rel or (it_rel == k_rel and event_date(it) < event_date(k)):
            kept[idx] = _absorb(it, k)
        else:
            kept[idx] = _absorb(k, it)
    mods = [ex[i] for i in ex if i in modified and ex[i] != ex_orig[i]]
    return kept, mods


# ---------------------------------------------------------------- 重大事件

def apply_major(item: NewsItem, cfg) -> NewsItem:
    if not _scored(item):
        return item.model_copy(update={"major": False, "major_reasons": []})
    tiers = {co.name.lower(): co.tier for co in _companies(cfg)}
    reasons: list[str] = []
    for r in cfg.pipeline.news.major:
        if r.event_types and item.event_type not in r.event_types:
            continue
        if r.min_amount_usd is not None and not (
            item.amount_usd is not None and item.amount_usd >= r.min_amount_usd
        ):
            continue
        if r.company_tier is not None and not any(
            tiers.get(c.lower()) == r.company_tier for c in item.companies
        ):
            continue
        if r.min_relevance is not None and not (
            item.relevance is not None and item.relevance >= r.min_relevance
        ):
            continue
        reasons.append(r.label)
    return item.model_copy(update={"major": bool(reasons), "major_reasons": reasons})


def publishable(items, min_relevance: int, *, scored_only: bool = False):
    """网站与周报共用的展示筛选：发布日期必须明确；已打分者 relevance 达标（未打分者仅在非 scored_only 时保留）。"""
    out = []
    for it in items:
        if it.published is None:
            continue
        scored = it.llm_status == "scored" and it.relevance is not None
        if scored:
            if it.relevance >= min_relevance:
                out.append(it)
        elif not scored_only:
            out.append(it)
    return out
