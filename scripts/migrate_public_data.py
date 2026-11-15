#!/usr/bin/env python
"""公开数据迁移：把公开库里的私有信息移到本机 local 目录，并清理缓存。

默认 dry-run（只打印统计，不写任何文件）；加 --apply 才真正写入；加 --verify 做只读核对。
按原始 JSON 字典逐行处理，不经过 Paper 模型再序列化，未涉及的行逐字节不变。

规则：
  1. source == "seed_pdf" 的记录整条移到 <local-dir>/data/papers/<原月份文件>，并从公开库删除。
  2. 其余记录若 local_file 非空或 tags 含白名单外的值：先把原始整条写进 local 目录作叠加信息，
     再把公开记录的 local_file 置空、tags 只保留白名单内的值。
  3. 所有公开记录删除 "highlight" 键。
  4. 公开月份文件变空则删除（保留 .gitkeep）。
  5. data/cache/llm.jsonl 只保留当前 prompt_version 且 key 属于某条公开论文的行。
  6. 给了 --backup-dir 且 <local-dir>/topics.yaml 不存在时，从备份的 keywords.yaml 的
     my_projects 生成 <local-dir>/topics.yaml。
  7. local 里已有同 id 记录时不重复写，可重复执行。

用法：
  uv run python scripts/migrate_public_data.py                       # dry-run
  uv run python scripts/migrate_public_data.py --backup-dir DIR --apply
  uv run python scripts/migrate_public_data.py --backup-dir DIR --verify
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from aivc.config import load_config
from aivc.llm import cache_key
from aivc.models import Paper
from aivc.storage import load_overlay
from aivc.topics import Topic

CACHE_REL = Path("cache") / "llm.jsonl"
VERIFY_SAME = ["llm", "llm_status", "final_score", "must_read", "title", "abstract",
               "keyword_score", "matched_keywords", "circle"]


# ------------------------------------------------------------------ io
@dataclass
class Rec:
    raw: dict
    line: str | None  # 原始行文本；被修改过则为 None（重新序列化）

    @property
    def id(self) -> str:
        return self.raw["id"]

    def text(self) -> str:
        return self.line if self.line is not None else json.dumps(self.raw, ensure_ascii=False)


def read_months(papers_dir: Path) -> dict[str, list[Rec]]:
    out: dict[str, list[Rec]] = {}
    if not papers_dir.is_dir():
        return out
    for f in sorted(papers_dir.glob("*.jsonl")):
        recs = []
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                recs.append(Rec(json.loads(line), line))
        out[f.name] = recs
    return out


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _write_month(path: Path, recs: list[Rec]) -> None:
    recs = sorted(recs, key=lambda r: r.id)
    _atomic_write(path, "".join(r.text() + "\n" for r in recs))


# ------------------------------------------------------------------ plan
@dataclass
class Plan:
    total_before: int = 0
    seeds: int = 0
    scrubbed: int = 0
    highlight_removed: int = 0
    total_after: int = 0
    local_written: int = 0
    empty_months: list[str] = field(default_factory=list)
    cache_keep: int = 0
    cache_drop: int = 0
    # 写盘动作
    public_months: dict[str, list[Rec]] = field(default_factory=dict)
    changed_months: set[str] = field(default_factory=set)
    local_months: dict[str, list[Rec]] = field(default_factory=dict)  # 合并后的完整内容
    local_changed: set[str] = field(default_factory=set)
    cache_lines: list[str] = field(default_factory=list)
    cache_exists: bool = False
    topics_text: str | None = None
    topics_note: str | None = None


def make_id(name: str, used: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-")
    if not base:
        base = "topic"
    cand, n = base, 2
    while cand in used:
        suffix = f"-{n}"
        cand = base[: 40 - len(suffix)] + suffix
        n += 1
    used.add(cand)
    return cand


def topics_from_backup(backup_dir: Path) -> tuple[str | None, str | None]:
    kw = backup_dir / "config" / "keywords.yaml"
    if not kw.is_file():
        return None, f"备份中没有 {kw}，未生成 topics.yaml"
    data = yaml.safe_load(kw.read_text(encoding="utf-8")) or {}
    mp = data.get("my_projects") if isinstance(data, dict) else None
    if not mp:
        return None, "备份的 keywords.yaml 没有 my_projects，未生成 topics.yaml"
    items: list[tuple[str, list[str]]] = []
    if isinstance(mp, dict):
        items = [(str(k), list(v or [])) for k, v in mp.items()]
    elif isinstance(mp, list):
        for e in mp:
            if isinstance(e, dict) and "name" in e:
                items.append((str(e["name"]), list(e.get("terms") or e.get("keywords") or [])))
    topics, used, skipped = [], set(), []
    for name, terms in items:
        try:
            t = Topic(id=make_id(name, used), name=name, terms=[str(x) for x in terms], default_enabled=True)
        except Exception as e:  # 单个课题无效不应阻止其余
            skipped.append(f"{name}: {e}")
            continue
        topics.append(t.model_dump())
    note = f"跳过 {len(skipped)} 个无效课题: " + "; ".join(skipped) if skipped else None
    if not topics:
        return None, note or "my_projects 为空"
    return yaml.safe_dump({"topics": topics}, allow_unicode=True, sort_keys=False), note


def build_plan(data_dir: Path, local_dir: Path, config_dir: Path, backup_dir: Path | None) -> Plan:
    cfg = load_config(config_dir)
    allowed = set(cfg.pipeline.public.allowed_tags)
    plan = Plan()
    pub = read_months(data_dir / "papers")
    local_papers = local_dir / "data" / "papers"
    local = read_months(local_papers)
    local_ids = {r.id for recs in local.values() for r in recs}
    plan.total_before = sum(len(v) for v in pub.values())

    for fname, recs in pub.items():
        keep: list[Rec] = []
        file_changed = False
        for r in recs:
            raw = r.raw
            if raw.get("source") == "seed_pdf":
                plan.seeds += 1
                file_changed = True
                if r.id not in local_ids:
                    local.setdefault(fname, []).append(Rec(raw, r.line))
                    local_ids.add(r.id)
                    plan.local_written += 1
                    plan.local_changed.add(fname)
                continue
            tags = raw.get("tags") or []
            new_tags = [t for t in tags if t in allowed]
            needs_scrub = bool(raw.get("local_file")) or new_tags != tags
            has_hl = "highlight" in raw
            if not (needs_scrub or has_hl):
                keep.append(r)
                continue
            if needs_scrub:
                plan.scrubbed += 1
                if r.id not in local_ids:
                    local.setdefault(fname, []).append(Rec(raw, r.line))
                    local_ids.add(r.id)
                    plan.local_written += 1
                    plan.local_changed.add(fname)
            new = dict(raw)
            if has_hl:
                plan.highlight_removed += 1
                del new["highlight"]
            if needs_scrub:
                new["local_file"] = None
                new["tags"] = new_tags
            keep.append(Rec(new, None))
            file_changed = True
        plan.public_months[fname] = keep
        if file_changed:
            plan.changed_months.add(fname)
        if not keep:
            plan.empty_months.append(fname)
    plan.total_after = sum(len(v) for v in plan.public_months.values())
    plan.local_months = local

    # 缓存
    cache_path = data_dir / CACHE_REL
    plan.cache_exists = cache_path.is_file()
    if plan.cache_exists:
        keys = {
            cache_key(Paper.model_validate(r.raw), cfg.llm.model, cfg.llm.prompt_version)
            for recs in plan.public_months.values() for r in recs
        }
        for line in cache_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                ok = row.get("prompt_version") == cfg.llm.prompt_version and row.get("key") in keys
            except (json.JSONDecodeError, AttributeError):
                ok = False
            if ok:
                plan.cache_keep += 1
                plan.cache_lines.append(line)
            else:
                plan.cache_drop += 1

    # topics.yaml
    if backup_dir is not None and not (local_dir / "topics.yaml").exists():
        plan.topics_text, plan.topics_note = topics_from_backup(backup_dir)
    return plan


def print_plan(plan: Plan, applied: bool) -> None:
    pre = "已" if applied else "将"
    print(f"公开库迁移前总数        : {plan.total_before}")
    print(f"{pre}移到 local 的种子数  : {plan.seeds}")
    print(f"脱敏条数(非种子)        : {plan.scrubbed}")
    print(f"删除 highlight 键的条数 : {plan.highlight_removed}")
    print(f"迁移后公开总数          : {plan.total_after}")
    print(f"{pre}写入 local 的记录数  : {plan.local_written}")
    print(f"{pre}删除的空月份文件    : {len(plan.empty_months)}" +
          (f" ({', '.join(plan.empty_months)})" if plan.empty_months else ""))
    print(f"缓存保留/丢弃行数       : {plan.cache_keep}/{plan.cache_drop}")
    if plan.topics_text:
        print("topics.yaml             : 将从备份生成" if not applied else "topics.yaml             : 已生成")
    if plan.topics_note:
        print(f"topics.yaml 提示        : {plan.topics_note}")
    nothing = not (plan.changed_months or plan.local_written or plan.empty_months
                   or plan.cache_drop or plan.topics_text)
    if nothing:
        print("无事可做")


def apply_plan(plan: Plan, data_dir: Path, local_dir: Path) -> None:
    local_papers = local_dir / "data" / "papers"
    for fname in sorted(plan.local_changed):
        _write_month(local_papers / fname, plan.local_months[fname])
    pdir = data_dir / "papers"
    for fname in sorted(plan.changed_months):
        recs = plan.public_months[fname]
        if recs:
            _write_month(pdir / fname, recs)
        else:
            (pdir / fname).unlink(missing_ok=True)
    if plan.cache_exists and plan.cache_drop:
        _atomic_write(data_dir / CACHE_REL, "".join(l + "\n" for l in plan.cache_lines))
    if plan.topics_text:
        _atomic_write(local_dir / "topics.yaml", plan.topics_text)


# ------------------------------------------------------------------ verify
def verify(data_dir: Path, local_dir: Path, config_dir: Path, backup_dir: Path) -> int:
    cfg = load_config(config_dir)
    allowed = set(cfg.pipeline.public.allowed_tags)
    pub = [r.raw for recs in read_months(data_dir / "papers").values() for r in recs]
    bdir = backup_dir / "data" / "papers"
    if not bdir.is_dir():
        bdir = backup_dir / "papers"
    bak = [r.raw for recs in read_months(bdir).values() for r in recs]
    pub_by_id = {p["id"]: p for p in pub}
    fails = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal fails
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))
        if not ok:
            fails += 1

    n_seed = sum(1 for p in pub if p.get("source") == "seed_pdf")
    check("公开库无 seed_pdf", n_seed == 0, f"{n_seed} 条")
    n_lf = sum(1 for p in pub if p.get("local_file"))
    check("公开库无非空 local_file", n_lf == 0, f"{n_lf} 条")
    n_hl = sum(1 for p in pub if "highlight" in p)
    check("公开库无 highlight 键", n_hl == 0, f"{n_hl} 条")
    n_tag = sum(1 for p in pub if any(t not in allowed for t in (p.get("tags") or [])))
    check("公开库 tags 全在白名单", n_tag == 0, f"{n_tag} 条")

    bad: list[str] = []
    missing: list[str] = []
    for b in bak:
        if b.get("source") == "seed_pdf":
            continue
        p = pub_by_id.get(b["id"])
        if p is None:
            missing.append(b["id"])
            continue
        for k in VERIFY_SAME:
            if p.get(k) != b.get(k):
                bad.append(f"{b['id']}.{k}")
    check("备份中非种子记录均仍在公开库", not missing, f"缺失 {len(missing)}: {missing[:3]}")
    check("公开库关键字段与备份完全相等", not bad, f"{len(bad)} 处: {bad[:3]}")

    overlay = load_overlay(data_dir, local_dir / "data")
    check("叠加后条数等于备份总数", len(overlay) == len(bak), f"{len(overlay)} != {len(bak)}")
    ov = {p.id: p for p in overlay}
    bad_lf, bad_tags = [], []
    for b in bak:
        o = ov.get(b["id"])
        if o is None:
            bad_lf.append(b["id"])
            continue
        if o.local_file != b.get("local_file"):
            bad_lf.append(b["id"])
        if set(o.tags) != set(b.get("tags") or []):
            bad_tags.append(b["id"])
    check("叠加后每条 local_file 与备份一致", not bad_lf, f"{len(bad_lf)} 条: {bad_lf[:3]}")
    check("叠加后每条 tags 集合与备份一致", not bad_tags, f"{len(bad_tags)} 条: {bad_tags[:3]}")

    cache_path = data_dir / CACHE_REL
    n_cache = (sum(1 for l in cache_path.read_text(encoding="utf-8").splitlines() if l.strip())
               if cache_path.is_file() else 0)
    n_scored = sum(1 for p in pub if p.get("llm_status") == "scored")
    check("缓存行数等于公开论文已打分条数", n_cache == n_scored, f"缓存 {n_cache} != 已打分 {n_scored}")
    print("verify 通过" if not fails else f"verify 失败：{fails} 项")
    return 1 if fails else 0


# ------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    ap.add_argument("--local-dir", type=Path, default=Path("local"))
    ap.add_argument("--config-dir", type=Path, default=Path("config"))
    ap.add_argument("--backup-dir", type=Path, default=None, help="仓库外的备份目录（布局与仓库一致）")
    ap.add_argument("--apply", action="store_true", help="真正写入（默认 dry-run）")
    ap.add_argument("--verify", action="store_true", help="只读核对（需要 --backup-dir）")
    a = ap.parse_args(argv)

    if a.verify:
        if a.backup_dir is None:
            ap.error("--verify 需要 --backup-dir")
        return verify(a.data_dir, a.local_dir, a.config_dir, a.backup_dir)
    if a.apply and a.backup_dir is None:
        print("警告：未给 --backup-dir，将无法 --verify，也不会生成 topics.yaml", file=sys.stderr)
    plan = build_plan(a.data_dir, a.local_dir, a.config_dir, a.backup_dir)
    if a.apply:
        apply_plan(plan, a.data_dir, a.local_dir)
        print_plan(plan, applied=True)
    else:
        print("[dry-run] 不写任何文件；加 --apply 执行")
        print_plan(plan, applied=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
