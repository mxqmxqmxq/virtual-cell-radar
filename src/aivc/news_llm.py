"""新闻的 LLM 结构化抽取与相关度打分（复用 LLMScorer 的后端选择与 LLMCache）。

版权约束：RSS 片段只在内存里喂给模型；缓存与入库只有哈希键和模型自写的中文字段。
"""
from __future__ import annotations

import hashlib
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .config import AppConfig
from .llm import MAX_CONSECUTIVE_ERRORS, LLMCache, LLMScorer
from .models import NewsItem, NewsLLM
from .textutil import normalize_title

log = logging.getLogger(__name__)

NEWS_TOOL_NAME = "record_news_event"
EVENT_TYPES = ["funding", "partnership", "m_and_a", "model_or_data_release", "clinical", "people", "other"]
SUMMARY_MAX = 200
SIGNIFICANCE_MAX = 120


# ---------------------------------------------------------------- cache key
def news_cache_key(item: NewsItem, snippet: str, model: str, prompt_version: str) -> str:
    snip_hash = hashlib.sha256((snippet or "").encode("utf-8")).hexdigest()
    raw = f"{model}|{prompt_version}|{normalize_title(item.title)}|{snip_hash}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- tool / prompt
def build_news_tool() -> dict:
    return {
        "name": NEWS_TOOL_NAME,
        "description": "记录对这条行业动态的结构化抽取结果。",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_type": {"type": "string", "enum": EVENT_TYPES},
                "relevance": {"type": "integer", "minimum": 0, "maximum": 10},
                "companies": {"type": "array", "items": {"type": "string"}},
                "amount_usd": {"type": "number"},
                "round": {"type": "string"},
                "investors": {"type": "array", "items": {"type": "string"}},
                "summary_zh": {"type": "string"},
                "significance_zh": {"type": "string"},
            },
            # amount_usd / round 不放进 required：文中没有就不要输出该字段（视为 null）
            "required": ["event_type", "relevance", "companies", "investors",
                         "summary_zh", "significance_zh"],
        },
    }


