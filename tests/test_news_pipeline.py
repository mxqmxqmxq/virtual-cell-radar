from datetime import date, datetime, timezone

import pytest

from aivc import news_pipeline
from aivc.config import load_config
from aivc.fetchers.news_rss import NewsFetchResult
from aivc.models import NewsEntry, NewsLLM
from aivc.news import NewsStore
from aivc.news_llm import NewsLLMStats
from aivc.news_pipeline import NewsRunSummary, NullNotifier, run_news
from aivc.pipeline import PipelineError

TODAY = date(2026, 11, 15)


@pytest.fixture
def cfg(config_dir):
    return load_config(config_dir)


def ent(i, title=None, company_text="Recursion", pub=TODAY, source="Fake Media", snippet="虚构片段 AI drug discovery"):
    return NewsEntry(title=title or f"{company_text} announces fictional event number {i} in AI drug discovery",
                     url=f"https://fake.example/n/{i}", source=source, published=pub, snippet=snippet)


class FakeFetcher:
    def __init__(self, entries=(), errors=None, per_feed=None, exc=None):
        self.entries, self.errors, self.exc = list(entries), errors or {}, exc
        self.per_feed = per_feed if per_feed is not None else {"Fake Media": len(self.entries)}
        self.calls = 0

    def fetch(self, since, until):
        self.calls += 1
        if self.exc:
            raise self.exc
        return NewsFetchResult(entries=self.entries, per_feed=self.per_feed, errors=self.errors, requests=3)


class FakeExtractor:
    """按 spec(item) 给条目打分；记录每次调用收到的条目。"""

    def __init__(self, spec=None, available=True):
        self.spec = spec or (lambda it: {})
        self.available = available
        self.calls = []

    def extract(self, cands, max_calls=None, max_requests=None):
        self.calls.append(([c[0].id for c in cands], max_calls))
        self.max_requests = max_requests
        out = []
        for it, _ in cands:
            if not self.available:
                out.append(it.model_copy(update={"llm_skip_reason": "no_backend"}))
                continue
            upd = {"llm_status": "scored", "event_type": "other", "relevance": 8, "companies": ["Recursion"],
                   "summary_zh": "虚构摘要", "significance_zh": "虚构意义", "amount_usd": 5e7,
                   "llm": NewsLLM(model="m", prompt_version="news-v1", scored_at=datetime.now(timezone.utc))}
            upd.update(self.spec(it))
            out.append(it.model_copy(update=upd))
        return out, NewsLLMStats(scored=len(out) if self.available else 0, api_calls=len(out))


class RecNotifier:
    def __init__(self):
        self.got = []

    def notify(self, items):
        self.got.append(list(items))


def go(cfg, tmp_path, entries, **kw):
    kw.setdefault("fetcher", FakeFetcher(entries))
    kw.setdefault("extractor", FakeExtractor())
    return run_news(cfg, data_dir=tmp_path, today=TODAY, **kw)


def test_end_to_end_store(cfg, tmp_path):
    s = go(cfg, tmp_path, [ent(1), ent(2, "Insitro unveils another fictional collaboration headline AI")])
    assert (s.n_fetched, s.n_candidates, s.n_new) == (2, 2, 2)
    assert s.window == (date(2026, 11, 9), TODAY) and s.requests == 3
    items = NewsStore(tmp_path).load_all()
    assert len(items) == 2 and all(i.llm_status == "scored" for i in items)
    assert (tmp_path / "news" / "2026-11.jsonl").exists()
    # 不落盘片段
    assert "虚构片段" not in (tmp_path / "news" / "2026-11.jsonl").read_text(encoding="utf-8")


def test_days_window(cfg, tmp_path):
    s = go(cfg, tmp_path, [ent(1)], days=3)
    assert s.window == (date(2026, 11, 13), TODAY)


def test_idempotent_rerun(cfg, tmp_path):
    es = [ent(1), ent(2, "Insitro unveils another fictional collaboration headline AI")]
    go(cfg, tmp_path, es)
    ext = FakeExtractor()
    s = go(cfg, tmp_path, es, extractor=ext)
    assert s.n_new == 0 and s.n_updated == 0 and s.n_duplicates == 2
    assert len(NewsStore(tmp_path).load_all()) == 2
    assert ext.calls == []  # 无待抽取条目则不调用


