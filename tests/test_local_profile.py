"""--local 私有档：只读叠加 + 输出隔离；不带 --local 时不读 local/。"""
import json
from datetime import date

import pytest

from aivc import cli, pipeline
from aivc.models import Paper
from aivc.storage import PaperStore
from test_pipeline import FakeFetcher, make_seed_dir, mk

LOCAL_TOPIC = "秘密私有课题XYZ"


def tree(d):
    return sorted((str(p.relative_to(d)), p.read_bytes()) for p in d.rglob("*") if p.is_file()) if d.exists() else []


def P(i, title, **kw):
    return Paper(id=i, title=title, source="openreview", first_seen=date(2026, 11, 10),
                 published=date(2026, 11, 10), abstract="virtual cell perturbation prediction", **kw)


@pytest.fixture
def env(config_dir, tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    pub = tmp_path / "data"
    loc = tmp_path / "local"
    PaperStore(pub).upsert([P("or:1", "Public virtual cell one", tags=["ICLR2027"]),
                            P("or:2", "Public virtual cell two")])
    PaperStore(loc / "data").upsert([P("or:1", "LOCAL TITLE SHOULD NOT WIN", tags=["private-tag"],
                                       local_file="secret/one.pdf"),
                                     P("or:9", "Private only paper")])
    (loc / "topics.yaml").write_text(
        f"topics:\n  - id: priv\n    name: {LOCAL_TOPIC}\n    default_enabled: true\n    terms: [virtual cell]\n",
        encoding="utf-8")
    base = ["--config-dir", str(config_dir), "--data-dir", str(pub),
            "--reports-dir", str(tmp_path / "reports"), "--site-dir", str(tmp_path / "site")]
    return base, tmp_path


def test_help_shows_flags(capsys):
    cli.main(["--help"])
    out = capsys.readouterr().out
    assert "--local" in out and "--local-dir" in out


def test_without_local_ignores_local_dir(env):
    base, tmp = env
    before = tree(tmp / "local")
    assert cli.main(base + ["build-site"]) == 0
    assert cli.main(base + ["weekly", "--week", "2026-W46", "--no-site"]) == 0
    assert tree(tmp / "local") == before
    papers = json.loads((tmp / "site/data/papers.json").read_text(encoding="utf-8"))
    ids = {p["id"] for p in papers["papers"]}
    assert ids == {"or:1", "or:2"}
    assert all("local_file" not in p for p in papers["papers"])
    blob = "".join(f.read_text(encoding="utf-8") for f in (tmp / "site/data").glob("*.json"))
    blob += (tmp / "reports/weekly/2026-W46.md").read_text(encoding="utf-8")
    for bad in (LOCAL_TOPIC, "secret/one.pdf", "private-tag", "Private only"):
        assert bad not in blob
    assert not (tmp / "local/site").exists() and not (tmp / "local/reports").exists()


def test_local_build_site_and_weekly(env, capsys):
    base, tmp = env
    pub_before = tree(tmp / "data")
    assert cli.main(["--local"] + base + ["build-site"]) == 0
    assert "local" in capsys.readouterr().out
    assert cli.main(["--local"] + base + ["weekly", "--week", "2026-W46"]) == 0
    papers = json.loads((tmp / "local/site/data/papers.json").read_text(encoding="utf-8"))["papers"]
    by = {p["id"]: p for p in papers}
    assert set(by) == {"or:1", "or:2", "or:9"}
    assert by["or:1"]["title"] == "Public virtual cell one"
    assert by["or:1"]["local_file"] == "secret/one.pdf"
    assert by["or:1"]["tags"] == ["ICLR2027", "private-tag"]
    topics = json.loads((tmp / "local/site/data/topics.json").read_text(encoding="utf-8"))
    assert LOCAL_TOPIC in json.dumps(topics, ensure_ascii=False)
    assert LOCAL_TOPIC in (tmp / "local/reports/weekly/2026-W46.md").read_text(encoding="utf-8")
    # 公开输出目录与公开库都没有被写
    assert not (tmp / "site").exists() and not (tmp / "reports").exists()
    assert tree(tmp / "data") == pub_before


def test_local_via_env_and_local_dir(env, monkeypatch, tmp_path):
    base, tmp = env
    other = tmp / "elsewhere"
    monkeypatch.setenv("AIVC_LOCAL", "1")
    assert cli.main(base + ["--local-dir", str(other), "build-site"]) == 0
    assert (other / "site/data/papers.json").exists()


def test_local_without_topics_file(env):
    base, tmp = env
    (tmp / "local/topics.yaml").unlink()
    assert cli.main(["--local"] + base + ["build-site"]) == 0
    assert (tmp / "local/site/data/papers.json").exists()


def test_seed_writes_only_local(env, monkeypatch):
    base, tmp = env
    src = tmp / "seed"
    make_seed_dir(src)
    pub_before = tree(tmp / "data")
    for flag in ([], ["--local"]):
        assert cli.main(flag + base + ["seed", "--source-dir", str(src), "--no-llm"]) == 0
        assert tree(tmp / "data") == pub_before
    assert not (tmp / "data/cache").exists()
    ids = {p.id for p in PaperStore(tmp / "local/data").load_all()}
    assert len(ids) >= 4  # 原有 2 条 + 种子新增


def test_update_local(env, monkeypatch):
    base, tmp = env
    monkeypatch.setattr(pipeline, "build_fetchers", lambda cfg: [FakeFetcher("arxiv", [mk(1)])])
    local_before = tree(tmp / "local/data")
    assert cli.main(["--local"] + base + ["update", "--days", "2", "--no-llm"]) == 0
    assert list((tmp / "data/papers").glob("*.jsonl"))
    assert tree(tmp / "local/data") == local_before  # 数据只进公开库
    assert list((tmp / "local/reports/daily").glob("*.md"))
    assert not (tmp / "reports").exists() and not (tmp / "site").exists()
    papers = json.loads((tmp / "local/site/data/papers.json").read_text(encoding="utf-8"))["papers"]
    assert "or:9" in {p["id"] for p in papers}


def test_score_reindex_local_same_as_public(env, tmp_path):
    base, tmp = env
    before = tree(tmp / "local")
    assert cli.main(["--local"] + base + ["reindex"]) == 0
    assert cli.main(["--local"] + base + ["score"]) == 0
    assert tree(tmp / "local") == before
