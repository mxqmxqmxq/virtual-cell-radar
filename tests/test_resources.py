import json
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from aivc import cli, resources as R
from aivc.config import ResourcesConfig, load_config
from aivc.fetchers.github import GitHubClient
from aivc.models import Resource, github_full_name_from_url

FIX = Path(__file__).parent / "fixtures" / "github"
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


def mk(id="demo", kind="repo", **kw) -> Resource:
    base = dict(id=id, name=id.title(), kind=kind, url=f"https://github.com/org/{id}",
                description_zh="一个示例仓库", added=date(2026, 11, 15))
    base.update(kw)
    return Resource(**base)


def api_json(name="org/demo", stars=10, pushed="2026-10-01T00:00:00Z", archived=False):
    return {"full_name": name, "html_url": f"https://github.com/{name}", "stargazers_count": stars,
            "pushed_at": pushed, "archived": archived, "license": {"spdx_id": "MIT"}, "language": "Python"}


def mock_client(handler=None, remaining=None):
    calls = []

    def default(req: httpx.Request):
        calls.append(req.url.path)
        name = req.url.path.removeprefix("/repos/")
        return httpx.Response(200, json=api_json(name, stars=len(calls)),
                              headers={"X-RateLimit-Remaining": str(remaining if remaining is not None else 50)})

    def wrapped(req):
        if handler is None:
            return default(req)
        calls.append(req.url.path)
        return handler(req)

    c = GitHubClient(token=None, client=httpx.Client(transport=httpx.MockTransport(wrapped), follow_redirects=True))
    return c, calls


def setup_repos(tmp_path, names):
    st = R.ResourceStore(tmp_path)
    st.save("repos.yaml", [mk(n) for n in names])
    return st


def rcfg(**kw):
    base = dict(request_delay_seconds=0, max_requests_per_run=100, refresh_min_interval_days=25)
    base.update(kw)
    return ResourcesConfig(**base)


# ---------- 模型 ----------

def test_model_github_auto_derived():
    assert mk().github == "org/demo"
    assert mk(url="https://github.com/Org/Demo.git").github == "Org/Demo"
    assert mk(url="https://github.com/org/demo/tree/main").github is None
    assert mk(url="https://example.org/x").github is None
    assert mk(github="a/b").github == "a/b"
    assert github_full_name_from_url("https://www.github.com/o/r/") == "o/r"


def test_model_defaults_and_validation():
    r = mk()
    assert (r.status, r.source, r.tags, r.paper_ids, r.dataset_names, r.note) == ("accepted", "manual", [], [], [], None)
    for bad in ({"id": "Bad_ID"}, {"id": "-x"}, {"kind": "github_repo"}, {"url": "ftp://x"}, {"github": "nope"},
                {"status": "rejected"}, {"source": "bot"}):
        with pytest.raises(ValidationError):
            mk(**bad)
    with pytest.raises(ValidationError):
        mk(url="")  # accepted 必须有 url
    assert mk(url="", status="candidate").url == ""
    assert mk(url="", status="archived").url == ""


# ---------- 存储 ----------

def test_skeleton_files_in_repo_data_dir():
    d = Path(__file__).parent.parent / "data" / "resources"
    for fn in ["repos.yaml", "tools.yaml", "data.yaml", "learning.yaml", "candidates.yaml"]:
        assert (d / fn).is_file(), fn
    assert not (d / "resources.yaml").exists()


def test_roundtrip_and_deterministic(tmp_path):
    st = R.ResourceStore(tmp_path)
    items = [mk("zeta", tags=["a", "b"], related_tasks=["spatial"], note="备注"),
             mk("alpha", paper_ids=["arxiv:1"], dataset_names=["X"], github="org/alpha")]
    p = st.save("repos.yaml", items)
    first = p.read_text(encoding="utf-8")
    assert first.startswith("# 知识资源 · 精选清单：GitHub 代码仓库")
    assert "本文件可能被命令重写" in first
    assert "2026-11-15" in first and "'2026-11-15'" not in first  # 日期是 YAML 日期，不是字符串
    loaded = st.load_file("repos.yaml")
    assert [r.id for r in loaded] == ["zeta", "alpha"]  # 保持原顺序
    assert loaded == items
    st.save("repos.yaml", loaded)
    assert p.read_text(encoding="utf-8") == first
    assert "description_zh: 一个示例仓库" in first  # 中文不转义
    assert "discovered_from:" not in first  # 空列表不输出