def test_limit_semantics(cfg, tmp_path):
    words = ["alpha harbor", "bridge lantern", "cobalt meadow", "dragon orchard", "ember canyon"]
    es = [ent(i, f"{w} says Recursion plans {len(w) * i} pilot sites for AI drug discovery") for i, w in enumerate(words, 1)]
    ext = FakeExtractor()
    s = go(cfg, tmp_path, es, limit=2, extractor=ext)
    assert s.n_new == 2 and s.n_skipped_limit == 3
    assert len(ext.calls[0][0]) == 2 and ext.calls[0][1] == 2
    assert len(NewsStore(tmp_path).load_all()) == 2
    assert any("--limit" in n for n in s.notes)


def test_limit_validation(cfg, tmp_path):
    with pytest.raises(PipelineError):
        go(cfg, tmp_path, [], limit=0)
    with pytest.raises(PipelineError):
        go(cfg, tmp_path, [], days=0)


def test_no_llm(cfg, tmp_path):
    ext = FakeExtractor()
    s = go(cfg, tmp_path, [ent(1)], use_llm=False, extractor=ext)
    assert s.llm is None and ext.calls == []
    it = NewsStore(tmp_path).load_all()[0]
    assert it.llm_status == "unscored" and it.llm_skip_reason == "no_llm_flag"
    assert any("--no-llm" in n for n in s.notes)


def test_rescore_reappearing_unscored(cfg, tmp_path):
    go(cfg, tmp_path, [ent(1)], use_llm=False)
    ext = FakeExtractor()
    s = go(cfg, tmp_path, [ent(1)], extractor=ext)
    assert s.n_new == 0 and s.n_duplicates == 1
    items = NewsStore(tmp_path).load_all()
    assert len(items) == 1 and items[0].llm_status == "scored"
    assert len(ext.calls[0][0]) == 1


def test_no_backend_marks_unscored(cfg, tmp_path):
    s = go(cfg, tmp_path, [ent(1)], extractor=FakeExtractor(available=False))
    assert any("没有可用的 LLM 后端" in n for n in s.notes)
    assert NewsStore(tmp_path).load_all()[0].llm_status == "unscored"


def test_event_merge_counts(cfg, tmp_path):
    es = [ent(1, "Recursion closes round A in fictional deal", source="Media A"),
          ent(2, "Fictional deal: Recursion gets funding round A today", source="Media B")]
    s = go(cfg, tmp_path, es, extractor=FakeExtractor(lambda it: {"event_type": "funding"}))
    assert s.n_merged == 1 and s.n_new == 1
    it = NewsStore(tmp_path).load_all()[0]
    assert len(it.sources) == 2


def test_major_triggers_notifier(cfg, tmp_path):
    n = RecNotifier()
    s = go(cfg, tmp_path, [ent(1)], extractor=FakeExtractor(lambda it: {"amount_usd": 2e8, "event_type": "funding"}), notifier=n)
    assert len(s.major) == 1 and s.major[0].major
    assert len(n.got) == 1 and n.got[0][0].id == s.major[0].id
    # 再跑一次不再算「新出现」
    n2 = RecNotifier()
    s2 = go(cfg, tmp_path, [ent(1)], notifier=n2)
    assert s2.major == [] and n2.got == []


def test_non_major_no_notify(cfg, tmp_path):
    n = RecNotifier()
    go(cfg, tmp_path, [ent(1)], notifier=n)
    assert n.got == []


def test_notifier_failure_swallowed(cfg, tmp_path):
    class Bad:
        def notify(self, items):
            raise RuntimeError("x")
    s = go(cfg, tmp_path, [ent(1)], extractor=FakeExtractor(lambda it: {"amount_usd": 2e8, "event_type": "funding"}), notifier=Bad())
    assert s.n_new == 1 and any("通知失败" in n for n in s.notes)


def test_all_feeds_failed(cfg, tmp_path):
    names = [f.name for f in [*cfg.sources.news.feeds, *cfg.sources.news.html_sources] if f.enabled]
    s = go(cfg, tmp_path, [], fetcher=FakeFetcher(errors={n: "boom" for n in names}, per_feed={}))
    assert s.all_feeds_failed and s.n_new == 0


