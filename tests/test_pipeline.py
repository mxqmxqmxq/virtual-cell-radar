import hashlib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from aivc import pipeline
from aivc.config import load_config
from aivc.fetchers.base import FetchError
from aivc.llm import LLMCache, LLMScorer
from aivc.models import Paper
from aivc.storage import PaperStore

TODAY = date(2026, 11, 14)


@pytest.fixture(autouse=True)
def _nokey(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def cfg(config_dir):
    c = load_config(config_dir)
    c.llm.concurrency = 1
    return c


def mk(i, title=None, abstract=None, source="arxiv"):
    topics = ["hepatocyte", "neuron", "T cell", "organoid", "fibroblast", "cardiomyocyte", "macrophage",
              "keratinocyte", "astrocyte", "podocyte", "osteoblast", "adipocyte"]
    title = title or f"Virtual cell perturbation prediction for {topics[i % len(topics)]} lineage {i}"
    return Paper(
        id=f"arxiv:2611.{i:05d}", title=title, source=source, arxiv_id=f"2611.{i:05d}",
        abstract=abstract or "We build a virtual cell model for perturbation prediction on Perturb-seq data.",
        published=TODAY - timedelta(days=1), first_seen=TODAY,
    )


def irrelevant(i=99):
    return Paper(id=f"arxiv:2611.{i:05d}", title="Quantum lattice gauge theory on a torus",
                 abstract="We study lattice gauge fields.", source="arxiv", arxiv_id=f"2611.{i:05d}",
                 published=TODAY, first_seen=TODAY)


class FakeFetcher:
    def __init__(self, name, papers=None, exc=None):
        self.name, self.papers, self.exc = name, papers or [], exc
        self.windows = []

    def fetch(self, since, until):
        self.windows.append((since, until))
        if self.exc:
            raise self.exc
        return list(self.papers)


class FakeClient:
    def __init__(self):
        self.calls = 0
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls += 1
        inp = {"tasks": [], "paradigms": [], "datasets_used": [], "releases_new_dataset": False,
               "new_dataset_names": [], "circle": "core", "relevance": 9,
               "novelty_zh": "新", "summary_zh": "摘要",
               "novelty_score": 7, "rigor_score": 7, "impact_score": 7}
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=inp)])


def run(cfg, tmp_path, fetchers, **kw):
    kw.setdefault("today", TODAY)
    return pipeline.run_update(cfg, data_dir=tmp_path / "data", reports_dir=tmp_path / "reports",
                               fetchers=fetchers, **kw)


def test_no_llm_flag(cfg, tmp_path):
    s = run(cfg, tmp_path, [FakeFetcher("arxiv", [mk(1), mk(2)])], use_llm=False)
    got = PaperStore(tmp_path / "data").load_all()
    assert len(got) == 2 and s.n_new == 2
    assert all(p.llm_status == "unscored" and p.llm_skip_reason == "no_llm_flag" for p in got)
    assert s.report_path and s.report_path.is_file()
    assert s.llm is None


def test_no_key_degrades(cfg, tmp_path):
    s = run(cfg, tmp_path, [FakeFetcher("arxiv", [mk(1)])], use_llm=True)
    got = PaperStore(tmp_path / "data").load_all()
    assert [p.llm_status for p in got] == ["unscored"]
    assert any("ANTHROPIC_API_KEY" in n for n in s.notes)
    assert s.report_path.is_file()


def test_mock_scorer_and_no_repeat_calls(cfg, tmp_path):
    client = FakeClient()
    scorer = LLMScorer(cfg, LLMCache(tmp_path / "data" / cfg.llm.cache_file), client=client)
    f = FakeFetcher("arxiv", [mk(1), mk(2)])
    s1 = run(cfg, tmp_path, [f], scorer=scorer)
    assert s1.n_new == 2 and client.calls == 2
    got = PaperStore(tmp_path / "data").load_all()
    assert all(p.llm_status == "scored" for p in got)
    s2 = run(cfg, tmp_path, [f], scorer=scorer)
    assert s2.n_new == 0 and client.calls == 2


