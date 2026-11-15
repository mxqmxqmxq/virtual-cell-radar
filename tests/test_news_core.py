from datetime import timedelta, date, datetime

import pytest

from aivc.config import Company, CompaniesConfig, MajorRule, load_config
from aivc.models import NewsEntry, NewsItem, NewsLLM, NewsSourceRef, make_news_id
from aivc.news import (
    NewsDedupResult, NewsStore, apply_major, canonical_companies, dedup_candidates,
    event_date, match_companies, merge_events, to_candidates,
)

TODAY = date(2026, 11, 10)


@pytest.fixture
def cfg(config_dir):
    c = load_config(config_dir)
    cos = [
        Company(name="Zorbex Bio", aliases=["Zorbex"], tier="top"),
        Company(name="Quillon", aliases=["QLN"], tier="normal"),
        Company(name="Arcade Labs", aliases=["Arc"], tier="normal"),
        Company(name="灵枢生物", tier="normal"),
    ]
    news = c.pipeline.news.model_copy(update={
        "keywords": ["virtual cell", "foundation model"], "title_threshold": 90, "merge_window_days": 5,
    })
    return c.model_copy(update={
        "companies": CompaniesConfig(companies=cos),
        "pipeline": c.pipeline.model_copy(update={"news": news}),
    })


def mk(title="T", url="https://a.example/x", *, scored=False, cos=(), et=None, amt=None, rel=5,
       pub=date(2026, 11, 8), source="Src", investors=(), rnd=None, srcs=None):
    return NewsItem(
        id=make_news_id(url), title=title, url=url, source=source,
        sources=srcs if srcs is not None else [NewsSourceRef(name=source, url=url)],
        published=pub, companies=list(cos), event_type=et, amount_usd=amt, round=rnd,
        investors=list(investors), relevance=rel if scored else None,
        llm=NewsLLM(model="m", prompt_version="news-v1", scored_at=datetime(2026, 11, 9)) if scored else None,
        llm_status="scored" if scored else "unscored", first_seen=TODAY,
    )


# ---- match_companies / canonical
def test_match_companies_basic_and_boundary(cfg):
    assert match_companies("Zorbex Bio raises money", cfg) == ["Zorbex Bio"]
    assert match_companies("zorbex bio raises", cfg) == ["Zorbex Bio"]  # 长名不区分大小写
    assert match_companies("Zorbexian stuff", cfg) == []


def test_short_alias_case_sensitive(cfg):
    assert match_companies("the Arc of history", cfg) == ["Arcade Labs"]  # 短别名区分大小写，此处按约定命中
    assert match_companies("an arc of light", cfg) == []
    assert match_companies("QLN signs deal", cfg) == ["Quillon"]
    assert match_companies("qln signs deal", cfg) == []
    assert match_companies("Arcane things", cfg) == []


def test_match_chinese_substring(cfg):
    assert match_companies("灵枢生物完成B轮融资", cfg) == ["灵枢生物"]
    assert match_companies("无关新闻", cfg) == []


def test_canonical_companies(cfg):
    assert canonical_companies([" zorbex ", "ZORBEX BIO", "Unknown Co", "Unknown Co", "qln"], cfg) == [
        "Zorbex Bio", "Unknown Co", "Quillon"]
    assert canonical_companies([], cfg) == []


def test_canonical_companies_parenthesized_and_reversed(cfg):
    # LLM 把括号内外的名称写反/拼接时，应回查到规范名并去重
    assert canonical_companies(["灵枢生物 (Zorbex)", "Zorbex Bio (灵枢生物)", "Foo (Bar)"], cfg)[-1] == "Foo (Bar)"
    out = canonical_companies(["Zorbex Bio (灵枢生物)", "zorbex"], cfg)
    assert len(out) == 1


# ---- to_candidates
def E(title, **kw):
    d = dict(title=title, url="https://m.example/" + title.replace(" ", "-"), source="Media")
    d.update(kw)
    return NewsEntry(**d)


