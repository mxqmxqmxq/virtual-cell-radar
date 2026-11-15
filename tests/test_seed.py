import os
from datetime import date
from pathlib import Path

from pypdf import PdfWriter

from aivc.config import SeedConfig
from aivc.seed import collect_seed, parse_iclr_markdown, scan_pdfs

TODAY = date(2026, 11, 14)
CFG = SeedConfig(exclude_dirs=[".venv", "__pycache__"], exclude_globs=["*Agenda*.pdf", "mmc*.pdf"])

MD = """# ICLR 2027 投稿：x

> 来源：OpenReview ICLR 2027 投稿，2026-10-11 抓取。共 3 篇。

## 虚拟细胞（2 篇）

### 1. RELAY: Relational In-Context Learning

- 类别：扰动预测、虚拟细胞
- 关键词：Single Cell; Virtual Cell; In-Context Learning
- 链接：[论文页](https://openreview.net/forum?id=0jupRjvLFA) · [PDF](https://openreview.net/pdf?id=0jupRjvLFA)
- OpenReview ID：`0jupRjvLFA`

We present **A** model for perturbation.
Second line of abstract.

### 2. Second Paper

- 类别:靶点发现
- 关键词: Target; Drug
- 链接：[论文页](https://openreview.net/forum?id=AbCdEfGhIj) · [PDF](https://openreview.net/pdf?id=AbCdEfGhIj)
- OpenReview ID：`AbCdEfGhIj`

Abstract two.

## 靶点（1 篇）

### 3. Third Paper

- 类别：多类、A、B
- 关键词：k1; k2
- 链接：[论文页](https://openreview.net/forum?id=ZZZZZZZZZZ) · [PDF](https://openreview.net/pdf?id=ZZZZZZZZZZ)
- OpenReview ID：`ZZZZZZZZZZ`

Abstract three.

### 4. Broken entry without id

- 类别：x

text
"""


