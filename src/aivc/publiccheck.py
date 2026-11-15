"""发布前自查（check-public）：只读，逐项返回问题列表。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .topics import load_topics

ABS_PATH_RE = re.compile(r"/(Users|home)/[A-Za-z0-9._-]+/")
_REPORT_PATH_RE = re.compile(r"(/Users/|/home/)")
EXCLUDE_DIRS = {".git", ".venv", "local", "site", ".shots", ".backup", "node_modules",
                "__pycache__", ".pytest_cache"}
EXCLUDE_FILES = {"uv.lock"}
TOPIC_SCAN_DIRS = ("config", "src", "tests", "docs", "data/resources")


def _has_path_hit(rx: re.Pattern, line: str) -> bool:
    """命中位置若处于 http(s) URL 内（同一 token 以 http(s):// 开头，且命中前无逗号/分号等分隔符）则不算；
    file://、vscode:// 等其他 scheme 照常报出。"""
    for m in rx.finditer(line):
        ts = m.start()
        while ts > 0 and not line[ts - 1].isspace():
            ts -= 1
        head = line[ts:m.start()]
        urls = list(re.finditer(r"https?://", head))
        if urls and not re.search(r"[,;|()<>\"'\[\]]", head[urls[-1].end():]):
            continue
        return True
    return False


@dataclass(frozen=True)
class Issue:
    path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line} {self.message}"


def check_data(data_dir: Path, allowed_tags: list[str]) -> list[Issue]:
    """(a) 公开库 papers/*.jsonl 逐行按原始 JSON 检查。"""
    issues: list[Issue] = []
    allowed = set(allowed_tags)
    for f in sorted((Path(data_dir) / "papers").glob("*.jsonl")):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except ValueError:
                issues.append(Issue(str(f), i, "无法解析的 JSON 行"))
                continue
            if not isinstance(d, dict):
                continue
            pid = d.get("id", "?")
            if d.get("source") == "seed_pdf":
                issues.append(Issue(str(f), i, f"source 为 seed_pdf（{pid}）"))
            if d.get("local_file"):
                issues.append(Issue(str(f), i, f"local_file 非空（{pid}）"))
            if "highlight" in d:
                issues.append(Issue(str(f), i, f"存在 highlight 键（{pid}）"))
            bad = [t for t in (d.get("tags") or []) if t not in allowed]
            if bad:
                issues.append(Issue(str(f), i, f"tags 含不在白名单的值 {bad}（{pid}）"))
    return issues


def check_reports_generic(reports_dir: Path) -> list[Issue]:
    """(b) reports 下 .md：seed_pdf 字样、绝对路径。"""
    issues: list[Issue] = []
    for f in sorted(Path(reports_dir).rglob("*.md")):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if "seed_pdf" in line:
                issues.append(Issue(str(f), i, "出现 seed_pdf 字样"))
            if _has_path_hit(_REPORT_PATH_RE, line):
                issues.append(Issue(str(f), i, "出现本机绝对路径"))
    return issues


def check_site_data(site_dir: Path) -> list[Issue]:
    """(c) site/data/papers.json。"""
    f = Path(site_dir) / "data" / "papers.json"
    if not f.is_file():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except ValueError:
        return [Issue(str(f), 1, "papers.json 无法解析")]
    items = data if isinstance(data, list) else (data.get("papers") if isinstance(data, dict) else None) or []
    text = f.read_text(encoding="utf-8").splitlines()
    issues: list[Issue] = []
    for n, p in enumerate(items):
        if not isinstance(p, dict):
            continue
        pid = p.get("id", f"#{n}")
        line = 1
        if len(text) > 1:  # 每条一行时可定位；否则落在第 1 行
            for i, t in enumerate(text, 1):
                if f'"{pid}"' in t and '"id"' in t:
                    line = i
                    break
        if p.get("source") == "seed_pdf":
            issues.append(Issue(str(f), line, f"source 为 seed_pdf（{pid}）"))
        if p.get("local_file"):
            issues.append(Issue(str(f), line, f"local_file 非空（{pid}）"))
        if "highlight" in p:
            issues.append(Issue(str(f), line, f"存在 highlight 键（{pid}）"))
    return issues


