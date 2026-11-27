import subprocess
import sys

import pytest

from aivc import cli, pipeline
from aivc.fetchers.base import FetchError
from test_pipeline import FakeFetcher, make_seed_dir, mk  # noqa: F401


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AIVC_SEED_DIR", raising=False)


def base(config_dir, tmp_path):
    return ["--config-dir", str(config_dir), "--data-dir", str(tmp_path / "data"),
            "--reports-dir", str(tmp_path / "reports")]


def test_update_no_llm(config_dir, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pipeline, "build_fetchers", lambda cfg: [FakeFetcher("arxiv", [mk(1)])])
    rc = cli.main(base(config_dir, tmp_path) + ["update", "--days", "2", "--no-llm"])
    assert rc == 0
    assert list((tmp_path / "data" / "papers").glob("*.jsonl"))
    assert list((tmp_path / "reports" / "daily").glob("*.md"))
    assert "新增 1" in capsys.readouterr().out


def test_update_no_key_returns_0(config_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "build_fetchers", lambda cfg: [FakeFetcher("arxiv", [mk(1)])])
    assert cli.main(base(config_dir, tmp_path) + ["update"]) == 0


def test_all_sources_fail(config_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "build_fetchers", lambda cfg: [
        FakeFetcher("arxiv", exc=FetchError("x")), FakeFetcher("biorxiv", exc=FetchError("y"))])
    assert cli.main(base(config_dir, tmp_path) + ["update", "--no-llm"]) == 1


def test_one_source_fails_returns_0(config_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "build_fetchers", lambda cfg: [
        FakeFetcher("arxiv", exc=FetchError("x")), FakeFetcher("biorxiv", [mk(1)])])
    assert cli.main(base(config_dir, tmp_path) + ["update", "--no-llm"]) == 0


def test_seed_missing_source_dir(config_dir, tmp_path):
    assert cli.main(base(config_dir, tmp_path) + ["seed", "--no-llm"]) == 2


def test_seed_nonexistent_dir(config_dir, tmp_path):
    assert cli.main(base(config_dir, tmp_path) + ["seed", "--source-dir", str(tmp_path / "nope")]) == 2


def test_seed_ok_via_env(config_dir, tmp_path, monkeypatch):
    src = tmp_path / "seed"
    make_seed_dir(src)
    monkeypatch.setenv("AIVC_SEED_DIR", str(src))
    assert cli.main(base(config_dir, tmp_path) + ["seed", "--no-llm"]) == 0
    # 种子一律写到 <local-dir>/data，公开库不动
    assert list((tmp_path / "local" / "data" / "papers").glob("*.jsonl"))
    assert not (tmp_path / "data" / "papers").exists()


def test_backfill_requires_days(config_dir, tmp_path):
    assert cli.main(base(config_dir, tmp_path) + ["backfill"]) == 2


def test_backfill_ok(config_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "build_fetchers", lambda cfg: [FakeFetcher("arxiv", [mk(1)])])
    assert cli.main(base(config_dir, tmp_path) + ["backfill", "--days", "5", "--no-llm"]) == 0
    assert list((tmp_path / "reports" / "backfill").glob("*_5d.md"))


def test_score_no_key(config_dir, tmp_path):
    assert cli.main(base(config_dir, tmp_path) + ["score"]) == 0


def test_bad_config_dir(tmp_path):
    assert cli.main(["--config-dir", str(tmp_path / "nope"), "update", "--no-llm"]) == 2


def test_help_subprocess():
    r = subprocess.run([sys.executable, "-m", "aivc", "--help"], capture_output=True, text=True)
    assert r.returncode == 0
    for w in ("update", "backfill", "seed", "score"):
        assert w in r.stdout


# ---------- Phase 2: weekly / build-site / serve ----------
import json  # noqa: E402
import shutil  # noqa: E402
from pathlib import Path  # noqa: E402

from aivc import site as site_mod  # noqa: E402


def site_args(tmp_path):
    return ["--site-dir", str(tmp_path / "site")]


def test_weekly_week_generates_report_empty(config_dir, tmp_path, capsys):
    rc = cli.main(base(config_dir, tmp_path) + ["weekly", "--week", "2026-W44", "--no-site"])
    assert rc == 0
    f = tmp_path / "reports" / "weekly" / "2026-W44.md"
    assert f.exists()
    assert f.read_text(encoding="utf-8").startswith("# AIVC 周报 · 2026-W44")
    out = capsys.readouterr().out
    assert "2026-W44" in out and "新增 0" in out