def mkpdf(path: Path, title: str | None = None, extra: dict | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    w = PdfWriter()
    w.add_blank_page(72, 72)
    md = {}
    if title is not None:
        md["/Title"] = title
    if extra:
        md.update(extra)
    if md:
        w.add_metadata(md)
    with open(path, "wb") as f:
        w.write(f)


def snapshot(root: Path):
    out = []
    for dp, dns, fns in os.walk(root):
        for n in dns:
            out.append(("d", str(Path(dp, n).relative_to(root))))
        for n in fns:
            st = os.stat(Path(dp, n))
            out.append(("f", str(Path(dp, n).relative_to(root)), st.st_size, st.st_mtime_ns))
    return sorted(out)


def by_local(papers):
    return {p.local_file: p for p in papers}


def test_parse_markdown(tmp_path):
    f = tmp_path / "x.md"
    f.write_text(MD, encoding="utf-8")
    ps = parse_iclr_markdown(f, TODAY)
    assert len(ps) == 3
    p = ps[0]
    assert p.title == "RELAY: Relational In-Context Learning"
    assert p.openreview_id == "0jupRjvLFA"
    assert p.id == "openreview:0jupRjvLFA"
    assert p.tags == ["ICLR2027", "扰动预测", "虚拟细胞"]
    assert p.keywords == ["Single Cell", "Virtual Cell", "In-Context Learning"]
    assert "**A**" in p.abstract and "Second line" in p.abstract
    assert p.first_seen == date(2026, 10, 11)
    assert p.url.endswith("id=0jupRjvLFA") and p.pdf_url == "https://openreview.net/pdf?id=0jupRjvLFA"
    assert p.source == "openreview" and p.authors == [] and p.published is None
    assert ps[1].tags == ["ICLR2027", "靶点发现"]  # half-width colon
    assert ps[2].tags == ["ICLR2027", "多类", "A", "B"]


def test_markdown_first_seen_fallback(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("### 1. T\n\n- OpenReview ID：`abcdefghij`\n\nabs\n", encoding="utf-8")
    assert parse_iclr_markdown(f, TODAY)[0].first_seen == TODAY


def test_filename_patterns(tmp_path):
    for n in ["2606.01042v1.pdf", "2025.02.27.640494v3.full.pdf", "s41586-024-08411-y (1).pdf",
              "CellNavi.pdf", " LCD-CLIPR.pdf",
              "Systematic reconstruction of molecular pathway signatures.pdf",
              "scCausalVI disentangles responses(科研通-ablesci.com).pdf"]:
        mkpdf(tmp_path / n)
    mkpdf(tmp_path / "iclr2027_pdf" / "ICLR2027_AIVC" / "0565GuBFYb_CELLKAFC-KNOWLEDGE-AWARE.pdf")
    ps = scan_pdfs(tmp_path, CFG, TODAY)
    d = {p.local_file: p for p in ps}
    a = d["2606.01042v1.pdf"]
    assert a.arxiv_id == "2606.01042" and a.version == "v1" and a.id == "arxiv:2606.01042"
    assert a.url == "https://arxiv.org/abs/2606.01042" and a.published == date(2026, 6, 1)
    assert a.source == "seed_pdf" and a.tags == ["seed"]
    b = d["2025.02.27.640494v3.full.pdf"]
    assert b.biorxiv_id == "2025.02.27.640494" and b.published == date(2025, 2, 27)
    assert b.doi is None and b.url is None and b.id == "biorxiv:2025.02.27.640494"
    s = d["s41586-024-08411-y (1).pdf"]
    assert s.doi == "10.1038/s41586-024-08411-y" and s.url == "https://doi.org/10.1038/s41586-024-08411-y"
    assert d["CellNavi.pdf"].title == "CellNavi"
    assert d[" LCD-CLIPR.pdf"].title == "LCD-CLIPR"
    assert d["Systematic reconstruction of molecular pathway signatures.pdf"].title.startswith("Systematic")
    assert d["scCausalVI disentangles responses(科研通-ablesci.com).pdf"].title == "scCausalVI disentangles responses"
    o = d["iclr2027_pdf/ICLR2027_AIVC/0565GuBFYb_CELLKAFC-KNOWLEDGE-AWARE.pdf"]
    assert o.openreview_id == "0565GuBFYb"
    assert o.title == "CELLKAFC KNOWLEDGE AWARE"
    assert o.tags == ["seed", "iclr2027_pdf", "ICLR2027_AIVC"]


def test_metadata_title_and_fallback(tmp_path):
    mkpdf(tmp_path / "CellNavi.pdf", title="CellNavi: navigating cell states with graphs")
    mkpdf(tmp_path / "Long Descriptive Filename Here.pdf", title="Microsoft Word - draft.docx")
    mkpdf(tmp_path / "Other Good Filename Here.pdf", title="paper.pdf")
    mkpdf(tmp_path / "Third Filename Of Paper.pdf", title="untitled")
    mkpdf(tmp_path / "Doi Holder.pdf", extra={"/Subject": "doi:10.1234/abc.def-5 more"})
    d = by_local(scan_pdfs(tmp_path, CFG, TODAY))
    c = d["CellNavi.pdf"]
    assert c.title == "CellNavi: navigating cell states with graphs"
    assert "CellNavi" in c.keywords
    assert d["Long Descriptive Filename Here.pdf"].title == "Long Descriptive Filename Here"
    assert d["Other Good Filename Here.pdf"].title == "Other Good Filename Here"
    assert d["Third Filename Of Paper.pdf"].title == "Third Filename Of Paper"
    assert d["Doi Holder.pdf"].doi == "10.1234/abc.def-5"
    assert all(p.authors == [] for p in d.values())


def test_corrupt_pdf(tmp_path):
    (tmp_path / "Broken One.pdf").write_bytes(b"not a pdf at all")
    ps = scan_pdfs(tmp_path, CFG, TODAY)
    assert len(ps) == 1 and ps[0].title == "Broken One"


def test_prune_and_globs(tmp_path):
    mkpdf(tmp_path / "keep.pdf")
    mkpdf(tmp_path / "vcc" / ".venv" / "lib" / "x.pdf")
    mkpdf(tmp_path / ".hidden" / "h.pdf")
    mkpdf(tmp_path / "Westlake AIVC Week Agenda.pdf")
    mkpdf(tmp_path / "mmc2.pdf")
    (tmp_path / "note.txt").write_text("x")
    ps = scan_pdfs(tmp_path, CFG, TODAY)
    assert [p.local_file for p in ps] == ["keep.pdf"]


def test_duplicates_merge(tmp_path):
    mkpdf(tmp_path / "2606.01042v1.pdf")
    mkpdf(tmp_path / "2606.01042v1 (1).pdf")
    mkpdf(tmp_path / "benchmark" / "2026.07.21.739736v2.full.pdf")
    mkpdf(tmp_path / "表型分子生成" / "2026.07.21.739736v2.full (1).pdf")
    ps = scan_pdfs(tmp_path, CFG, TODAY)
    assert len(ps) == 2
    d = {p.id: p for p in ps}
    assert d["arxiv:2606.01042"].local_file == "2606.01042v1.pdf"
    bx = d["biorxiv:2026.07.21.739736"]
    assert bx.tags == ["seed", "benchmark", "表型分子生成"]
    assert bx.local_file == "benchmark/2026.07.21.739736v2.full.pdf"


def test_collect_attaches_iclr_pdf_and_readonly(tmp_path):
    root = tmp_path / "seed"
    (root / "iclr2027_pdf").mkdir(parents=True)
    (root / "iclr2027_pdf" / "ICLR2027_摘要汇总.md").write_text(MD, encoding="utf-8")
    mkpdf(root / "iclr2027_pdf" / "ICLR2027_AIVC" / "0jupRjvLFA_RELAY-Relational-In-Context.pdf")
    mkpdf(root / "iclr2027_pdf" / "ICLR2027_AIVC" / "Qqqqqqqqqq_Not-In-Markdown.pdf")
    mkpdf(root / "CellNavi.pdf")
    before = snapshot(root)
    ps = collect_seed(root, None, CFG, TODAY)
    assert snapshot(root) == before
    ids = [p.id for p in ps]
    assert ids == sorted(ids)
    d = {p.id: p for p in ps}
    relay = d["openreview:0jupRjvLFA"]
    assert relay.local_file == "iclr2027_pdf/ICLR2027_AIVC/0jupRjvLFA_RELAY-Relational-In-Context.pdf"
    assert relay.abstract and relay.title.startswith("RELAY:")
    assert "seed" in relay.tags and "ICLR2027" in relay.tags
    assert len(ps) == 3 + 1 + 1  # 3 md + unmatched ICLR pdf + CellNavi
    assert not any(Path(p.local_file).is_absolute() for p in ps if p.local_file)
    assert d["openreview:AbCdEfGhIj"].local_file is None


def test_collect_missing_md(tmp_path):
    mkpdf(tmp_path / "CellNavi.pdf")
    ps = collect_seed(tmp_path, None, CFG, TODAY)
    assert len(ps) == 1


def test_arxiv_doi_in_metadata_maps_to_arxiv_id(tmp_path):
    mkpdf(tmp_path / "ORBIT.pdf", extra={"/Subject": "https://doi.org/10.48550/arxiv.2605.02142"})
    p = scan_pdfs(tmp_path, CFG, TODAY)[0]
    assert p.arxiv_id == "2605.02142" and p.id == "arxiv:2605.02142" and p.doi is None