def build_news_prompt(item: NewsItem, snippet: str, cfg: AppConfig) -> tuple[str, str]:
    ncfg = cfg.pipeline.news
    system = "\n".join([
        f"你是 AI 虚拟细胞（AIVC）方向的行业动态分析员。本站只关注「用 AI 对细胞建模」：虚拟细胞、细胞基础模型、"
        f"扰动响应预测，以及大规模扰动/单细胞/多组学/细胞表型数据；一般的 AI 药物发现不是重点。"
        f"请只根据提供的标题和 RSS 片段，调用工具 {NEWS_TOOL_NAME} 输出结构化抽取结果（提示词版本 {ncfg.prompt_version}）。",
        "",
        "## 事件类型（event_type，七选一）",
        "- funding 融资；partnership 合作；m_and_a 并购；model_or_data_release 模型或数据发布；"
        "clinical 临床进展；people 重要人事；other 其他。",
        "",
        "## 评分细则（relevance，0-10 整数）",
        "relevance 衡量这条动态与「虚拟细胞 / 细胞层面 AI 建模」方向的贴近程度，不是新闻本身的大小。"
        "先判断事件核心是否落在细胞层面（细胞状态、基因表达、扰动响应、单细胞/多组学、细胞成像表型），再定档；"
        "分数用于从大量动态里挑出最值得关注的少数几条，请拉开差距。",
        "- 9-10：虚拟细胞、细胞基础模型、扰动图谱/大规模扰动数据方向的重大事件：该方向机构或公司"
        "（如 Arc Institute、Xaira、Tahoe、Noetik、CZ Biohub/CZI，仅为举例）的大额融资、旗舰模型或大型数据集发布、"
        "重大合作或并购。",
        "- 7-8：核心业务就是细胞建模、扰动筛选、单细胞/多组学/表型 AI 平台的公司的融资、合作、模型或数据发布；"
        "大药企或科技公司专门针对细胞层面建模或扰动数据的合作与投入。",
        "- 6：属于 AI 药物发现或 AI 生物学（含细胞层面计算模型）的新闻，且有明确的细胞、组学或表型筛选成分，"
        "该成分是事件的重要部分（片段里能指出具体的细胞层面模型、数据集或筛选平台），但细胞建模不是事件主体。"
        "没有 AI 或计算建模成分的纯生物学发现、图谱或机制研究，最多给 5；片段没有说明具体模型、数据集或筛选平台时也最多给 5。",
        "- 5：事件本身仍属于 AI 生物学或细胞层面研究，只是细胞、组学或表型数据为配角或一笔带过；"
        "或属于下文例外的方向性分析稿。实验室自动化、诊断设备合作、一般 AI 制药合作，即使提到细胞也按 0-4 处理。",
        "- 0-4：没有细胞层面成分的一般 AI 药物发现：分子生成/设计、结构预测、蛋白或抗体设计、虚拟筛选、ADMET；"
        "AI 设计药物的临床进展（IND、临床试验获批或启动、临床数据）；没有指明细胞/组学内容的 AI 药物发现融资或合作"
        "（无论金额多大）；通用 AI 产品；实验室自动化/机器人（除非直接用于大规模产生扰动或细胞数据）；"
        "AI 医学影像、诊断、临床决策。其中与 AI 生物医药相关的给 3-4，无关的给 0-2。",
        "- 封顶规则：金额、公司知名度、临床阶段都不能把没有细胞层面成分的事件抬到 5 以上；"
        "「细胞治疗」「细胞与基因疗法递送」只是治疗形式，不等于细胞建模，不因此加分；"
        "「foundation model」「AI 平台」字样若不是针对细胞/组学数据也不加分；"
        "举例名单里的机构不自动高分，仍按事件本身判断。",
        "- 公司自家发布的品牌活动、公益、科普、获奖、股价/指数入选、与生物医药无关的业务（如材料、能源）一律 ≤3。",
        "- 泛科普、观点/评论、分析稿、访谈、会议参会回顾、年度盘点/追踪榜单类文章：即使提到融资、合作或交易，也一律 ≤4；"
        "只有报道主体就是某项已完成的具体交易、或首次发布某个具体模型/数据集时才可高于 4。"
        "例外：这类文章的主题本身就是虚拟细胞、细胞基础模型或大规模扰动/单细胞数据方向"
        "（标题或片段明确以该方向的格局、进展或资金为主题，而不只是顺带提到）时给 5，不得高于 5。",
        "- funding 与 amount_usd 只用于已完成、已宣布的公司融资。传闻或洽谈中的融资、公益/基金会/机构资助计划、"
        "VC 或基金自身的募资，不得取 funding 也不得填 amount_usd（event_type 取 other 或 partnership）。",
        "- 公司与非生物医药主体（地方政府、高校行政、咨询、能源/材料等）签订的框架协议、备忘录、区域市场拓展：片段里没有"
        "具体的药物、靶点、模型或数据内容时 ≤4。",
        "- 研究机构、期刊新闻栏目与大型科技公司博客的条目：只有涉及 AI/计算方法用于细胞建模、扰动预测、单细胞/多组学分析，"
        "或发布细胞层面的模型、数据集、基准时才给 ≥5；普通基础生物学发现、通用 AI 产品、人事、获奖、募捐 ≤3。",
        "- 按事件本身判断，不因公司或机构知名而加分；只有标题、没有片段时，依据标题保守打分（不确定就取低分）。",
        "- 月度/季度融资或交易汇总类文章：只根据片段里点名的细胞建模/扰动/组学 AI 方向的公司判断；片段里没有点名任何这类公司时 ≤4，"
        "有则 companies 列出这些公司、event_type 取 funding 或 partnership，summary_zh 简述其中相关的几笔。",
        "",
        "## 输出要求",
        "- 只依据给出的标题与片段，不得臆测其中没有的信息。",
        "- amount_usd：只在文中明确给出金额时填写，并折算为美元数值（如 1.2 亿美元 → 120000000）；"
        "没有金额、或非美元币种且无法可靠折算时不要输出该字段。",
        "- round：如 Seed、Series A、IPO；文中没有就不要输出该字段。investors 只列文中点名的。",
        "- companies：事件涉及的主要公司/机构；有规范名时用规范名的写法。",
        "- summary_zh：1-2 句，不超过 120 字；significance_zh：一句，不超过 60 字，说明为什么值得关注。",
        "- summary_zh 与 significance_zh 必须用自己的话写，不得照抄或逐句翻译原文，不得使用引号引用原文。",
        "- 公司名、模型名、轮次等专名保留英文。",
    ])
    names = [c.name for c in cfg.companies.companies]
    maxc = cfg.sources.news.snippet_max_chars
    user = "\n".join([
        f"标题：{item.title}",
        f"来源：{item.source}",
        f"发布日期：{item.published.isoformat() if item.published else '未知'}",
        f"预筛命中的公司：{'、'.join(item.companies) if item.companies else '无'}",
        f"公司规范名列表：{'、'.join(names)}",
        "片段：",
        (snippet or "")[:maxc],
    ])
    return system, user