def test_cache_prevents_repeat_even_if_store_lost(cfg, tmp_path):
    client = FakeClient()
    cache_path = tmp_path / "data" / cfg.llm.cache_file
    run(cfg, tmp_path, [FakeFetcher("arxiv", [mk(1)])], scorer=LLMScorer(cfg, LLMCache(cache_path), client=client))
    for f in (tmp_path / "data" / "papers").glob("*.jsonl"):
        f.unlink()
    run(cfg, tmp_path, [FakeFetcher("arxiv", [mk(1)])], scorer=LLMScorer(cfg, LLMCache(cache_path), client=client))
    assert client.calls == 1


def test_one_source_fails(cfg, tmp_path):
    ok = FakeFetcher("arxiv", [mk(1)])
    bad = FakeFetcher("biorxiv", exc=FetchError("boom"))
    weird = FakeFetcher("medrxiv", exc=RuntimeError("unexpected"))
    s = run(cfg, tmp_path, [bad, ok, weird], use_llm=False)
    assert set(s.fetch_errors) == {"biorxiv", "medrxiv"}
    assert s.n_new == 1 and s.fetched["arxiv"] == 1
    assert not s.all_sources_failed


def test_all_sources_fail_flag(cfg, tmp_path):
    s = run(cfg, tmp_path, [FakeFetcher("arxiv", exc=FetchError("x"))], use_llm=False)
    assert s.all_sources_failed and s.n_new == 0 and s.report_path.is_file()


def test_irrelevant_not_stored(cfg, tmp_path):
    s = run(cfg, tmp_path, [FakeFetcher("arxiv", [mk(1), irrelevant()])], use_llm=False)
    ids = [p.id for p in PaperStore(tmp_path / "data").load_all()]
    assert ids == ["arxiv:2611.00001"] and s.fetched["arxiv"] == 2 and s.kept_after_filter == 1


def snapshot(d: Path):
    # state/fetch_state.json 每次运行都会记录 run_at，不属于幂等对比范围
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(d.rglob("*")) if p.is_file() and p.relative_to(d).parts[0] != "state"}


def test_idempotent(cfg, tmp_path):
    f = FakeFetcher("arxiv", [mk(1), mk(2)])
    run(cfg, tmp_path, [f], use_llm=False)
    a = snapshot(tmp_path / "data")
    s = run(cfg, tmp_path, [f], use_llm=False)
    assert snapshot(tmp_path / "data") == a
    assert s.n_new == 0 and s.n_updated == 0


def test_idempotent_with_scorer(cfg, tmp_path):
    client = FakeClient()
    scorer = LLMScorer(cfg, LLMCache(tmp_path / "data" / cfg.llm.cache_file), client=client)
    f = FakeFetcher("arxiv", [mk(1)])
    run(cfg, tmp_path, [f], scorer=scorer)
    a = snapshot(tmp_path / "data")
    s = run(cfg, tmp_path, [f], scorer=scorer)
    assert snapshot(tmp_path / "data") == a and s.n_updated == 0


def test_backfill_windows(cfg, tmp_path):
    cfg.pipeline.update.backfill_chunk_days = 10
    f = FakeFetcher("arxiv", [mk(1)])
    s = pipeline.run_backfill(cfg, data_dir=tmp_path / "data", reports_dir=tmp_path / "reports",
                              days=25, use_llm=False, today=TODAY, fetchers=[f])
    since = TODAY - timedelta(days=24)  # 含今天共 25 天
    w = f.windows
    assert len(w) == 3
    assert w[0][1] == TODAY and w[-1][0] == since  # 从最近往最早
    for (lo, hi) in w:
        assert since <= lo <= hi <= TODAY
    for (lo1, _), (_, hi2) in zip(w, w[1:]):
        assert hi2 == lo1 - timedelta(days=1)  # 无缝无重叠
    assert s.report_path == tmp_path / "reports" / "backfill" / f"{TODAY.isoformat()}_25d.md"
    text = s.report_path.read_text(encoding="utf-8")
    assert "AIVC 回填 · 最近 25 天" in text
    assert s.n_new == 1 and len(PaperStore(tmp_path / "data").load_all()) == 1