NEWS_ITEM_KEYS = frozenset({
    "id", "title", "url", "source", "sources", "published", "companies", "event_type", "amount_usd",
    "round", "investors", "summary_zh", "significance_zh", "relevance", "llm", "llm_status",
    "llm_skip_reason", "major", "major_reasons", "keyword_score", "matched_terms", "first_seen",
    "schema_version",
})
SITE_NEWS_ITEM_KEYS = frozenset({
    "id", "title", "url", "source", "sources", "published", "date", "week", "quarter", "companies",
    "company_ids", "event_type", "amount_usd", "round", "investors", "summary_zh", "significance_zh",
    "relevance", "scored", "tier", "major", "major_reasons", "first_seen",
})
_LONG_EN_RE = re.compile(r"(?:[A-Za-z][A-Za-z'’\-]*[\s,;.]+){24}[A-Za-z][A-Za-z'’\-]*")
MAX_TITLE, MAX_SUMMARY, MAX_SIGNIF = 300, 400, 200


def _http(u) -> bool:
    return isinstance(u, str) and re.match(r"https?://\S+$", u) is not None


def _check_news_row(d: dict, allowed: frozenset, path: str, line: int) -> list[Issue]:
    issues: list[Issue] = []
    nid = d.get("id", "?")
    extra = sorted(set(d) - allowed)
    if extra:
        issues.append(Issue(path, line, f"新闻含白名单外的键 {extra}（{nid}）"))
    if not _http(d.get("url")):
        issues.append(Issue(path, line, f"url 不是 http(s) 链接（{nid}）"))
    srcs = d.get("sources") or []
    if not isinstance(srcs, list):
        srcs = []
    for s in srcs:
        if not isinstance(s, dict) or not _http(s.get("url")):
            issues.append(Issue(path, line, f"sources 含非 http(s) 链接（{nid}）"))
            break
    title = d.get("title")
    if isinstance(title, str) and len(title) > MAX_TITLE:
        issues.append(Issue(path, line, f"title 超过 {MAX_TITLE} 字符（{nid}）"))
    for key, mx in (("summary_zh", MAX_SUMMARY), ("significance_zh", MAX_SIGNIF)):
        v = d.get(key)
        if not isinstance(v, str):
            continue
        if len(v) > mx:
            issues.append(Issue(path, line, f"{key} 超过 {mx} 字（{nid}）"))
        if _LONG_EN_RE.search(v):
            issues.append(Issue(path, line, f"{key} 疑似长段引用原文（连续 ≥25 个英文单词）（{nid}）"))
    return issues


def check_news_data(data_dir: Path) -> list[Issue]:
    """(f) data/news/*.jsonl：键白名单、链接、长度、长引用。"""
    issues: list[Issue] = []
    for f in sorted((Path(data_dir) / "news").glob("*.jsonl")):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except ValueError:
                issues.append(Issue(str(f), i, "无法解析的 JSON 行"))
                continue
            if not isinstance(d, dict):
                issues.append(Issue(str(f), i, "新闻行不是 JSON 对象"))
                continue
            issues += _check_news_row(d, NEWS_ITEM_KEYS, str(f), i)
    return issues


def check_site_news(site_dir: Path) -> list[Issue]:
    """(g) site/data/news.json 的 items。"""
    f = Path(site_dir) / "data" / "news.json"
    if not f.is_file():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except ValueError:
        return [Issue(str(f), 1, "news.json 无法解析")]
    items = data.get("items") if isinstance(data, dict) else None
    issues: list[Issue] = []
    for n, d in enumerate(items or []):
        if isinstance(d, dict):
            issues += _check_news_row(d, SITE_NEWS_ITEM_KEYS, str(f), 1)
        else:
            issues.append(Issue(str(f), 1, f"items[{n}] 不是对象"))
    return issues