# ---------------------------------------------------------------- stats
@dataclass
class NewsLLMStats:
    scored: int = 0
    cache_hits: int = 0
    api_calls: int = 0
    requests: int = 0   # 真实 complete_structured 调用次数（含重试）
    errors: int = 0
    skipped_no_backend: int = 0
    skipped_budget: int = 0
    disabled_reason: str | None = None


# ---------------------------------------------------------------- validation
def _validate(raw: Any) -> dict:
    """校验并后处理 tool input；非法时抛 ValueError。"""
    if not isinstance(raw, dict):
        raise ValueError("tool input is not an object")
    et = raw.get("event_type")
    if et not in EVENT_TYPES:
        raise ValueError(f"invalid event_type: {et!r}")
    try:
        rel = raw["relevance"]
        if isinstance(rel, bool):
            raise ValueError("relevance is bool")
        rel = max(0, min(10, int(round(float(rel)))))
    except (KeyError, TypeError) as e:
        raise ValueError(f"invalid relevance: {e}") from e
    summary = raw.get("summary_zh")
    sig = raw.get("significance_zh")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("summary_zh missing")
    if not isinstance(sig, str):
        raise ValueError("significance_zh missing")

    def _strs(key: str) -> list[str]:
        v = raw.get(key) or []
        if not isinstance(v, list):
            raise ValueError(f"{key} is not a list")
        out: list[str] = []
        for x in v:
            if isinstance(x, str) and x.strip() and x.strip() not in out:
                out.append(x.strip())
        return out

    amount = raw.get("amount_usd")
    if isinstance(amount, bool):
        raise ValueError("amount_usd is bool")
    if amount is not None:
        try:
            amount = float(amount)
        except (TypeError, ValueError) as e:
            raise ValueError("amount_usd not numeric") from e
        if amount != amount or amount <= 0:
            amount = None
    rnd = raw.get("round")
    if rnd is not None and not isinstance(rnd, str):
        raise ValueError("round not a string")
    rnd = (rnd or "").strip() or None

    return {
        "event_type": et,
        "relevance": rel,
        "companies": _strs("companies"),
        "amount_usd": amount,
        "round": rnd,
        "investors": _strs("investors"),
        "summary_zh": summary.strip()[:SUMMARY_MAX],
        "significance_zh": sig.strip()[:SIGNIFICANCE_MAX],
    }


