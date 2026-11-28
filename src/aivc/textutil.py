"""Text normalisation helpers shared by dedup, storage and fetchers."""
from __future__ import annotations

import re
import unicodedata

_BIORXIV_RE = re.compile(r"^10\.(1101|64898)/(\d{4}\.\d{2}\.\d{2}\.\d{6,})")
_VALID_DOI_RE = re.compile(r"^10\.\d+/\S+$")
_VERSION_RE = re.compile(r"v\d+$", re.IGNORECASE)


def normalize_title(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = re.sub(r"[$\\{}]", "", s)
    s = re.sub(r"[^\w\s]|_", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_doi(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"^(https?://(dx\.)?doi\.org/)", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^doi:\s*", "", s, flags=re.IGNORECASE)
    s = s.strip().lower()
    return _VERSION_RE.sub("", s)


def is_valid_doi(s: str | None) -> bool:
    return bool(s and _VALID_DOI_RE.match(normalize_doi(s)))


def strip_arxiv_version(s: str) -> str:
    return _VERSION_RE.sub("", (s or "").strip())


def biorxiv_id_from_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    m = _BIORXIV_RE.match(normalize_doi(doi))
    return m.group(2) if m else None
