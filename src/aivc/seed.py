"""Seed ingestion: build initial Paper records from a local PDF folder and an ICLR summary markdown.

Read-only with respect to the source directory. Paths are always passed in by the caller.
"""
from __future__ import annotations

import fnmatch
import logging
import os
import re
from datetime import date, datetime
from pathlib import Path

from .models import Paper, make_paper_id

log = logging.getLogger(__name__)

ICLR_VENUE = "ICLR 2027 (submission, under review)"
_ICLR_MD_REL = Path("iclr2027_pdf") / "ICLR2027_摘要汇总.md"

_ARXIV_RE = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$")
_BIORXIV_RE = re.compile(r"^(\d{4}\.\d{2}\.\d{2}\.\d{6,})(v\d+)?$")
_SPRINGER_RE = re.compile(r"^(s\d{5}-\d{3}-\d{5}-[0-9a-z])$")
_OPENREVIEW_RE = re.compile(r"^([0-9A-Za-z_-]{10})_(.+)$")
_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>]+")
_COPY_RE = re.compile(r"\s*\(\d+\)\s*$")
_AD_RE = re.compile(r"\s*\((?:科研通[^)]*|[^)]*ablesci\.com[^)]*)\)\s*$")
_BAD_TITLE_SUFFIX = (".pdf", ".doc", ".docx", ".tex", ".dvi")


def _split(s: str, seps: str) -> list[str]:
    return [x.strip() for x in re.split(f"[{seps}]", s) if x.strip()]


# ---------------------------------------------------------------- ICLR markdown

def parse_iclr_markdown(path: Path, today: date) -> list[Paper]:
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    first_seen = today
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s*抓取", text)
    if m:
        try:
            first_seen = date.fromisoformat(m.group(1))
        except ValueError:
            pass

    papers: list[Paper] = []
    # split on level-3 headings; text before the first is header
    parts = re.split(r"(?m)^### ", text)[1:]
    for part in parts:
        # cut at next level-2 heading (category section)
        part = re.split(r"(?m)^## ", part)[0]
        lines = part.split("\n")
        head = lines[0].strip()
        hm = re.match(r"^\d+\.\s*(.+)$", head)
        if not hm:
            log.warning("ICLR md: bad entry heading %r, skipped", head[:60])
            continue
        title = hm.group(1).strip()
        meta: dict[str, str] = {}
        body: list[str] = []
        in_meta = True
        for ln in lines[1:]:
            s = ln.strip()
            if in_meta and s.startswith("- "):
                mm = re.match(r"^-\s*([^：:]+?)\s*[：:]\s*(.*)$", s)
                if mm:
                    meta[mm.group(1).strip()] = mm.group(2).strip()
                    continue
            if s:
                in_meta = False
                body.append(s)
        orid = None
        if "OpenReview ID" in meta:
            om = re.search(r"`([^`]+)`", meta["OpenReview ID"])
            orid = om.group(1) if om else meta["OpenReview ID"].strip("` ")
        if not orid:
            log.warning("ICLR md: entry %r has no OpenReview ID, skipped", title[:60])
            continue
        url = pdf_url = None
        link = meta.get("链接", "")
        um = re.search(r"\[论文页\]\(([^)]+)\)", link)
        if um:
            url = um.group(1)
        pm = re.search(r"\[PDF\]\(([^)]+)\)", link)
        if pm:
            pdf_url = pm.group(1)
        tags = ["ICLR2027"]
        for c in _split(meta.get("类别", ""), "、"):
            if c not in tags:
                tags.append(c)
        papers.append(Paper(
            id=make_paper_id(openreview_id=orid),
            title=title,
            abstract=" ".join(body) or None,
            source="openreview",
            venue=ICLR_VENUE,
            url=url,
            pdf_url=pdf_url,
            openreview_id=orid,
            keywords=_split(meta.get("关键词", ""), ";；"),
            tags=tags,
            first_seen=first_seen,
        ))
    return papers


# ---------------------------------------------------------------- PDF scan

def _clean_stem(name: str) -> str:
    stem = name[:-4] if name.lower().endswith(".pdf") else name
    stem = stem.strip()
    for _ in range(3):  # strip ads / copy markers in any order
        new = _AD_RE.sub("", stem)
        new = _COPY_RE.sub("", new).strip()
        if new == stem:
            break
        stem = new
    if stem.endswith(".full"):
        stem = stem[:-5]
    return stem.strip()


def _usable_title(t: str | None) -> bool:
    if not t:
        return False
    t = t.strip()
    low = t.lower()
    return (len(t) >= 15 and " " in t and not low.endswith(_BAD_TITLE_SUFFIX)
            and not low.startswith("microsoft word") and low != "untitled")


def _read_pdf_meta(path: Path) -> tuple[str | None, str | None]:
    """Return (metadata title, doi found in metadata). Never raises."""
    try:
        from pypdf import PdfReader
        with open(path, "rb") as f:
            reader = PdfReader(f, strict=False)
            md = reader.metadata
            if not md:
                return None, None
            title = md.title if isinstance(md.title, str) else None
            doi = None
            for v in md.values():
                if isinstance(v, str):
                    dm = _DOI_RE.search(v)
                    if dm:
                        doi = dm.group(0).rstrip(".,;)")
                        break
            return title, doi
    except Exception as e:  # noqa: BLE001 - corrupt/encrypted files must not crash seeding
        log.warning("cannot read PDF metadata %s: %s", path.name, e)
        return None, None


def _ym_date(yymm: str) -> date | None:
    try:
        return date(2000 + int(yymm[:2]), int(yymm[2:4]), 1)
    except ValueError:
        return None


