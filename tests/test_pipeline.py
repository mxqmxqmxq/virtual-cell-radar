import hashlib
from datetime import date, timedelta
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
               "novelty_zh": "新", "summary_zh": "摘要"}
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
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(d.rglob("*")) if p.is_file()}


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