def test_backfill_shared_budget(cfg, tmp_path):
    cfg.pipeline.update.backfill_chunk_days = 5
    cfg.llm.max_calls_per_run = 3
    client = FakeClient()
    scorer = LLMScorer(cfg, LLMCache(tmp_path / "data" / cfg.llm.cache_file), client=client)

    class PerWindow(FakeFetcher):
        def fetch(self, since, until):
            super().fetch(since, until)
            i = len(self.windows)
            return [mk(i * 10 + k) for k in range(2)]

    pipeline.run_backfill(cfg, data_dir=tmp_path / "data", reports_dir=tmp_path / "reports",
                          days=14, today=TODAY, fetchers=[PerWindow("arxiv")], scorer=scorer)
    assert client.calls == 3


def make_seed_dir(root: Path):
    root.mkdir()
    (root / "notes.md").write_text("x", encoding="utf-8")
    w = PdfWriter()
    w.add_blank_page(72, 72)
    w.add_metadata({"/Title": "A paper about something unrelated to cells entirely"})
    with open(root / "2204.13545v2.pdf", "wb") as fh:
        w.write(fh)
    (root / "iclr2027_pdf").mkdir()
    (root / "iclr2027_pdf" / "ICLR2027_摘要汇总.md").write_text(
        "# ICLR\n\n## 虚拟细胞（1 篇）\n\n### 1. Quantum gardening\n\n- 类别：杂项\n- 关键词：a; b\n"
        "- 链接：[论文页](https://openreview.net/forum?id=ABCDEFGHIJ) · [PDF](https://openreview.net/pdf?id=ABCDEFGHIJ)\n"
        "- OpenReview ID：`ABCDEFGHIJ`\n\nAn abstract that is not about virtual cells.\n",
        encoding="utf-8")


def tree(d: Path):
    return sorted((str(p.relative_to(d)), p.stat().st_mtime_ns, p.stat().st_size)
                  for p in d.rglob("*") if p.is_file())


def test_seed(cfg, tmp_path):
    src = tmp_path / "seed"
    make_seed_dir(src)
    before = tree(src)
    kw = dict(data_dir=tmp_path / "data", source_dir=src, use_llm=False, today=TODAY)
    s = pipeline.run_seed(cfg, **kw)
    got = PaperStore(tmp_path / "data").load_all()
    assert s.n_new == len(got) >= 2  # 无关键词命中的种子也保留
    assert any(p.openreview_id == "ABCDEFGHIJ" for p in got)
    assert any("种子共" in n for n in s.notes)
    snap = snapshot(tmp_path / "data")
    s2 = pipeline.run_seed(cfg, **kw)
    assert s2.n_new == 0 and snapshot(tmp_path / "data") == snap
    assert tree(src) == before


def test_seed_missing_dir(cfg, tmp_path):
    with pytest.raises(pipeline.PipelineError, match="种子目录"):
        pipeline.run_seed(cfg, data_dir=tmp_path / "d", source_dir=tmp_path / "nope", use_llm=False)


def test_seed_scoring_only_with_abstract(cfg, tmp_path):
    src = tmp_path / "seed"
    make_seed_dir(src)
    client = FakeClient()
    scorer = LLMScorer(cfg, LLMCache(tmp_path / "data" / cfg.llm.cache_file), client=client)
    pipeline.run_seed(cfg, data_dir=tmp_path / "data", source_dir=src, today=TODAY, scorer=scorer)
    assert client.calls == 1  # 只有 ICLR 条目有摘要


def test_score_no_key(cfg, tmp_path):
    run(cfg, tmp_path, [FakeFetcher("arxiv", [mk(1)])], use_llm=False)
    a = snapshot(tmp_path / "data")
    s = pipeline.run_score(cfg, data_dir=tmp_path / "data")
    assert any("ANTHROPIC_API_KEY" in n for n in s.notes)
    assert snapshot(tmp_path / "data") == a


def test_score_with_mock_and_limit(cfg, tmp_path):
    run(cfg, tmp_path, [FakeFetcher("arxiv", [mk(1), mk(2), mk(3)])], use_llm=False)
    client = FakeClient()
    scorer = LLMScorer(cfg, LLMCache(tmp_path / "data" / cfg.llm.cache_file), client=client)
    s = pipeline.run_score(cfg, data_dir=tmp_path / "data", limit=2, scorer=scorer)
    assert client.calls == 2 and s.n_updated == 2
    got = PaperStore(tmp_path / "data").load_all()
    assert sorted(p.llm_status for p in got) == ["scored", "scored", "unscored"]