def test_empty_file_and_missing_file(tmp_path):
    st = R.ResourceStore(tmp_path)
    assert st.load_curated() == [] and st.load_candidates() == []
    st.save("candidates.yaml", [])
    assert "resources: []" in st.path("candidates.yaml").read_text(encoding="utf-8")
    assert st.load_candidates() == []
    with pytest.raises(ValueError):
        st.save("nope.yaml", [])


def test_load_curated_spans_files_and_bad_entry_errors(tmp_path):
    st = R.ResourceStore(tmp_path)
    st.save("repos.yaml", [mk("r1")])
    st.save("data.yaml", [mk("d1", kind="data_portal", url="https://example.org/d")])
    assert [r.id for r in st.load_curated()] == ["r1", "d1"]
    st.path("tools.yaml").write_text("resources:\n- id: Bad\n  name: x\n", encoding="utf-8")
    with pytest.raises(R.ResourceFileError) as ei:
        st.load_curated()
    assert "tools.yaml" in str(ei.value)


# ---------- 校验 ----------

@pytest.fixture
def cfg(config_dir):
    return load_config(config_dir)


def test_validate_clean(tmp_path, cfg):
    st = R.ResourceStore(tmp_path)
    st.save("repos.yaml", [mk("r1", related_tasks=["spatial"], related_paradigms=["world_model_jepa"])])
    st.save("candidates.yaml", [mk("c1", url="https://github.com/org/c1", status="candidate", source="auto_discovered")])
    assert st.validate(cfg) == []


def test_validate_reports_problems(tmp_path, cfg):
    st = R.ResourceStore(tmp_path)
    st.save("repos.yaml", [
        mk("dup"), mk("dup", url="https://github.com/org/other"),            # id 重复
        mk("u1", url="https://github.com/org/Same"), mk("u2", url="https://www.github.com/org/same/"),  # url 规范化后重复
        mk("wrongkind", kind="tool", url="https://example.org/t"),            # kind 与文件不符
        mk("cand", url="https://github.com/org/cand", status="candidate"),    # 精选里的 candidate
        mk("badtax", url="https://github.com/org/badtax", related_tasks=["nope"], related_paradigms=["nope2"]),
        mk("nodesc", url="https://github.com/org/nodesc", description_zh="  "),
    ])
    st.save("candidates.yaml", [mk("acc", url="https://github.com/org/acc")])  # 候选文件里的 accepted
    text = "\n".join(st.validate(cfg))
    for needle in ["dup: id 重复", "u2: url 与", "wrongkind: kind=tool", "cand: 精选文件里不应出现 status=candidate",
                   "badtax: related_tasks 含未知 id 'nope'", "badtax: related_paradigms 含未知 id 'nope2'",
                   "nodesc: accepted 条目缺 description_zh", "acc: candidates.yaml 里不应出现 status=accepted"]:
        assert needle in text, needle


def test_validate_missing_url_and_unparseable_file(tmp_path, cfg):
    st = R.ResourceStore(tmp_path)
    st.path("repos.yaml").parent.mkdir(parents=True, exist_ok=True)
    st.path("repos.yaml").write_text("resources:\n- id: x\n  name: X\n  kind: repo\n  status: accepted\n  added: 2026-11-15\n",
                                     encoding="utf-8")
    st.path("learning.yaml").write_text("resources: [\n", encoding="utf-8")
    text = "\n".join(st.validate(cfg))
    assert "repos.yaml" in text and "learning.yaml" in text