def test_one_feed_failed_not_all(cfg, tmp_path):
    names = [f.name for f in cfg.sources.news.feeds if f.enabled]
    s = go(cfg, tmp_path, [ent(1)], fetcher=FakeFetcher([ent(1)], errors={names[0]: "boom"}))
    assert not s.all_feeds_failed and s.n_new == 1


def test_fetcher_exception_swallowed(cfg, tmp_path):
    s = go(cfg, tmp_path, [], fetcher=FakeFetcher(exc=RuntimeError("net")))
    assert s.all_feeds_failed and s.n_new == 0


def test_news_disabled(cfg, tmp_path):
    cfg.sources.news.enabled = False
    f = FakeFetcher([ent(1)])
    s = go(cfg, tmp_path, [], fetcher=f)
    assert f.calls == 0 and s.notes and not s.attempted


def test_no_enabled_feeds(cfg, tmp_path):
    for fd in [*cfg.sources.news.feeds, *cfg.sources.news.html_sources]:
        fd.enabled = False
    s = run_news(cfg, data_dir=tmp_path, today=TODAY)
    assert s.notes and s.n_fetched == 0 and not s.all_feeds_failed


def test_prefilter_drops_irrelevant(cfg, tmp_path):
    s = go(cfg, tmp_path, [ent(1, "Local bakery opens new store", snippet="bread")])
    assert s.n_fetched == 1 and s.n_candidates == 0 and s.n_new == 0


def test_summary_property_and_null_notifier(caplog):
    assert not NewsRunSummary().all_feeds_failed
    s = NewsRunSummary(attempted=["a"], fetch_errors={"a": "x"})
    assert s.all_feeds_failed
    NullNotifier().notify([])


# ---------------------------------------------------------------- N4: rescore-stale / source rotation
class VerExtractor(FakeExtractor):
    """打分时写入当前版本号；fail_ids 中的条目返回 error；记录每次调用。"""

    def __init__(self, version="news-v6.1", fail_ids=(), budget_cut=None, **kw):
        super().__init__(**kw)
        self.version, self.fail_ids, self.budget_cut = version, set(fail_ids), budget_cut

    def extract(self, cands, max_calls=None, max_requests=None):
        self.calls.append(([c[0].id for c in cands], max_calls))
        self.max_requests = max_requests
        out, n_err, n_budget = [], 0, 0
        for k, (it, _) in enumerate(cands):
            if it.id in self.fail_ids:
                out.append(it.model_copy(update={"llm_status": "error", "llm_skip_reason": "x"}))
                n_err += 1
            elif max_calls is not None and k >= max_calls:
                out.append(it.model_copy(update={"llm_status": "unscored", "llm_skip_reason": "budget"}))
                n_budget += 1
            else:
                out.append(it.model_copy(update={
                    "llm_status": "scored", "relevance": 9, "summary_zh": "新摘要",
                    "llm": NewsLLM(model="m", prompt_version=self.version,
                                   scored_at=datetime.now(timezone.utc))}))
        return out, NewsLLMStats(scored=len(out) - n_err - n_budget, api_calls=len(out) - n_budget,
                                 errors=n_err, skipped_budget=n_budget)


def seed_old(cfg, tmp_path, es):
    """用 news-v1 版本入库（FakeExtractor 默认写 news-v1）。"""
    go(cfg, tmp_path, es)


def test_rescore_stale_off_by_default(cfg, tmp_path):
    es = [ent(1)]
    seed_old(cfg, tmp_path, es)
    ext = VerExtractor()
    s = go(cfg, tmp_path, es, extractor=ext)
    assert ext.calls == [] and s.n_rescored == 0
    assert NewsStore(tmp_path).load_all()[0].llm.prompt_version == "news-v1"