def test_score_rescoring_stale_prompt_version(cfg, tmp_path):
    run(cfg, tmp_path, [FakeFetcher("arxiv", [mk(1), mk(2)])], use_llm=False)
    data = tmp_path / "data"
    c1 = FakeClient()
    pipeline.run_score(cfg, data_dir=data, scorer=LLMScorer(cfg, LLMCache(data / cfg.llm.cache_file), client=c1))
    assert c1.calls == 2
    # same version: nothing left to do
    c2 = FakeClient()
    s = pipeline.run_score(cfg, data_dir=data, scorer=LLMScorer(cfg, LLMCache(data / cfg.llm.cache_file), client=c2))
    assert c2.calls == 0
    # bump version: scored papers are re-scored
    cfg.llm.prompt_version = "v-next"
    c3 = FakeClient()
    pipeline.run_score(cfg, data_dir=data, scorer=LLMScorer(cfg, LLMCache(data / cfg.llm.cache_file), client=c3))
    assert c3.calls == 2
    got = PaperStore(data).load_all()
    assert {p.llm.prompt_version for p in got} == {"v-next"}


def test_update_days_includes_today(cfg, tmp_path):
    f = FakeFetcher("arxiv", [])
    run(cfg, tmp_path, [f], use_llm=False, days=2)
    assert f.windows == [(TODAY - timedelta(days=1), TODAY)]


def test_same_day_rerun_report_keeps_earlier_papers(cfg, tmp_path):
    run(cfg, tmp_path, [FakeFetcher("arxiv", [mk(1)])], use_llm=False)
    s = run(cfg, tmp_path, [FakeFetcher("arxiv", [mk(2)])], use_llm=False)
    text = s.report_path.read_text(encoding="utf-8")
    assert mk(1).title in text and mk(2).title in text


def test_updated_paper_with_new_abstract_is_rescored(cfg, tmp_path):
    store = PaperStore(tmp_path / "data")
    old = mk(1).model_copy(update={"abstract": None, "llm_status": "unscored",
                                   "llm_skip_reason": "no_abstract"})
    store.upsert([old])
    client = FakeClient()
    scorer = LLMScorer(cfg, LLMCache(tmp_path / "data" / "cache" / "llm.jsonl"), client=client)
    run(cfg, tmp_path, [FakeFetcher("arxiv", [mk(1)])], scorer=scorer)
    p = {x.id: x for x in store.load_all()}[mk(1).id]
    assert p.abstract and p.llm_status == "scored" and client.calls == 1


def test_reindex_recomputes_and_prunes(cfg, tmp_path):
    store = PaperStore(tmp_path / "data")
    stale = irrelevant().model_copy(update={"circle": "core", "keyword_score": 5.0})
    seed = irrelevant(98).model_copy(update={"source": "seed_pdf", "circle": "core", "keyword_score": 5.0})
    store.upsert([stale, seed, mk(1)])
    s = pipeline.run_reindex(cfg, data_dir=tmp_path / "data", prune=True)
    ids = {p.id: p for p in store.load_all()}
    assert stale.id not in ids  # 抓取条目不再相关 -> 删除
    assert ids[seed.id].circle == "none"  # 种子保留但重算
    assert ids[mk(1).id].circle == "core"
    assert s.n_updated >= 1


# ---------- v2: 抓取器接线 / 水位线 / backlog / enrich ----------
from datetime import datetime  # noqa: E402

from aivc.state import FetchState  # noqa: E402


def _state(tmp_path):
    return FetchState.load(tmp_path / "data" / "state" / "fetch_state.json")


def test_build_fetchers_real_config(cfg):
    assert [f.name for f in pipeline.build_fetchers(cfg)] == [
        "arxiv", "biorxiv", "openalex", "europepmc", "hf_papers"]


def test_watermark_extends_window(cfg, tmp_path):
    f = FakeFetcher("arxiv", [])
    run(cfg, tmp_path, [f], use_llm=False, today=TODAY - timedelta(days=5))
    run(cfg, tmp_path, [f], use_llm=False)
    # 第二次：last_success = today-5，overlap=1 -> since = today-6，大于默认 3 天
    assert f.windows[1] == (TODAY - timedelta(days=6), TODAY)
    assert f.windows[1][0] >= TODAY - timedelta(days=13)
    assert (tmp_path / "data" / "state" / "fetch_state.json").is_file()