def test_to_candidates_filter_and_score(cfg):
    ents = [
        E("Zorbex Bio unveils a virtual cell model", snippet="a foundation model too"),
        E("Weather today"),
        E("Generic virtual cell news"),
    ]
    out = to_candidates(ents, cfg, TODAY)
    assert [i.title for i, _ in out] == ["Zorbex Bio unveils a virtual cell model", "Generic virtual cell news"]
    it, snip = out[0]
    # 2 个关键词 + 1 条组合规则（foundation model + cell）+ 公司名 ×2
    assert it.keyword_score == 3 + 2 and it.companies == ["Zorbex Bio"]
    assert set(it.matched_terms) == {"virtual cell", "foundation model", "combo:ai+bio", "Zorbex Bio"}
    assert it.llm_status == "unscored" and it.first_seen == TODAY and snip == "a foundation model too"
    assert it.id == make_news_id(it.url) and it.sources[0].url == it.url


def test_to_candidates_company_feed_requires_match(cfg):
    # 公司自家 feed 的公关/行政稿不再无条件放行
    pr = E("Office move announced", feed_kind="company", feed_company="qln")
    assert to_candidates([pr], cfg, TODAY) == []
    # 命中组合规则（AI + 药物）才入库，并预填自家公司
    hit = E("Quillon partners to bring AI to drug development", feed_kind="company", feed_company="qln")
    out = to_candidates([hit], cfg, TODAY)
    assert len(out) == 1 and out[0][0].companies == ["Quillon"] and "combo:ai+bio" in out[0][0].matched_terms
    # 非公司源同样无关内容被丢弃
    assert to_candidates([E("Office move announced")], cfg, TODAY) == []


def test_to_candidates_company_feed_switch_off(cfg):
    cfg.pipeline.news.company_feed_requires_match = False
    pr = E("Office move announced", feed_kind="company", feed_company="qln")
    assert len(to_candidates([pr], cfg, TODAY)) == 1


@pytest.mark.parametrize("title", [
    "Proximal Ventures raises debut fund to back startups bringing AI to drug development",
    "Former OpenAI science lead launches Warp Science to automate wet labs with robots",
    "The biggest biotech funding rounds in October 2026",
    "皮尔法伯集团与晶泰科技宣布达成一项AI驱动药物发现的合作",
])
def test_combo_rules_catch_real_phrasings(cfg, title):
    assert len(to_candidates([E(title)], cfg, TODAY)) == 1, title


@pytest.mark.parametrize("title", [
    "Senators introduce bill to curb conflicts of interest on preventive care panel",
    "TechCrunch Founder Summit agenda is only half the reason to be in Boston",
    "From 1985 to Today: The Evolution of the Patient Foundation",
])
def test_combo_rules_skip_irrelevant(cfg, title):
    assert to_candidates([E(title)], cfg, TODAY) == [], title


def test_cap_per_company_week():
    from aivc.news import cap_per_company_week
    items = [mk(f"t{i}", f"https://a.example/{i}", scored=True, cos=["Quillon"], rel=r)
             for i, r in enumerate([5, 9, 7, 8])]
    kept = cap_per_company_week(items, 2)
    assert sorted(x.relevance for x in kept) == [8, 9]
    assert cap_per_company_week(items, 0) == items


def _rk(n, d, rel, co="Quillon"):
    return mk(f"t{n}", f"https://a.example/r{n}", scored=True, cos=[co] if co else [], rel=rel, pub=d)


def test_cap_rolling_across_weeks_within_30d():
    from aivc.news import cap_per_company_rolling
    items = [_rk(i, date(2026, 10, 1) + timedelta(days=7 * i), 5 + i) for i in range(6)]  # 间隔一周，共跨 35 天
    kept = cap_per_company_rolling(items, 4)
    # 日期为第 0/7/…/35 天。相关度 10/9/8/7（第 35/28/21/14 天）先保留；
    # 第 7 天（rel 6）会使 [7,36] 窗口内达 5 条而被丢弃；第 0 天（rel 5）的最大窗口只有 4 条，可保留
    assert [x.relevance for x in kept] == [5, 7, 8, 9, 10]
    assert kept == [x for x in items if x in kept]  # 保持输入顺序


