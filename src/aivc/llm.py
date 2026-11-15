"""LLM relevance scoring and field extraction (Anthropic API, cached, degrades gracefully)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from .claude_cli import ClaudeCLIClient, cli_available
from .config import AppConfig
from .models import LLMAnnotation, Paper
from .textutil import normalize_title

log = logging.getLogger(__name__)

TOOL_NAME = "record_paper_annotation"
NONE_CIRCLE_WEIGHT = 0.3
MAX_CONSECUTIVE_ERRORS = 5


# ---------------------------------------------------------------- cache
class LLMCache:
    """Append-only JSONL cache: one line per scored paper."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        if self.path.exists():
            with self.path.open(encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                        self._data[row["key"]] = row["value"]
                    except (json.JSONDecodeError, KeyError, TypeError):
                        log.warning("LLM 缓存 %s 第 %d 行损坏，已跳过", self.path, n)

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key: str) -> dict | None:
        with self._lock:
            return self._data.get(key)

    def put(self, key: str, value: dict, **meta: Any) -> None:
        row = {"key": key, "value": value, **meta}
        row.setdefault("created", datetime.now(timezone.utc).isoformat())
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                f.flush()
            self._data[key] = value


def cache_key(paper: Paper, model: str, prompt_version: str) -> str:
    abs_hash = hashlib.sha256((paper.abstract or "").encode("utf-8")).hexdigest()
    raw = f"{model}|{prompt_version}|{normalize_title(paper.title)}|{abs_hash}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- prompt
def _ids(items) -> list[str]:
    return [i.id for i in items]


def build_tool(cfg: AppConfig) -> dict:
    tasks = _ids(cfg.scope.taxonomy.tasks)
    paradigms = _ids(cfg.scope.taxonomy.paradigms)
    return {
        "name": TOOL_NAME,
        "description": "记录对这篇论文的结构化标注结果。",
        "input_schema": {
            "type": "object",
            "properties": {
                "tasks": {"type": "array", "items": {"type": "string", "enum": tasks}},
                "paradigms": {"type": "array", "items": {"type": "string", "enum": paradigms}},
                "datasets_used": {"type": "array", "items": {"type": "string"}},
                "releases_new_dataset": {"type": "boolean"},
                "new_dataset_names": {"type": "array", "items": {"type": "string"}},
                "circle": {"type": "string", "enum": ["core", "peripheral", "none"]},
                "relevance": {"type": "integer", "minimum": 0, "maximum": 10},
                "novelty_zh": {"type": "string"},
                "summary_zh": {"type": "string"},
            },
            "required": [
                "tasks", "paradigms", "datasets_used", "releases_new_dataset",
                "new_dataset_names", "circle", "relevance", "novelty_zh", "summary_zh",
            ],
        },
    }