def test_watermark_never_shrinks_default_window(cfg, tmp_path):
    f = FakeFetcher("arxiv", [])
    run(cfg, tmp_path, [f], use_llm=False)
    s = run(cfg, tmp_path, [f], use_llm=False)
    assert f.windows[1] == (TODAY - timedelta(days=2), TODAY)
    assert not any("水位线追赶" in n for n in s.notes)


def test_watermark_clamped_to_max_catchup(cfg, tmp_path):
    f = FakeFetcher("arxiv", [])
    run(cfg, tmp_path, [f], use_llm=False, today=TODAY - timedelta(days=40))
    s = run(cfg, tmp_path, [f], use_llm=False)
    assert f.windows[1][0] == TODAY - timedelta(days=cfg.pipeline.update.max_catchup_days - 1)
    assert s.windows_by_source["arxiv"] == f.windows[1]
    assert any("水位线追赶：arxiv" in n for n in s.notes)


def test_explicit_days_ignores_watermark(cfg, tmp_path):
    f = FakeFetcher("arxiv", [])
    run(cfg, tmp_path, [f], use_llm=False, today=TODAY - timedelta(days=8))
    run(cfg, tmp_path, [f], use_llm=False, days=2)
    assert f.windows[1] == (TODAY - timedelta(days=1), TODAY)


def test_fetch_failure_recorded_in_state(cfg, tmp_path):
    bad = FakeFetcher("biorxiv", exc=FetchError("boom"))
    ok = FakeFetcher("arxiv", [])
    run(cfg, tmp_path, [bad, ok], use_llm=False)
    run(cfg, tmp_path, [bad, ok], use_llm=False)
    st = _state(tmp_path)
    assert st.sources["biorxiv"].consecutive_failures == 2
    assert st.sources["biorxiv"].last_success_date is None
    assert st.sources["arxiv"].last_success_date == TODAY
    assert len(st.runs) == 2


def _scorer(cfg, tmp_path, client):
    return LLMScorer(cfg, LLMCache(tmp_path / "data" / cfg.llm.cache_file), client=client)


def test_backlog_retry(cfg, tmp_path):
    run(cfg, tmp_path, [FakeFetcher("arxiv", [mk(1), mk(2), mk(3)])], use_llm=False)
    # no_llm_flag 的条目不会被补打：改成普通 unscored
    store = PaperStore(tmp_path / "data")
    store.upsert([p.model_copy(update={"llm_skip_reason": None}) for p in store.load_all()])
    client = FakeClient()
    s = run(cfg, tmp_path, [FakeFetcher("arxiv", [])], scorer=_scorer(cfg, tmp_path, client))
    assert s.n_backlog_scored == 3 and client.calls == 3
    assert any("补打积压 3 篇" in n for n in s.notes)
    assert {p.llm_status for p in store.load_all()} == {"scored"}


def test_backlog_skips_no_llm_flag(cfg, tmp_path):
    run(cfg, tmp_path, [FakeFetcher("arxiv", [mk(1)])], use_llm=False)
    client = FakeClient()
    s = run(cfg, tmp_path, [FakeFetcher("arxiv", [])], scorer=_scorer(cfg, tmp_path, client))
    assert s.n_backlog_scored == 0 and client.calls == 0


def test_backlog_disabled_when_zero(cfg, tmp_path):
    run(cfg, tmp_path, [FakeFetcher("arxiv", [mk(1)])], use_llm=False)
    store = PaperStore(tmp_path / "data")
    store.upsert([p.model_copy(update={"llm_skip_reason": None}) for p in store.load_all()])
    cfg.pipeline.update.retry_backlog_per_run = 0
    client = FakeClient()
    s = run(cfg, tmp_path, [FakeFetcher("arxiv", [])], scorer=_scorer(cfg, tmp_path, client))
    assert s.n_backlog_scored == 0 and client.calls == 0


