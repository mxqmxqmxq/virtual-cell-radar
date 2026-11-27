"""Shared resource-link extraction (code repositories, weights, datasets).

``*_url`` is any mentioned link; ``has_*`` is True only with release-context evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ExtractedLinks:
    code_url: str | None
    weights_url: str | None
    has_code: bool
    has_weights: bool
    dataset_url: str | None = None
    has_dataset: bool = False


_TRAIL = ".,;:!?)]}'\"*>"

_NON_REPO_OWNERS = frozenset(
    {
        "topics", "orgs", "features", "about", "site", "explore", "sponsors",
        "marketplace", "settings", "login", "search", "collections", "trending",
        "enterprise",
    }
)

_CODE_RE = re.compile(
    r"https?://(?:www\.)?(github\.com|gitlab\.com)/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)

_HF_RE = re.compile(
    r"https?://(?:www\.)?huggingface\.co/((?:datasets/|spaces/)?)([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)
_HF_BAD_FIRST = frozenset(
    {
        "papers", "blog", "docs", "collections", "models", "datasets", "spaces",
        "organizations", "settings", "join", "login", "api", "tasks", "learn",
        "pricing", "enterprise", "posts", "chat", "welcome",
    }
)

_ZENODO_RE = re.compile(
    r"https?://(?:www\.)?zenodo\.org/records?/(\d+)", re.IGNORECASE
)
_ZENODO_DOI_RE = re.compile(
    r"https?://(?:dx\.)?doi\.org/10\.5281/zenodo\.(\d+)", re.IGNORECASE
)
_FIGSHARE_RE = re.compile(
    r"https?://(?:[A-Za-z0-9-]+\.)*figshare\.com/(?:[A-Za-z0-9_.-]+/)*?"
    r"(?:articles|s|collections)/[^\s<>\"'\])]+",
    re.IGNORECASE,
)


def clean_url(u: str) -> str:
    u = (u or "").strip().lstrip("'\"<([{")
    while u and u[-1] in _TRAIL:
        u = u[:-1]
    u = u.rstrip("/")
    if u.lower().endswith(".git"):
        u = u[:-4]
    return u.rstrip("/" + _TRAIL)


def _clean_repo(name: str) -> str:
    name = name.rstrip(".,;:-_")
    if name.lower().endswith(".git"):
        name = name[:-4]
    return name


_Span = tuple[int, int, str]

_POS_RE = re.compile(
    r"available|availability|code|source code|software|repository|repo\b|implementation|"
    r"released?|release|open[- ]?source|publicly|our (?:model|code|weights)\b|weights|"
    r"checkpoints?|pre-?trained|hosted|download|deposited|data (?:is|are) available|"
    r"github:|huggingface:",
    re.IGNORECASE,
)
_NEG_RE = re.compile(
    r"\b(?:us(?:e|ed|ing)|utili[sz](?:e|ed|ing)|employ(?:ed|s)?|leverag(?:e|ed|ing)|"
    r"based on|built on|building on|adapted from|from|following|extends?|"
    r"fork(?:ed)? of|compared (?:to|with)|baselines?|trained on|via)"
    r"\s*(?:the\s+)?(?:code|implementation|repository|repo|model|weights|library|toolkit|package)?"
    r"\s*(?:of|from|at|in)?\s*[(\[]?\s*$",
    re.IGNORECASE,
)


def has_release_context(text: str, start: int, end: int) -> bool:
    """True if the URL at text[start:end] is presented as a release by the authors."""
    if not text:
        return False
    before = text[max(0, start - 120):start]
    after = text[end:end + 60]
    if _NEG_RE.search(before[-30:]):
        return False
    return bool(_POS_RE.search(before) or _POS_RE.search(after))


def _code_spans(text: str) -> list[_Span]:
    out: list[_Span] = []
    for m in _CODE_RE.finditer(text or ""):
        host, owner, repo = m.group(1).lower(), m.group(2), _clean_repo(m.group(3))
        if owner.lower() in _NON_REPO_OWNERS or not repo or not owner.strip("."):
            continue
        if repo in {".", ".."}:
            continue
        out.append((m.start(), m.end(), f"https://{host}/{owner}/{repo}"))
    return out


def _weights_spans(text: str) -> list[_Span]:
    out: list[_Span] = []
    for m in _HF_RE.finditer(text or ""):
        prefix, a, b = m.group(1).lower(), m.group(2), _clean_repo(m.group(3))
        if prefix or a.lower() in _HF_BAD_FIRST or not b:
            continue
        out.append((m.start(), m.end(), f"https://huggingface.co/{a}/{b}"))
    return out


def _dataset_spans(text: str) -> list[_Span]:
    out: list[_Span] = []
    text = text or ""
    for m in _HF_RE.finditer(text):
        prefix, a, b = m.group(1).lower(), m.group(2), _clean_repo(m.group(3))
        if prefix == "datasets/" and b:
            out.append((m.start(), m.end(), f"https://huggingface.co/datasets/{a}/{b}"))
    for m in _ZENODO_RE.finditer(text):
        out.append((m.start(), m.end(), f"https://zenodo.org/records/{m.group(1)}"))
    for m in _ZENODO_DOI_RE.finditer(text):
        out.append((m.start(), m.end(), f"https://doi.org/10.5281/zenodo.{m.group(1)}"))
    for m in _FIGSHARE_RE.finditer(text):
        url = clean_url(m.group(0))
        if re.search(r"/(?:articles|s|collections)/[^/]+", url):
            out.append((m.start(), m.end(), url))
    return sorted(out, key=lambda c: c[0])


def find_code_url(text: str) -> str | None:
    spans = _code_spans(text)
    return spans[0][2] if spans else None


def find_weights_url(text: str) -> str | None:
    spans = _weights_spans(text)
    return spans[0][2] if spans else None


def find_dataset_url(text: str) -> str | None:
    spans = _dataset_spans(text)
    return spans[0][2] if spans else None


def _pick(text: str, spans: list[_Span]) -> tuple[str | None, bool]:
    """First span with release evidence, else first span (no evidence)."""
    if not spans:
        return None, False
    for s, e, url in spans:
        if has_release_context(text, s, e):
            return url, True
    return spans[0][2], False


def extract_links(*texts: str | None) -> ExtractedLinks:
    code = weights = dataset = None
    hc = hw = hd = False
    for t in texts:
        if not t:
            continue
        if not hc:
            u, ev = _pick(t, _code_spans(t))
            if u and (code is None or ev):
                code, hc = u, ev
        if not hw:
            u, ev = _pick(t, _weights_spans(t))
            if u and (weights is None or ev):
                weights, hw = u, ev
        if not hd:
            u, ev = _pick(t, _dataset_spans(t))
            if u and (dataset is None or ev):
                dataset, hd = u, ev
    return ExtractedLinks(code, weights, hc, hw, dataset, hd)