# ---------------------------------------------------------------- extractor
class NewsExtractor:
    def __init__(self, cfg: AppConfig, cache: LLMCache, scorer: LLMScorer | None = None, client=None):
        self.cfg = cfg
        self.cache = cache
        self.scorer = scorer if scorer is not None else LLMScorer(cfg, cache, client=client)
        ncfg = cfg.pipeline.news
        self.model = ncfg.model or cfg.llm.model
        self.prompt_version = ncfg.prompt_version
        self._lock = threading.Lock()
        self._disabled: str | None = None
        self._consec_errors = 0

    @property
    def available(self) -> bool:
        return self.scorer.available

    def extract(self, cands: list[tuple[NewsItem, str]], max_calls: int | None = None,
                max_requests: int | None = None) -> tuple[list[NewsItem], NewsLLMStats]:
        stats = NewsLLMStats()
        budget = self.cfg.pipeline.news.max_llm_calls_per_run if max_calls is None else max_calls
        results: list[NewsItem | None] = [None] * len(cands)
        todo: list[int] = []

        for i, (item, snippet) in enumerate(cands):
            try:
                if (item.llm_status == "scored" and item.llm is not None
                        and item.llm.prompt_version == self.prompt_version):
                    results[i] = item
                    continue
                cached = self.cache.get(news_cache_key(item, snippet, self.model, self.prompt_version))
                if cached is not None:
                    try:
                        results[i] = self._apply(item, _validate(cached))
                        stats.cache_hits += 1
                        stats.scored += 1
                        continue
                    except ValueError:
                        log.warning("新闻缓存条目无效，重新抽取: %s", item.id)
                if not self.available:
                    results[i] = item.model_copy(update={"llm_status": "unscored",
                                                         "llm_skip_reason": "no_backend"})
                    stats.skipped_no_backend += 1
                    continue
                todo.append(i)
            except Exception as e:  # noqa: BLE001
                log.warning("处理新闻 %s 出错: %s", item.id, e)
                results[i] = item.model_copy(update={"llm_status": "error", "llm_skip_reason": str(e)[:200]})
                stats.errors += 1

        calls = {"n": 0}

        def work(i: int) -> None:
            item, snippet = cands[i]
            try:
                results[i] = self._extract_one(item, snippet, budget, calls, stats, max_requests)
            except Exception as e:  # noqa: BLE001
                with self._lock:
                    stats.errors += 1
                results[i] = item.model_copy(update={"llm_status": "error", "llm_skip_reason": str(e)[:200]})

        conc = max(1, self.cfg.llm.concurrency)
        if conc > 1 and len(todo) > 1:
            with ThreadPoolExecutor(max_workers=conc) as ex:
                list(ex.map(work, todo))
        else:
            for i in todo:
                work(i)

        stats.disabled_reason = self._disabled
        return [r if r is not None else cands[i][0] for i, r in enumerate(results)], stats

    # -- internals
    def _apply(self, item: NewsItem, ann: dict) -> NewsItem:
        return item.model_copy(update={
            **ann,
            "llm": NewsLLM(model=self.model, prompt_version=self.prompt_version,
                           scored_at=datetime.now(timezone.utc)),
            "llm_status": "scored",
            "llm_skip_reason": None,
        })

    def _extract_one(self, item: NewsItem, snippet: str, budget: int, calls: dict,
                     stats: NewsLLMStats, max_requests: int | None = None) -> NewsItem:
        with self._lock:
            if self._disabled:
                return item.model_copy(update={"llm_status": "unscored", "llm_skip_reason": "llm_disabled"})
            if calls["n"] >= budget or (max_requests is not None and stats.requests >= max_requests):
                stats.skipped_budget += 1
                return item.model_copy(update={"llm_status": "unscored", "llm_skip_reason": "budget"})
            calls["n"] += 1
            stats.api_calls += 1

        system, user = build_news_prompt(item, snippet, self.cfg)
        tool = build_news_tool()
        last_err = "no_output"
        for _attempt in range(2):
            with self._lock:
                if max_requests is not None and stats.requests >= max_requests:
                    if _attempt == 0:   # 首次尝试即被拦下：按预算跳过
                        stats.skipped_budget += 1
                        return item.model_copy(update={"llm_status": "unscored",
                                                       "llm_skip_reason": "budget"})
                    last_err = "request_limit"   # 重试被拦下：按失败处理
                    break
                stats.requests += 1
            raw = self.scorer.complete_structured(system=system, user=user, tool=tool,
                                                  model=self.model, max_tokens=self.cfg.llm.max_tokens)
            if raw is None:
                last_err = "no_output"
                continue
            try:
                ann = _validate(raw)
            except ValueError as e:
                last_err = f"invalid_output: {str(e)[:120]}"
                continue
            with self._lock:
                self._consec_errors = 0
                stats.scored += 1
            self.cache.put(news_cache_key(item, snippet, self.model, self.prompt_version), ann,
                           model=self.model, prompt_version=self.prompt_version)
            return self._apply(item, ann)

        with self._lock:
            stats.errors += 1
            self._consec_errors += 1
            if self._consec_errors >= MAX_CONSECUTIVE_ERRORS and not self._disabled:
                self._disabled = f"连续 {MAX_CONSECUTIVE_ERRORS} 条失败: {last_err}"
                log.warning("新闻 LLM 熔断: %s", self._disabled)
        return item.model_copy(update={"llm_status": "error", "llm_skip_reason": last_err})