def test_enrich_offline_in_pipeline(cfg, tmp_path):
    p = mk(1).model_copy(update={"affiliations": ["Arc Institute, Palo Alto"]})
    run(cfg, tmp_path, [FakeFetcher("arxiv", [p])], use_llm=False)
    got = PaperStore(tmp_path / "data").load_all()[0]
    assert any("Arc" in o for o in got.matched_orgs)
    assert "arxiv" in got.sources


def test_summary_semantic_fields(cfg, tmp_path):
    s = run(cfg, tmp_path, [FakeFetcher("arxiv", [mk(1)])], use_llm=False)
    assert s.n_semantic_rescued == 0 and s.n_backlog_scored == 0


def test_run_score_prioritizes_error_unscored(cfg, tmp_path):
    run(cfg, tmp_path, [FakeFetcher("arxiv", [mk(1), mk(2), mk(3)])], use_llm=False)
    data = tmp_path / "data"
    pipeline.run_score(cfg, data_dir=data, limit=1, scorer=_scorer(cfg, tmp_path, FakeClient()))
    cfg.llm.prompt_version = "v-next"  # 已打分的那篇变成旧版本
    client = FakeClient()
    pipeline.run_score(cfg, data_dir=data, limit=2, scorer=_scorer(cfg, tmp_path, client))
    got = PaperStore(data).load_all()
    # 限额 2：两篇 unscored 优先，旧版本那篇仍是旧 prompt_version
    assert sum(1 for p in got if p.llm and p.llm.prompt_version == "v-next") == 2


# ---------- T16: 语义捞回一致性 / 抓取不完整 / /pubs 回填 / 积压计数 / backfill labs ----------
import json  # noqa: E402

import httpx  # noqa: E402

from aivc.fetchers.biorxiv import BiorxivFetcher  # noqa: E402
from aivc.fetchers.hf_papers import HFPapersFetcher  # noqa: E402
from aivc.fetchers.openalex import OpenAlexFetcher  # noqa: E402
from aivc.prefilter import apply_keywords  # noqa: E402

FIXD = Path(__file__).parent / "fixtures"
HTTP = SimpleNamespace(max_retries=0, user_agent="t", timeout=5)
_EX_ABS = ("Zorblax manifold forecasting of xenocyte regulatory trajectories with latent "
           "zorblax tensors; virtual cell perturbation prediction.")
_NEW_ABS = ("Zorblax manifold forecasting of xenocyte regulatory trajectories with latent "
            "zorblax tensors and xenocyte trajectories.")


def _scored(cfg, p, circle="core", final=9.0):
    from aivc.llm import LLMAnnotation

    ann = LLMAnnotation(circle=circle, relevance=9, novelty_score=8, rigor_score=8, impact_score=8,
                        novelty_zh="新", summary_zh="摘要", prompt_version=cfg.llm.prompt_version,
                        model="m", scored_at=datetime(2026, 11, 14, 0, 0, 0, tzinfo=timezone.utc))
    return p.model_copy(update={"llm": ann, "llm_status": "scored", "final_score": final,
                                "must_read": True})


def _exemplars(cfg, n=4):
    out = []
    for i in range(n):
        p = Paper(id=f"arxiv:2610.{i:05d}", title=f"Zorblax xenocyte trajectory forecasting {i}",
                  abstract=_EX_ABS, source="arxiv", arxiv_id=f"2610.{i:05d}",
                  published=TODAY, first_seen=TODAY)
        out.append(_scored(cfg, apply_keywords(p, cfg)))
    return out


def _rescue_paper():
    return Paper(id="arxiv:2611.07777", title="Zorblax manifold forecasting for xenocytes",
                 abstract=_NEW_ABS, source="arxiv", arxiv_id="2611.07777",
                 published=TODAY, first_seen=TODAY)