def test_rescore_stale_only_old_version_and_reappearing(cfg, tmp_path):
    es = [ent(1), ent(2, "Insitro unveils another fictional collaboration headline AI"),
          ent(3, "Genentech details fictional zebra quartz pipeline in AI drug discovery")]
    seed_old(cfg, tmp_path, es)
    # 3 号升到当前版本；2 号本次不再出现
    store = NewsStore(tmp_path)
    items = {i.url: i for i in store.load_all()}
    cur = items["https://fake.example/n/3"]
    store.upsert([cur.model_copy(update={"llm": cur.llm.model_copy(update={"prompt_version": "news-v6.1"})})])
    ext = VerExtractor()
    s = go(cfg, tmp_path, [es[0], es[2]], extractor=ext, rescore_stale=True)
    assert len(ext.calls) == 1 and len(ext.calls[0][0]) == 1
    assert (s.n_rescored, s.n_rescore_failed, s.n_rescore_skipped_budget) == (1, 0, 0)
    got = {i.url: i for i in NewsStore(tmp_path).load_all()}
    assert got["https://fake.example/n/1"].llm.prompt_version == "news-v6.1"
    assert got["https://fake.example/n/1"].relevance == 9
    assert got["https://fake.example/n/2"].llm.prompt_version == "news-v1"
    assert got["https://fake.example/n/3"].relevance == 8


def test_rescore_stale_failure_keeps_old_score(cfg, tmp_path):
    es = [ent(1)]
    seed_old(cfg, tmp_path, es)
    old = NewsStore(tmp_path).load_all()[0]
    ext = VerExtractor(fail_ids=[old.id])
    s = go(cfg, tmp_path, es, extractor=ext, rescore_stale=True)
    assert (s.n_rescored, s.n_rescore_failed) == (0, 1)
    now = NewsStore(tmp_path).load_all()[0]
    assert now.llm_status == "scored" and now.relevance == old.relevance
    assert now.llm.prompt_version == "news-v1" and now.summary_zh == old.summary_zh


def test_rescore_stale_independent_budget_and_not_limited(cfg, tmp_path):
    words = ["alpha harbor", "bridge lantern", "cobalt meadow", "dragon orchard"]
    es = [ent(i, f"{w} says Recursion plans {len(w) * i} pilot sites for AI drug discovery") for i, w in enumerate(words, 1)]
    seed_old(cfg, tmp_path, es)
    cfg.pipeline.news.rescore_stale_max_calls = 2
    new = ent(9, "Insitro unveils fictional brand new ninth headline for AI drug discovery")
    ext = VerExtractor()
    s = go(cfg, tmp_path, es + [new], extractor=ext, rescore_stale=True, limit=1)
    # 第一次调用：新候选，预算受 limit 约束；第二次：4 条旧版本全部传入（不受 --limit 截断），预算取独立配置
    assert len(ext.calls) == 2
    assert len(ext.calls[0][0]) == 1 and ext.calls[0][1] == 1
    assert len(ext.calls[1][0]) == 4 and ext.calls[1][1] == 2
    assert (s.n_rescored, s.n_rescore_skipped_budget) == (2, 2)
    assert s.llm.api_calls == 1 + 2  # 两次统计合并
    versions = sorted(i.llm.prompt_version for i in NewsStore(tmp_path).load_all() if i.llm)
    assert versions.count("news-v6.1") == 3 and versions.count("news-v1") == 2


def test_limit_rotates_sources(cfg, tmp_path):
    words = ["alpha harbor", "bridge lantern", "cobalt meadow", "dragon orchard"]
    a = [ent(i, f"{w} says Recursion plans {len(w) * i} pilot sites for AI drug discovery", source="A")
         for i, w in enumerate(words, 1)]
    b = ent(11, "Insitro beta fictional AI drug discovery collaboration", source="B")
    c = ent(12, "Genentech gamma fictional AI drug discovery dataset", source="C")
    ext = FakeExtractor()
    s = go(cfg, tmp_path, a + [b, c], limit=3, extractor=ext)
    got = {i.source for i in NewsStore(tmp_path).load_all()}
    assert got == {"A", "B", "C"} and s.n_skipped_limit == 3


