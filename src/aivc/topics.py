"""课题（我的课题）模型与匹配。不依赖 config.py。"""
from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator

from .termmatch import any_term_matches, compile_term, paper_text

MAX_TOPICS = 20
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


class Topic(BaseModel):
    id: str
    name: str
    terms: list[str]
    default_enabled: bool = False

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not _ID.match(v):
            raise ValueError(f"课题 id 非法: {v!r}（需匹配 ^[a-z0-9][a-z0-9_-]{{0,39}}$）")
        return v

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        v = v.strip()
        if not 1 <= len(v) <= 40:
            raise ValueError("课题名称需为 1–40 个字符")
        return v

    @field_validator("terms")
    @classmethod
    def _check_terms(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in v:
            t = _CTRL.sub("", raw).strip()
            if not 2 <= len(t) <= 80:
                raise ValueError(f"词项长度需为 2–80 个字符: {raw!r}")
            if not t.strip("*").strip():
                raise ValueError(f"词项去掉 * 后不能为空: {raw!r}")
            key = t.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(t)
        if not 1 <= len(out) <= 40:
            raise ValueError("每个课题需要 1–40 个词项")
        return out


def load_topics(path: Path) -> list[Topic]:
    path = Path(path)
    if not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        raw = data.get("topics") or [] if isinstance(data, dict) else None
        if raw is None:
            raise ValueError("顶层必须是 topics: [...]")
        topics = [Topic.model_validate(t) for t in raw]
    except Exception as e:  # yaml / pydantic / 结构错误统一成 ValueError
        raise ValueError(f"课题文件 {path} 无效: {e}") from e
    ids = [t.id for t in topics]
    if len(set(ids)) != len(ids):
        raise ValueError(f"课题文件 {path} 中 id 重复: {sorted({i for i in ids if ids.count(i) > 1})}")
    if len(topics) > MAX_TOPICS:
        raise ValueError(f"课题文件 {path} 课题数 {len(topics)} 超过上限 {MAX_TOPICS}")
    return topics


def merge_topics(base: list[Topic], extra: list[Topic]) -> list[Topic]:
    over = {t.id: t for t in extra}
    out = [over.pop(t.id, t) for t in base]
    out.extend(t for t in extra if t.id in over)
    return out


def report_topics(topics: list[Topic]) -> list[Topic]:
    return [t for t in topics if t.default_enabled]


_PAT_CACHE: dict[tuple[str, ...], list] = {}


def _patterns(topic: Topic) -> list:
    key = tuple(topic.terms)
    pats = _PAT_CACHE.get(key)
    if pats is None:
        pats = [p for t in topic.terms if (p := compile_term(t))]
        _PAT_CACHE[key] = pats
    return pats


def match_topics(paper, topics: list[Topic]) -> list[Topic]:
    text = paper_text(paper.title, paper.abstract, paper.keywords)
    return [t for t in topics if any_term_matches(text, _patterns(t))]