def _paper_from_pdf(path: Path, rel: Path, today: date) -> Paper:
    stem = _clean_stem(path.name)
    kw = dict(arxiv_id=None, biorxiv_id=None, doi=None, openreview_id=None)
    version = url = None
    published: date | None = None
    fname_title = stem
    short_name = False
    m_ar, m_bx, m_sp = _ARXIV_RE.match(stem), _BIORXIV_RE.match(stem), _SPRINGER_RE.match(stem)
    m_or = _OPENREVIEW_RE.match(stem) if any("iclr" in p.lower() for p in rel.parts[:-1]) else None
    if m_ar:
        kw["arxiv_id"] = m_ar.group(1)
        version = m_ar.group(2)
        url = f"https://arxiv.org/abs/{m_ar.group(1)}"
        published = _ym_date(m_ar.group(1)[:4])
    elif m_bx:
        kw["biorxiv_id"] = m_bx.group(1)
        version = m_bx.group(2)
        y, mo, d = m_bx.group(1).split(".")[:3]
        try:
            published = date(int(y), int(mo), int(d))
        except ValueError:
            pass
    elif m_sp:
        kw["doi"] = f"10.1038/{stem}"
        url = f"https://doi.org/{kw['doi']}"
    elif m_or:
        kw["openreview_id"] = m_or.group(1)
        fname_title = m_or.group(2).replace("-", " ").replace("_", " ").strip()
    else:
        short_name = " " not in stem and len(stem) < 25

    meta_title, meta_doi = _read_pdf_meta(path)
    title = fname_title
    keywords: list[str] = []
    if _usable_title(meta_title):
        title = meta_title.strip()
        if short_name:
            keywords.append(stem)
    if not kw["doi"] and meta_doi:
        am = re.match(r"10\.48550/arxiv\.(\d{4}\.\d{4,5})", meta_doi, re.IGNORECASE)
        if am and not kw["arxiv_id"]:
            kw["arxiv_id"] = am.group(1)
            url = url or f"https://arxiv.org/abs/{am.group(1)}"
        else:
            kw["doi"] = meta_doi
    if not title:
        title = path.stem.strip() or path.name

    try:
        first_seen = datetime.fromtimestamp(os.stat(path).st_mtime).date()
    except OSError:
        first_seen = today
    tags = ["seed"] + list(rel.parts[:-1])
    return Paper(
        id=make_paper_id(title=title, **kw),
        title=title,
        source="seed_pdf",
        published=published,
        version=version,
        url=url,
        doi=kw["doi"],
        arxiv_id=kw["arxiv_id"],
        biorxiv_id=kw["biorxiv_id"],
        openreview_id=kw["openreview_id"],
        keywords=keywords,
        tags=tags,
        local_file=rel.as_posix(),
        first_seen=first_seen,
    )


def _merge_into(a: Paper, b: Paper) -> Paper:
    tags = list(a.tags) + [t for t in b.tags if t not in a.tags]
    kws = list(a.keywords) + [k for k in b.keywords if k not in a.keywords]
    lf = a.local_file
    if b.local_file and (not lf or (len(b.local_file), b.local_file) < (len(lf), lf)):
        lf = b.local_file
    return a.model_copy(update={"tags": tags, "keywords": kws, "local_file": lf})


def scan_pdfs(source_dir: Path, seed_cfg, today: date) -> list[Paper]:
    source_dir = Path(source_dir)
    exclude_dirs = set(getattr(seed_cfg, "exclude_dirs", []) or [])
    globs = list(getattr(seed_cfg, "exclude_globs", []) or [])
    merged: dict[str, Paper] = {}
    for dirpath, dirnames, filenames in os.walk(source_dir):
        dirnames[:] = sorted(d for d in dirnames if d not in exclude_dirs and not d.startswith("."))
        for fn in sorted(filenames):
            if not fn.lower().endswith(".pdf") or fn.startswith("._"):
                continue
            if any(fnmatch.fnmatch(fn, g) for g in globs):
                log.info("excluded by glob: %s", fn)
                continue
            full = Path(dirpath) / fn
            rel = full.relative_to(source_dir)
            try:
                p = _paper_from_pdf(full, rel, today)
            except Exception as e:  # noqa: BLE001
                log.warning("skip %s: %s", rel, e)
                continue
            merged[p.id] = _merge_into(merged[p.id], p) if p.id in merged else p
    return list(merged.values())


# ---------------------------------------------------------------- combine

def collect_seed(source_dir: Path, iclr_md: Path | None, seed_cfg, today: date) -> list[Paper]:
    source_dir = Path(source_dir)
    md_papers: list[Paper] = []
    if iclr_md is None:
        cand = source_dir / _ICLR_MD_REL
        iclr_md = cand if cand.is_file() else None
        if iclr_md is None:
            log.warning("ICLR summary markdown not found at %s, skipping", cand)
    if iclr_md is not None:
        md_papers = parse_iclr_markdown(Path(iclr_md), today)
    by_or = {p.openreview_id: i for i, p in enumerate(md_papers) if p.openreview_id}
    out = list(md_papers)
    others: dict[str, Paper] = {}
    for p in scan_pdfs(source_dir, seed_cfg, today):
        idx = by_or.get(p.openreview_id) if p.openreview_id else None
        if idx is not None:
            out[idx] = _merge_into(out[idx], p.model_copy(update={"keywords": []}))
        else:
            others[p.id] = _merge_into(others[p.id], p) if p.id in others else p
    out.extend(others.values())
    # md ids are unique per openreview id; a pdf with a different id never collides with them
    return sorted(out, key=lambda p: p.id)