def test_rotate_by_source_unit_and_no_limit_same_set():
    from aivc.models import NewsItem, make_news_id
    from aivc.news_pipeline import _rotate_by_source

    def mk(i, src, kw):
        return (NewsItem(id=make_news_id(f"https://x/{i}"), title=f"t{i}", url=f"https://x/{i}", source=src,
                         published=TODAY, first_seen=TODAY, keyword_score=kw), "")
    todo = [mk(1, "A", 5), mk(2, "A", 4), mk(3, "A", 3), mk(4, "B", 2), mk(5, "C", 1)]
    out = _rotate_by_source(todo)
    assert [c[0].source for c in out] == ["A", "B", "C", "A", "A"]
    assert {c[0].id for c in out} == {c[0].id for c in todo}


# ---------------------------------------------------------------- rescore_stored
from aivc.news_pipeline import STORED_SNIPPET_PREFIX, rescore_stored  # noqa: E402


class SnipExtractor(VerExtractor):
    """记录收到的片段。"""

    def extract(self, cands, max_calls=None, max_requests=None):
        self.snips = {c[0].id: c[1] for c in cands}
        out, st = super().extract(cands, max_calls=max_calls)
        self.max_requests = max_requests
        st.requests = st.api_calls
        return out, st


def seed_stored(cfg, tmp_path, es):
    seed_old(cfg, tmp_path, es)
    return {i.url: i for i in NewsStore(tmp_path).load_all()}


def test_rescore_stored_basic_and_new_not_stored(cfg, tmp_path):
    es = [ent(1), ent(2, "Insitro unveils another fictional collaboration headline AI")]
    got = seed_stored(cfg, tmp_path, es)
    stranger = ent(9, "Recursion stranger entry fictional never stored AI drug discovery")
    f = FakeFetcher([ent(1, snippet="新鲜片段 AI"), stranger])
    ext = SnipExtractor()
    s = rescore_stored(cfg, data_dir=tmp_path, today=TODAY, fetcher=f, extractor=ext)
    assert s.n_targets == 2 and s.n_refetched == 1 and s.n_stored_summary == 1
    assert s.n_rescored == 2 and s.n_failed == 0
    a = got["https://fake.example/n/1"]
    assert ext.snips[a.id] == "新鲜片段 AI"
    b = got["https://fake.example/n/2"]
    assert ext.snips[b.id].startswith(STORED_SNIPPET_PREFIX) and "虚构摘要" in ext.snips[b.id]
    now = NewsStore(tmp_path).load_all()
    assert len(now) == 2 and all(i.llm.prompt_version == "news-v6.1" and i.relevance == 9 for i in now)
    assert all(r[2] == 8 and r[3] == 9 for r in s.rows)
    assert ext.max_requests == cfg.pipeline.news.rescore_stale_max_calls


def test_rescore_stored_skips_current_and_undated(cfg, tmp_path):
    got = seed_stored(cfg, tmp_path, [ent(1), ent(2, "Insitro unveils another fictional collaboration headline AI")])
    store = NewsStore(tmp_path)
    a, b = got["https://fake.example/n/1"], got["https://fake.example/n/2"]
    store.upsert([a.model_copy(update={"llm": a.llm.model_copy(update={"prompt_version": "news-v6.1"})}),
                  b.model_copy(update={"published": None, "llm_status": "unscored",
                                       "llm_skip_reason": "undated_excluded"})])
    ext = SnipExtractor()
    s = rescore_stored(cfg, data_dir=tmp_path, today=TODAY, fetch=False, extractor=ext, include_unscored=True)
    assert s.n_targets == 0 and ext.calls == []


def test_rescore_stored_no_fetch_title_only_and_include_unscored(cfg, tmp_path):
    got = seed_stored(cfg, tmp_path, [ent(1), ent(2, "Insitro unveils another fictional collaboration headline AI")])
    store = NewsStore(tmp_path)
    b = got["https://fake.example/n/2"]
    store.upsert([b.model_copy(update={"llm_status": "unscored", "relevance": None, "summary_zh": None,
                                       "significance_zh": None, "llm": None})])
    f = FakeFetcher([])
    ext = SnipExtractor()
    s = rescore_stored(cfg, data_dir=tmp_path, today=TODAY, fetch=False, fetcher=f, extractor=ext, dry_run=True)
    assert f.calls == 0 and s.n_targets == 1       # 未打分的默认不处理
    ext = SnipExtractor()
    s = rescore_stored(cfg, data_dir=tmp_path, today=TODAY, fetch=False, extractor=ext, include_unscored=True)
    assert s.n_targets == 2 and s.n_title_only == 1 and ext.snips[b.id] == ""


