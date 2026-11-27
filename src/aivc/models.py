"""Pydantic schemas: Paper, NewsItem, Resource."""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .textutil import normalize_doi, normalize_title, strip_arxiv_version

Circle = Literal["core", "peripheral", "none"]
LLMStatus = Literal["unscored", "scored", "error"]


class LLMAnnotation(BaseModel):
    tasks: list[str] = Field(default_factory=list)
    paradigms: list[str] = Field(default_factory=list)
    datasets_used: list[str] = Field(default_factory=list)
    releases_new_dataset: bool = False
    new_dataset_names: list[str] = Field(default_factory=list)
    circle: Circle = "none"
    relevance: int = Field(ge=0, le=10)
    novelty_zh: str = ""
    summary_zh: str = ""
    novelty_score: int | None = Field(default=None, ge=0, le=10)
    rigor_score: int | None = Field(default=None, ge=0, le=10)
    impact_score: int | None = Field(default=None, ge=0, le=10)
    why_it_matters_zh: str = ""
    limitations_zh: str = ""
    model: str
    prompt_version: str
    scored_at: datetime


class Paper(BaseModel):
    id: str
    title: str
    abstract: str | None = None
    authors: list[str] = Field(default_factory=list)
    affiliations: list[str] = Field(default_factory=list)
    source: Literal[
        "arxiv", "biorxiv", "medrxiv", "openreview", "journal", "seed_pdf", "other",
        "openalex", "europepmc", "pubmed", "hf_papers", "chemrxiv",
    ]
    venue: str | None = None
    published: date | None = None
    updated: date | None = None
    version: str | None = None
    url: str | None = None
    pdf_url: str | None = None
    doi: str | None = None
    # Source-provided license identifier, stored verbatim (e.g. bioRxiv "cc_by", "cc_no"); not interpreted.
    license: str | None = None
    arxiv_id: str | None = None
    biorxiv_id: str | None = None
    openreview_id: str | None = None
    categories: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    code_url: str | None = None
    has_code: bool | None = None
    has_weights: bool | None = None
    pmid: str | None = None
    pmcid: str | None = None
    openalex_id: str | None = None
    published_doi: str | None = None
    published_venue: str | None = None
    weights_url: str | None = None
    citation_count: int | None = None
    hf_upvotes: int | None = None
    github_stars: int | None = None
    matched_orgs: list[str] = Field(default_factory=list)  # 机构归属（来自 affiliations）
    mentioned_orgs: list[str] = Field(default_factory=list)  # 标题/摘要提及的公司（非归属）
    semantic_score: float = 0.0
    sources: list[str] = Field(default_factory=list)
    local_file: str | None = None  # relative to the seed root, never absolute
    alt_ids: list[str] = Field(default_factory=list)
    alt_urls: list[str] = Field(default_factory=list)
    keyword_score: float = 0.0
    matched_keywords: list[str] = Field(default_factory=list)
    circle: Circle = "none"
    llm_status: LLMStatus = "unscored"
    llm_skip_reason: str | None = None
    llm: LLMAnnotation | None = None
    final_score: float | None = None
    must_read: bool = False
    first_seen: date
    schema_version: int = 1


NewsEventType = Literal[
    "funding", "partnership", "m_and_a", "model_or_data_release", "clinical", "people", "other"
]


class NewsSourceRef(BaseModel):
    name: str
    url: str


class NewsLLM(BaseModel):
    model: str
    prompt_version: str
    scored_at: datetime


class NewsItem(BaseModel):
    """入库的新闻条目：只存标题、链接、来源、时间与 LLM 自写的中文字段，不存原文或摘要片段。"""
    id: str
    title: str
    url: str
    source: str
    sources: list[NewsSourceRef] = Field(default_factory=list)
    published: date | None = None
    companies: list[str] = Field(default_factory=list)
    event_type: NewsEventType | None = None
    amount_usd: float | None = None
    round: str | None = None
    investors: list[str] = Field(default_factory=list)
    summary_zh: str | None = None
    significance_zh: str | None = None
    relevance: int | None = Field(default=None, ge=0, le=10)
    llm: NewsLLM | None = None
    llm_status: LLMStatus = "unscored"
    llm_skip_reason: str | None = None
    major: bool = False
    major_reasons: list[str] = Field(default_factory=list)
    keyword_score: float = 0.0
    matched_terms: list[str] = Field(default_factory=list)
    first_seen: date
    schema_version: int = 1


