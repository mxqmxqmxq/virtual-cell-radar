import importlib.util
import json
import shutil
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

from aivc.config import load_config
from aivc.llm import cache_key
from aivc.models import Paper

SCRIPT = Path(__file__).parent.parent / "scripts" / "migrate_public_data.py"
spec = importlib.util.spec_from_file_location("migrate_public_data", SCRIPT)
mig = importlib.util.module_from_spec(spec)
sys.modules["migrate_public_data"] = mig
spec.loader.exec_module(mig)

LLM = {"tasks": ["a"], "relevance": 8, "novelty_zh": "新颖性：测试 ✓", "model": "m", "prompt_version": "v3",
       "scored_at": "2026-11-01T00:00:00Z"}
LLM_RAW_EXTRA = {"unknown_field": {"k": [1, 2.5, None]}}


def _paper(pid, source, month, **kw) -> dict:
    p = Paper(id=pid, title=f"Title {pid}", abstract=f"abstract {pid}" if source != "seed_pdf" else None,
              source=source, first_seen=date.fromisoformat(month + "-05"), **kw)
    d = p.model_dump(mode="json")
    d["highlight"] = ["x"]
    if d["llm"]:
        d["llm"].update(LLM_RAW_EXTRA)
    return d


def _write(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in sorted(rows, key=lambda r: r["id"])),
                    encoding="utf-8")


def _snapshot(*dirs: Path) -> dict:
    out = {}
    for d in dirs:
        for f in sorted(d.rglob("*")):
            if f.is_file():
                out[str(f)] = f.read_bytes()
    return out


@pytest.fixture
def env(tmp_path, config_dir):
    cfg = load_config(config_dir)
    model, pv = cfg.llm.model, cfg.llm.prompt_version
    data, local, backup = tmp_path / "data", tmp_path / "local", tmp_path / "backup"

    seed = _paper("seed:1", "seed_pdf", "2026-11", tags=["seed", "private_dir"], local_file="dir/a.pdf")
    orp = _paper("or:1", "openreview", "2026-10", tags=["ICLR2027", "seed", "private_dir"],
                 local_file="dir/b.pdf", llm_status="scored", llm=LLM, final_score=9.0, must_read=True)
    orp2 = _paper("or:2", "openreview", "2026-10", tags=["ICLR2027"], llm_status="scored", llm=LLM, final_score=7.0)
    bio = _paper("bio:1", "biorxiv", "2026-11", llm_status="scored", llm=LLM, final_score=6.5)
    old = _paper("old:1", "arxiv", "2025-09")  # 未打分，单独月份
    seed2 = _paper("seed:2", "seed_pdf", "2025-09", tags=["seed"])  # 使 2025-09 之外还有一个仅含种子的月份
    only_seed_file = _paper("seed:3", "seed_pdf", "2025-03", tags=["seed"])
    rows = [seed, orp, orp2, bio, old, seed2, only_seed_file]
    _write(data / "papers/2026-11.jsonl", [seed, bio])
    _write(data / "papers/2026-10.jsonl", [orp, orp2])
    _write(data / "papers/2025-09.jsonl", [old, seed2])
    _write(data / "papers/2025-03.jsonl", [only_seed_file])
    (data / "papers/.gitkeep").write_text("")
    # 缓存：3 行当前版本（其中 2 行对应公开已打分论文，1 行对应种子）+ 1 行旧版本 + 1 行无主
    pubs = [orp, orp2, bio]
    cache = []
    for p in pubs:
        k = cache_key(Paper.model_validate(p), model, pv)
        cache.append({"key": k, "value": LLM, "model": model, "prompt_version": pv, "created": "t"})
    cache.append({"key": cache_key(Paper.model_validate(orp), model, "v0"), "value": LLM, "model": model,
                  "prompt_version": "v0", "created": "t"})
    cache.append({"key": "deadbeef", "value": LLM, "model": model, "prompt_version": pv, "created": "t"})
    (data / "cache").mkdir(parents=True)
    (data / "cache/llm.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cache),
                                          encoding="utf-8")
    # 备份：完整复制 + keywords.yaml
    shutil.copytree(data, backup / "data")
    (backup / "config").mkdir()
    (backup / "config/keywords.yaml").write_text(
        yaml.safe_dump({"my_projects": {"Alpha Proj": ["alpha", "beta gene"], "项目乙": ["gamma-x"]}},
                       allow_unicode=True), encoding="utf-8")
    return dict(data=data, local=local, backup=backup, config=config_dir, rows=rows, tmp=tmp_path)


def _args(e, *extra, backup=True):
    a = ["--data-dir", str(e["data"]), "--local-dir", str(e["local"]), "--config-dir", str(e["config"])]
    if backup:
        a += ["--backup-dir", str(e["backup"])]
    return a + list(extra)