def test_url_normalization():
    n = R.normalize_resource_url
    assert n("https://www.GitHub.com/Org/Repo.git/") == n("http://github.com/org/repo")
    assert n("https://example.org/a/") == "example.org/a"


# ---------- 指标 / refresh ----------

def test_refresh_writes_metrics(tmp_path):
    setup_repos(tmp_path, ["a", "b"])
    c, calls = mock_client()
    rep = R.refresh_github(rcfg(), data_dir=tmp_path, client=c, now=NOW, sleep=lambda s: None)
    assert (rep.ok, rep.not_found, rep.errors, rep.skipped_recent, rep.deferred, rep.requests) == (2, 0, 0, 0, 0, 2)
    data = json.loads((tmp_path / "resources" / "github_metrics.json").read_text(encoding="utf-8"))
    assert data["fetched_at"] == "2026-11-15T12:00:00Z"
    m = data["repos"]["org/a"]
    assert m["status"] == "ok" and m["license"] == "MIT" and m["language"] == "Python"
    assert m["fetched_at"] == "2026-11-15T12:00:00Z"
    assert m["history"] == [{"date": "2026-11-15", "stars": m["stars"]}]


def test_refresh_only_accepted_with_github(tmp_path):
    st = R.ResourceStore(tmp_path)
    st.save("repos.yaml", [mk("a"), mk("arch", status="archived"), mk("same", url="https://example.org/x")])
    st.save("learning.yaml", [mk("blog1", kind="blog", url="https://example.org/blog")])
    st.save("tools.yaml", [mk("t", kind="tool", url="https://example.org/t", github="org/t")])
    c, calls = mock_client()
    R.refresh_github(rcfg(), data_dir=tmp_path, client=c, now=NOW, sleep=lambda s: None)
    assert sorted(calls) == ["/repos/org/a", "/repos/org/t"]


def test_refresh_order_never_fetched_first_then_oldest(tmp_path):
    setup_repos(tmp_path, ["a", "b", "c", "d"])
    ms = R.MetricsStore(tmp_path)
    ms.save({"fetched_at": None, "repos": {
        "org/a": {"status": "ok", "fetched_at": "2026-09-01T00:00:00Z", "stars": 1, "history": []},
        "org/b": {"status": "ok", "fetched_at": "2026-08-01T00:00:00Z", "stars": 1, "history": []},
        "org/c": {"status": "error", "fetched_at": "2026-10-30T00:00:00Z"},
    }})  # d 从未抓取；b 最旧
    c, calls = mock_client()
    R.refresh_github(rcfg(), data_dir=tmp_path, client=c, now=NOW, sleep=lambda s: None)
    assert calls == ["/repos/org/d", "/repos/org/b", "/repos/org/a", "/repos/org/c"]


def test_refresh_skips_recent_unless_force(tmp_path):
    setup_repos(tmp_path, ["a", "b"])
    R.MetricsStore(tmp_path).save({"fetched_at": None, "repos": {
        "org/a": {"status": "ok", "fetched_at": "2026-11-01T00:00:00Z", "stars": 5, "history": []},   # 14 天前
        "org/b": {"status": "ok", "fetched_at": "2026-10-01T00:00:00Z", "stars": 5, "history": []},   # 45 天前
    }})
    c, calls = mock_client()
    rep = R.refresh_github(rcfg(), data_dir=tmp_path, client=c, now=NOW, sleep=lambda s: None)
    assert calls == ["/repos/org/b"] and rep.skipped_recent == 1 and rep.ok == 1
    c2, calls2 = mock_client()
    rep2 = R.refresh_github(rcfg(), data_dir=tmp_path, client=c2, now=NOW, sleep=lambda s: None, force=True)
    assert sorted(calls2) == ["/repos/org/a", "/repos/org/b"] and rep2.skipped_recent == 0