def test_cap_rolling_gap_over_30d_not_capped():
    from aivc.news import cap_per_company_rolling
    items = [_rk(i, date(2026, 8, 1) + timedelta(days=d), 6) for i, d in enumerate([0, 1, 2, 60, 61, 62])]
    assert len(cap_per_company_rolling(items, 3)) == 6
    # 恰好相隔 30 天：窗口为 [d, d+29]，不算同一窗口
    items = [_rk(i, date(2026, 8, 1) + timedelta(days=d), 6) for i, d in enumerate([0, 30])]
    assert len(cap_per_company_rolling(items, 1)) == 2
    items = [_rk(i, date(2026, 8, 1) + timedelta(days=d), 6) for i, d in enumerate([0, 29])]
    assert len(cap_per_company_rolling(items, 1)) == 1


def test_cap_rolling_prefers_relevance_and_unlimited_and_no_company():
    from aivc.news import cap_per_company_rolling
    items = [_rk(i, date(2026, 11, 1), r) for i, r in enumerate([5, 9, 7, 8])]
    assert sorted(x.relevance for x in cap_per_company_rolling(items, 2)) == [8, 9]
    assert cap_per_company_rolling(items, 0) == items
    assert cap_per_company_rolling(items, -1) == items
    nocos = [_rk(i, date(2026, 11, 1), 6, co=None) for i in range(6)]
    assert cap_per_company_rolling(nocos, 1) == nocos


def test_interleave_by_company():
    from aivc.news import interleave_by_company
    mkc = lambda i, rel, co: _rk(i, date(2026, 11, 1), rel, co=co)
    xs = [mkc(1, 7, "A"), mkc(2, 7, "A"), mkc(3, 7, "A"), mkc(4, 7, "B"), mkc(5, 7, None), mkc(6, 7, None),
          mkc(7, 5, "C"), mkc(8, 5, "C")]
    key = lambda x: x.relevance
    out = interleave_by_company(xs, key)
    assert [x.title for x in out] == ["t1", "t4", "t5", "t6", "t2", "t3", "t7", "t8"]
    # 不同分数区段间顺序不变；全部不同分时原样返回
    assert interleave_by_company(xs, lambda x: x.title) == xs
    assert interleave_by_company([], key) == []
    # 稳定：重复调用结果一致
    assert interleave_by_company(out, key) == out


# ---- dedup_candidates
def test_dedup_same_url_tracking_params(cfg):
    ex = mk("Zorbex funding", "https://a.example/p")
    c = mk("Totally different words", "https://A.example/p/?utm_source=x#top", source="Other")
    r = dedup_candidates([(c, "s")], [ex], cfg)
    assert isinstance(r, NewsDedupResult)
    assert r.new == [] and r.n_duplicates == 1
    assert r.updated == []  # 同一 URL，来源无新增


def test_dedup_matches_existing_secondary_source_url(cfg):
    ex = mk("Zorbex funding", "https://a.example/p",
            srcs=[NewsSourceRef(name="Src", url="https://a.example/p"), NewsSourceRef(name="B", url="https://b.example/q")])
    c = mk("Whatever", "https://b.example/q?fbclid=1", source="B")
    r = dedup_candidates([(c, "")], [ex], cfg)
    assert r.new == [] and r.n_duplicates == 1 and r.updated == []  # 来源已有，无变化


def test_dedup_title_fuzzy_and_short_guard(cfg):
    ex = mk("Zorbex Bio raises 100 million dollars in Series B funding", "https://a.example/1")
    c = mk("Zorbex Bio raises 100 million dollars in Series B funding!", "https://z.example/2", source="Z")
    r = dedup_candidates([(c, "")], [ex], cfg)
    assert r.n_duplicates == 1 and len(r.updated[0].sources) == 2
    # 短标题：只接受完全相等
    a = mk("Zorbex hires CEO", "https://a.example/3")
    b = mk("Zorbex hires CFO", "https://b.example/4")
    assert dedup_candidates([(b, "")], [a], cfg).n_duplicates == 0
    b2 = mk("zorbex hires ceo!", "https://b.example/5")
    assert dedup_candidates([(b2, "")], [a], cfg).n_duplicates == 1


