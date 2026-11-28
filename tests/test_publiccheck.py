import json
from pathlib import Path

from aivc import publiccheck as pc
from aivc.cli import main

ALLOWED = ["ICLR2027"]
U = "/Us" + "ers/"   # 拆开写，避免本测试文件自己被 check-public 的路径扫描命中
H = "/ho" + "me/"


def _w(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _rec(**kw):
    d = {"id": "p1", "title": "T", "source": "openreview", "tags": ["ICLR2027"]}
    d.update(kw)
    return json.dumps(d, ensure_ascii=False)


# (a)
def test_data_clean(tmp_path):
    _w(tmp_path / "papers/2026-10.jsonl", _rec() + "\n")
    assert pc.check_data(tmp_path, ALLOWED) == []


def test_data_violations(tmp_path):
    lines = [_rec(), _rec(source="seed_pdf"), _rec(local_file="x.pdf"),
             _rec(highlight=["a"]), _rec(tags=["ICLR2027", "seed"])]
    _w(tmp_path / "papers/2026-10.jsonl", "\n".join(lines) + "\n")
    issues = pc.check_data(tmp_path, ALLOWED)
    assert sorted(i.line for i in issues) == [2, 3, 4, 5]
    assert all("2026-10.jsonl" in i.path for i in issues)
    # 空 local_file 与空 tags 不报
    _w(tmp_path / "papers/2026-10.jsonl", _rec(local_file=None, tags=[]) + "\n")
    assert pc.check_data(tmp_path, ALLOWED) == []


# (b)
def test_reports_generic(tmp_path):
    _w(tmp_path / "weekly/a.md", "# ok\n正常内容\n")
    assert pc.check_reports_generic(tmp_path) == []
    _w(tmp_path / "weekly/b.md", "ok\nsource: seed_pdf\n见 " + U + "bob/x.pdf 与 " + H + "al/y\n")
    issues = pc.check_reports_generic(tmp_path)
    assert sorted(i.line for i in issues) == [2, 3]


# (c)
def test_site_data(tmp_path):
    assert pc.check_site_data(tmp_path) == []  # 目录不存在
    _w(tmp_path / "data/papers.json", json.dumps([{"id": "a", "source": "openreview", "local_file": None}]))
    assert pc.check_site_data(tmp_path) == []
    _w(tmp_path / "data/papers.json", json.dumps(
        [{"id": "a", "source": "seed_pdf"}, {"id": "b", "local_file": "f.pdf"}, {"id": "c", "highlight": []}]))
    assert len(pc.check_site_data(tmp_path)) == 3


# (d)
def _topics(local: Path):
    _w(local / "topics.yaml", "topics:\n  - id: sec\n    name: SecretTopic\n    terms: [foo]\n")


def test_topic_skipped_without_local(tmp_path):
    issues, note = pc.check_private_topics(tmp_path, tmp_path / "nolocal", tmp_path / "r", tmp_path / "s")
    assert issues == [] and note


def test_topic_in_code_and_config(tmp_path):
    root = tmp_path / "repo"
    _topics(tmp_path / "local")
    _w(root / "src/a.py", "x = 'SecretTopic'\n")
    _w(root / "config/t.yaml", "name: SecretTopicX\n")   # 非整词，不报
    _w(root / "README.md", "hello\nsecrettopic\n")       # 区分大小写，不报
    _w(root / "site-data/x", "SecretTopic")              # 不在扫描范围
    issues, note = pc.check_private_topics(root, tmp_path / "local", tmp_path / "r", tmp_path / "s")
    assert note is None
    assert [(Path(i.path).name, i.line) for i in issues] == [("a.py", 1)]


def test_topic_in_site_topics_json(tmp_path):
    _topics(tmp_path / "local")
    _w(tmp_path / "s/data/topics.json", '[{"name": "SecretTopic"}]')
    issues, _ = pc.check_private_topics(tmp_path / "repo", tmp_path / "local", tmp_path / "r", tmp_path / "s")
    assert len(issues) == 1


def test_topic_in_reports_contexts(tmp_path):
    _topics(tmp_path / "local")
    rep = tmp_path / "r"
    _w(rep / "weekly/w.md", "\n".join([
        "# 周报",
        "## Top 论文",
        "### SecretTopic for cells",            # 论文标题：不报
        "摘要提到 SecretTopic",                 # 正文：不报
        "- 相关课题：SecretTopic",              # 后缀：报（第 5 行）
        "## 主题追踪",
        "### SecretTopic",                       # 章节内：报（第 7 行）
        "## 未打分",
        "SecretTopic again",                    # 章节外：不报
    ]) + "\n")
    issues, _ = pc.check_private_topics(tmp_path / "repo", tmp_path / "local", rep, tmp_path / "s")
    assert sorted(i.line for i in issues) == [5, 7]


# (e)
def test_repo_paths(tmp_path):
    _w(tmp_path / "a.py", "ok = 1\n")
    _w(tmp_path / "b.md", "x\npath " + U + "someone/proj/f\n")
    _w(tmp_path / "local/c.md", "" + U + "someone/x/\n")
    _w(tmp_path / "site/d.html", "" + H + "zed/x/\n")
    _w(tmp_path / "uv.lock", "" + U + "a/b/\n")
    (tmp_path / "bin.dat").write_bytes(b"\0" + (U + "a/b/").encode())
    _w(tmp_path / "e.txt", "" + U + " 无用户名\n")
    issues = pc.check_repo_paths(tmp_path)
    assert [(i.path, i.line) for i in issues] == [("b.md", 2)]


def test_repo_paths_git_file_pointer(tmp_path):
    abs_ptr = f"gitdir: {U}alice/repo/.git/worktrees/y\n"
    # 根目录 .git 为合法 gitdir 指针 → 不报
    _w(tmp_path / ".git", abs_ptr)
    assert pc.check_repo_paths(tmp_path) == []
    # 子目录 sub/.git 文件含同样路径 → 报 1 条，路径为 sub/.git
    _w(tmp_path / "sub/.git", abs_ptr)
    issues = pc.check_repo_paths(tmp_path)
    assert [(i.path, i.line) for i in issues] == [("sub/.git", 1)]
    (tmp_path / "sub/.git").unlink()
    # 根目录 .git 不以 gitdir: 开头但含路径 → 报出
    _w(tmp_path / ".git", "see " + U + "alice/repo\n")
    issues = pc.check_repo_paths(tmp_path)
    assert [(i.path, i.line) for i in issues] == [(".git", 1)]


def test_repo_paths_uv_lock_skipped(tmp_path):
    _w(tmp_path / "uv.lock", "" + U + "alice/repo/\n")
    assert pc.check_repo_paths(tmp_path) == []


# CLI
def _cfg_dir(tmp_path, config_dir):
    import shutil
    root = tmp_path / "repo"
    shutil.copytree(config_dir, root / "config")
    return root


def test_cli_clean_and_dirty(tmp_path, config_dir, capsys):
    root = _cfg_dir(tmp_path, config_dir)
    data, reports, site = tmp_path / "data", tmp_path / "reports", tmp_path / "site"
    _w(data / "papers/2026-10.jsonl", _rec() + "\n")
    args = ["--config-dir", str(root / "config"), "--data-dir", str(data), "--reports-dir", str(reports),
            "--site-dir", str(site), "--local-dir", str(tmp_path / "nolocal"), "check-public"]
    assert main(args) == 0
    assert "通过" in capsys.readouterr().out
    _w(data / "papers/2026-11.jsonl", _rec(source="seed_pdf") + "\n")
    assert main(args) == 1
    assert "2026-11.jsonl:1" in capsys.readouterr().out


# (f)(g) 新闻
def _news(**kw):
    d = {"id": "news:1", "title": "T", "url": "https://a.example/x", "source": "S",
         "sources": [{"name": "S", "url": "https://a.example/x"}], "summary_zh": "中文摘要",
         "significance_zh": "意义", "first_seen": "2026-11-10"}
    d.update(kw)
    return json.dumps(d, ensure_ascii=False)


def test_news_data_clean_and_missing(tmp_path):
    assert pc.check_news_data(tmp_path) == []
    _w(tmp_path / "news/2026-11.jsonl", _news() + "\n\n")
    assert pc.check_news_data(tmp_path) == []


def test_news_data_violations(tmp_path):
    words = " ".join(["word"] * 25)
    lines = [
        _news(),                                              # 1 ok
        _news(content="full text"),                           # 2 白名单外
        _news(snippet="x"),                                   # 3
        _news(url="javascript:alert(1)"),                     # 4
        _news(sources=[{"name": "S", "url": "ftp://x/y"}]),   # 5
        _news(title="t" * 301),                               # 6
        _news(summary_zh="字" * 401),                         # 7
        _news(significance_zh="字" * 201),                    # 8
        _news(summary_zh="融资 " + words),                    # 9 长引用
        _news(summary_zh="融资 " + " ".join(["word"] * 24)),  # 10 24 词不报
        "not json",                                           # 11
    ]
    _w(tmp_path / "news/2026-11.jsonl", "\n".join(lines) + "\n")
    assert sorted(i.line for i in pc.check_news_data(tmp_path)) == [2, 3, 4, 5, 6, 7, 8, 9, 11]


def test_site_news(tmp_path):
    assert pc.check_site_news(tmp_path) == []
    ok = {"id": "n", "title": "T", "url": "https://a.example/x", "sources": [], "summary_zh": "好",
          "date": "2026-11-10", "scored": True}
    _w(tmp_path / "data/news.json", json.dumps({"items": [ok]}))
    assert pc.check_site_news(tmp_path) == []
    _w(tmp_path / "data/news.json", json.dumps({"items": [dict(ok, tier="core"), dict(ok, tier="related"), dict(ok, tier=None)]}))
    assert pc.check_site_news(tmp_path) == []
    bad = [dict(ok, body="x"), dict(ok, url="file:///x"), dict(ok, summary_zh=" ".join(["w"] * 30)),
           dict(ok, first_seen="2026-11-10", keyword_score=1.0)]  # keyword_score 不在站点字段集
    _w(tmp_path / "data/news.json", json.dumps({"items": [ok] + bad}))
    assert len(pc.check_site_news(tmp_path)) == 4
    _w(tmp_path / "data/news.json", "{bad")
    assert len(pc.check_site_news(tmp_path)) == 1


def test_url_path_segments_not_flagged(tmp_path):
    url = "https://www.businesswire.com/news/" + "ho" + "me/2026/en/x"
    url2 = "https://example.com" + U + "someone/x"
    _w(tmp_path / "data/news/a.jsonl", f'{{"url": "{url}"}}\n')
    _w(tmp_path / "doc.md", f"see [x]({url}) and {url2}\n")
    _w(tmp_path / "reports/daily.md", f"- [t]({url}) {url2}\n")
    assert pc.check_repo_paths(tmp_path) == []
    assert pc.check_reports_generic(tmp_path / "reports") == []
    # 真实本机路径仍报（同行出现 URL 也一样）
    _w(tmp_path / "bad.md", f"{url} 路径 {U}someone/x\n")
    _w(tmp_path / "reports/bad.md", f"{url} 路径 {H}zed/y\n")
    assert [(i.path, i.line) for i in pc.check_repo_paths(tmp_path)] == [("bad.md", 1), ("reports/bad.md", 1)]
    assert [i.line for i in pc.check_reports_generic(tmp_path / "reports")] == [1]


def test_non_http_schemes_and_comma_joined_still_flagged(tmp_path):
    _w(tmp_path / "a.md", f"file://{U}bob/x.txt\n")
    _w(tmp_path / "b.md", f"vscode://file{U}bob/a\n")
    _w(tmp_path / "c.md", f"https://x.com/a,{U}bob/b\n")
    got = sorted(i.path for i in pc.check_repo_paths(tmp_path))
    assert got == ["a.md", "b.md", "c.md"]


# resources
def _res(**kw):
    d = {"id": "r1", "name": "R1", "kind": "repo", "url": "https://github.com/o/r1", "description_zh": "一句话说明",
         "status": "accepted", "added": "2026-11-01", "source": "manual", "github": "o/r1"}
    d.update(kw)
    return d


def _ry(path: Path, items):
    import yaml
    _w(path, yaml.safe_dump({"resources": items}, allow_unicode=True))


def test_resources_data_clean_and_missing(tmp_path):
    assert pc.check_resources_data(tmp_path) == []
    _ry(tmp_path / "resources/repos.yaml", [_res()])
    _ry(tmp_path / "resources/candidates.yaml", [_res(id="c", url="", status="candidate", github=None)])
    _w(tmp_path / "resources/github_metrics.json", json.dumps({"fetched_at": None, "repos": {"o/r1": {
        "full_name": "o/r1", "stars": 1, "history": []}}}))
    assert pc.check_resources_data(tmp_path) == []


def test_resources_data_violations(tmp_path):
    long_en = " ".join(["word"] * 30)
    _ry(tmp_path / "resources/repos.yaml", [
        _res(id="a", extra="x"), _res(id="b", url="ftp://x"), _res(id="c", url=""),
        _res(id="d", description_zh="长" * 301), _res(id="e", description_zh=long_en),
        _res(id="f", github="not a repo")])
    msgs = " ".join(i.message for i in pc.check_resources_data(tmp_path))
    for k in ("白名单外的键", "url 不是 http(s)", "超过 300 字", "长段引用", "owner/repo"):
        assert k in msgs
    assert sum("url 不是" in i.message for i in pc.check_resources_data(tmp_path)) == 2
    _w(tmp_path / "resources/broken.yaml", "a: [")
    assert any("YAML" in i.message for i in pc.check_resources_data(tmp_path))


def test_resources_metrics_violations(tmp_path):
    _w(tmp_path / "resources/github_metrics.json", json.dumps({"repos": {"o/r": {"stars": 1, "token": "x"}}}))
    assert any("token" in i.message for i in pc.check_resources_data(tmp_path))
    _w(tmp_path / "resources/github_metrics.json", "{bad")
    assert any("无法解析" in i.message for i in pc.check_resources_data(tmp_path))


def test_site_resources(tmp_path):
    assert pc.check_site_resources(tmp_path) == []
    ok = {"id": "a", "name": "A", "url": "https://x.org", "kind": "repo"}
    _w(tmp_path / "data/resources.json", json.dumps({"items": [ok]}))
    assert pc.check_site_resources(tmp_path) == []
    bad = [{**ok, "note": "n"}, {**ok, "status": "candidate"}, {**ok, "url": "javascript:x"}]
    _w(tmp_path / "data/resources.json", json.dumps({"items": bad}))
    msgs = " ".join(i.message for i in pc.check_site_resources(tmp_path))
    assert "白名单外" in msgs and "status=candidate" in msgs and "http(s)" in msgs


def test_topic_in_data_resources(tmp_path):
    root = tmp_path / "repo"
    _topics(tmp_path / "local")
    _ry(root / "data/resources/repos.yaml", [_res(description_zh="涉及 SecretTopic 的说明")])
    issues, _ = pc.check_private_topics(root, tmp_path / "local", tmp_path / "r", tmp_path / "s")
    assert len(issues) == 1 and "repos.yaml" in issues[0].path


# ---- v2: state 与新字段 ----
def _state(**kw):
    d = {"version": 1, "sources": {"arxiv": {"last_success_date": "2026-11-20", "consecutive_failures": 0}},
         "runs": [{"run_at": "2026-11-20T00:00:00", "fetched": 3}]}
    d.update(kw)
    return json.dumps(d)


def test_state_clean_and_missing(tmp_path):
    assert pc.check_state_data(tmp_path) == []
    _w(tmp_path / "state/fetch_state.json", _state())
    assert pc.check_state_data(tmp_path) == []


def test_state_dirty(tmp_path):
    f = tmp_path / "state/fetch_state.json"
    _w(f, _state(extra=1))
    assert len(pc.check_state_data(tmp_path)) == 1
    _w(f, _state(sources={"arxiv": {"bogus": 1}}))
    assert len(pc.check_state_data(tmp_path)) == 1
    _w(f, _state(sources={"arxiv": {"last_error": "boom at " + U + "bob/x"}}))
    assert any("绝对路径" in i.message for i in pc.check_state_data(tmp_path))
    _w(f, _state(runs=[{"fetched": 1}] * 31))
    assert any("30" in i.message for i in pc.check_state_data(tmp_path))
    _w(f, "{not json")
    assert len(pc.check_state_data(tmp_path)) == 1


def test_papers_new_fields(tmp_path):
    long_en = " ".join(["word"] * 30)
    lines = [
        _rec(),
        _rec(sources=["arxiv", "openalex"], weights_url="https://huggingface.co/a/b",
             published_doi="10.1038/x", matched_orgs=["Acme", "Genentech/Roche"], llm={"why_it_matters_zh": "简短"}),
        _rec(sources=["bogus"]),
        _rec(llm={"why_it_matters_zh": long_en}),
        _rec(weights_url="ftp://x"),
        _rec(published_doi="notadoi"),
        _rec(matched_orgs=["/tmp/local"]),
    ]
    _w(tmp_path / "papers/2026-10.jsonl", "\n".join(lines) + "\n")
    issues = pc.check_data(tmp_path, ALLOWED)
    assert sorted(i.line for i in issues) == [3, 4, 5, 6, 7]


def test_site_papers_new_fields(tmp_path):
    long_en = " ".join(["word"] * 30)
    _w(tmp_path / "data/papers.json", json.dumps(
        [{"id": "a", "weights_url": "https://x.org/a", "why_it_matters_zh": "好"},
         {"id": "b", "weights_url": "file:///x", "limitations_zh": long_en}]))
    assert len(pc.check_site_data(tmp_path)) == 2