def test_weekly_default_week(config_dir, tmp_path, monkeypatch):
    from datetime import date as _d
    from aivc import weekly as wk
    monkeypatch.setattr(wk, "last_complete_week", lambda today: wk.parse_week("2026-W45"))
    assert cli.main(base(config_dir, tmp_path) + ["weekly", "--no-site"]) == 0
    assert (tmp_path / "reports" / "weekly" / "2026-W45.md").exists()


def test_weekly_end_option(config_dir, tmp_path):
    assert cli.main(base(config_dir, tmp_path) + ["weekly", "--end", "2026-11-12", "--no-site"]) == 0
    assert (tmp_path / "reports" / "weekly" / "2026-W46.md").exists()


def test_weekly_week_and_end_conflict(config_dir, tmp_path):
    assert cli.main(base(config_dir, tmp_path) + ["weekly", "--week", "2026-W44", "--end", "2026-11-01"]) == 2


@pytest.mark.parametrize("bad", ["2026-44", "abc", "2026-W99"])
def test_weekly_bad_week(config_dir, tmp_path, bad):
    assert cli.main(base(config_dir, tmp_path) + ["weekly", "--week", bad]) == 2


def test_weekly_bad_end(config_dir, tmp_path, capsys):
    assert cli.main(base(config_dir, tmp_path) + ["weekly", "--end", "nope"]) == 2
    assert "错误" in capsys.readouterr().err


def test_weekly_auto_site_and_no_site(config_dir, tmp_path):
    assert cli.main(base(config_dir, tmp_path) + ["weekly", "--week", "2026-W44", "--no-site"]) == 0
    assert not (tmp_path / "site" / "data" / "papers.json").exists()
    assert cli.main(base(config_dir, tmp_path) + ["weekly", "--week", "2026-W44"]) == 0
    assert (tmp_path / "site" / "data" / "papers.json").exists()


def test_weekly_unscored_hint(config_dir, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pipeline, "build_fetchers", lambda cfg: [FakeFetcher("arxiv", [mk(1)])])
    assert cli.main(base(config_dir, tmp_path) + ["update", "--no-llm", "--no-site"]) == 0
    capsys.readouterr()
    from datetime import date as _d
    from aivc.weekly import week_id
    wid = week_id(pipeline.TODAY if hasattr(pipeline, "TODAY") else _d.today())
    assert cli.main(base(config_dir, tmp_path) + ["weekly", "--end", _d.today().isoformat(), "--no-site"]) == 0
    assert "score" in capsys.readouterr().out


def test_build_site(config_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "build_fetchers", lambda cfg: [FakeFetcher("arxiv", [mk(1), mk(2)])])
    assert cli.main(base(config_dir, tmp_path) + ["update", "--no-llm", "--no-site"]) == 0
    assert cli.main(base(config_dir, tmp_path) + site_args(tmp_path) + ["build-site"]) == 0
    s = tmp_path / "site"
    assert (s / "index.html").exists() and (s / "assets").is_dir()
    for n in ("papers.json", "meta.json", "datasets.json", "weekly.json"):
        assert (s / "data" / n).exists()
    d = json.loads((s / "data" / "papers.json").read_text(encoding="utf-8"))
    assert d["count"] == 2 == len(d["papers"])


def _config_copy(config_dir, tmp_path, auto_build: bool):
    import re
    dst = tmp_path / "cfg"
    shutil.copytree(config_dir, dst)
    y = (dst / "pipeline.yaml").read_text(encoding="utf-8")
    y2 = re.sub(r"auto_build:\s*\w+", f"auto_build: {'true' if auto_build else 'false'}", y)
    assert y2 != y or not auto_build
    (dst / "pipeline.yaml").write_text(y2, encoding="utf-8")
    return dst


def test_update_auto_builds_site(config_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "build_fetchers", lambda cfg: [FakeFetcher("arxiv", [mk(1)])])
    assert cli.main(base(config_dir, tmp_path) + ["update", "--no-llm"]) == 0
    assert (tmp_path / "site" / "data" / "papers.json").exists()


def test_update_no_site_flag(config_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "build_fetchers", lambda cfg: [FakeFetcher("arxiv", [mk(1)])])
    assert cli.main(base(config_dir, tmp_path) + ["update", "--no-llm", "--no-site"]) == 0
    assert not (tmp_path / "site").exists()


