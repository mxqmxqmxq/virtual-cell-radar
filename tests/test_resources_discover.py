from datetime import date, datetime, timezone

import pytest

from aivc import cli, resources as R
from aivc.config import ResourcesConfig, load_config
from aivc.models import LLMAnnotation, NewsItem, NewsSourceRef, Paper, Resource
from aivc.news import NewsStore
from aivc.storage import PaperStore

TODAY = date(2026, 11, 15)
CFG = ResourcesConfig()


def llm(**kw) -> LLMAnnotation:
    base = dict(relevance=5, model="m", prompt_version="v", scored_at=datetime(2026, 11, 1, tzinfo=timezone.utc))
    base.update(kw)
    return LLMAnnotation(**base)


def paper(pid="arxiv:1", **kw) -> Paper:
    return Paper(id=pid, title=f"title {pid}", source="arxiv", first_seen=TODAY, **kw)


def news(nid="n1", **kw) -> NewsItem:
    base = dict(id=nid, title="t", url="https://example.com/a", source="s", first_seen=TODAY)
    base.update(kw)
    return NewsItem(**base)


def res(id="x", **kw) -> Resource:
    base = dict(id=id, name=id, kind="repo", url=f"https://github.com/o/{id}", description_zh="d", added=TODAY)
    base.update(kw)
    return Resource(**base)


def disc(papers=(), items=(), curated=(), cands=(), cfg=CFG):
    return R.discover(list(papers), list(items), list(curated), list(cands), cfg, TODAY)


# ---------- github 链接提取 ----------

def test_extract_github_normalization():
    text = ("见 https://github.com/Foo/Bar.git, 以及 (github.com/baz/qux). "
            "另有 http://www.github.com/a-b/c_d/tree/main/x 与 https://github.com/Foo/bar。")
    assert R.extract_github_repos(text) == ["Foo/Bar", "baz/qux", "a-b/c_d"]


def test_extract_github_reserved_and_nonrepo():
    text = ("https://github.com/orgs/acme/people https://github.com/topics/ai https://github.com/features/copilot "
            "https://github.com/sponsors/x https://github.com/about https://github.com/settings/profile "
            "https://github.com/onlyowner https://notgithub.com/a/b https://raw.githubusercontent.com/a/b/c")
    assert R.extract_github_repos(text) == []
    assert R.extract_github_repos(None) == [] and R.extract_github_repos("") == []


def test_repo_candidate_fields_and_sources():
    p = paper("arxiv:1", code_url="https://github.com/Foo/Bar.git", abstract="Code at github.com/foo/bar.")
    n = news("n1", summary_zh="开源于 https://github.com/foo/bar，另见 https://github.com/Zed/Tool.",
             sources=[NewsSourceRef(name="s", url="https://github.com/zed/tool/releases")])
    out = disc([p], [n]).new
    assert [r.id for r in out] == ["gh-foo-bar", "gh-zed-tool"]
    r = out[0]
    assert (r.kind, r.name, r.url, r.github) == ("repo", "Bar", "https://github.com/Foo/Bar", "Foo/Bar")
    assert (r.status, r.source, r.added, r.description_zh) == ("candidate", "auto_discovered", TODAY, "")
    assert r.discovered_from == ["arxiv:1", "n1"]


def test_discovered_from_capped_and_deduped():
    papers = [paper(f"arxiv:{i:02d}", code_url="https://github.com/o/r") for i in range(15)]
    r = disc(papers).new[0]
    assert len(r.discovered_from) == 10 and len(set(r.discovered_from)) == 10


def test_id_collision_gets_suffix():
    out = disc([paper(code_url="https://github.com/a.b/c", abstract="https://github.com/a-b/c")]).new
    assert [r.id for r in out] == ["gh-a-b-c", "gh-a-b-c-2"]


# ---------- 数据集 ----------

