"""知识资源板块：精选清单的读写与校验、GitHub 指标刷新、「是否仍在维护」判断。

模块结构（后续任务在此基础上扩展，不改变已有函数的签名）：

1. 存储      ResourceStore：人工精选 repos/tools/data/learning.yaml + 机器写的 candidates.yaml
2. 校验      ResourceStore.validate（离线，`aivc resources check`）
3. 指标      MetricsStore（github_metrics.json）、refresh_github、is_maintained
4. 展示      list_lines（`aivc resources list`）
5. 候选发现  discover_candidates / discover（纯函数，不联网）；accept_candidate / archive_resource（审核命令）
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import textwrap
import time
from dataclasses import dataclass, field
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import yaml
from pydantic import ValidationError

from .config import AppConfig, ResourcesConfig
from .fetchers.github import GitHubClient, GitHubError, RateLimited
from .models import LLMAnnotation, NewsItem, Paper, Resource, github_full_name_from_url  # noqa: F401

log = logging.getLogger("aivc.resources")

RESOURCES_SUBDIR = "resources"
CANDIDATES_FILE = "candidates.yaml"
METRICS_FILE = "github_metrics.json"

# 精选文件 -> 允许的 kind（同一 kind 只能出现在一个文件里）
CURATED_FILES: dict[str, tuple[str, ...]] = {
    "labs.yaml": ("lab",),
    "repos.yaml": ("repo",),
    "tools.yaml": ("tool",),
    "data.yaml": ("data_portal", "benchmark"),
    "learning.yaml": ("course", "review", "tutorial", "blog", "podcast", "other"),
    "community.yaml": ("awesome_list",),
}
KIND_ORDER = ["lab", "repo", "tool", "data_portal", "benchmark", "review", "blog", "podcast", "course", "tutorial",
              "other", "awesome_list"]

_FILE_PURPOSE = {
    "labs.yaml": "精选清单：虚拟细胞方向的代表性实验室、研究团队与机构（kind=lab），人工维护",
    "repos.yaml": "精选清单：GitHub 代码仓库（kind=repo），人工维护",
    "tools.yaml": "精选清单：工具库与软件（kind=tool），人工维护",
    "data.yaml": "精选清单：数据门户与基准/竞赛（kind=data_portal、benchmark），人工维护",
    "learning.yaml": "精选清单：课程、综述、教程、博客、播客等学习资源（kind=course/review/tutorial/blog/podcast/other），人工维护",
    "community.yaml": "精选清单：社区维护的资源合集（awesome 列表等，kind=awesome_list），人工维护；只收录链接，不复制其内容",
    CANDIDATES_FILE: "候选清单：自动发现或待审核的资源（status=candidate/archived），由 aivc resources 命令写入，不会直接展示在公开页",
}
_HEADER_FIELDS = (
    "# 字段：id（小写字母数字与连字符，全库唯一）、name、kind、url、description_zh（用自己的话写的可核实事实）、\n"
    "#       tags、related_tasks / related_paradigms（config/scope.yaml 里的 id）、status（accepted/candidate/archived）、\n"
    "#       added、source（manual/auto_discovered）、github（owner/repo，url 是仓库地址时可省略）、\n"
    "#       paper_ids（论文库 id）、dataset_names（对应的数据集名/别名）、discovered_from、note。\n"
    "# 本文件可能被命令重写（手写注释不会保留），说明请写在 note 字段。\n"
)


class ResourceFileError(ValueError):
    """资源文件无法解析或条目不合法。"""


def _header(filename: str) -> str:
    return f"# 知识资源 · {_FILE_PURPOSE[filename]}\n{_HEADER_FIELDS}"


def _dump_item(r: Resource) -> dict[str, Any]:
    d = r.model_dump(mode="python")
    return {k: v for k, v in d.items() if v is not None and v != []}


def render_file(filename: str, resources: list[Resource]) -> str:
    """确定性输出：固定文件头 + 按原顺序逐条 dump。"""
    if not resources:
        return _header(filename) + "\nresources: []\n"
    blocks = []
    for r in resources:
        text = yaml.safe_dump([_dump_item(r)], allow_unicode=True, sort_keys=False,
                              default_flow_style=False, width=100000)
        blocks.append(textwrap.indent(text, "  ").rstrip("\n"))
    return _header(filename) + "\nresources:\n" + "\n\n".join(blocks) + "\n"


def normalize_resource_url(url: str) -> str:
    u = urlsplit(url.strip())
    host = u.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = u.path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if host == "github.com":
        path = path.lower()
    q = f"?{u.query}" if u.query else ""
    return f"{host}{path}{q}"


class ResourceStore:
    def __init__(self, data_dir: Path | str):
        self.dir = Path(data_dir) / RESOURCES_SUBDIR

    def path(self, filename: str) -> Path:
        return self.dir / filename

    # ---- 读 ----
    def load_file(self, filename: str) -> list[Resource]:
        p = self.path(filename)
        if not p.is_file():
            return []
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise ResourceFileError(f"{filename}: YAML 解析失败：{e}") from e
        items = data.get("resources") if isinstance(data, dict) else None
        if items is None:
            items = []
        if not isinstance(items, list):
            raise ResourceFileError(f"{filename}: 顶层 resources 必须是列表")
        out: list[Resource] = []
        for i, raw in enumerate(items):
            try:
                out.append(Resource.model_validate(raw))
            except ValidationError as e:
                label = raw.get("id", f"#{i + 1}") if isinstance(raw, dict) else f"#{i + 1}"
                msgs = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
                raise ResourceFileError(f"{filename}: 条目 {label} 不合法：{msgs}") from e
        return out

    def load_curated(self) -> list[Resource]:
        out: list[Resource] = []
        for fn in CURATED_FILES:
            out.extend(self.load_file(fn))
        return out

    def load_candidates(self) -> list[Resource]:
        return self.load_file(CANDIDATES_FILE)

    # ---- 写 ----
    def save(self, filename: str, resources: list[Resource]) -> Path:
        if filename not in _FILE_PURPOSE:
            raise ValueError(f"未知资源文件：{filename}")
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self.path(filename)
        p.write_text(render_file(filename, resources), encoding="utf-8")
        return p

    def save_candidates(self, resources: list[Resource]) -> Path:
        return self.save(CANDIDATES_FILE, resources)

    # ---- 校验 ----
    def validate(self, cfg: AppConfig) -> list[str]:
        problems: list[str] = []
        task_ids = {t.id for t in cfg.scope.taxonomy.tasks}
        para_ids = {t.id for t in cfg.scope.taxonomy.paradigms}
        loaded: list[tuple[str, Resource]] = []
        for fn in [*CURATED_FILES, CANDIDATES_FILE]:
            try:
                for r in self.load_file(fn):
                    loaded.append((fn, r))
            except ResourceFileError as e:
                problems.append(str(e))
        seen_id: dict[str, str] = {}
        seen_url: dict[str, str] = {}
        for fn, r in loaded:
            where = f"{fn}: {r.id}"
            if r.id in seen_id:
                problems.append(f"{where}: id 重复（另见 {seen_id[r.id]}）")
            else:
                seen_id[r.id] = fn
            if r.url:
                key = normalize_resource_url(r.url)
                if key in seen_url:
                    problems.append(f"{where}: url 与 {seen_url[key]} 重复（{r.url}）")
                else:
                    seen_url[key] = f"{fn}: {r.id}"
            if fn in CURATED_FILES:
                if r.kind not in CURATED_FILES[fn]:
                    problems.append(f"{where}: kind={r.kind} 不应放在 {fn}（允许：{', '.join(CURATED_FILES[fn])}）")
                if r.status == "candidate":
                    problems.append(f"{where}: 精选文件里不应出现 status=candidate（候选请放 {CANDIDATES_FILE}）")
            elif r.status == "accepted":
                problems.append(f"{where}: {CANDIDATES_FILE} 里不应出现 status=accepted（应移入对应精选文件）")
            if r.status == "accepted":
                if not r.url:
                    problems.append(f"{where}: accepted 条目缺 url")
                if not r.description_zh.strip():
                    problems.append(f"{where}: accepted 条目缺 description_zh")
            for t in r.related_tasks:
                if t not in task_ids:
                    problems.append(f"{where}: related_tasks 含未知 id {t!r}")
            for t in r.related_paradigms:
                if t not in para_ids:
                    problems.append(f"{where}: related_paradigms 含未知 id {t!r}")
        return problems


# ---------------------------------------------------------------------------
# GitHub 指标
# ---------------------------------------------------------------------------

def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def repo_key(full_name: str) -> str:
    return full_name.strip().lower()


class MetricsStore:
    HISTORY_MAX = 24

    def __init__(self, data_dir: Path | str):
        self.path = Path(data_dir) / RESOURCES_SUBDIR / METRICS_FILE

    def load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"fetched_at": None, "repos": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ResourceFileError(f"{METRICS_FILE} 不是合法 JSON：{e}") from e
        data.setdefault("fetched_at", None)
        data.setdefault("repos", {})
        return data

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"fetched_at": data.get("fetched_at"), "repos": dict(sorted(data.get("repos", {}).items()))}
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def add_history(history: list[dict], today: date, stars: int) -> list[dict]:
    """每个自然月最多一条快照（同月覆盖为最新），最多保留最近 24 条。"""
    month = today.isoformat()[:7]
    hist = [h for h in history if str(h.get("date", ""))[:7] != month]
    hist.append({"date": today.isoformat(), "stars": stars})
    hist.sort(key=lambda h: h["date"])
    return hist[-MetricsStore.HISTORY_MAX:]


def is_maintained(metrics: dict | None, today: date, within_days: int) -> bool | None:
    """未归档且最近推送距今 <= within_days 为 True；已归档或太久没推送为 False；无指标返回 None。"""
    if not metrics or metrics.get("status", "ok") != "ok":
        return None
    pushed = _parse_iso(metrics.get("pushed_at"))
    if pushed is None:
        return None
    if metrics.get("archived"):
        return False
    return (today - pushed.date()).days <= within_days


@dataclass
class RefreshReport:
    ok: int = 0
    not_found: int = 0
    errors: int = 0
    skipped_recent: int = 0
    deferred: int = 0
    requests: int = 0
    total_targets: int = 0
    rate_limited: bool = False
    stop_reason: str | None = None
    retry_at: datetime | None = None
    error_details: list[str] = field(default_factory=list)
    not_found_names: list[str] = field(default_factory=list)
    renamed: list[tuple[str, str]] = field(default_factory=list)


def github_targets(resources: list[Resource]) -> list[str]:
    """精选里 status=accepted 且有 github 的仓库（去重，按首次出现顺序，保留原大小写）。"""
    seen: dict[str, str] = {}
    for r in resources:
        if r.status == "accepted" and r.github and repo_key(r.github) not in seen:
            seen[repo_key(r.github)] = r.github
    return list(seen.values())


def refresh_github(rcfg: ResourcesConfig, *, data_dir: Path | str, client: GitHubClient,
                   force: bool = False, limit: int | None = None,
                   now: datetime | None = None, sleep: Callable[[float], None] = time.sleep,
                   store: ResourceStore | None = None) -> RefreshReport:
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(timezone.utc).date()
    store = store or ResourceStore(data_dir)
    mstore = MetricsStore(data_dir)
    data = mstore.load()
    repos: dict[str, dict] = data["repos"]
    rep = RefreshReport()

    names = github_targets(store.load_curated())
    rep.total_targets = len(names)

    def sort_key(item: tuple[int, str]):
        idx, name = item
        m = repos.get(repo_key(name))
        ts = _parse_iso(m.get("fetched_at")) if m else None
        return (0, idx, "") if ts is None else (1, 0, _iso(ts))

    ordered = [n for _, n in sorted(enumerate(names), key=sort_key)]

    todo: list[str] = []
    for name in ordered:
        m = repos.get(repo_key(name))
        ts = _parse_iso(m.get("fetched_at")) if m else None
        if (not force and m and ts and m.get("status") in ("ok", "not_found")
                and (now - ts).days < rcfg.refresh_min_interval_days):
            rep.skipped_recent += 1
            continue
        todo.append(name)

    cap = rcfg.max_requests_per_run if limit is None else min(limit, rcfg.max_requests_per_run)
    cap = max(cap, 0)
    delay = max(rcfg.request_delay_seconds, 0.0)
    done = 0
    for i, name in enumerate(todo):
        if rep.requests >= cap:
            rep.stop_reason = ("达到 --limit" if limit is not None and limit < rcfg.max_requests_per_run
                               else "达到 max_requests_per_run")
            break
        if rep.requests > 0 and delay:
            sleep(delay)
        key = repo_key(name)
        try:
            info = client.get_repo(name)
        except RateLimited as e:
            rep.requests += 1
            rep.rate_limited = True
            rep.retry_at = e.reset_at
            rep.stop_reason = "GitHub API 限流"
            break
        except GitHubError as e:
            rep.requests += 1
            rep.errors += 1
            rep.error_details.append(f"{name}: {e}")
            prev = repos.get(key, {})
            prev.update({"status": "error", "fetched_at": _iso(now)})
            repos[key] = prev
            done += 1
            continue
        rep.requests += 1
        done += 1
        prev = repos.get(key, {})
        if info.status == "not_found":
            prev.update({"status": "not_found", "fetched_at": _iso(now)})
            repos[key] = prev
            rep.not_found += 1
            rep.not_found_names.append(name)
        else:
            if info.full_name and repo_key(info.full_name) != key:
                rep.renamed.append((name, info.full_name))
            repos[key] = {
                "full_name": info.full_name, "html_url": info.html_url, "stars": info.stars,
                "pushed_at": info.pushed_at, "archived": info.archived, "license": info.license,
                "language": info.language, "fetched_at": _iso(now), "status": "ok",
                "history": add_history(prev.get("history", []), today, info.stars or 0),
            }
            rep.ok += 1
        if client.exhausted:
            rep.rate_limited = True
            rep.retry_at = client.reset_at
            rep.stop_reason = "GitHub API 剩余额度为 0"
            break
    rep.deferred = len(todo) - done
    if rep.requests:
        data["fetched_at"] = _iso(now)
        mstore.save(data)
    return rep


# ---------------------------------------------------------------------------
# 展示
# ---------------------------------------------------------------------------

def list_lines(resources: list[Resource], metrics: dict[str, Any], today: date, within_days: int,
               *, status: str | None = None, kind: str | None = None) -> list[str]:
    rows = [r for r in resources if (status is None or r.status == status) and (kind is None or r.kind == kind)]
    lines: list[str] = []
    counts: dict[str, int] = {}
    for k in KIND_ORDER:
        grp = [r for r in rows if r.kind == k]
        if not grp:
            continue
        counts[k] = len(grp)
        lines.append(f"[{k}] {len(grp)} 条")
        for r in grp:
            m = metrics.get("repos", {}).get(repo_key(r.github)) if r.github else None
            stars = str(m["stars"]) if m and m.get("stars") is not None else "-"
            pushed = (m.get("pushed_at") or "-")[:10] if m else "-"
            mt = is_maintained(m, today, within_days)
            maint = "-" if mt is None else ("维护中" if mt else "不活跃/已归档")
            lines.append(f"  {r.id}  {r.name}  [{r.status}]  star={stars}  推送={pushed}  {maint}")
    lines.append("合计：" + (", ".join(f"{k}={n}" for k, n in counts.items()) or "无") + f"（共 {len(rows)} 条）")
    return lines


# ---------------------------------------------------------------------------
# 自动发现候选（纯函数，不联网、不调 LLM；只产出 candidate，绝不写精选文件）
# ---------------------------------------------------------------------------

class ResourceError(ValueError):
    """资源操作（accept/archive）的用户可见错误；CLI 以退出码 2 报告。"""


DISCOVERED_FROM_MAX = 10

_GH_RE = re.compile(
    r"(?<![\w.-])(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", re.IGNORECASE)
# github.com/<owner>/... 里 owner 位置不是用户或组织的保留路径
GITHUB_RESERVED_OWNERS = frozenset({
    "orgs", "topics", "features", "sponsors", "about", "settings", "marketplace", "apps", "collections",
    "explore", "search", "issues", "pulls", "notifications", "login", "logout", "join", "pricing",
    "enterprise", "security", "trending", "events", "readme", "site", "contact", "customer-stories",
    "team", "new", "codespaces", "copilot", "users", "organizations", "sessions", "advisories",
    "resources", "solutions", "stars", "watching", "dashboard", "account", "password_reset", "git-guides",
    "premium-support", "education", "mobile", "downloads", "sitemap", "pages", "topic", "tos", "partners",
})


def extract_github_repos(text: str | None) -> list[str]:
    """从文本中提取 owner/repo（保留首次出现的大小写，按 lower 去重，顺序稳定）。"""
    if not text:
        return []
    out: dict[str, str] = {}
    for m in _GH_RE.finditer(text):
        owner, repo = m.group(1).strip("."), m.group(2).rstrip(".")
        if repo.lower().endswith(".git"):
            repo = repo[:-4]
        repo = repo.rstrip(".")
        if not owner or not repo or owner.lower() in GITHUB_RESERVED_OWNERS:
            continue
        out.setdefault(f"{owner}/{repo}".lower(), f"{owner}/{repo}")
    return list(out.values())


def norm_name(name: str) -> str:
    """名称归一化：小写并去掉非字母数字字符。"""
    return "".join(ch for ch in name.casefold() if ch.isalnum())


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _unique_id(base: str, taken: set[str]) -> str:
    cand, n = base, 2
    while cand in taken:
        cand = f"{base}-{n}"
        n += 1
    taken.add(cand)
    return cand


def _append_from(existing: list[str], new: list[str]) -> list[str]:
    out = list(existing)
    for x in new:
        if x not in out and len(out) < DISCOVERED_FROM_MAX:
            out.append(x)
    return out


@dataclass
class DiscoverResult:
    new: list[Resource]
    candidates: list[Resource]          # 合并 discovered_from 之后的完整候选清单（已有 + 新增）
    merged: list[str] = field(default_factory=list)   # discovered_from 有变化的已有候选 id


class _Index:
    """用于去重的资源索引：github / 规范化 url / 名称 / 数据集名。"""

    def __init__(self, items: list[Resource]):
        self.github: dict[str, Resource] = {}
        self.url: dict[str, Resource] = {}
        self.name: dict[str, Resource] = {}
        for r in items:
            self.add(r)

    def add(self, r: Resource) -> None:
        if r.github:
            self.github.setdefault(repo_key(r.github), r)
        if r.url:
            self.url.setdefault(normalize_resource_url(r.url), r)
        for n in [r.name, *r.dataset_names]:
            if norm_name(n):
                self.name.setdefault(norm_name(n), r)

    def find_repo(self, full_name: str, url: str, *, by_name: bool = True) -> Resource | None:
        hit = self.github.get(repo_key(full_name)) or self.url.get(normalize_resource_url(url))
        if hit is None and by_name:
            hit = self.name.get(norm_name(full_name.split("/", 1)[1]))
        return hit

    def find_dataset(self, name: str) -> Resource | None:
        return self.name.get(norm_name(name))


def discover(papers: list[Paper], news_items: list[NewsItem], curated: list[Resource],
             candidates: list[Resource], cfg: ResourcesConfig, today: date) -> DiscoverResult:
    """从论文与新闻里提取候选仓库与数据集。

    已有候选（含 archived）不会再生成，只合并 discovered_from；与精选重复的不生成。
    """
    min_mentions = max(int(cfg.discover_min_dataset_mentions), 1)

    # --- 收集仓库：full_name(lower) -> (显示名, [来源 id]) ---
    repos: dict[str, tuple[str, list[str]]] = {}

    def see_repo(text: str | None, src_id: str) -> None:
        for fn in extract_github_repos(text):
            disp, srcs = repos.setdefault(repo_key(fn), (fn, []))
            if src_id not in srcs:
                srcs.append(src_id)

    for p in sorted(papers, key=lambda x: x.id):
        see_repo(p.code_url, p.id)
        see_repo(p.abstract, p.id)
    for n in sorted(news_items, key=lambda x: x.id):
        see_repo(n.url, n.id)
        for s in n.sources:
            see_repo(s.url, n.id)
        see_repo(n.summary_zh, n.id)

    # --- 收集数据集：norm -> {names: Counter, papers: [id], new: bool} ---
    ds: dict[str, dict] = {}

    def see_ds(name: str, pid: str, *, new: bool) -> None:
        key = norm_name(name)
        if len(key) < 2:
            return
        d = ds.setdefault(key, {"names": Counter(), "papers": [], "new": False})
        d["names"][name.strip()] += 1
        if pid not in d["papers"]:
            d["papers"].append(pid)
        d["new"] = d["new"] or new

    for p in sorted(papers, key=lambda x: x.id):
        llm = p.llm
        if llm is None:
            continue
        if llm.releases_new_dataset:
            for nm in llm.new_dataset_names:
                see_ds(nm, p.id, new=True)
        mentioned = {norm_name(x): x for x in llm.datasets_used if len(norm_name(x)) >= 2}
        for nm in mentioned.values():
            see_ds(nm, p.id, new=False)

    cur_idx = _Index(curated)
    cand_list = [c.model_copy(deep=True) for c in candidates]
    cand_idx = _Index(cand_list)
    taken = {r.id for r in curated} | {c.id for c in cand_list}

    new: list[Resource] = []
    merged: dict[str, Resource] = {}

    def attach(existing: Resource, src: list[str]) -> None:
        upd = _append_from(existing.discovered_from, src)
        if upd != existing.discovered_from:
            existing.discovered_from = upd
            merged[existing.id] = existing

    for key in sorted(repos):
        disp, srcs = repos[key]
        owner, repo = disp.split("/", 1)
        url = f"https://github.com/{disp}"
        if cur_idx.find_repo(disp, url):
            continue
        ex = cand_idx.find_repo(disp, url, by_name=False)   # 候选之间只按 github/url 判重（同名 fork 不合并）
        if ex is not None:
            attach(ex, srcs)
            continue
        base = f"gh-{_slug(owner)}-{_slug(repo)}"
        if base in ("gh-", "gh--") or not _slug(owner) or not _slug(repo):
            base = "gh-" + hashlib.sha1(key.encode()).hexdigest()[:10]
        r = Resource(id=_unique_id(base, taken), name=repo, kind="repo", url=url, github=disp,
                     status="candidate", source="auto_discovered", added=today,
                     discovered_from=_append_from([], srcs))
        new.append(r)
        cand_idx.add(r)

    for key in sorted(ds):
        d = ds[key]
        if not (d["new"] or len(d["papers"]) >= min_mentions):
            continue
        name = sorted(d["names"].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        if cur_idx.find_dataset(name):
            continue
        ex = cand_idx.find_dataset(name)
        if ex is not None:
            attach(ex, d["papers"])
            continue
        base = "ds-" + (_slug(name) or hashlib.sha1(key.encode()).hexdigest()[:10])
        r = Resource(id=_unique_id(base, taken), name=name, kind="data_portal", url="",
                     status="candidate", source="auto_discovered", added=today,
                     dataset_names=[name], discovered_from=_append_from([], d["papers"]))
        new.append(r)
        cand_idx.add(r)

    return DiscoverResult(new=new, candidates=cand_list + new, merged=list(merged))


def discover_candidates(papers: list[Paper], news_items: list[NewsItem], curated: list[Resource],
                        candidates: list[Resource], cfg: ResourcesConfig, today: date) -> list[Resource]:
    """只返回新候选列表（见 discover）。"""
    return discover(papers, news_items, curated, candidates, cfg, today).new


# ---------------------------------------------------------------------------
# 候选审核：accept / archive
# ---------------------------------------------------------------------------

def file_for_kind(kind: str) -> str:
    for fn, kinds in CURATED_FILES.items():
        if kind in kinds:
            return fn
    raise ResourceError(f"未知 kind：{kind}")


def accept_candidate(store: ResourceStore, rid: str, *, kind: str | None = None, url: str | None = None,
                     name: str | None = None, description: str | None = None,
                     today: date | None = None) -> tuple[Resource, list[str]]:
    """把候选移入对应 kind 的精选文件并置为 accepted。返回（条目，提醒列表）。"""
    cands = store.load_candidates()
    cand = next((c for c in cands if c.id == rid), None)
    if cand is None:
        if any(r.id == rid for r in store.load_curated()):
            raise ResourceError(f"{rid} 已在精选清单里，不是候选")
        raise ResourceError(f"候选里没有 id={rid}")
    data = cand.model_dump(mode="python")
    if url is not None:
        data["url"] = url
        data["github"] = None   # 让模型按新 url 重新推导
    if name:
        data["name"] = name
    if kind:
        data["kind"] = kind
    if description is not None:
        data["description_zh"] = description.strip()
    data["status"] = "accepted"
    if not (data.get("url") or "").strip():
        raise ResourceError(f"{rid} 没有 url：请用 --url 提供资源链接后再 accept")
    try:
        acc = Resource.model_validate(data)
    except ValidationError as e:
        msgs = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
        raise ResourceError(f"{rid} 不能 accept：{msgs}") from e
    curated = store.load_curated()
    key = normalize_resource_url(acc.url)
    for r in curated:
        if normalize_resource_url(r.url or "") == key and r.url:
            raise ResourceError(f"url 与精选条目 {r.id} 重复：{acc.url}")
    fn = file_for_kind(acc.kind)
    store.save(fn, store.load_file(fn) + [acc])
    store.save_candidates([c for c in cands if c.id != rid])
    warns = []
    if not acc.description_zh.strip():
        warns.append(f"提醒：{rid} 的 description_zh 为空，`aivc resources check` 会要求补全（可在 {fn} 里直接编辑，或重新带 --description）")
    return acc, warns


def archive_resource(store: ResourceStore, rid: str) -> tuple[str, str]:
    """候选 -> 在 candidates.yaml 里标 archived（不再被重新发现）；精选 -> 在原文件内标 archived。

    返回（所在文件, 说明）。
    """
    cands = store.load_candidates()
    for c in cands:
        if c.id == rid:
            if c.status == "archived":
                return CANDIDATES_FILE, "已是 archived，无需改动"
            c.status = "archived"
            store.save_candidates(cands)
            return CANDIDATES_FILE, "候选已标为 archived（保留在 candidates.yaml，不会再被发现）"
    for fn in CURATED_FILES:
        items = store.load_file(fn)
        for r in items:
            if r.id == rid:
                if r.status == "archived":
                    return fn, "已是 archived，无需改动"
                r.status = "archived"
                store.save(fn, items)
                return fn, f"精选条目已在 {fn} 内标为 archived（网站不再展示）"
    raise ResourceError(f"没有 id={rid} 的资源")