def test_rescore_stored_failure_keeps_old(cfg, tmp_path):
    got = seed_stored(cfg, tmp_path, [ent(1)])
    old = got["https://fake.example/n/1"]
    ext = SnipExtractor(fail_ids=[old.id])
    s = rescore_stored(cfg, data_dir=tmp_path, today=TODAY, fetch=False, extractor=ext)
    assert (s.n_rescored, s.n_failed) == (0, 1) and s.rows[0][3] is None
    now = NewsStore(tmp_path).load_all()[0]
    assert now.relevance == old.relevance and now.llm.prompt_version == "news-v1"


def test_rescore_stored_ids_filter_and_unknown(cfg, tmp_path):
    got = seed_stored(cfg, tmp_path, [ent(1), ent(2, "Insitro unveils another fictional collaboration headline AI")])
    a = got["https://fake.example/n/1"]
    ext = SnipExtractor()
    s = rescore_stored(cfg, data_dir=tmp_path, today=TODAY, fetch=False, extractor=ext,
                       ids=[a.id, "nope"])
    assert s.n_targets == 1 and ext.calls[0][0] == [a.id]
    assert any("nope" in n for n in s.notes)


def test_rescore_stored_dry_run_no_llm_no_write(cfg, tmp_path):
    seed_stored(cfg, tmp_path, [ent(1)])
    before = (tmp_path / "news" / "2026-11.jsonl").read_bytes()

    class Boom:
        available = True

        def extract(self, *a, **k):
            raise AssertionError("must not call LLM")

    s = rescore_stored(cfg, data_dir=tmp_path, today=TODAY, dry_run=True, extractor=Boom(),
                       fetcher=FakeFetcher([ent(1, snippet="x AI")]))
    assert s.dry_run and s.n_targets == 1 and s.rows[0][4] == "refetched" and s.requests == 0
    assert (tmp_path / "news" / "2026-11.jsonl").read_bytes() == before


def test_rescore_stored_max_requests_passthrough_and_fetch_error(cfg, tmp_path):
    seed_stored(cfg, tmp_path, [ent(1)])
    ext = SnipExtractor()
    s = rescore_stored(cfg, data_dir=tmp_path, today=TODAY, extractor=ext, max_requests=5,
                       fetcher=FakeFetcher(exc=RuntimeError("boom")))
    assert ext.max_requests == 5 and s.n_rescored == 1
    assert any("抓取失败" in n for n in s.notes)
    assert s.n_stored_summary == 1


def test_undated_stored_item_gets_date_and_scored(cfg, tmp_path):
    """库内无日期且未打分的条目，再次抓到同 URL 且带日期（由文章页补日期）后，补上 published 并打分。"""
    from aivc.fetchers.news_dates import ArticleDateCache, extract_published_date

    url = "https://fake.example/n/900"
    title = "Recursion announces fictional virtual cell model in AI drug discovery"
    undated = NewsEntry(title=title, url=url, source="Fake Media", published=None, snippet="虚构片段 virtual cell")
    # 第一次：无日期，抓取器给出无日期条目 -> 入库为未打分、无日期
    go(cfg, tmp_path, [undated], extractor=FakeExtractor(available=False))
    items = NewsStore(tmp_path).load_all()
    assert len(items) == 1 and items[0].published is None and items[0].llm_status == "unscored"
    # 第二次：fake 抓取器内部用 fake 提取器（真实 extract_published_date）补日期
    page = '<meta property="article:published_time" content="2026-11-10T08:00:00Z">'
    cache = ArticleDateCache(tmp_path / "cache" / "news_dates.json")
    cache.put(url, extract_published_date(page, today=TODAY), today=TODAY)
    d = cache.get(url, today=TODAY)[1]
    assert d == date(2026, 11, 10)
    dated = undated.model_copy(update={"published": d})
    ex = FakeExtractor()
    go(cfg, tmp_path, [dated], extractor=ex)
    items = NewsStore(tmp_path).load_all()
    assert len(items) == 1
    assert items[0].published == date(2026, 11, 10) and items[0].llm_status == "scored"
    assert ex.calls