def test_dataset_threshold_and_normalization():
    ps = [paper("arxiv:1", llm=llm(datasets_used=["Replogle K562", "Norman"])),
          paper("arxiv:2", llm=llm(datasets_used=["replogle-k562"])),
          paper("arxiv:3", llm=llm(datasets_used=["REPLOGLE K562", "Norman"]))]
    out = disc(ps).new
    assert len(out) == 1
    r = out[0]
    assert R.norm_name(r.name) == "replogle" + "k562"
    assert r.kind == "data_portal" and r.url == "" and r.id == "ds-" + R._slug(r.name)
    assert r.dataset_names == [r.name] and r.discovered_from == ["arxiv:1", "arxiv:2", "arxiv:3"]
    assert (r.status, r.source) == ("candidate", "auto_discovered")
    # 阈值可配置
    assert len(disc(ps, cfg=ResourcesConfig(discover_min_dataset_mentions=2)).new) == 2
    assert disc(ps, cfg=ResourcesConfig(discover_min_dataset_mentions=4)).new == []


def test_same_paper_mentioning_twice_counts_once():
    ps = [paper("arxiv:1", llm=llm(datasets_used=["Foo-1", "foo 1", "FOO1"]))]
    assert disc(ps).new == []


def test_new_dataset_names_need_release_flag():
    ps = [paper("arxiv:1", llm=llm(releases_new_dataset=True, new_dataset_names=["Tahoe-X"])),
          paper("arxiv:2", llm=llm(releases_new_dataset=False, new_dataset_names=["Ghost"]))]
    out = disc(ps).new
    assert [r.name for r in out] == ["Tahoe-X"] and out[0].discovered_from == ["arxiv:1"]


def test_short_or_empty_dataset_names_ignored():
    ps = [paper("arxiv:1", llm=llm(releases_new_dataset=True, new_dataset_names=["", "A", "--"]))]
    assert disc(ps).new == []


# ---------- 去重 ----------

def test_dedup_against_curated():
    curated = [res("scanpy", github="scverse/scanpy", url="https://github.com/scverse/scanpy"),
               res("cx", kind="data_portal", name="CELLxGENE", url="https://cellxgene.example.org/",
                   dataset_names=["CZ CELLxGENE Census"]),
               res("other", name="Foo Tool", url="https://example.org/foo")]
    ps = [paper("arxiv:1", code_url="https://github.com/SCVERSE/scanpy.git",
                abstract="also https://github.com/x/foo-tool and github.com/u/zzz",
                llm=llm(releases_new_dataset=True, new_dataset_names=["cellxgene", "cz-cellxgene-census", "Brand New"]))]
    out = disc(ps, curated=curated).new
    assert sorted(r.name for r in out) == ["Brand New", "zzz"]   # x/foo-tool 的仓库名与精选名 Foo Tool 归一化后相同


def test_dedup_against_candidates_including_archived_and_merge():
    c_open = res("gh-o-a", url="https://github.com/o/a", github="o/a", status="candidate", source="auto_discovered",
                 discovered_from=["old:1"])
    c_arch = res("gh-o-b", url="https://github.com/o/b", github="o/b", status="archived", source="auto_discovered")
    c_ds = res("ds-x1", kind="data_portal", url="", name="X1 Atlas", dataset_names=["X1 Atlas"],
               status="archived", source="auto_discovered")
    ps = [paper("arxiv:1", abstract="github.com/o/a github.com/o/b github.com/o/c",
                llm=llm(releases_new_dataset=True, new_dataset_names=["x1-atlas"]))]
    r = disc(ps, cands=[c_open, c_arch, c_ds])
    assert [x.id for x in r.new] == ["gh-o-c"]
    byid = {x.id: x for x in r.candidates}
    assert byid["gh-o-a"].discovered_from == ["old:1", "arxiv:1"]            # 合并来源
    assert byid["gh-o-b"].status == "archived" and byid["gh-o-b"].discovered_from == ["arxiv:1"]  # 状态不变
    assert byid["ds-x1"].status == "archived"
    assert set(r.merged) == {"gh-o-a", "gh-o-b", "ds-x1"}
    assert [c.id for c in r.candidates] == ["gh-o-a", "gh-o-b", "ds-x1", "gh-o-c"]
    assert c_open.discovered_from == ["old:1"]                                # 输入对象不被原地修改
    r2 = disc(ps, cands=r.candidates)                                         # 幂等
    assert r2.new == [] and r2.merged == []