def test_refresh_respects_max_requests_and_limit(tmp_path):
    setup_repos(tmp_path, ["a", "b", "c", "d"])
    c, calls = mock_client()
    rep = R.refresh_github(rcfg(max_requests_per_run=3), data_dir=tmp_path, client=c, now=NOW, sleep=lambda s: None)
    assert len(calls) == 3 and rep.ok == 3 and rep.deferred == 1 and "max_requests_per_run" in rep.stop_reason
    c2, calls2 = mock_client()
    rep2 = R.refresh_github(rcfg(), data_dir=tmp_path, client=c2, now=NOW, sleep=lambda s: None, limit=1, force=True)
    assert len(calls2) == 1 and rep2.ok == 1 and "--limit" in rep2.stop_reason
    assert len(R.MetricsStore(tmp_path).load()["repos"]) == 4


def test_refresh_stops_on_rate_limit_and_keeps_results(tmp_path):
    setup_repos(tmp_path, ["a", "b", "c"])
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        if n["i"] == 2:
            return httpx.Response(403, json={"message": "rate limit"},
                                  headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1795000000"})
        return httpx.Response(200, json=api_json(req.url.path.removeprefix("/repos/")),
                              headers={"X-RateLimit-Remaining": "30"})

    c, calls = mock_client(handler)
    rep = R.refresh_github(rcfg(), data_dir=tmp_path, client=c, now=NOW, sleep=lambda s: None)
    assert len(calls) == 2 and rep.ok == 1 and rep.rate_limited and rep.deferred == 2
    assert rep.retry_at is not None and rep.retry_at.timestamp() == 1795000000
    assert list(R.MetricsStore(tmp_path).load()["repos"]) == ["org/a"]


def test_refresh_stops_when_remaining_hits_zero(tmp_path):
    setup_repos(tmp_path, ["a", "b", "c"])
    c, calls = mock_client(remaining=0)
    rep = R.refresh_github(rcfg(), data_dir=tmp_path, client=c, now=NOW, sleep=lambda s: None)
    assert len(calls) == 1 and rep.ok == 1 and rep.rate_limited and rep.deferred == 2


def test_refresh_not_found_error_and_rename(tmp_path):
    setup_repos(tmp_path, ["gone", "boom", "moved"])

    def handler(req):
        p = req.url.path
        if p.endswith("/gone"):
            return httpx.Response(404, json={"message": "Not Found"})
        if p.endswith("/boom"):
            return httpx.Response(500)
        return httpx.Response(200, json=api_json("org/moved-new"))

    c, _ = mock_client(handler)
    rep = R.refresh_github(rcfg(), data_dir=tmp_path, client=c, now=NOW, sleep=lambda s: None)
    assert (rep.ok, rep.not_found, rep.errors) == (1, 1, 1)
    assert rep.not_found_names == ["org/gone"] and rep.renamed == [("org/moved", "org/moved-new")]
    repos = R.MetricsStore(tmp_path).load()["repos"]
    assert repos["org/gone"]["status"] == "not_found"
    assert repos["org/boom"]["status"] == "error"
    assert repos["org/moved"]["full_name"] == "org/moved-new"  # 以原名为键，记录新名
    # 出错的下次仍会重试，未找到的在间隔内跳过
    c2, calls2 = mock_client(handler)
    rep2 = R.refresh_github(rcfg(), data_dir=tmp_path, client=c2, now=NOW, sleep=lambda s: None)
    assert calls2 == ["/repos/org/boom"] and rep2.skipped_recent == 2


def test_refresh_error_keeps_previous_metrics(tmp_path):
    setup_repos(tmp_path, ["a"])
    R.MetricsStore(tmp_path).save({"fetched_at": None, "repos": {
        "org/a": {"status": "ok", "fetched_at": "2026-01-01T00:00:00Z", "stars": 99, "history": []}}})
    c, _ = mock_client(lambda req: httpx.Response(500))
    R.refresh_github(rcfg(), data_dir=tmp_path, client=c, now=NOW, sleep=lambda s: None)
    m = R.MetricsStore(tmp_path).load()["repos"]["org/a"]
    assert m["stars"] == 99 and m["status"] == "error"


def test_refresh_sleeps_between_requests(tmp_path):
    setup_repos(tmp_path, ["a", "b", "c"])
    c, _ = mock_client()
    slept = []
    R.refresh_github(rcfg(request_delay_seconds=1.5), data_dir=tmp_path, client=c, now=NOW, sleep=slept.append)
    assert slept == [1.5, 1.5]  # 首个请求前不等待


def test_refresh_no_requests_does_not_write(tmp_path):
    R.ResourceStore(tmp_path).save("repos.yaml", [])
    c, calls = mock_client()
    rep = R.refresh_github(rcfg(), data_dir=tmp_path, client=c, now=NOW, sleep=lambda s: None)
    assert rep.requests == 0 and not (tmp_path / "resources" / "github_metrics.json").exists()


def test_history_monthly_snapshots_and_cap():
    h = R.add_history([], date(2026, 9, 3), 10)
    h = R.add_history(h, date(2026, 9, 28), 12)   # 同月覆盖
    h = R.add_history(h, date(2026, 10, 2), 15)
    assert h == [{"date": "2026-09-28", "stars": 12}, {"date": "2026-10-02", "stars": 15}]
    big = []
    for i in range(30):
        y, m = 2024 + i // 12, i % 12 + 1
        big = R.add_history(big, date(y, m, 1), i)
    assert len(big) == 24 and big[0]["date"] == "2024-07-01" and big[-1]["stars"] == 29


def test_refresh_history_across_runs(tmp_path):
    setup_repos(tmp_path, ["a"])
    c, _ = mock_client()
    R.refresh_github(rcfg(), data_dir=tmp_path, client=c, now=datetime(2026, 9, 1, tzinfo=timezone.utc), sleep=lambda s: None)
    c, _ = mock_client()
    R.refresh_github(rcfg(), data_dir=tmp_path, client=c, now=datetime(2026, 10, 15, tzinfo=timezone.utc), sleep=lambda s: None)
    hist = R.MetricsStore(tmp_path).load()["repos"]["org/a"]["history"]
    assert [h["date"] for h in hist] == ["2026-09-01", "2026-10-15"]


# ---------- is_maintained ----------

def test_is_maintained_boundaries():
    today = date(2026, 11, 15)
    f = lambda **k: R.is_maintained({"status": "ok", "archived": False, **k}, today, 365)  # noqa: E731
    assert f(pushed_at="2025-11-15T00:00:00Z") is True      # 恰好 365 天
    assert f(pushed_at="2025-11-14T23:59:59Z") is False     # 366 天
    assert f(pushed_at="2026-11-15T00:00:00Z") is True
    assert f(pushed_at="2026-10-01T00:00:00Z", archived=True) is False
    assert R.is_maintained(None, today, 365) is None
    assert R.is_maintained({"status": "not_found"}, today, 365) is None
    assert R.is_maintained({"status": "ok", "pushed_at": None}, today, 365) is None
    assert R.is_maintained({"status": "ok", "pushed_at": "2026-11-01T00:00:00Z"}, today, 7) is False


# ---------- CLI ----------

def cli_base(config_dir, tmp_path):
    return ["--config-dir", str(config_dir), "--data-dir", str(tmp_path / "data"),
            "--reports-dir", str(tmp_path / "reports")]


def test_cli_check_and_list(config_dir, tmp_path, capsys):
    data = tmp_path / "data"
    st = R.ResourceStore(data)
    st.save("repos.yaml", [mk("a"), mk("b")])
    st.save("learning.yaml", [mk("l1", kind="course", url="https://example.org/c")])
    assert cli.main(cli_base(config_dir, tmp_path) + ["resources", "check"]) == 0
    R.MetricsStore(data).save({"fetched_at": None, "repos": {"org/a": {
        "status": "ok", "stars": 321, "pushed_at": "2026-11-10T00:00:00Z", "archived": False}}})
    capsys.readouterr()
    assert cli.main(cli_base(config_dir, tmp_path) + ["resources", "list"]) == 0
    out = capsys.readouterr().out
    assert "[repo] 2 条" in out and "[course] 1 条" in out and "star=321" in out and "推送=2026-11-10" in out
    assert "合计：repo=2, course=1（共 3 条）" in out
    assert cli.main(cli_base(config_dir, tmp_path) + ["resources", "list", "--kind", "course"]) == 0
    out = capsys.readouterr().out
    assert "l1" in out and "org/a" not in out and "共 1 条" in out
    assert cli.main(cli_base(config_dir, tmp_path) + ["resources", "list", "--status", "candidate"]) == 0
    assert "共 0 条" in capsys.readouterr().out


def test_cli_check_fails_with_problems(config_dir, tmp_path, capsys):
    R.ResourceStore(tmp_path / "data").save("repos.yaml", [mk("a"), mk("a", url="https://github.com/org/z")])
    assert cli.main(cli_base(config_dir, tmp_path) + ["resources", "check"]) == 1
    assert "id 重复" in capsys.readouterr().out


def test_cli_refresh_with_mock_transport(config_dir, tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"
    setup_repos(data, ["a", "b"])
    c, calls = mock_client()
    monkeypatch.setattr(cli, "make_github_client", lambda cfg: c)
    monkeypatch.setattr(R.time, "sleep", lambda s: None)
    assert cli.main(cli_base(config_dir, tmp_path) + ["resources", "refresh"]) == 0
    out = capsys.readouterr().out
    assert "成功 2 / 未找到 0 / 出错 0" in out and len(calls) == 2
    assert cli.main(cli_base(config_dir, tmp_path) + ["resources", "refresh"]) == 0
    assert "近期已抓取跳过 2" in capsys.readouterr().out and len(calls) == 2
    assert cli.main(cli_base(config_dir, tmp_path) + ["resources", "refresh", "--force", "--limit", "1"]) == 0
    assert len(calls) == 3
    assert cli.main(cli_base(config_dir, tmp_path) + ["resources", "refresh", "--limit", "0"]) == 2


def test_cli_refresh_rate_limited_exit_zero_and_error_exit_one(config_dir, tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"
    setup_repos(data, ["a"])
    monkeypatch.setattr(R.time, "sleep", lambda s: None)
    c, _ = mock_client(lambda req: httpx.Response(429, headers={"Retry-After": "120"}))
    monkeypatch.setattr(cli, "make_github_client", lambda cfg: c)
    assert cli.main(cli_base(config_dir, tmp_path) + ["resources", "refresh"]) == 0
    assert "下次可重试时间" in capsys.readouterr().out
    c2, _ = mock_client(lambda req: httpx.Response(500))
    monkeypatch.setattr(cli, "make_github_client", lambda cfg: c2)
    assert cli.main(cli_base(config_dir, tmp_path) + ["resources", "refresh", "--force"]) == 1


def test_cli_help_available(capsys):
    for sub in ("list", "check", "refresh"):
        assert cli.main(["resources", sub, "--help"]) == 0
        capsys.readouterr()


def test_update_refresh_resources_flag(config_dir, tmp_path, monkeypatch, capsys):
    from aivc import pipeline
    from test_pipeline import FakeFetcher, mk as mkpaper
    monkeypatch.setattr(pipeline, "build_fetchers", lambda cfg: [FakeFetcher("arxiv", [mkpaper(1)])])
    called = []
    monkeypatch.setattr(cli, "_refresh_resources",
                        lambda cfg, data_dir, **k: called.append(1) or R.RefreshReport())
    base = cli_base(config_dir, tmp_path) + ["update", "--no-llm", "--no-news", "--no-site"]
    assert cli.main(base) == 0 and called == []          # 默认关
    assert cli.main(base + ["--refresh-resources"]) == 0 and called == [1]

    def boom(cfg, data_dir, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr(cli, "_refresh_resources", boom)
    assert cli.main(base + ["--refresh-resources"]) == 0  # 失败只警告
    assert "资源指标刷新失败" in capsys.readouterr().err