def test_dedup_in_batch_merge_keeps_order_and_earlier_date(cfg):
    t = "Quillon launches a very large foundation model today"
    c1 = mk(t, "https://a.example/1", source="A", pub=date(2026, 11, 9))
    c2 = mk(t, "https://b.example/2", source="B", pub=date(2026, 11, 7))
    c3 = mk(t, "https://c.example/3?utm_x=1", source="C", pub=None)
    c4 = mk("Unrelated article about something else entirely", "https://d.example/4")
    r = dedup_candidates([(c1, "s1"), (c2, "s2"), (c3, "s3"), (c4, "s4")], [], cfg)
    assert r.n_duplicates == 2 and len(r.new) == 2
    first, snip = r.new[0]
    assert snip == "s1" and [s.name for s in first.sources] == ["A", "B", "C"]
    assert first.published == date(2026, 11, 7) and r.updated == []


# ---- merge_events
def F(url, **kw):
    base = dict(scored=True, cos=["Zorbex Bio"], et="funding", amt=1e8, title="Zorbex Bio raises 100M",
                pub=date(2026, 11, 8))
    base.update(kw)
    return mk(url=url, **base)


def test_merge_events_positive_and_primary_by_relevance(cfg):
    a = F("https://a.example/1", rel=5, source="A", investors=["X"])
    b = F("https://b.example/2", rel=8, source="B", investors=["Y", "X"], rnd="Series B", amt=1.05e8)
    kept, mods = merge_events([a, b], [], cfg)
    assert mods == [] and len(kept) == 1
    m = kept[0]
    assert m.url == b.url and [s.name for s in m.sources] == ["B", "A"]
    assert m.investors == ["Y", "X"] and m.round == "Series B"


def test_merge_events_ties_keep_earlier(cfg):
    a = F("https://a.example/1", rel=5, pub=date(2026, 11, 8))
    b = F("https://b.example/2", rel=5, pub=date(2026, 11, 6))
    kept, _ = merge_events([a, b], [], cfg)
    assert len(kept) == 1 and kept[0].url == b.url


def test_merge_events_fills_missing_fields(cfg):
    a = F("https://a.example/1", rel=9, amt=None)
    b = F("https://b.example/2", rel=3, amt=2e8, rnd="A")
    kept, _ = merge_events([a, b], [], cfg)
    assert kept[0].amount_usd == 2e8 and kept[0].round == "A" and kept[0].url == a.url


def test_merge_events_into_existing(cfg):
    ex = F("https://a.example/1", source="A")
    new = F("https://b.example/2", source="B", rel=9)
    kept, mods = merge_events([new], [ex], cfg)
    assert kept == [] and len(mods) == 1 and mods[0].id == ex.id
    assert [s.name for s in mods[0].sources] == ["A", "B"]


@pytest.mark.parametrize("kw", [
    dict(et="partnership"),                      # 类型不同
    dict(cos=["Quillon"]),                       # 公司无交集
    dict(pub=date(2026, 11, 20)),                # 日期超窗
    dict(amt=5e8),                               # 金额不相容
])
def test_merge_events_counterexamples(cfg, kw):
    a = F("https://a.example/1")
    b = F("https://b.example/2", **kw)
    kept, mods = merge_events([a, b], [], cfg)
    assert len(kept) == 2 and mods == []


def test_merge_events_amount_none_compatible_and_boundary(cfg):
    a = F("https://a.example/1", amt=None)
    assert len(merge_events([a, F("https://b.example/2", amt=3e8)], [], cfg)[0]) == 1
    a = F("https://a.example/1", amt=1e8)
    assert len(merge_events([a, F("https://b.example/2", amt=0.9e8)], [], cfg)[0]) == 1  # 恰好 10%
    # 日期恰好 5 天仍合并
    c = F("https://c.example/3", pub=date(2026, 11, 3))
    assert len(merge_events([F("https://a.example/1"), c], [], cfg)[0]) == 1


