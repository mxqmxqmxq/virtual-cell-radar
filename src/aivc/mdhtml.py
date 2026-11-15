"""Tiny, safe Markdown -> HTML converter for the controlled subset used by weekly reports.

Everything is html.escape'd first; raw HTML is never passed through; only http(s) links are linked.
"""
from __future__ import annotations

import html
import re

_CODE_RE = re.compile(r"`([^`\n]+)`")
_LINK_RE = re.compile(r"\[([^\[\]\n]*)\]\(([^()\s]+)\)")
_BOLD_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_HEADING_RE = re.compile(r"^(#{1,4})\s+(.*?)(?:\s+#+)?\s*$")
_UL_RE = re.compile(r"^\s*[-*]\s+(.*)$")
_OL_RE = re.compile(r"^\s*\d+\.\s+(.*)$")
_HR_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")
_SENTINEL = "\x00"


def _inline(text: str) -> str:
    """Escape then apply inline syntax. Code spans and links are stashed to avoid nested processing."""
    s = html.escape(text, quote=True)
    stash: list[str] = []

    def keep(h: str) -> str:
        stash.append(h)
        return f"{_SENTINEL}{len(stash) - 1}{_SENTINEL}"

    s = s.replace(_SENTINEL, "")
    s = _CODE_RE.sub(lambda m: keep(f"<code>{m.group(1)}</code>"), s)

    def link(m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        if re.match(r"(?i)^https?://", url.replace("&amp;", "&")):
            label_html = _BOLD_RE.sub(r"<strong>\1</strong>", label)
            return keep(f'<a href="{url}" target="_blank" rel="noopener noreferrer">{label_html}</a>')
        return m.group(0)

    s = _LINK_RE.sub(link, s)
    s = _BOLD_RE.sub(r"<strong>\1</strong>", s)
    return re.sub(f"{_SENTINEL}(\\d+){_SENTINEL}", lambda m: stash[int(m.group(1))], s)


def _split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    cells: list[str] = []
    cur: list[str] = []
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line) and line[i + 1] == "|":
            cur.append("|")
            i += 2
            continue
        if ch == "|":
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    tail = "".join(cur).strip()
    if tail or not line.endswith("|"):
        cells.append(tail)
    return cells


def _is_table_start(lines: list[str], i: int) -> bool:
    return (
        i + 1 < len(lines)
        and "|" in lines[i]
        and "|" in lines[i + 1]
        and bool(_TABLE_SEP_RE.match(lines[i + 1]))
        and "-" in lines[i + 1]
    )


def _is_block_start(lines: list[str], i: int) -> bool:
    ln = lines[i]
    return bool(
        _HEADING_RE.match(ln) or _HR_RE.match(ln) or ln.lstrip().startswith(">")
        or _UL_RE.match(ln) or _OL_RE.match(ln) or _is_table_start(lines, i)
    )


def render(md: str) -> str:
    try:
        return _render(md or "")
    except Exception:  # never raise on any input
        return f"<p>{html.escape(str(md))}</p>"


def _render(md: str) -> str:
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue
        m = _HEADING_RE.match(ln)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{_inline(m.group(2))}</h{lvl}>")
            i += 1
            continue
        if _HR_RE.match(ln):
            out.append("<hr>")
            i += 1
            continue
        if _is_table_start(lines, i):
            head = _split_row(lines[i])
            i += 2
            rows: list[list[str]] = []
            while i < n and lines[i].strip() and "|" in lines[i]:
                rows.append(_split_row(lines[i]))
                i += 1
            t = ["<table>", "<thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead>", "<tbody>"]
            for r in rows:
                r = (r + [""] * len(head))[: max(len(head), len(r))]
                t.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>")
            t.append("</tbody></table>")
            out.append("".join(t))
            continue
        if ln.lstrip().startswith(">"):
            buf = []
            while i < n and lines[i].lstrip().startswith(">"):
                buf.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append("<blockquote><p>" + "<br>".join(_inline(b) for b in buf) + "</p></blockquote>")
            continue
        um, om = _UL_RE.match(ln), _OL_RE.match(ln)
        if um or om:
            rx, tag = (_UL_RE, "ul") if um else (_OL_RE, "ol")
            items = []
            while i < n and rx.match(lines[i]):
                items.append(rx.match(lines[i]).group(1))
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + f"</{tag}>")
            continue
        buf = [ln.strip()]
        i += 1
        while i < n and lines[i].strip() and not _is_block_start(lines, i):
            buf.append(lines[i].strip())
            i += 1
        out.append("<p>" + "<br>".join(_inline(b) for b in buf) + "</p>")
    return "\n".join(out)
