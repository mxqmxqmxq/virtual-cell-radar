"""词项匹配（匹配规则 v1）。预筛、课题匹配与前端 topics.js 共用这一套规则。

规则 v1：
- 词项先去首尾空格。
- 含 CJK（U+4E00–U+9FFF）字符时：按字面子串匹配，区分大小写。
- 否则不区分大小写；词内的空格与连字符等价（[\\s\\-]+）。
- 结尾 ``*`` 表示前缀匹配（后接 \\w*）；开头 ``*`` 允许前面紧挨字母（\\w*）。
- 全大写且不含空格的缩写不加复数，其余自动可选 s/es。
- 命中处前面不能紧挨 [\\w-]，后面不能紧挨 \\w。

已知差异：Python 的 \\w 是 Unicode 的，前端 JS 的 \\w 仅 ASCII；
共享用例（tests/fixtures/term_match_cases.json）因此只用 ASCII 相邻字符。
"""
from __future__ import annotations

import re
from collections.abc import Iterable

_CJK = re.compile(r"[一-鿿]")


def compile_term(term: str) -> re.Pattern[str] | None:
    term = term.strip()
    if not term:
        return None
    if _CJK.search(term):
        return re.compile(re.escape(term))
    wildcard = term.endswith("*")  # 'transcriptom*' -> prefix match
    lead = r"\w*" if term.startswith("*") else ""  # '*MeanFlow' -> scMeanFlow
    term = term.strip("*")
    parts = [re.escape(p) for p in re.split(r"[\s\-]+", term) if p]
    body = r"[\s\-]+".join(parts)
    if wildcard:
        suffix = r"\w*"
    elif is_acronym(term):
        suffix = ""  # UCE / LINCS / L1000: no plural
    else:
        suffix = r"(?:s|es)?"  # allow plural: 'virtual cells'
    # lookarounds instead of \b so terms like 'AIDO.Cell' or 'Perturb-seq' work
    return re.compile(rf"(?<![\w\-]){lead}{body}{suffix}(?!\w)", re.IGNORECASE)


def is_acronym(term: str) -> bool:
    letters = [c for c in term if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters) and " " not in term.strip()


def paper_text(title: str, abstract: str | None, keywords: Iterable[str]) -> str:
    return (title or "") + "\n" + (abstract or "") + "\n" + " ; ".join(keywords)


def any_term_matches(text: str, patterns: Iterable[re.Pattern[str]]) -> bool:
    return any(p.search(text) for p in patterns)