def test_merge_events_title_condition_for_non_amount_types(cfg):
    a = mk("Zorbex Bio partners with Quillon on cell atlas", "https://a.example/1", scored=True,
           cos=["Zorbex Bio"], et="partnership")
    b = mk("Quarterly weather report for farmers", "https://b.example/2", scored=True,
           cos=["Zorbex Bio"], et="partnership")
    assert len(merge_events([a, b], [], cfg)[0]) == 2
    c = mk("Zorbex Bio and Quillon announce partnership on cell atlas", "https://c.example/3",
           scored=True, cos=["Zorbex Bio"], et="partnership")
    assert len(merge_events([a, c], [], cfg)[0]) == 1


def test_merge_events_ignores_unscored_and_unmergeable_types(cfg):
    a = F("https://a.example/1", scored=False)
    b = F("https://b.example/2", scored=False)
    assert len(merge_events([a, b], [], cfg)[0]) == 2
    p1 = F("https://a.example/1", et="people", title="Zorbex Bio names CEO")
    p2 = F("https://b.example/2", et="people", title="Zorbex Bio names CEO")
    assert len(merge_events([p1, p2], [], cfg)[0]) == 2


# ---- apply_major
def test_apply_major_default_rules(cfg):
    big = apply_major(F("https://a.example/1", amt=1.5e8), cfg)
    assert big.major and big.major_reasons == ["融资 ≥ 1 亿美元"]
    small = apply_major(F("https://a.example/1", amt=5e7, cos=["Quillon"]), cfg)
    assert not small.major and small.major_reasons == []
    rel = apply_major(mk("m", "https://a.example/2", scored=True, cos=["Zorbex Bio"], et="model_or_data_release"), cfg)
    assert rel.major and "头部" in rel.major_reasons[0]
    non_top = apply_major(mk("m", "https://a.example/3", scored=True, cos=["Quillon"], et="model_or_data_release"), cfg)
    assert not non_top.major


def test_apply_major_unscored_never_hits_and_min_relevance(cfg):
    assert not apply_major(F("https://a.example/1", scored=False, amt=9e9), cfg).major
    rule = MajorRule(id="r", label="高相关", min_relevance=8)
    c2 = cfg.model_copy(update={"pipeline": cfg.pipeline.model_copy(update={
        "news": cfg.pipeline.news.model_copy(update={"major": [rule]})})})
    assert apply_major(F("https://a.example/1", rel=8), c2).major
    assert not apply_major(F("https://a.example/1", rel=7), c2).major
    # 重新判定会清除旧标记
    old = F("https://a.example/1", rel=7).model_copy(update={"major": True, "major_reasons": ["x"]})
    assert not apply_major(old, c2).major


def test_event_date():
    assert event_date(mk(pub=date(2026, 1, 2))) == date(2026, 1, 2)
    assert event_date(mk(pub=None)) == TODAY


# ---- NewsStore
def test_store_sharding_idempotent_delete(tmp_path):
    s = NewsStore(tmp_path)
    assert s.load_all() == []
    a = mk("A", "https://a.example/1", pub=date(2026, 10, 30))
    b = mk("B", "https://a.example/2", pub=date(2026, 11, 2))
    c = mk("C", "https://a.example/3", pub=None)  # first_seen 2026-11-10
    assert s.upsert([a, b, c]) == (3, 0)
    assert sorted(p.name for p in (tmp_path / "news").glob("*.jsonl")) == ["2026-10.jsonl", "2026-11.jsonl"]
    assert s.upsert([a, b, c]) == (0, 0)
    a2 = a.model_copy(update={"published": date(2026, 11, 5), "summary_zh": "更新"})
    assert s.upsert([a2]) == (0, 1)  # 原地更新，不换文件
    assert (tmp_path / "news" / "2026-10.jsonl").exists()
    assert {x.id: x for x in s.load_all()}[a.id].summary_zh == "更新"
    assert not list((tmp_path / "news").glob("*.tmp"))
    assert s.delete([b.id, "nope"]) == 1
    assert {x.id for x in s.load_all()} == {a.id, c.id}