def test_update_auto_build_disabled_in_config(config_dir, tmp_path, monkeypatch):
    cfgd = _config_copy(config_dir, tmp_path, False)
    monkeypatch.setattr(pipeline, "build_fetchers", lambda cfg: [FakeFetcher("arxiv", [mk(1)])])
    assert cli.main(base(cfgd, tmp_path) + ["update", "--no-llm"]) == 0
    assert not (tmp_path / "site").exists()


def test_update_survives_build_site_failure(config_dir, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pipeline, "build_fetchers", lambda cfg: [FakeFetcher("arxiv", [mk(1)])])

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(site_mod, "build_site", boom)
    assert cli.main(base(config_dir, tmp_path) + ["update", "--no-llm"]) == 0
    cap = capsys.readouterr()
    assert "新增 1" in cap.out
    assert "警告" in cap.err


def test_serve_default_port_builds_site_first(config_dir, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(site_mod, "serve", lambda d, port: calls.append((Path(d), port)))
    assert cli.main(base(config_dir, tmp_path) + ["serve"]) == 0
    assert calls == [(tmp_path / "site", 8000)]
    assert (tmp_path / "site" / "index.html").exists()


def test_serve_custom_port_existing_site(config_dir, tmp_path, monkeypatch):
    s = tmp_path / "site"
    s.mkdir()
    (s / "index.html").write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(site_mod, "build_site", lambda *a, **k: pytest.fail("不应重建"))
    calls = []
    monkeypatch.setattr(site_mod, "serve", lambda d, port: calls.append(port))
    assert cli.main(base(config_dir, tmp_path) + ["serve", "--port", "9001"]) == 0
    assert calls == [9001]


def test_serve_port_in_use(config_dir, tmp_path, monkeypatch, capsys):
    def busy(d, port):
        raise site_mod.ServerStartError("端口被占用")

    monkeypatch.setattr(site_mod, "serve", busy)
    assert cli.main(base(config_dir, tmp_path) + ["serve"]) == 2
    assert "错误" in capsys.readouterr().err


def test_serve_ctrl_c(config_dir, tmp_path, monkeypatch):
    def kb(d, port):
        raise KeyboardInterrupt

    monkeypatch.setattr(site_mod, "serve", kb)
    assert cli.main(base(config_dir, tmp_path) + ["serve"]) == 0


def test_help_lists_phase2_commands():
    r = subprocess.run([sys.executable, "-m", "aivc", "--help"], capture_output=True, text=True)
    assert r.returncode == 0
    for w in ("weekly", "build-site", "serve"):
        assert w in r.stdout


# ---------------------------------------------------------------- news
from aivc import news_pipeline  # noqa: E402
from aivc.models import NewsItem, make_news_id  # noqa: E402
from aivc.news import NewsStore  # noqa: E402
from aivc.news_pipeline import NewsRunSummary  # noqa: E402


@pytest.mark.parametrize("extra", [["--limit", "0"], ["--limit", "-1"], ["--days", "0"]])
def test_news_arg_validation(config_dir, tmp_path, extra):
    assert cli.main(base(config_dir, tmp_path) + ["news", "--no-site"] + extra) == 2


def test_news_runs_and_prints(config_dir, tmp_path, monkeypatch, capsys):
    seen = {}

    def fake(cfg, **kw):
        seen.update(kw)
        return NewsRunSummary(window=None, n_fetched=3, n_candidates=2, n_new=2, requests=5,
                              per_feed={"A": 3}, attempted=["A"])

    monkeypatch.setattr(news_pipeline, "run_news", fake)
    rc = cli.main(base(config_dir, tmp_path) + ["news", "--days", "3", "--no-llm", "--limit", "5", "--no-site"])
    assert rc == 0
    assert seen["days"] == 3 and seen["use_llm"] is False and seen["limit"] == 5
    out = capsys.readouterr().out
    assert "HTTP 请求数：5" in out and "新增 2" in out


def test_news_all_feeds_failed_rc1(config_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(news_pipeline, "run_news", lambda cfg, **kw: NewsRunSummary(
        attempted=["A"], fetch_errors={"A": "x"}))
    assert cli.main(base(config_dir, tmp_path) + ["news", "--no-site"]) == 1


def test_news_auto_builds_site(config_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(news_pipeline, "run_news", lambda cfg, **kw: NewsRunSummary())
    assert cli.main(base(config_dir, tmp_path) + ["news"]) == 0
    assert (tmp_path / "site" / "data" / "papers.json").exists()


def test_update_runs_news_by_default_and_no_news(config_dir, tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(pipeline, "build_fetchers", lambda cfg: [FakeFetcher("arxiv", [mk(1)])])
    monkeypatch.setattr(news_pipeline, "run_news",
                        lambda cfg, **kw: calls.append(kw) or NewsRunSummary(n_new=4))
    assert cli.main(base(config_dir, tmp_path) + ["update", "--no-llm", "--no-site"]) == 0
    assert len(calls) == 1 and calls[0]["use_llm"] is False
    assert "新闻：新增 4" in capsys.readouterr().out
    assert cli.main(base(config_dir, tmp_path) + ["update", "--no-llm", "--no-site", "--no-news"]) == 0
    assert len(calls) == 1


def test_update_news_exception_does_not_break(config_dir, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pipeline, "build_fetchers", lambda cfg: [FakeFetcher("arxiv", [mk(1)])])

    def boom(cfg, **kw):
        raise RuntimeError("news boom")

    monkeypatch.setattr(news_pipeline, "run_news", boom)
    assert cli.main(base(config_dir, tmp_path) + ["update", "--no-llm", "--no-site"]) == 0
    cap = capsys.readouterr()
    assert "警告" in cap.err and "新增 1" in cap.out


def test_update_pins_todays_major_news(config_dir, tmp_path, monkeypatch):
    from datetime import date
    today = date.today()
    it = NewsItem(id=make_news_id("https://x.example/m"), title="虚构重大融资", url="https://x.example/m",
                  source="Fake", published=today, first_seen=today, llm_status="scored", event_type="funding",
                  amount_usd=3e8, major=True, relevance=9, significance_zh="虚构意义")
    NewsStore(tmp_path / "data").upsert([it])
    monkeypatch.setattr(pipeline, "build_fetchers", lambda cfg: [FakeFetcher("arxiv", [mk(1)])])
    monkeypatch.setattr(news_pipeline, "run_news", lambda cfg, **kw: NewsRunSummary())
    assert cli.main(base(config_dir, tmp_path) + ["update", "--no-llm", "--no-site"]) == 0
    daily = next((tmp_path / "reports" / "daily").glob("*.md")).read_text(encoding="utf-8")
    assert "## 重大行业事件" in daily and "虚构重大融资" in daily


def test_news_in_help():
    r = subprocess.run([sys.executable, "-m", "aivc", "news", "--help"], capture_output=True, text=True)
    assert r.returncode == 0
    for w in ("--days", "--no-llm", "--limit", "--no-site"):
        assert w in r.stdout
    r = subprocess.run([sys.executable, "-m", "aivc", "update", "--help"], capture_output=True, text=True)
    assert "--no-news" in r.stdout


def test_news_rescore_stale_flag_and_summary_line(config_dir, tmp_path, monkeypatch, capsys):
    seen = {}

    def fake(cfg, **kw):
        seen.update(kw)
        return NewsRunSummary(n_rescored=3, n_rescore_failed=1, n_rescore_skipped_budget=2)

    monkeypatch.setattr(news_pipeline, "run_news", fake)
    assert cli.main(base(config_dir, tmp_path) + ["news", "--rescore-stale", "--no-site"]) == 0
    assert seen["rescore_stale"] is True
    out = capsys.readouterr().out
    assert "重打" in out and "成功 3" in out and "预算跳过 2" in out
    seen.clear()
    assert cli.main(base(config_dir, tmp_path) + ["news", "--no-site"]) == 0
    assert seen["rescore_stale"] is False


# ---------------------------------------------------------------- news-rescore
def test_news_rescore_help_and_parse():
    p = cli._build_parser()
    a = p.parse_args(["news-rescore", "--days", "30", "--no-fetch", "--ids", "a,b", "--include-unscored",
                      "--max-requests", "7", "--dry-run", "--no-site"])
    assert (a.days, a.no_fetch, a.ids, a.include_unscored, a.max_requests, a.dry_run, a.no_site) == \
        (30, True, "a,b", True, 7, True, True)
    r = subprocess.run([sys.executable, "-m", "aivc", "news-rescore", "--help"], capture_output=True, text=True)
    assert r.returncode == 0 and "--max-requests" in r.stdout


def test_news_rescore_dry_run_smoke(config_dir, tmp_path, capsys):
    from datetime import date
    it = NewsItem(id=make_news_id("https://ex.com/a"), title="Acme Bio fictional", url="https://ex.com/a",
                  source="Ex", published=date(2026, 11, 1), first_seen=date(2026, 11, 2), llm_status="scored",
                  relevance=7, summary_zh="虚构摘要",
                  llm={"model": "m", "prompt_version": "news-v1", "scored_at": "2026-11-02T00:00:00Z"})
    NewsStore(tmp_path / "data").upsert([it])
    rc = cli.main(base(config_dir, tmp_path) + ["news-rescore", "--dry-run", "--no-fetch"])
    out = capsys.readouterr().out
    assert rc == 0 and "LLM 真实请求数：0" in out and "7 → -" in out and "stored_summary" in out


def _write_papers(tmp_path, papers):
    from aivc.storage import PaperStore
    PaperStore(tmp_path / "data").upsert(papers)


def test_help_lists_stats_enrich():
    h = cli._build_parser().format_help()
    assert "stats" in h and "enrich" in h


def test_stats_cmd(config_dir, tmp_path, capsys):
    _write_papers(tmp_path, [mk(1), mk(2)])
    assert cli.main(base(config_dir, tmp_path) + ["stats"]) == 0
    assert "总数：2" in capsys.readouterr().out
    assert cli.main(base(config_dir, tmp_path) + ["stats", "--json"]) == 0
    import json
    assert json.loads(capsys.readouterr().out)["total"] == 2
    assert not (tmp_path / "data" / "state").exists()


def _arc_paper():
    p = mk(5)
    return p.model_copy(update={"affiliations": ["Arc Institute, Palo Alto, CA"]})


def test_enrich_offline_dry_run_no_write(config_dir, tmp_path, capsys):
    _write_papers(tmp_path, [_arc_paper()])
    f = next((tmp_path / "data" / "papers").glob("*.jsonl"))
    before = f.read_bytes()
    assert cli.main(base(config_dir, tmp_path) + ["enrich", "--offline-only", "--dry-run"]) == 0
    assert f.read_bytes() == before


def test_enrich_offline_writes_orgs(config_dir, tmp_path):
    from aivc.storage import PaperStore
    _write_papers(tmp_path, [_arc_paper()])
    assert cli.main(base(config_dir, tmp_path) + ["enrich", "--offline-only"]) == 0
    got = PaperStore(tmp_path / "data").load_all()[0]
    assert any("Arc" in o for o in got.matched_orgs)


def _scored_paper():
    from datetime import datetime
    from aivc.models import LLMAnnotation
    ann = LLMAnnotation(circle="core", relevance=7, novelty_score=6, rigor_score=6, impact_score=6,
                        model="m", prompt_version="v4", scored_at=datetime(2026, 11, 1))
    return mk(7).model_copy(update={
        "doi": "10.1038/S41592-026-00002-2", "llm": ann, "llm_status": "scored"})


def test_enrich_all_batches_fail_returns_1(config_dir, tmp_path, monkeypatch, capsys):
    import httpx
    from aivc import enrich as en
    _write_papers(tmp_path, [mk(8).model_copy(update={"doi": "10.1101/2026.10.01.123456"})])
    real = en.enrich_via_openalex
    monkeypatch.setattr(en, "enrich_via_openalex", lambda papers, cfg, **kw: real(
        papers, cfg, client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
        sleep=lambda s: None, **kw))
    assert cli.main(base(config_dir, tmp_path) + ["enrich"]) == 1


def test_enrich_recomputes_final_score(config_dir, tmp_path, monkeypatch):
    import json
    from pathlib import Path
    import httpx
    from aivc import enrich as en
    from aivc.config import load_config
    from aivc.llm import recompute_final_score
    from aivc.storage import PaperStore
    cfg = load_config(config_dir)
    p = recompute_final_score(_scored_paper(), cfg)
    _write_papers(tmp_path, [p])
    body = json.loads((Path(__file__).parent / "fixtures" / "openalex" / "enrich_batch.json").read_text())
    body["results"][0]["primary_location"]["source"]["display_name"] = "Nature"
    real = en.enrich_via_openalex
    monkeypatch.setattr(en, "enrich_via_openalex", lambda papers, cfg, **kw: real(
        papers, cfg, client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))),
        sleep=lambda s: None, **kw))
    assert cli.main(base(config_dir, tmp_path) + ["enrich"]) == 0
    got = PaperStore(tmp_path / "data").load_all()[0]
    assert got.published_venue == "Nature"
    assert got.final_score > (p.final_score or 0)
