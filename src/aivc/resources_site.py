"""知识资源的静态站导出（site/data/resources.json）：只导出 status=accepted 的条目。"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from .config import AppConfig
from .models import Resource
from .resources import is_maintained

KIND_LABELS: list[tuple[str, str]] = [
    ("lab", "实验室与团队"), ("repo", "模型与方法仓库"), ("tool", "基础工具"), ("data_portal", "数据门户"),
    ("benchmark", "基准与竞赛"), ("review", "综述与观点"), ("blog", "博客与报告"), ("podcast", "讲座与播客"),
    ("course", "课程"), ("tutorial", "教程"), ("other", "其他"), ("awesome_list", "社区精选列表"),
]
# 页面的五大板块：每个板块包含若干 kind，顺序即页面顺序
SECTIONS: list[tuple[str, str, tuple[str, ...]]] = [
    ("labs", "实验室与团队", ("lab",)),
    ("repos", "代表性仓库", ("repo", "tool")),
    ("data", "重要数据集与基准", ("data_portal", "benchmark")),
    ("reading", "综述与博客", ("review", "blog", "podcast", "course", "tutorial", "other")),
    ("lists", "社区精选列表", ("awesome_list",)),
]
RESOURCE_ITEM_KEYS = (
    "id", "name", "kind", "url", "description_zh", "tags", "related_tasks", "related_paradigms",
    "added", "source", "dataset_names", "paper_ids", "github",
)
RESOURCE_GITHUB_KEYS = (
    "full_name", "stars", "pushed_at", "archived", "license", "language", "maintained",
    "fetched_at", "stars_delta", "status",
)


def _github_block(r: Resource, metrics: dict[str, Any], today: date, within_days: int) -> dict[str, Any] | None:
    if not r.github:
        return None
    m = (metrics.get("repos") or {}).get(r.github.lower())
    if not m:
        return None
    hist = m.get("history") or []
    delta = None
    if len(hist) >= 2 and isinstance(m.get("stars"), int) and isinstance(hist[-2].get("stars"), int):
        delta = m["stars"] - hist[-2]["stars"]
    return {
        "full_name": m.get("full_name") or r.github,
        "stars": m.get("stars"),
        "pushed_at": m.get("pushed_at"),
        "archived": m.get("archived"),
        "license": m.get("license"),
        "language": m.get("language"),
        "maintained": is_maintained(m, today, within_days),
        "fetched_at": m.get("fetched_at"),
        "stars_delta": delta,
        "status": m.get("status", "ok"),
    }


def export_resources(curated: Iterable[Resource], metrics: dict[str, Any] | None, paper_ids: Iterable[str],
                     cfg: AppConfig, *, today: date, generated_at: str, n_candidates: int = 0) -> dict[str, Any]:
    metrics = metrics or {}
    within = cfg.pipeline.resources.maintained_within_days
    known_papers = set(paper_ids)
    accepted = [r for r in curated if r.status == "accepted"]

    rows: list[tuple[Resource, dict[str, Any] | None]] = []
    for r in accepted:
        rows.append((r, _github_block(r, metrics, today, within)))

    order = {k: i for i, (k, _) in enumerate(KIND_LABELS)}

    def sort_key(row):
        r, gh = row
        stars = gh.get("stars") if gh else None
        has = isinstance(stars, int)
        return (order.get(r.kind, len(order)), 0 if has else 1, -(stars if has else 0), r.name.casefold(), r.id)

    rows.sort(key=sort_key)
    items = []
    for r, gh in rows:
        items.append({
            "id": r.id, "name": r.name, "kind": r.kind, "url": r.url, "description_zh": r.description_zh,
            "tags": list(r.tags), "related_tasks": list(r.related_tasks),
            "related_paradigms": list(r.related_paradigms), "added": r.added.isoformat(),
            "source": r.source, "dataset_names": list(r.dataset_names),
            "paper_ids": [p for p in r.paper_ids if p in known_papers], "github": gh,
        })

    counts: dict[str, int] = {}
    for it in items:
        counts[it["kind"]] = counts.get(it["kind"], 0) + 1
    used_tasks = {t for it in items for t in it["related_tasks"]}
    used_paradigms = {p for it in items for p in it["related_paradigms"]}
    tax = cfg.scope.taxonomy
    return {
        "generated_at": generated_at,
        "metrics_fetched_at": metrics.get("fetched_at"),
        "count": len(items),
        "candidates_count": n_candidates,
        "maintained_within_days": within,
        "kinds": [{"id": k, "label": lab, "count": counts.get(k, 0)} for k, lab in KIND_LABELS],
        "sections": [{"id": sid, "label": lab, "kinds": list(ks), "count": sum(counts.get(k, 0) for k in ks)}
                     for sid, lab, ks in SECTIONS],
        "tasks": [{"id": t.id, "label": t.label} for t in tax.tasks if t.id in used_tasks],
        "paradigms": [{"id": p.id, "label": p.label} for p in tax.paradigms if p.id in used_paradigms],
        "items": items,
    }