def test_semantic_rescue_survives_reindex_and_prune(cfg, tmp_path):
    store = PaperStore(tmp_path / "data")
    exs = _exemplars(cfg)
    assert all(e.circle != "none" for e in exs)
    old_irr = irrelevant(98)
    store.upsert([*exs, old_irr])
    rp = _rescue_paper()
    assert apply_keywords(rp, cfg).circle == "none"
    s = run(cfg, tmp_path, [FakeFetcher("arxiv", [rp])], use_llm=False)
    assert s.n_semantic_rescued == 1
    got = {p.id: p for p in store.load_all()}[rp.id]
    assert got.circle == "peripheral" and "semantic:rescue" in got.matched_keywords

    data = tmp_path / "data"
    r1 = pipeline.run_reindex(cfg, data_dir=data, prune=False)
    p1 = {p.id: p for p in store.load_all()}
    assert rp.id in p1 and old_irr.id in p1
    assert "semantic:rescue" in p1[rp.id].matched_keywords and p1[rp.id].circle == "peripheral"
    assert r1.n_semantic_rescued >= 1

    pipeline.run_reindex(cfg, data_dir=data, prune=True)
    p2 = {p.id: p for p in store.load_all()}
    assert rp.id in p2 and "semantic:rescue" in p2[rp.id].matched_keywords
    assert p2[rp.id].circle == "peripheral"
    assert old_irr.id not in p2  # 真正无关的旧条目仍被 prune 删除


class IncFetcher(FakeFetcher):
    def __init__(self, name, papers, inc=True, warn="部分结果"):
        super().__init__(name, papers)
        self.last_incomplete, self.last_warning = inc, warn


def test_incomplete_fetcher_does_not_advance_watermark(cfg, tmp_path):
    f = IncFetcher("arxiv", [mk(1)])
    s = run(cfg, tmp_path, [f], use_llm=False)
    assert s.incomplete == {"arxiv": "部分结果"} and s.n_new == 1
    assert any("未推进水位线" in n and "部分结果" in n for n in s.notes)
    st = _state(tmp_path)
    assert st.sources["arxiv"].last_success_date is None
    assert st.runs[0]["incomplete"] == {"arxiv": "部分结果"}


def _hf(handler):
    cfg_ = SimpleNamespace(base_url="https://huggingface.co/api/daily_papers",
                           delay_seconds=0.0, max_days=31)
    return HFPapersFetcher(cfg_, HTTP, client=httpx.Client(transport=httpx.MockTransport(handler)),
                           sleep=lambda s: None, today=TODAY)


def test_hf_historical_400_errors_and_keeps_watermark(cfg, tmp_path):
    hist = (TODAY - timedelta(days=1)).isoformat()

    def h(req):
        if req.url.params["date"] == hist:
            return httpx.Response(400, json={"error": "bad"})
        return httpx.Response(200, json=[])
    s = run(cfg, tmp_path, [_hf(h)], use_llm=False, days=2)
    assert "hf_papers" in s.fetch_errors and hist in s.fetch_errors["hf_papers"]
    assert _state(tmp_path).sources["hf_papers"].last_success_date is None


def test_hf_dict_response_errors(cfg, tmp_path):
    s = run(cfg, tmp_path, [_hf(lambda req: httpx.Response(200, json={"error": "x"}))],
            use_llm=False, days=2)
    assert "hf_papers" in s.fetch_errors
    assert _state(tmp_path).sources["hf_papers"].last_success_date is None


def _oa(handler):
    from aivc.config import JournalIssn

    c = SimpleNamespace(
        base_url="https://api.openalex.org/works",
        journal_issns=[JournalIssn(name="Nature Methods", issn=["1548-7091"])],
        domain_terms=["virtual cell"], queries=[], mailto="", api_key_env="OPENALEX_API_KEY",
        per_page=50, delay_seconds=0.0, max_pages=10)
    return OpenAlexFetcher(c, HTTP, client=httpx.Client(transport=httpx.MockTransport(handler)),
                           sleep=lambda s: None, today=TODAY, api_key=None)


def _oa_load(name):
    return json.loads((FIXD / "openalex" / name).read_text())