RESOURCE_YAML_KEYS = frozenset({
    "id", "name", "kind", "url", "description_zh", "tags", "related_tasks", "related_paradigms", "status",
    "added", "source", "github", "paper_ids", "dataset_names", "discovered_from", "note",
})
SITE_RESOURCE_KEYS = frozenset({
    "id", "name", "kind", "url", "description_zh", "tags", "related_tasks", "related_paradigms", "added",
    "source", "dataset_names", "paper_ids", "github",
})
METRICS_KEYS = frozenset({
    "full_name", "html_url", "stars", "pushed_at", "archived", "license", "language", "fetched_at",
    "status", "history",
})
MAX_RES_DESC = 300
_GH_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def check_resources_data(data_dir: Path) -> list[Issue]:
    """(h) data/resources/*.yaml 与 github_metrics.json。"""
    import yaml
    issues: list[Issue] = []
    rdir = Path(data_dir) / "resources"
    for f in sorted(rdir.glob("*.yaml")):
        try:
            doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            issues.append(Issue(str(f), 1, "YAML 无法解析"))
            continue
        items = doc.get("resources") if isinstance(doc, dict) else None
        if items is None:
            items = []
        if not isinstance(items, list):
            issues.append(Issue(str(f), 1, "顶层 resources 不是列表"))
            continue
        is_cand = f.name == "candidates.yaml"
        for n, d in enumerate(items, 1):
            if not isinstance(d, dict):
                issues.append(Issue(str(f), 1, f"resources[{n}] 不是对象"))
                continue
            rid = d.get("id", f"#{n}")
            extra = sorted(set(d) - RESOURCE_YAML_KEYS)
            if extra:
                issues.append(Issue(str(f), 1, f"资源含白名单外的键 {extra}（{rid}）"))
            url = d.get("url")
            url_empty = url in (None, "")
            if not (is_cand and url_empty) and not _http(url):
                issues.append(Issue(str(f), 1, f"url 不是 http(s) 链接（{rid}）"))
            desc = d.get("description_zh")
            if isinstance(desc, str):
                if len(desc) > MAX_RES_DESC:
                    issues.append(Issue(str(f), 1, f"description_zh 超过 {MAX_RES_DESC} 字（{rid}）"))
                if _LONG_EN_RE.search(desc):
                    issues.append(Issue(str(f), 1, f"description_zh 疑似长段引用原文（连续 ≥25 个英文单词）（{rid}）"))
            gh = d.get("github")
            if gh not in (None, "") and not (isinstance(gh, str) and _GH_NAME_RE.match(gh)):
                issues.append(Issue(str(f), 1, f"github 不是 owner/repo 形式（{rid}）"))
    mf = rdir / "github_metrics.json"
    if mf.is_file():
        try:
            m = json.loads(mf.read_text(encoding="utf-8"))
        except ValueError:
            return issues + [Issue(str(mf), 1, "github_metrics.json 无法解析")]
        repos = m.get("repos") if isinstance(m, dict) else None
        if not isinstance(repos, dict):
            issues.append(Issue(str(mf), 1, "repos 不是对象"))
        else:
            for name, rec in repos.items():
                if not isinstance(rec, dict):
                    issues.append(Issue(str(mf), 1, f"指标记录不是对象（{name}）"))
                    continue
                extra = sorted(set(rec) - METRICS_KEYS)
                if extra:
                    issues.append(Issue(str(mf), 1, f"指标记录含白名单外的键 {extra}（{name}）"))
    return issues


def check_site_resources(site_dir: Path) -> list[Issue]:
    """(i) site/data/resources.json 的 items。"""
    f = Path(site_dir) / "data" / "resources.json"
    if not f.is_file():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except ValueError:
        return [Issue(str(f), 1, "resources.json 无法解析")]
    items = data.get("items") if isinstance(data, dict) else None
    issues: list[Issue] = []
    for n, d in enumerate(items or []):
        if not isinstance(d, dict):
            issues.append(Issue(str(f), 1, f"items[{n}] 不是对象"))
            continue
        rid = d.get("id", f"#{n}")
        extra = sorted(set(d) - SITE_RESOURCE_KEYS)
        if extra:
            issues.append(Issue(str(f), 1, f"资源含白名单外的键 {extra}（{rid}）"))
        if d.get("status") in ("candidate", "archived"):
            issues.append(Issue(str(f), 1, f"含 status={d.get('status')} 的条目（{rid}）"))
        if not _http(d.get("url")):
            issues.append(Issue(str(f), 1, f"url 不是 http(s) 链接（{rid}）"))
    return issues