def _lines(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_dry_run_writes_nothing(env, capsys):
    before = _snapshot(env["data"], env["tmp"] / "local")
    assert mig.main(_args(env)) == 0
    assert _snapshot(env["data"], env["tmp"] / "local") == before
    assert not env["local"].exists()
    out = capsys.readouterr().out
    assert "迁移前总数        : 7" in out
    assert "种子数  : 3" in out
    assert "脱敏条数(非种子)        : 1" in out
    assert "删除 highlight 键的条数 : 4" in out
    assert "迁移后公开总数          : 4" in out
    assert "缓存保留/丢弃行数       : 3/2" in out
    assert "2025-03.jsonl" in out


def test_apply_result(env):
    assert mig.main(_args(env, "--apply")) == 0
    pub = {r["id"]: r for f in (env["data"] / "papers").glob("*.jsonl") for r in _lines(f)}
    assert set(pub) == {"or:1", "or:2", "bio:1", "old:1"}
    assert all("highlight" not in r for r in pub.values())
    assert pub["or:1"]["local_file"] is None and pub["or:1"]["tags"] == ["ICLR2027"]
    assert not (env["data"] / "papers/2025-03.jsonl").exists()
    assert (env["data"] / "papers/.gitkeep").exists()
    # local：3 个种子 + or:1 的原始记录
    loc = {r["id"]: r for f in (env["local"] / "data/papers").glob("*.jsonl") for r in _lines(f)}
    assert set(loc) == {"seed:1", "seed:2", "seed:3", "or:1"}
    assert loc["or:1"]["local_file"] == "dir/b.pdf" and loc["or:1"]["tags"] == ["ICLR2027", "seed", "private_dir"]
    # 缓存只剩 3 行
    assert len(_lines(env["data"] / "cache/llm.jsonl")) == 3
    # topics.yaml
    t = yaml.safe_load((env["local"] / "topics.yaml").read_text(encoding="utf-8"))["topics"]
    assert [x["id"] for x in t] == ["alpha-proj", "topic"]
    assert t[0]["name"] == "Alpha Proj" and t[0]["default_enabled"] is True and t[1]["name"] == "项目乙"


def test_llm_dict_unchanged_and_untouched_lines_byte_equal(env):
    mig.main(_args(env, "--apply"))
    bak = {r["id"]: r for f in (env["backup"] / "data/papers").glob("*.jsonl") for r in _lines(f)}
    for f in (env["data"] / "papers").glob("*.jsonl"):
        for r in _lines(f):
            b = bak[r["id"]]
            for k in ("llm", "llm_status", "final_score", "must_read", "title", "abstract", "keyword_score",
                      "matched_keywords", "circle"):
                assert r[k] == b[k]
            assert json.dumps(r["llm"], ensure_ascii=False, sort_keys=True) == \
                json.dumps(b["llm"], ensure_ascii=False, sort_keys=True)
    # 缓存保留行逐字节来自原文件
    orig = set((env["backup"] / "data/cache/llm.jsonl").read_text(encoding="utf-8").splitlines())
    kept = (env["data"] / "cache/llm.jsonl").read_text(encoding="utf-8").splitlines()
    assert set(kept) <= orig


def test_idempotent(env, capsys):
    mig.main(_args(env, "--apply"))
    snap = _snapshot(env["data"], env["local"])
    mig.main(_args(env, "--apply"))
    assert _snapshot(env["data"], env["local"]) == snap
    capsys.readouterr()
    mig.main(_args(env))
    out = capsys.readouterr().out
    assert "无事可做" in out
    assert "迁移前总数        : 4" in out


def test_existing_local_record_not_duplicated(env):
    _write(env["local"] / "data/papers/2026-11.jsonl", [env["rows"][0]])  # seed:1 已在 local
    mig.main(_args(env, "--apply"))
    ids = [r["id"] for f in (env["local"] / "data/papers").glob("*.jsonl") for r in _lines(f)]
    assert sorted(ids) == sorted(set(ids)) and "seed:1" in ids and len(ids) == 4


def test_existing_topics_yaml_not_overwritten(env):
    env["local"].mkdir()
    (env["local"] / "topics.yaml").write_text("topics: []\n", encoding="utf-8")
    mig.main(_args(env, "--apply"))
    assert (env["local"] / "topics.yaml").read_text(encoding="utf-8") == "topics: []\n"


def test_verify_passes_after_apply(env, capsys):
    mig.main(_args(env, "--apply"))
    capsys.readouterr()
    assert mig.main(_args(env, "--verify")) == 0
    out = capsys.readouterr().out
    assert "FAIL" not in out and out.count("PASS") >= 9


def test_verify_fails_before_apply(env, capsys):
    assert mig.main(_args(env, "--verify")) == 1
    assert "FAIL  公开库无 seed_pdf" in capsys.readouterr().out


def test_verify_detects_llm_change(env, capsys):
    mig.main(_args(env, "--apply"))
    f = env["data"] / "papers/2026-10.jsonl"
    rows = _lines(f)
    rows[0]["llm"] = {**LLM, "relevance": 1}
    _write(f, rows)
    capsys.readouterr()
    assert mig.main(_args(env, "--verify")) == 1
    assert "FAIL  公开库关键字段与备份完全相等" in capsys.readouterr().out


def test_verify_detects_overlay_mismatch_and_cache(env, capsys):
    mig.main(_args(env, "--apply"))
    (env["local"] / "data/papers/2026-10.jsonl").unlink()  # 丢掉 or:1 的私有叠加
    (env["data"] / "cache/llm.jsonl").write_text("", encoding="utf-8")
    capsys.readouterr()
    assert mig.main(_args(env, "--verify")) == 1
    out = capsys.readouterr().out
    assert "FAIL  叠加后每条 local_file 与备份一致" in out
    assert "FAIL  缓存行数等于公开论文已打分条数" in out


def test_verify_requires_backup(env):
    with pytest.raises(SystemExit):
        mig.main(_args(env, "--verify", backup=False))


def test_apply_without_backup_skips_topics(env):
    assert mig.main(_args(env, "--apply", backup=False)) == 0
    assert not (env["local"] / "topics.yaml").exists()