def build_prompt(paper: Paper, cfg: AppConfig) -> tuple[str, str]:
    circles = cfg.scope.circles
    lines = ["你是 AIVC（AI 虚拟细胞）领域的文献分析员。请只根据提供的标题和摘要，调用工具 "
             f"{TOOL_NAME} 输出结构化标注。", "", "## 范围定义"]
    for name in ("core", "peripheral"):
        c = circles.get(name)
        if c:
            lines.append(f"- {name}：{c.description}；主题：{'、'.join(c.topics)}（权重 {c.weight}）")
    lines.append("- none：与以上两圈都无关。")
    lines += [
        "",
        "## 评分细则（relevance，0-10 整数）",
        "分数用于从大量相关论文里挑出最值得读的少数几篇，请拉开差距，不要集中给 7-8 分。",
        "- 10：可能改变领域方向的工作（全新范式、大规模新数据集/基准且明显优于现有方法），极少给出",
        "- 9：核心圈，且满足任一：提出新范式/新思路并有实证支持；发布新的大规模数据集或基准；在公认基准上相对强基线有明确提升。这样的论文约占核心圈的 10-15%，符合就直接给 9，不要因保守而压到 8",
        "- 8：核心圈中有清楚的方法创新且实验扎实，约占 25%",
        "- 7：核心圈的常规增量工作（已有框架上的改进、组合、调参式提升）",
        "- 6：核心圈但贡献有限（小规模验证、应用型、仅分析已有方法），或外围圈中的重要工作",
        "- 4-5：外围圈的一般工作，或与虚拟细胞只有间接关系",
        "- 0-3：基本无关",
        "按摘要中明确声称并有实验支撑的贡献打分，不要因为篇幅短而扣分。",
        "",
        "## 可选枚举（可多选，都不匹配就用 other）",
        "- paradigms 选 1-3 个最主要的建模范式；只要有一个枚举大致贴切就不要用 other。",
        "- tasks：" + "、".join(f"{i.id}（{i.label}）" for i in cfg.scope.taxonomy.tasks),
        "- paradigms：" + "、".join(f"{i.id}（{i.label}）" for i in cfg.scope.taxonomy.paradigms),
        "",
        "## 输出要求",
        "- summary_zh：2-3 句中文摘要；novelty_zh：一句话中文说明「新在哪里」。",
        "- 模型名、数据集名、方法名等术语保留英文原文；论文标题不翻译。",
        "- datasets_used：列出所用数据集的英文名。",
        "- releases_new_dataset：只有论文明确发布新数据集或新基准时才为 true，并把名称写入 new_dataset_names。",
        "- 只根据提供的标题和摘要判断，不得臆测摘要之外的信息。",
    ]
    system = "\n".join(lines)
    abstract = (paper.abstract or "")[: cfg.llm.abstract_max_chars]
    user = "\n".join([
        f"标题：{paper.title}",
        f"来源：{paper.source}" + (f"（{paper.venue}）" if paper.venue else ""),
        f"类目：{', '.join(paper.categories) if paper.categories else '无'}",
        "摘要：",
        abstract,
    ])
    return system, user


# ---------------------------------------------------------------- stats
@dataclass
class ScoreStats:
    scored: int = 0
    cache_hits: int = 0
    api_calls: int = 0
    errors: int = 0
    skipped_no_key: int = 0
    skipped_no_abstract: int = 0
    skipped_budget: int = 0
    skipped_already: int = 0
    disabled_reason: str | None = None


# ---------------------------------------------------------------- validation
class _RawAnnotation(BaseModel):
    tasks: list[str] = Field(default_factory=list)
    paradigms: list[str] = Field(default_factory=list)
    datasets_used: list[str] = Field(default_factory=list)
    releases_new_dataset: bool = False
    new_dataset_names: list[str] = Field(default_factory=list)
    circle: str
    relevance: int
    novelty_zh: str
    summary_zh: str


def _validate(raw: Any, cfg: AppConfig) -> dict:
    """Validate tool input, clamp relevance, map out-of-enum values. Raises on invalid."""
    if not isinstance(raw, dict):
        raise ValueError("tool input is not an object")
    r = _RawAnnotation.model_validate(raw)
    task_ids = set(_ids(cfg.scope.taxonomy.tasks))
    para_ids = set(_ids(cfg.scope.taxonomy.paradigms))

    def _map(vals: list[str], allowed: set[str]) -> list[str]:
        out: list[str] = []
        for v in vals:
            v = v if v in allowed else "other"
            if v not in out:
                out.append(v)
        return out

    circle = r.circle if r.circle in ("core", "peripheral", "none") else "none"
    return {
        "tasks": _map(r.tasks, task_ids),
        "paradigms": _map(r.paradigms, para_ids),
        "datasets_used": r.datasets_used,
        "releases_new_dataset": r.releases_new_dataset,
        "new_dataset_names": r.new_dataset_names,
        "circle": circle,
        "relevance": max(0, min(10, r.relevance)),
        "novelty_zh": r.novelty_zh,
        "summary_zh": r.summary_zh,
    }


def _is_fatal(exc: BaseException) -> bool:
    try:
        import anthropic

        if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError,
                            anthropic.NotFoundError)):
            return True
    except Exception:  # noqa: BLE001
        pass
    return getattr(exc, "status_code", None) in (401, 403, 404)