def _name_re(name: str) -> re.Pattern:
    return re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)")


def _text_files(root: Path):
    for p in sorted(root.rglob("*")):
        if p.is_file():
            yield p


def _read_text(p: Path) -> str | None:
    try:
        b = p.read_bytes()
    except OSError:
        return None
    if b"\0" in b[:8192]:
        return None
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return None


def check_private_topics(root: Path, local_dir: Path, reports_dir: Path, site_dir: Path
                         ) -> tuple[list[Issue], str | None]:
    """(d) 私有课题名不得出现在公开位置。返回 (问题, 跳过说明)。"""
    tf = Path(local_dir) / "topics.yaml"
    if not tf.is_file():
        return [], f"跳过课题名检查：未找到 {tf}（CI 环境正常）"
    names = [t.name for t in load_topics(tf)]
    pats = [(n, _name_re(n)) for n in names if n.strip()]
    issues: list[Issue] = []
    root = Path(root)

    def scan_file(p: Path, only_lines=None):
        txt = _read_text(p)
        if txt is None:
            return
        for i, line in enumerate(txt.splitlines(), 1):
            if only_lines is not None and i not in only_lines:
                continue
            for n, rx in pats:
                if rx.search(line):
                    issues.append(Issue(str(p), i, f"出现私有课题名「{n}」"))

    for d in TOPIC_SCAN_DIRS:
        base = root / d
        if base.is_dir():
            for p in _text_files(base):
                if not (set(p.relative_to(root).parts) & EXCLUDE_DIRS):
                    scan_file(p)
    for p in sorted(root.glob("README*.md")):
        scan_file(p)
    tj = Path(site_dir) / "data" / "topics.json"
    if tj.is_file():
        scan_file(tj)
    rd = Path(reports_dir)
    if rd.is_dir():
        for p in sorted(rd.rglob("*.md")):
            lines = p.read_text(encoding="utf-8").splitlines()
            sel: set[int] = set()
            in_sec = False
            for i, line in enumerate(lines, 1):
                if line.startswith("## "):
                    in_sec = line.strip() == "## 主题追踪"
                    if in_sec:
                        sel.add(i)
                    continue
                if in_sec or "相关课题" in line:
                    sel.add(i)
            if sel:
                scan_file(p, sel)
    return issues, None


def check_repo_paths(root: Path) -> list[Issue]:
    """(e) 仓库文本文件不得含本机绝对路径。"""
    issues: list[Issue] = []
    root = Path(root)
    for p in _text_files(root):
        rel = p.relative_to(root)
        if set(rel.parts[:-1]) & EXCLUDE_DIRS or p.name in EXCLUDE_FILES:
            continue
        txt = _read_text(p)
        if txt is None:
            continue
        for i, line in enumerate(txt.splitlines(), 1):
            if _has_path_hit(ABS_PATH_RE, line):
                issues.append(Issue(str(rel), i, "含本机绝对路径"))
    return issues


def run_checks(*, root: Path, data_dir: Path, reports_dir: Path, site_dir: Path,
               local_dir: Path, allowed_tags: list[str]) -> tuple[list[Issue], list[str]]:
    notes: list[str] = []
    issues = check_data(data_dir, allowed_tags)
    issues += check_reports_generic(reports_dir)
    issues += check_site_data(site_dir)
    issues += check_news_data(data_dir)
    issues += check_site_news(site_dir)
    issues += check_resources_data(data_dir)
    issues += check_site_resources(site_dir)
    ti, note = check_private_topics(root, local_dir, reports_dir, site_dir)
    issues += ti
    if note:
        notes.append(note)
    issues += check_repo_paths(root)
    return issues, notes