class NewsEntry(BaseModel):
    """抓取器输出的瞬时条目，不入库；snippet 只在内存中喂给 LLM。"""
    title: str
    url: str
    source: str
    published: date | None = None
    snippet: str = ""
    feed_kind: str = "media"
    feed_company: str | None = None
    # 来源的预筛档位与每源严格词（瞬时字段，不入库）
    feed_prefilter: str = "default"
    feed_strict_terms: list[str] = []


_TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "igshid", "ref", "ref_src", "_hsenc", "_hsmi", "yclid", "msclkid"}


def normalize_news_url(url: str) -> str:
    """规范化 URL：小写 scheme/host，去 fragment、utm_* 等跟踪参数与末尾斜杠。"""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    u = urlsplit(url.strip())
    query = [
        (k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    path = u.path.rstrip("/")
    return urlunsplit((u.scheme.lower(), u.netloc.lower(), path, urlencode(query), ""))


def make_news_id(url: str) -> str:
    return "news:" + hashlib.sha1(normalize_news_url(url).encode("utf-8")).hexdigest()[:16]


ResourceKind = Literal["lab", "repo", "data_portal", "benchmark", "course", "review", "tutorial",
                       "blog", "podcast", "tool", "awesome_list", "other"]
ResourceStatus = Literal["accepted", "candidate", "archived"]
ResourceSource = Literal["manual", "auto_discovered"]

_RESOURCE_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_GITHUB_REPO_RE = re.compile(r"^https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
_GITHUB_FULL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def github_full_name_from_url(url: str) -> str | None:
    """https://github.com/owner/repo（可带 .git 或末尾斜杠）-> "owner/repo"；其他形式（含子路径）返回 None。"""
    m = _GITHUB_REPO_RE.match((url or "").strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


class Resource(BaseModel):
    """知识资源：仓库、数据门户、基准、课程、综述等的精选条目（人工维护，候选由命令写入）。"""
    id: str
    name: str
    kind: ResourceKind
    url: str = ""
    description_zh: str = ""
    tags: list[str] = Field(default_factory=list)
    related_tasks: list[str] = Field(default_factory=list)
    related_paradigms: list[str] = Field(default_factory=list)
    status: ResourceStatus = "accepted"
    added: date
    source: ResourceSource = "manual"
    github: str | None = None
    paper_ids: list[str] = Field(default_factory=list)
    dataset_names: list[str] = Field(default_factory=list)
    discovered_from: list[str] = Field(default_factory=list)
    note: str | None = None

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not _RESOURCE_ID_RE.match(v):
            raise ValueError(f"资源 id 必须是小写字母、数字与连字符组成的 slug：{v!r}")
        return v

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        v = v.strip()
        if v and not re.match(r"^https?://\S+$", v):
            raise ValueError(f"url 必须是 http(s) 链接：{v!r}")
        return v

    @field_validator("github")
    @classmethod
    def _check_github(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not _GITHUB_FULL_NAME_RE.match(v):
            raise ValueError(f"github 必须是 owner/repo 形式：{v!r}")
        return v

    @model_validator(mode="after")
    def _derive(self) -> "Resource":
        if not self.url and self.status == "accepted":
            raise ValueError("status=accepted 的资源必须有 url")
        if self.github is None and self.url:
            self.github = github_full_name_from_url(self.url)
        return self


def make_paper_id(
    *,
    arxiv_id: str | None = None,
    biorxiv_id: str | None = None,
    doi: str | None = None,
    openreview_id: str | None = None,
    pmid: str | None = None,
    openalex_id: str | None = None,
    title: str | None = None,
) -> str:
    if arxiv_id:
        return f"arxiv:{strip_arxiv_version(arxiv_id)}"
    if biorxiv_id:
        return f"biorxiv:{biorxiv_id}"
    if doi:
        return f"doi:{normalize_doi(doi)}"
    if openreview_id:
        return f"openreview:{openreview_id}"
    if pmid and pmid.strip():
        return f"pmid:{pmid.strip()}"
    if openalex_id and openalex_id.strip():
        oid = openalex_id.strip().rsplit("openalex.org/", 1)[-1]
        return f"openalex:{oid}"
    if title:
        return "title:" + hashlib.sha1(normalize_title(title).encode("utf-8")).hexdigest()[:16]
    raise ValueError("make_paper_id needs at least one identifier or a title")