# ---------------------------------------------------------------- scorer
class LLMScorer:
    def __init__(self, cfg: AppConfig, cache: LLMCache, client=None, api_key: str | None = None):
        self.cfg = cfg
        self.cache = cache
        self._client = client
        if api_key is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
        self._api_key = api_key or None
        self.backend = self._resolve_backend()
        if client is None and self.backend == "claude_cli":
            self._client = ClaudeCLIClient(cfg.llm.cli_command, cfg.llm.cli_timeout)
        self._lock = threading.Lock()
        self._disabled: str | None = None
        self._consec_errors = 0
        self._warned = False
        self._client_failed = False

    def _resolve_backend(self) -> str | None:
        want = (self.cfg.llm.backend or "auto").lower()
        if self._client is not None:
            return "injected"
        has_cli = cli_available(self.cfg.llm.cli_command)
        if want == "api":
            return "api" if self._api_key else None
        if want == "claude_cli":
            return "claude_cli" if has_cli else None
        if self._api_key:
            return "api"
        return "claude_cli" if has_cli else None

    @property
    def available(self) -> bool:
        return self._client is not None or self.backend is not None

    # -- client
    def _get_client(self):
        with self._lock:
            if self._client is None and self.backend == "api" and not self._client_failed:
                try:
                    import anthropic

                    self._client = anthropic.Anthropic(api_key=self._api_key, max_retries=3)
                except Exception as e:  # noqa: BLE001
                    self._client_failed = True
                    self._disabled = f"anthropic 客户端创建失败: {e}"
                    log.warning("LLM 已停用: %s", self._disabled)
            return self._client

    # -- public
    def complete_structured(self, *, system: str, user: str, tool: dict,
                            model: str | None = None, max_tokens: int | None = None) -> dict | None:
        """单次结构化调用（强制 tool_use），返回 tool input；不可用或出错返回 None。"""
        if not self.available:
            return None
        try:
            client = self._get_client()
            if client is None:
                return None
            resp = client.messages.create(
                model=model or self.cfg.llm.model,
                max_tokens=max_tokens or self.cfg.llm.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]},
            )
            block = next((b for b in resp.content if getattr(b, "type", None) == "tool_use"), None)
            if block is None or not isinstance(block.input, dict):
                log.warning("complete_structured: 响应中没有可用的 tool_use")
                return None
            return block.input
        except Exception as e:  # noqa: BLE001
            log.warning("complete_structured 失败: %s: %s", type(e).__name__, str(e)[:150])
            return None

    def score_papers(self, papers: list[Paper], max_calls: int | None = None) -> tuple[list[Paper], ScoreStats]:
        cfg = self.cfg
        stats = ScoreStats()
        budget = cfg.llm.max_calls_per_run if max_calls is None else max_calls
        results: list[Paper | None] = [None] * len(papers)
        todo: list[int] = []

        if not self.available:
            log.warning("没有可用的打分后端（未配置 ANTHROPIC_API_KEY，也找不到 claude 命令），跳过 LLM 打分")
        elif self.backend == "claude_cli":
            log.info("使用本机 Claude Code（claude -p）打分，模型 %s", cfg.llm.model)

        for i, p in enumerate(papers):
            try:
                if (p.llm_status == "scored" and p.llm is not None
                        and p.llm.prompt_version == cfg.llm.prompt_version):
                    results[i] = p
                    stats.skipped_already += 1
                    continue
                if not (p.abstract or "").strip():
                    results[i] = p.model_copy(update={"llm_status": "unscored",
                                                      "llm_skip_reason": "no_abstract"})
                    stats.skipped_no_abstract += 1
                    continue
                cached = self.cache.get(cache_key(p, cfg.llm.model, cfg.llm.prompt_version))
                if cached is not None:
                    try:
                        results[i] = self._apply(p, _validate(cached, cfg))
                        stats.cache_hits += 1
                        stats.scored += 1
                        continue
                    except (ValidationError, ValueError):
                        log.warning("缓存条目无效，重新打分: %s", p.id)
                if not self.available:
                    results[i] = p.model_copy(update={"llm_status": "unscored",
                                                      "llm_skip_reason": "no_api_key"})
                    stats.skipped_no_key += 1
                    continue
                todo.append(i)
            except Exception as e:  # noqa: BLE001
                log.warning("处理 %s 出错: %s", p.id, e)
                results[i] = p.model_copy(update={"llm_status": "error", "llm_skip_reason": str(e)[:200]})
                stats.errors += 1

        calls = {"n": 0}

        def work(i: int) -> None:
            p = papers[i]
            try:
                results[i] = self._score_one(p, budget, calls, stats)
            except Exception as e:  # noqa: BLE001  never propagate
                with self._lock:
                    stats.errors += 1
                results[i] = p.model_copy(update={"llm_status": "error", "llm_skip_reason": str(e)[:200]})

        conc = max(1, cfg.llm.concurrency)
        if conc > 1 and len(todo) > 1:
            with ThreadPoolExecutor(max_workers=conc) as ex:
                list(ex.map(work, todo))
        else:
            for i in todo:
                work(i)

        stats.disabled_reason = self._disabled
        return [r if r is not None else papers[i] for i, r in enumerate(results)], stats

    # -- internals
    def _apply(self, p: Paper, ann: dict) -> Paper:
        cfg = self.cfg
        annotation = LLMAnnotation(
            **ann, model=cfg.llm.model, prompt_version=cfg.llm.prompt_version,
            scored_at=datetime.now(timezone.utc),
        )
        circ = cfg.scope.circles.get(annotation.circle)
        weight = circ.weight if circ is not None else NONE_CIRCLE_WEIGHT
        final = round(annotation.relevance * weight, 1)
        return p.model_copy(update={
            "llm": annotation, "llm_status": "scored", "llm_skip_reason": None,
            "final_score": final, "must_read": final >= cfg.llm.must_read_threshold,
        })

    def _skip(self, p: Paper, reason: str) -> Paper:
        return p.model_copy(update={"llm_status": "unscored", "llm_skip_reason": reason})

    def _score_one(self, p: Paper, budget: int, calls: dict, stats: ScoreStats) -> Paper:
        cfg = self.cfg
        with self._lock:
            if self._disabled:
                return self._skip(p, "llm_disabled")
            if calls["n"] >= budget:
                stats.skipped_budget += 1
                return self._skip(p, "budget")
            calls["n"] += 1
            stats.api_calls += 1
        client = self._get_client()
        if client is None:
            return self._skip(p, "llm_disabled")

        system, user = build_prompt(p, cfg)
        tool = build_tool(cfg)
        last_err = "unknown"
        for attempt in range(2):
            try:
                resp = client.messages.create(
                    model=cfg.llm.model, max_tokens=cfg.llm.max_tokens, system=system,
                    messages=[{"role": "user", "content": user}], tools=[tool],
                    tool_choice={"type": "tool", "name": TOOL_NAME},
                )
                block = next((b for b in resp.content if getattr(b, "type", None) == "tool_use"), None)
                if block is None:
                    raise ValueError("响应中没有 tool_use")
                ann = _validate(block.input, cfg)
            except (ValidationError, ValueError) as e:
                last_err = f"invalid_output: {str(e)[:120]}"
                if attempt == 0:
                    continue  # retry shares the same budget slot
                break
            except Exception as e:  # noqa: BLE001
                if _is_fatal(e):
                    with self._lock:
                        if not self._disabled:
                            self._disabled = f"{type(e).__name__}: {str(e)[:150]}"
                            log.warning("LLM 致命错误，本次运行停用: %s", self._disabled)
                        stats.errors += 1
                    return self._skip(p, "llm_disabled")
                last_err = f"{type(e).__name__}: {str(e)[:120]}"
                break
            else:
                with self._lock:
                    self._consec_errors = 0
                    stats.scored += 1
                self.cache.put(cache_key(p, cfg.llm.model, cfg.llm.prompt_version), ann,
                               model=cfg.llm.model, prompt_version=cfg.llm.prompt_version)
                return self._apply(p, ann)

        with self._lock:
            stats.errors += 1
            self._consec_errors += 1
            if self._consec_errors >= MAX_CONSECUTIVE_ERRORS and not self._disabled:
                self._disabled = f"连续 {MAX_CONSECUTIVE_ERRORS} 篇失败: {last_err}"
                log.warning("LLM 熔断: %s", self._disabled)
        return p.model_copy(update={"llm_status": "error", "llm_skip_reason": last_err})
