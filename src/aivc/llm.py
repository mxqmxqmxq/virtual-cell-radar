"""LLM relevance scoring and field extraction (Anthropic API, cached, degrades gracefully)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, Field, ValidationError

from .claude_cli import ClaudeCLIClient, cli_available
from .config import AppConfig
from .models import LLMAnnotation, Paper
from .openai_compat import OpenAICompatClient
from .textutil import normalize_title

log = logging.getLogger(__name__)

TOOL_NAME = "record_paper_annotation"
NONE_CIRCLE_WEIGHT = 0.3
MAX_CONSECUTIVE_ERRORS = 5
LEGACY_PROMPT_VERSIONS = ("v1", "v2", "v3")  # 这些版本允许缺四维分；其余（v4+）强制校验
_sleep = time.sleep  # 测试中可 monkeypatch


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


def _abs_hash(paper: Paper) -> str:
    return hashlib.sha256((paper.abstract or "").encode("utf-8")).hexdigest()


def cache_key(paper: Paper, model: str, prompt_version: str, backend: str = "api") -> str:
    """缓存键：(backend, 有效模型, prompt_version, 规范化标题, 摘要哈希)。"""
    raw = f"{backend}|{model}|{prompt_version}|{normalize_title(paper.title)}|{_abs_hash(paper)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def legacy_cache_key(paper: Paper, model: str, prompt_version: str) -> str:
    """旧缓存键（不含 backend），仅用于兼容读取已有缓存。"""
    raw = f"{model}|{prompt_version}|{normalize_title(paper.title)}|{_abs_hash(paper)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- prompt
def _ids(items) -> list[str]:
    return [i.id for i in items]


DEFAULT_RUBRIC_LINES = [
    "## 评分细则（relevance 相关度，0-10 整数）",
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
    "## 其余三个维度（各 0-10 整数，独立于 relevance）",
    "- novelty_score 新颖性：思路/范式/数据是否新；仅组合已有组件或调参为 4-5，提出新范式或新数据资源为 8 以上。",
    "- rigor_score 严谨性：实验设计、基线、消融、数据规模与可复现性；摘要未体现验证则不高于 5。",
    "- impact_score 潜在影响：对领域方法、数据基础设施或应用的潜在推动；社区可直接复用（代码、权重、数据）时适当上调。",
]

DEFAULT_ANCHOR_LINES = [
    "以下示例均为虚构，仅用于校准分档，不对应任何真实论文：",
    "- 6 档：某工作在单一细胞系的小规模数据上复现已有扰动预测方法并做对比分析（relevance 6，novelty 3，rigor 5，impact 3）。",
    "- 7 档：某工作在已有基础模型上增加一个条件输入模块，在两个公开基准上小幅提升（relevance 7，novelty 5，rigor 6，impact 5）。",
    "- 8 档：某工作提出新的潜空间扰动建模目标，在多个数据集上相对强基线有稳定提升并含消融（relevance 8，novelty 7，rigor 7，impact 7）。",
    "- 9 档：某工作发布跨多细胞类型的大规模扰动数据集并配套统一基准，同时给出明显优于现有方法的新模型（relevance 9，novelty 8，rigor 8，impact 9）。",
    "- 10 档：某工作提出全新的虚拟细胞世界模型范式，在湿实验中前瞻性验证了预测并全面开源（relevance 10，novelty 10，rigor 9，impact 10），极少给出。",
]


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
                "novelty_score": {"type": "integer", "minimum": 0, "maximum": 10},
                "rigor_score": {"type": "integer", "minimum": 0, "maximum": 10},
                "impact_score": {"type": "integer", "minimum": 0, "maximum": 10},
                "novelty_zh": {"type": "string"},
                "summary_zh": {"type": "string"},
                "why_it_matters_zh": {"type": "string"},
                "limitations_zh": {"type": "string"},
            },
            "required": [
                "tasks", "paradigms", "datasets_used", "releases_new_dataset",
                "new_dataset_names", "circle", "relevance", "novelty_score", "rigor_score",
                "impact_score", "novelty_zh", "summary_zh", "why_it_matters_zh", "limitations_zh",
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
    rubric = list(cfg.llm.rubric_lines) or DEFAULT_RUBRIC_LINES
    anchors = list(cfg.llm.anchor_lines) or DEFAULT_ANCHOR_LINES
    lines += ["", *rubric, "", "## 锚点校准（Few-shot）", *anchors, "",
        "## 可选枚举（可多选，都不匹配就用 other）",
        "- paradigms 选 1-3 个最主要的建模范式；只要有一个枚举大致贴切就不要用 other。",
        "- tasks：" + "、".join(f"{i.id}（{i.label}）" for i in cfg.scope.taxonomy.tasks),
        "- paradigms：" + "、".join(f"{i.id}（{i.label}）" for i in cfg.scope.taxonomy.paradigms),
        "",
        "## 输出要求",
        "- summary_zh：2-3 句中文摘要；novelty_zh：一句话中文说明「新在哪里」。",
        "- why_it_matters_zh：1-2 句中文，说明这篇为何值得关注；limitations_zh：1 句中文写主要局限，摘要未提及则写「摘要未提及」。",
        "- novelty_score / rigor_score / impact_score 各 0-10 整数，独立于 relevance 打分。",
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
    papers_attempted: int = 0  # 真正发起过请求的论文数；api_calls 为真实请求数（含重试/降级重发）
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
    novelty_score: int | None = None
    rigor_score: int | None = None
    impact_score: int | None = None
    why_it_matters_zh: str = ""
    limitations_zh: str = ""


class _RawAnnotationV4(_RawAnnotation):
    """v4+：三项分数必填，0-10 整数（严格，不接受 bool/字符串/越界）。"""
    novelty_score: Annotated[int, Field(strict=True, ge=0, le=10)]
    rigor_score: Annotated[int, Field(strict=True, ge=0, le=10)]
    impact_score: Annotated[int, Field(strict=True, ge=0, le=10)]


def _clamp_opt(v: int | None) -> int | None:
    return None if v is None else max(0, min(10, v))


def _validate(raw: Any, cfg: AppConfig) -> dict:
    """Validate tool input, clamp relevance, map out-of-enum values. Raises on invalid."""
    if not isinstance(raw, dict):
        raise ValueError("tool input is not an object")
    strict = cfg.llm.prompt_version not in LEGACY_PROMPT_VERSIONS
    r = (_RawAnnotationV4 if strict else _RawAnnotation).model_validate(raw)
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
        "novelty_score": _clamp_opt(r.novelty_score),
        "rigor_score": _clamp_opt(r.rigor_score),
        "impact_score": _clamp_opt(r.impact_score),
        "why_it_matters_zh": r.why_it_matters_zh,
        "limitations_zh": r.limitations_zh,
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


def _is_transient(exc: BaseException) -> bool:
    """网络/超时（无状态码）、429、5xx 才值得退避重试；400/422 等请求本身的问题不重试。"""
    code = getattr(exc, "status_code", None)
    return code is None or code == 429 or (isinstance(code, int) and code >= 500)


def recompute_final_score(paper: Paper, cfg: AppConfig) -> Paper:
    """纯函数：按当前客观信号零成本重算 final_score / must_read。未打分则原样返回。"""
    ann = paper.llm
    if paper.llm_status != "scored" or ann is None:
        return paper
    lc = cfg.llm
    nov, rig, imp = ann.novelty_score, ann.rigor_score, ann.impact_score
    if nov is not None and rig is not None and imp is not None:
        w = lc.score_weights
        base = w.relevance * ann.relevance + w.novelty * nov + w.rigor * rig + w.impact * imp
    else:
        base = float(ann.relevance)
    bonus = 0.0
    if paper.has_code:
        bonus += lc.bonus_code
    if paper.has_weights or ann.releases_new_dataset:
        bonus += lc.bonus_weights_or_dataset
    if (_is_top_venue(paper.published_venue or paper.venue, lc.top_venues)
            or (paper.hf_upvotes or 0) >= lc.impact_hf_upvotes
            or (paper.citation_count or 0) >= lc.impact_citations
            or (paper.github_stars or 0) >= lc.impact_github_stars):
        bonus += lc.bonus_impact
    bonus = min(bonus, lc.max_objective_bonus)
    circ = cfg.scope.circles.get(ann.circle)
    weight = circ.weight if circ is not None else NONE_CIRCLE_WEIGHT
    final = round(base * weight + (bonus if ann.circle != "none" else 0.0), 1)
    final = max(0.0, min(10.0, final))
    must = final >= lc.must_read_threshold
    if paper.final_score == final and paper.must_read == must:
        return paper
    return paper.model_copy(update={"final_score": final, "must_read": must})


def _is_top_venue(venue: str | None, top_venues: list[str]) -> bool:
    """大小写不敏感；去掉年份/括号后与 top_venues 相等，或以其开头且后接标点而非单词。"""
    if not venue:
        return False
    v = re.sub(r"\([^)]*\)", " ", venue)
    v = re.sub(r"\b(19|20)\d{2}\b", " ", v)
    v = re.sub(r"\s+", " ", v).strip().lower()
    for t in top_venues:
        t = t.strip().lower()
        if not t or not v.startswith(t):
            continue
        # 相等，或后接标点（如 "Nature: Methods"/"Cell, 2026"）；空格后接词视为另一期刊（Nature != Nature Communications）
        if len(v) == len(t) or v[len(t)] in ":,;-–—/|.":
            return True
    return False


# ---------------------------------------------------------------- scorer
class LLMScorer:
    def __init__(self, cfg: AppConfig, cache: LLMCache, client=None, api_key: str | None = None):
        self.cfg = cfg
        self.cache = cache
        self._client = client
        if api_key is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
        self._api_key = api_key or None
        self._disabled: str | None = None
        self.backend = self._resolve_backend()
        if (self.backend == "openai_compat" and not cfg.llm.openai_model
                and cfg.llm.model.lower().startswith("claude")):
            self.backend = None
            self._disabled = ("openai_compat 后端未配置 openai_model，且 model 为 Claude 模型名，"
                              "不会把 Claude 模型名发给 OpenAI 兼容接口")
            log.warning("LLM 已停用: %s", self._disabled)
        if client is None and self.backend == "claude_cli":
            self._client = ClaudeCLIClient(cfg.llm.cli_command, cfg.llm.cli_timeout)
        self._lock = threading.Lock()
        self._consec_errors = 0
        self._warned = False
        self._client_failed = False

    def _resolve_backend(self) -> str | None:
        want = (self.cfg.llm.backend or "auto").lower()
        if self._client is not None:
            return "injected"
        has_cli = cli_available(self.cfg.llm.cli_command)
        oa_key = os.environ.get(self.cfg.llm.openai_api_key_env) or None
        self._openai_key = oa_key
        url = self.cfg.llm.openai_base_url or ""
        oa_ok = bool(oa_key) or "localhost" in url or "127.0.0.1" in url
        if want == "api":
            return "api" if self._api_key else None
        if want == "claude_cli":
            return "claude_cli" if has_cli else None
        if want == "openai_compat":
            return "openai_compat" if oa_ok else None
        if self._api_key:
            return "api"
        if oa_key:
            return "openai_compat"
        return "claude_cli" if has_cli else None

    @property
    def effective_model(self) -> str:
        if self.backend == "openai_compat" and self.cfg.llm.openai_model:
            return self.cfg.llm.openai_model
        return self.cfg.llm.model

    @property
    def _key_backend(self) -> str:
        return self.backend if self.backend in ("api", "claude_cli", "openai_compat") else "api"

    def _key(self, p: Paper) -> str:
        return cache_key(p, self.effective_model, self.cfg.llm.prompt_version, self._key_backend)

    def _cache_get(self, p: Paper) -> dict | None:
        v = self.cache.get(self._key(p))
        if v is None:
            v = self.cache.get(legacy_cache_key(p, self.effective_model, self.cfg.llm.prompt_version))
        return v

    @property
    def available(self) -> bool:
        return self._client is not None or self.backend is not None

    # -- client
    def _get_client(self):
        with self._lock:
            if self._client is None and self.backend == "api" and not self._client_failed:
                try:
                    import anthropic

                    self._client = anthropic.Anthropic(api_key=self._api_key, max_retries=0)  # 重试只走 _score_one（计入预算）
                except Exception as e:  # noqa: BLE001
                    self._client_failed = True
                    self._disabled = f"anthropic 客户端创建失败: {e}"
                    log.warning("LLM 已停用: %s", self._disabled)
            if self._client is None and self.backend == "openai_compat":
                self._client = OpenAICompatClient(
                    self.cfg.llm.openai_base_url, self._openai_key,
                    timeout=self.cfg.llm.cli_timeout, model_override=self.effective_model)
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
                model=model or self.effective_model,
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
                    results[i] = self._degrade(p, "unscored", "no_abstract")
                    stats.skipped_no_abstract += 1
                    continue
                cached = self._cache_get(p)
                if cached is not None:
                    try:
                        results[i] = self._apply(p, _validate(cached, cfg))
                        stats.cache_hits += 1
                        stats.scored += 1
                        continue
                    except (ValidationError, ValueError):
                        log.warning("缓存条目无效，重新打分: %s", p.id)
                if not self.available:
                    results[i] = self._degrade(p, "unscored", "no_api_key")
                    stats.skipped_no_key += 1
                    continue
                todo.append(i)
            except Exception as e:  # noqa: BLE001
                log.warning("处理 %s 出错: %s", p.id, e)
                results[i] = self._degrade(p, "error", str(e)[:200])
                stats.errors += 1

        calls = {"n": 0}

        def work(i: int) -> None:
            p = papers[i]
            try:
                results[i] = self._score_one(p, budget, calls, stats)
            except Exception as e:  # noqa: BLE001  never propagate
                with self._lock:
                    stats.errors += 1
                results[i] = self._degrade(p, "error", str(e)[:200])

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
        annotation = LLMAnnotation(
            **ann, model=self.effective_model, prompt_version=self.cfg.llm.prompt_version,
            scored_at=datetime.now(timezone.utc),
        )
        q = p.model_copy(update={"llm": annotation, "llm_status": "scored", "llm_skip_reason": None})
        return recompute_final_score(q, self.cfg)

    def _degrade(self, p: Paper, status: str, reason: str) -> Paper:
        """未能（重新）打分时的降级：已有有效旧打分保持不动，仅记录跳过原因。"""
        if p.llm_status == "scored" and p.llm is not None:
            log.info("保留旧打分（升级跳过: %s）: %s", reason, p.id)
            return p.model_copy(update={"llm_skip_reason": f"upgrade_skipped:{reason}"})
        return p.model_copy(update={"llm_status": status, "llm_skip_reason": reason})

    @staticmethod
    def _extra_requests(obj: Any) -> int:
        n = getattr(obj, "n_requests", 1)
        return n - 1 if isinstance(n, int) and n > 1 else 0

    def _add_extra(self, obj: Any, calls: dict, stats: ScoreStats) -> None:
        extra = self._extra_requests(obj)
        if extra:
            with self._lock:
                calls["n"] += extra
                stats.api_calls += extra

    def _score_one(self, p: Paper, budget: int, calls: dict, stats: ScoreStats) -> Paper:
        cfg = self.cfg
        if self._disabled:
            return self._degrade(p, "unscored", "llm_disabled")
        client = self._get_client()
        if client is None:
            return self._degrade(p, "unscored", "llm_disabled")

        system, user = build_prompt(p, cfg)
        tool = build_tool(cfg)
        last_err = "unknown"
        invalid_tries = 0
        net_tries = 0
        first = True
        while True:
            with self._lock:
                if calls["n"] >= budget:  # 真实请求数硬上限（含重试）
                    if first:
                        stats.skipped_budget += 1
                        return self._degrade(p, "unscored", "budget")
                    last_err = "budget_exhausted"
                    break
                calls["n"] += 1
                stats.api_calls += 1
                if first:
                    stats.papers_attempted += 1
                    first = False
            try:
                resp = client.messages.create(
                    model=self.effective_model, max_tokens=cfg.llm.max_tokens, system=system,
                    messages=[{"role": "user", "content": user}], tools=[tool],
                    tool_choice={"type": "tool", "name": TOOL_NAME},
                )
            except Exception as e:  # noqa: BLE001
                self._add_extra(e, calls, stats)
                if _is_fatal(e):
                    with self._lock:
                        if not self._disabled:
                            self._disabled = f"{type(e).__name__}: {str(e)[:150]}"
                            log.warning("LLM 致命错误，本次运行停用: %s", self._disabled)
                        stats.errors += 1
                    return self._degrade(p, "unscored", "llm_disabled")
                last_err = f"{type(e).__name__}: {str(e)[:120]}"
                if _is_transient(e) and net_tries < cfg.llm.api_retries:
                    with self._lock:
                        exhausted = calls["n"] >= budget
                    if exhausted:
                        last_err = "budget_exhausted"
                        break
                    _sleep(cfg.llm.retry_backoff_seconds * (2 ** net_tries))
                    net_tries += 1
                    continue
                break  # 400/422 等非瞬态错误不重试
            self._add_extra(resp, calls, stats)
            try:
                block = next((b for b in resp.content if getattr(b, "type", None) == "tool_use"), None)
                if block is None:
                    raise ValueError("响应中没有 tool_use")
                ann = _validate(block.input, cfg)
            except (ValidationError, ValueError) as e:
                last_err = f"invalid_output: {str(e)[:120]}"
                invalid_tries += 1
                if invalid_tries < 2:
                    continue  # 重试同样占真实请求计数
                break
            with self._lock:
                self._consec_errors = 0
                stats.scored += 1
            self.cache.put(self._key(p), ann, model=self.effective_model,
                           backend=self._key_backend, prompt_version=cfg.llm.prompt_version)
            return self._apply(p, ann)

        with self._lock:
            stats.errors += 1
            self._consec_errors += 1
            if self._consec_errors >= MAX_CONSECUTIVE_ERRORS and not self._disabled:
                self._disabled = f"连续 {MAX_CONSECUTIVE_ERRORS} 篇失败: {last_err}"
                log.warning("LLM 熔断: %s", self._disabled)
        return self._degrade(p, "error", last_err)