def test_discover_candidates_returns_only_new():
    out = R.discover_candidates([paper(code_url="https://github.com/o/a")], [], [], [], CFG, TODAY)
    assert [r.id for r in out] == ["gh-o-a"]


# ---------- 存储与 CLI ----------

def base(config_dir, tmp_path):
    return ["--config-dir", str(config_dir), "--data-dir", str(tmp_path / "data"),
            "--reports-dir", str(tmp_path / "reports")]


def seed(tmp_path):
    data = tmp_path / "data"
    PaperStore(data).upsert([paper("arxiv:1", code_url="https://github.com/o/a",
                                   llm=llm(releases_new_dataset=True, new_dataset_names=["Brand New"]))])
    NewsStore(data).upsert([news("n1", summary_zh="见 https://github.com/o/b")])
    return data


def test_cli_discover_dry_run_does_not_write(config_dir, tmp_path, capsys):
    data = seed(tmp_path)
    assert cli.main(base(config_dir, tmp_path) + ["resources", "discover", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "新增候选 3 个" in out and "dry-run" in out
    assert not (data / "resources" / "candidates.yaml").exists()


def test_cli_discover_writes_candidates_only(config_dir, tmp_path, capsys):
    data = seed(tmp_path)
    st = R.ResourceStore(data)
    st.save("repos.yaml", [res("keep")])
    before = st.path("repos.yaml").read_text(encoding="utf-8")
    assert cli.main(base(config_dir, tmp_path) + ["resources", "discover"]) == 0
    cands = st.load_candidates()
    assert sorted(c.id for c in cands) == ["ds-brand-new", "gh-o-a", "gh-o-b"]
    assert all(c.status == "candidate" and c.source == "auto_discovered" for c in cands)
    assert st.path("repos.yaml").read_text(encoding="utf-8") == before
    assert [r.id for r in st.load_curated()] == ["keep"]
    assert st.validate(load_config(config_dir)) == []
    assert cli.main(base(config_dir, tmp_path) + ["resources", "discover"]) == 0
    assert "新增候选 0 个" in capsys.readouterr().out
    assert len(st.load_candidates()) == 3


def cand_store(tmp_path):
    st = R.ResourceStore(tmp_path / "data")
    st.save_candidates([
        res("gh-o-a", url="https://github.com/o/a", github="o/a", name="a", description_zh="",
            status="candidate", source="auto_discovered"),
        res("ds-brand-new", kind="data_portal", url="", name="Brand New", dataset_names=["Brand New"],
            description_zh="", status="candidate", source="auto_discovered"),
    ])
    st.save("repos.yaml", [res("existing")])
    return st


def test_accept_repo_moves_to_repos_file(config_dir, tmp_path):
    st = cand_store(tmp_path)
    assert cli.main(base(config_dir, tmp_path) + ["resources", "accept", "gh-o-a", "--description", "示例仓库"]) == 0
    assert [r.id for r in st.load_file("repos.yaml")] == ["existing", "gh-o-a"]
    acc = st.load_file("repos.yaml")[1]
    assert acc.status == "accepted" and acc.source == "auto_discovered" and acc.description_zh == "示例仓库"
    assert [c.id for c in st.load_candidates()] == ["ds-brand-new"]
    assert st.validate(load_config(config_dir)) == []


def test_accept_without_description_warns_and_check_flags(config_dir, tmp_path, capsys):
    st = cand_store(tmp_path)
    assert cli.main(base(config_dir, tmp_path) + ["resources", "accept", "gh-o-a"]) == 0
    assert "description_zh 为空" in capsys.readouterr().out
    assert any("缺 description_zh" in p for p in st.validate(load_config(config_dir)))


def test_accept_dataset_needs_url_and_kind_routing(config_dir, tmp_path, capsys):
    st = cand_store(tmp_path)
    b = base(config_dir, tmp_path)
    assert cli.main(b + ["resources", "accept", "ds-brand-new"]) == 2
    assert "url" in capsys.readouterr().err
    assert [c.id for c in st.load_candidates()] == ["gh-o-a", "ds-brand-new"]   # 失败不动文件
    assert cli.main(b + ["resources", "accept", "ds-brand-new", "--url", "https://data.example.org/bn",
                         "--name", "Brand New Atlas", "--kind", "benchmark", "--description", "某数据集"]) == 0
    d = st.load_file("data.yaml")
    assert [(r.id, r.kind, r.name, r.url, r.status) for r in d] == [
        ("ds-brand-new", "benchmark", "Brand New Atlas", "https://data.example.org/bn", "accepted")]
    assert d[0].dataset_names == ["Brand New"]
    assert [c.id for c in st.load_candidates()] == ["gh-o-a"]


def test_accept_url_override_rederives_github_and_rejects_duplicates(tmp_path):
    st = cand_store(tmp_path)
    acc, _ = R.accept_candidate(st, "gh-o-a", url="https://github.com/new/place", description="x")
    assert acc.github == "new/place"
    st = cand_store(tmp_path)
    st.save("repos.yaml", [res("existing", url="https://github.com/o/a")])
    with pytest.raises(R.ResourceError, match="重复"):
        R.accept_candidate(st, "gh-o-a", description="x")


def test_accept_and_archive_unknown_id_exit_2(config_dir, tmp_path, capsys):
    cand_store(tmp_path)
    b = base(config_dir, tmp_path)
    assert cli.main(b + ["resources", "accept", "nope"]) == 2
    assert cli.main(b + ["resources", "accept", "existing"]) == 2      # 精选里的不是候选
    assert cli.main(b + ["resources", "archive", "nope"]) == 2
    assert "nope" in capsys.readouterr().err


def test_archive_candidate_keeps_it_and_blocks_rediscovery(config_dir, tmp_path):
    data = seed(tmp_path)
    b = base(config_dir, tmp_path)
    assert cli.main(b + ["resources", "discover"]) == 0
    assert cli.main(b + ["resources", "archive", "gh-o-a"]) == 0
    st = R.ResourceStore(data)
    byid = {c.id: c for c in st.load_candidates()}
    assert byid["gh-o-a"].status == "archived" and byid["gh-o-b"].status == "candidate"
    assert st.validate(load_config(config_dir)) == []
    assert cli.main(b + ["resources", "discover"]) == 0
    assert len(st.load_candidates()) == 3
    assert {c.status for c in st.load_candidates()} == {"candidate", "archived"}
    assert R.archive_resource(st, "gh-o-a")[1].startswith("已是 archived")


def test_archive_curated_in_place(config_dir, tmp_path):
    st = R.ResourceStore(tmp_path / "data")
    st.save("repos.yaml", [res("a"), res("b")])
    st.save("learning.yaml", [res("c", kind="course", url="https://example.org/c")])
    fn, msg = R.archive_resource(st, "c")
    assert fn == "learning.yaml" and "archived" in msg
    assert [(r.id, r.status) for r in st.load_file("learning.yaml")] == [("c", "archived")]
    assert [r.status for r in st.load_file("repos.yaml")] == ["accepted", "accepted"]
    assert st.validate(load_config(config_dir)) == []


def test_resources_discover_help():
    for sub in ("discover", "accept", "archive"):
        assert cli.main(["resources", sub, "--help"]) == 0