def test_openalex_second_page_401_keeps_first_and_watermark(cfg, tmp_path):
    def ok(req):
        cur = req.url.params.get("cursor")
        return httpx.Response(200, json=_oa_load("works_page1.json" if cur == "*" else "works_empty.json"))

    def half(req):
        if req.url.params.get("cursor") == "*":
            return httpx.Response(200, json=_oa_load("works_page1.json"))
        return httpx.Response(401, json={"error": "key"})

    # 从未成功过：水位线保持为 None
    s = run(cfg, tmp_path, [_oa(half)], use_llm=False)
    assert s.fetched["openalex"] == 2 and "openalex" in s.incomplete
    assert any("未推进水位线" in n for n in s.notes)
    assert _state(tmp_path).sources["openalex"].last_success_date is None

    # 先完整成功一次，再遇不完整：保持旧水位线
    run(cfg, tmp_path, [_oa(ok)], use_llm=False, today=TODAY - timedelta(days=1))
    assert _state(tmp_path).sources["openalex"].last_success_date == TODAY - timedelta(days=1)
    s2 = run(cfg, tmp_path, [_oa(half)], use_llm=False)
    assert "openalex" in s2.incomplete
    assert _state(tmp_path).sources["openalex"].last_success_date == TODAY - timedelta(days=1)


def test_pubs_backfills_existing_preprint(cfg, tmp_path):
    doi = "10.64898/2026.11.12.700001"
    pre = Paper(id="biorxiv:2026.11.12.700001", title="Foundation model for single-cell perturbation response",
                abstract="We build a virtual cell model for perturbation prediction.", source="biorxiv",
                doi=doi, biorxiv_id="2026.11.12.700001", venue="bioRxiv", published=TODAY,
                first_seen=TODAY)
    pre = _scored(cfg, apply_keywords(pre, cfg), final=7.0)
    store = PaperStore(tmp_path / "data")
    store.upsert([pre])

    def h(req):
        if "/pubs/" in req.url.path:
            return httpx.Response(200, json=json.loads((FIXD / "biorxiv" / "pubs_page1.json").read_text()))
        return httpx.Response(200, json=json.loads((FIXD / "biorxiv" / "empty.json").read_text()))
    bc = SimpleNamespace(server="biorxiv", base_url="https://api.biorxiv.org", categories=["systems biology"],
                         delay_seconds=0.0, max_pages_per_category=5, fetch_published_links=True)
    f = BiorxivFetcher(bc, HTTP, client=httpx.Client(transport=httpx.MockTransport(h)),
                       sleep=lambda s: None, today=TODAY)
    s = run(cfg, tmp_path, [f], use_llm=False)
    got = {p.id: p for p in store.load_all()}[pre.id]
    assert got.published_doi == "10.1038/s41592-026-00002-y"
    assert got.published_venue == "Nature Methods" and got.venue == "Nature Methods"
    assert got.final_score is not None and got.final_score > 7.0
    assert any("/pubs 回填库内 1 篇" in n for n in s.notes)


class _FailSecondClient(FakeClient):
    def _create(self, **kw):
        if "FAILME" in str(kw):
            self.calls += 1
            e = RuntimeError("bad request")
            e.status_code = 400
            raise e
        return super()._create(**kw)


def test_backlog_counts_only_really_scored(cfg, tmp_path):
    run(cfg, tmp_path, [FakeFetcher("arxiv", [mk(1), mk(2, title="Virtual cell FAILME perturbation prediction")])],
        use_llm=False)
    store = PaperStore(tmp_path / "data")
    store.upsert([p.model_copy(update={"llm_skip_reason": None}) for p in store.load_all()])
    s = run(cfg, tmp_path, [FakeFetcher("arxiv", [])], scorer=_scorer(cfg, tmp_path, _FailSecondClient()))
    assert s.n_backlog_scored == 1
    assert any("补打积压 1 篇" in n for n in s.notes)
    assert sorted(p.llm_status for p in store.load_all()) == ["error", "scored"]


def test_backfill_passes_labs(cfg, tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "resources").mkdir(parents=True)
    (data / "resources" / "labs.yaml").write_text(
        "resources:\n  - id: lab-x\n    name: Zorblax Lab\n    kind: lab\n", encoding="utf-8")
    seen = []
    real = pipeline.enrich.enrich_offline

    def spy(papers, cfg_, labs=None):
        seen.append(labs)
        return real(papers, cfg_, labs)
    monkeypatch.setattr(pipeline.enrich, "enrich_offline", spy)
    pipeline.run_backfill(cfg, data_dir=data, reports_dir=tmp_path / "reports", days=3,
                          use_llm=False, today=TODAY, fetchers=[FakeFetcher("arxiv", [mk(1)])])
    assert seen and all(x is not None and "Zorblax Lab" in x for x in seen)
