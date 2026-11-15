"""Typed loading of config/*.yaml."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

from .topics import Topic, load_topics


class KeywordGroup(BaseModel):
    name: str
    circle: Literal["core", "peripheral"]
    weight: float
    terms: list[str] = Field(default_factory=list)
    # 组合规则：每个子列表至少命中一个词，所有子列表同时命中才算该组命中
    all_of: list[list[str]] = Field(default_factory=list)


class KeywordsConfig(BaseModel):
    groups: list[KeywordGroup]
    exclude_terms: list[str] = Field(default_factory=list)
    min_keyword_score: float = 1.0


class ArxivConfig(BaseModel):
    enabled: bool = True
    base_url: str
    bio_categories: list[str]
    ml_categories: list[str]
    query_terms: list[str]
    page_size: int = 100
    delay_seconds: float = 3.0
    max_results_per_window: int = 2000


class RxivConfig(BaseModel):
    enabled: bool = True
    base_url: str
    server: str
    categories: list[str]
    delay_seconds: float = 1.0
    max_pages_per_category: int = 400


class FeedItem(BaseModel):
    name: str
    url: str = ""


class FeedsConfig(BaseModel):
    enabled: bool = False
    feeds: list[FeedItem] = Field(default_factory=list)


class NewsFeed(BaseModel):
    name: str
    url: str
    kind: Literal["media", "company", "wire"] = "media"
    company: str | None = None
    enabled: bool = True
    # True：条目没有发布日期就丢弃（防止无日期旧闻按首次抓取日被当成新闻）
    require_date: bool = False
    # True：RSS 条目没有发布日期时，去文章页只取发布日期（遵守 robots 与请求间隔，不存正文）
    date_from_article: bool = False
    # 预筛档位：default 命中任一关键词/组合规则/公司名即可；strict 必须命中严格词（泛科技媒体、泛 AI 播客用）；
    # none 不预筛、直接交给 LLM 打分（只用于低产量且高度对口的官方新闻稿源，列表页无摘要时标题词表会误杀）
    prefilter: Literal["default", "strict", "none"] = "default"
    # strict 档的每源严格词；非空则覆盖 news.strict_keywords
    strict_terms: list[str] = Field(default_factory=list)


class NewsHtmlSource(BaseModel):
    """无 RSS 的新闻列表页：只取标题、链接、日期，不抓正文。"""
    name: str
    list_url: str
    kind: Literal["media", "company", "wire"] = "company"
    company: str | None = None
    enabled: bool = True
    link_pattern: str = ""
    exclude_pattern: str | None = None
    same_host_only: bool = True
    min_title_chars: int = 15
    max_items: int = 30
    require_date: bool = True
    # before_nearest：日期写在卡片内、锚点之前（标题/摘要夹在中间），且两个保留锚点之间可能夹着
    # 被过滤掉的其他卡片时使用：取锚点之前、上一个保留锚点之后、date_lookback_chars 以内「离锚点最近」的日期
    date_scope: Literal["inside", "after", "before", "before_nearest", "auto"] = "auto"
    # 只对 before_nearest 生效：向锚点之前回看的最大字符数
    date_lookback_chars: int = 1500
    # 对标题候选逐个 re.sub(pattern, " ") 的正则（例如去掉标题末尾混入的日期）。默认不清洗
    title_strip_patterns: list[str] = Field(default_factory=list)
    # 预筛档位：default 命中任一关键词/组合规则/公司名即可；strict 必须命中严格词；none 不预筛（仅限低产量、高度对口的官方新闻稿源）
    prefilter: Literal["default", "strict", "none"] = "default"
    # strict 档的每源严格词；非空则覆盖 news.strict_keywords
    strict_terms: list[str] = Field(default_factory=list)


class NewsSourceConfig(BaseModel):
    enabled: bool = True
    feeds: list[NewsFeed] = Field(default_factory=list)
    html_sources: list[NewsHtmlSource] = Field(default_factory=list)
    delay_seconds: float = 1.0
    user_agent: str = "virtual-cell-radar/0.1 (+https://github.com/mxqmxqmxq/virtual-cell-radar)"
    respect_robots: bool = True
    max_items_per_feed: int = 100
    snippet_max_chars: int = 1200
    # 每次运行最多为多少个无日期条目请求文章页（只取发布日期）
    max_date_lookups_per_run: int = 20

    @property
    def effective_delay(self) -> float:
        """请求间隔，实际不低于 1 秒。"""
        return max(1.0, self.delay_seconds)


class OpenReviewConfig(BaseModel):
    enabled: bool = False
    venues: list[str] = Field(default_factory=list)
    # refresh-openreview 使用的官方 API v2 地址与请求间隔（秒；实际不会低于 1 秒）
    api_base_url: str = "https://api2.openreview.net"
    delay_seconds: float = 1.0


class HttpConfig(BaseModel):
    timeout: float = 30
    user_agent: str = "virtual-cell-radar/0.1 (+https://github.com/mxqmxqmxq/virtual-cell-radar)"
    max_retries: int = 3


class SeedConfig(BaseModel):
    source_dir: str | None = None
    iclr_md: str | None = None
    exclude_dirs: list[str] = Field(default_factory=list)
    exclude_globs: list[str] = Field(default_factory=list)


class SourcesConfig(BaseModel):
    arxiv: ArxivConfig
    biorxiv: RxivConfig
    medrxiv: RxivConfig
    journals_rss: FeedsConfig = Field(default_factory=FeedsConfig)
    openreview: OpenReviewConfig = Field(default_factory=OpenReviewConfig)
    news: NewsSourceConfig = Field(default_factory=NewsSourceConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    seed: SeedConfig = Field(default_factory=SeedConfig)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        slug = "c-" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return slug


class Company(BaseModel):
    id: str = ""
    name: str
    aliases: list[str] = Field(default_factory=list)
    url: str = ""
    blog_rss: str | None = None
    category: str = "other"
    models: list[str] = Field(default_factory=list)
    paper_terms: list[str] = Field(default_factory=list)
    tier: Literal["top", "normal"] = "normal"

    @model_validator(mode="after")
    def _fill_id(self) -> "Company":
        if not self.id:
            self.id = _slugify(self.name)
        return self


class CompaniesConfig(BaseModel):
    companies: list[Company]


class CircleConfig(BaseModel):
    weight: float
    description: str = ""
    topics: list[str] = Field(default_factory=list)


class TaxonomyItem(BaseModel):
    id: str
    label: str


class Taxonomy(BaseModel):
    tasks: list[TaxonomyItem]
    paradigms: list[TaxonomyItem]


class ScopeConfig(BaseModel):
    circles: dict[Literal["core", "peripheral"], CircleConfig]
    taxonomy: Taxonomy


class LLMConfig(BaseModel):
    model: str = "claude-haiku-5-5"
    max_tokens: int = 1024
    prompt_version: str = "v1"
    max_calls_per_run: int = 300
    concurrency: int = 4
    must_read_threshold: float = 8
    abstract_max_chars: int = 6000
    cache_file: str = "cache/llm.jsonl"
    # 打分后端：auto = 有 ANTHROPIC_API_KEY 用 API，否则有 claude 命令就用本机 Claude Code；
    # api = 只用 API；claude_cli = 只用本机 Claude Code（订阅额度，不需要 key）
    backend: str = "auto"
    cli_command: str = "claude"
    cli_timeout: int = 180


class UpdateConfig(BaseModel):
    default_days: int = 3
    backfill_chunk_days: int = 30


class DedupConfig(BaseModel):
    title_threshold: float = 93


class ReportConfig(BaseModel):
    max_items: int = 200


class WeeklyConfig(BaseModel):
    top_n: int = 10
    max_unscored: int = 15
    max_per_topic: int = 10
    llm_overview: bool = False
    overview_model: str | None = None
    overview_max_tokens: int = 600


class SiteConfig(BaseModel):
    auto_build: bool = True
    port: int = 8000
    top_datasets_used: int = 30


class PublicConfig(BaseModel):
    """公开库的发布约束。"""
    allowed_tags: list[str] = Field(default_factory=lambda: ["ICLR2027"])


class MajorRule(BaseModel):
    id: str
    label: str
    event_types: list[str] = Field(default_factory=list)
    min_amount_usd: float | None = None
    company_tier: str | None = None
    min_relevance: int | None = None


def _default_news_keywords() -> list[str]:
    return [
        "virtual cell", "foundation model", "AI drug discovery", "AI-driven", "machine learning",
        "generative AI", "perturbation", "single-cell", "protein design", "computational biology",
        "drug discovery platform", "AI-enabled", "biological foundation model", "in silico",
    ]


def _default_strict_keywords() -> list[str]:
    return [
        "虚拟细胞", "数字细胞", "细胞基础模型", "细胞大模型", "生物大模型", "生命科学大模型", "单细胞",
        "多组学", "空间组学", "细胞图谱", "基因扰动", "扰动预测", "扰动筛选", "细胞扰动", "扰动数据",
        "AI制药", "AI药物研发", "AI for Science", "AI4S",
        "virtual cell", "cell foundation model", "single-cell", "perturbation", "Perturb-seq", "multi-omics",
        "spatial transcriptomics", "cell atlas", "AI drug discovery", "computational biology", "systems biology",
    ]


class NewsCombo(BaseModel):
    """组合规则：all_of 的每个子列表都至少命中一个词才算命中（用于「AI 词 + 生物医药词」这类单独出现太泛的组合）。"""
    name: str
    all_of: list[list[str]] = Field(default_factory=list)


_AI_TERMS = ["AI", "A.I.", "artificial intelligence", "machine learning", "deep learning", "generative",
             "foundation model", "large language model", "LLM", "neural network", "robot*", "automat*",
             "人工智能", "AI驱动", "AI赋能", "AI原生", "机器人", "大模型"]
_BIO_TERMS = ["drug*", "biotech*", "pharma*", "therapeutic*", "lab", "laboratory", "wet lab", "protein*",
              "cell*", "molecul*", "genom*", "biolog*", "clinical", "antibod*", "life science*",
              "药物", "制药", "生物", "蛋白", "细胞", "分子", "临床", "新药"]


def _default_news_combos() -> list[NewsCombo]:
    return [
        NewsCombo(name="ai+bio", all_of=[_AI_TERMS, _BIO_TERMS]),
        NewsCombo(name="funding+ai", all_of=[["raise*", "funding", "Series A", "Series B", "Series C", "Series D",
                                              "seed round", "venture", "fund", "融资", "投资"], _AI_TERMS]),
        NewsCombo(name="roundup", all_of=[["funding rounds", "deals", "roundup", "round-up", "funding round-up"],
                                          ["biotech*", "pharma*", "drug*"]]),
    ]


def _default_major_rules() -> list[MajorRule]:
    return [
        MajorRule(id="big_funding", label="融资 ≥ 1 亿美元", event_types=["funding"], min_amount_usd=1e8),
        MajorRule(id="top_model_release", label="头部公司模型/数据发布",
                  event_types=["model_or_data_release"], company_tier="top"),
    ]


class NewsConfig(BaseModel):
    run_with_update: bool = True
    default_days: int = 7
    keywords: list[str] = Field(default_factory=_default_news_keywords)
    combos: list[NewsCombo] = Field(default_factory=_default_news_combos)
    # 严格预筛词表：prefilter: strict 的源必须命中其一（源自带 strict_terms 时以后者为准）
    strict_keywords: list[str] = Field(default_factory=_default_strict_keywords)
    # 公司官方 feed 的条目也必须命中关键词或组合规则才入库（避免把公关稿、活动通稿当成行业动态）
    company_feed_requires_match: bool = True
    # 网站与周报展示时，同一公司每个自然周最多展示几条（按相关度取前 N，防止一家刷屏）
    max_per_company_per_week: int = 3
    # 任意连续 30 天内同一公司最多展示几条（滚动上限，<=0 表示不限）
    max_per_company_per_30d: int = 4
    title_threshold: float = 90
    merge_window_days: int = 5
    max_llm_calls_per_run: int = 60
    # --rescore-stale 的独立预算：库内旧提示词版本条目的重打次数上限
    rescore_stale_max_calls: int = 40
    prompt_version: str = "news-v6.1"
    model: str | None = None
    cache_file: str = "cache/news_llm.jsonl"
    site_min_relevance: int = 6
    # 网站新闻页相关动态区的最低相关度；不低于 site_min_relevance 时等于不设相关区
    site_related_min_relevance: int = 5
    weekly_top_n: int = 8
    major: list[MajorRule] = Field(default_factory=_default_major_rules)


class ResourcesConfig(BaseModel):
    """知识资源板块：GitHub 指标刷新与「是否仍在维护」判断。"""
    # 未归档且最近推送在这么多天内算「仍在维护」
    maintained_within_days: int = 365
    # 距上次抓取不足这么多天的仓库刷新时跳过（--force 忽略）
    refresh_min_interval_days: int = 25
    # 单次刷新最多发出的 GitHub API 请求数（匿名限额 60 次/小时，有 GITHUB_TOKEN 为 5000 次/小时）
    max_requests_per_run: int = 100
    # 请求间隔（秒）
    request_delay_seconds: float = 1.0
    # aivc update 时是否一并刷新（命令行 --refresh-resources 也可单次开启）
    run_with_update: bool = False
    github_api_base: str = "https://api.github.com"
    # 自动发现：llm.datasets_used 里被不少于这么多篇论文提到的数据集名，才成为候选
    discover_min_dataset_mentions: int = 3


class PipelineConfig(BaseModel):
    update: UpdateConfig = Field(default_factory=UpdateConfig)
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    report: ReportConfig = Field(default_factory=ReportConfig)
    weekly: WeeklyConfig = Field(default_factory=WeeklyConfig)
    site: SiteConfig = Field(default_factory=SiteConfig)
    public: PublicConfig = Field(default_factory=PublicConfig)
    news: NewsConfig = Field(default_factory=NewsConfig)
    resources: ResourcesConfig = Field(default_factory=ResourcesConfig)


class TopicsConfig(BaseModel):
    topics: list[Topic] = Field(default_factory=list)


class AppConfig(BaseModel):
    keywords: KeywordsConfig
    sources: SourcesConfig
    companies: CompaniesConfig
    scope: ScopeConfig
    llm: LLMConfig
    pipeline: PipelineConfig
    topics: TopicsConfig = Field(default_factory=TopicsConfig)


def _load(config_dir: Path, name: str, model: type[BaseModel]) -> Any:
    path = Path(config_dir) / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"配置文件缺失: {path}（请检查 --config-dir / AIVC_CONFIG_DIR）")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return model.model_validate(data)
    except (yaml.YAMLError, ValidationError) as e:
        raise ValueError(f"配置文件 {path} 无效: {e}") from e


def load_config(config_dir: Path) -> AppConfig:
    d = Path(config_dir)
    return AppConfig(
        keywords=_load(d, "keywords", KeywordsConfig),
        sources=_load(d, "sources", SourcesConfig),
        companies=_load(d, "companies", CompaniesConfig),
        scope=_load(d, "scope", ScopeConfig),
        llm=_load(d, "llm", LLMConfig),
        pipeline=_load(d, "pipeline", PipelineConfig),
        topics=TopicsConfig(topics=load_topics(d / "topics.yaml")),
    )
