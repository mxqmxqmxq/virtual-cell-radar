from pathlib import Path
from datetime import timedelta, date, datetime

import pytest

from aivc.config import Company, CompaniesConfig, load_config
from aivc.models import NewsItem, NewsLLM, NewsSourceRef, Paper, make_news_id
from aivc.news_site import NEWS_ITEM_KEYS, export_companies, export_news

GEN = "2026-11-15T00:00:00"


@pytest.fixture
def cfg(config_dir):
    c = load_config(config_dir)
    cos = [
        Company(id="zorbex", name="Zorbex Bio", aliases=["Zorbex"], url="https://zorbex.example",
                category="biotech", models=["ZetaNet"], paper_terms=["Zorbex Bio"], tier="top"),
        Company(id="quillon", name="Quillon", aliases=["QLN"], category="pharma"),
        Company(id="nolink", name="NoLink", paper_terms=[], models=[]),
    ]
    news = c.pipeline.news.model_copy(update={"site_min_relevance": 4})
    return c.model_copy(update={
        "companies": CompaniesConfig(companies=cos),
        "pipeline": c.pipeline.model_copy(update={"news": news}),
    })


def mk(n, *, scored=True, rel=6, pub=date(2026, 11, 10), et="funding", amt=None, cos=(), rnd=None, **kw):
    url = f"https://news.example/{n}"
    return NewsItem(
        id=make_news_id(url), title=f"T{n}", url=url, source="Src",
        sources=[NewsSourceRef(name="Src", url=url)], published=pub, companies=list(cos),
        event_type=et if scored else None, amount_usd=amt, round=rnd,
        relevance=rel if scored else None,
        llm=NewsLLM(model="m", prompt_version="news-v1", scored_at=datetime(2026, 11, 11)) if scored else None,
        llm_status="scored" if scored else "unscored", first_seen=date(2026, 11, 12), **kw,
    )


def test_threshold_and_unscored(cfg):
    items = [mk("a", rel=3), mk("b", rel=4), mk("c", scored=False)]
    out = export_news(items, cfg, generated_at=GEN)
    by = {r["title"]: r for r in out["items"]}
    assert set(by) == {"Tb", "Tc"}
    assert by["Tb"]["scored"] is True and by["Tc"]["scored"] is False
    assert out["count"] == 2 and out["generated_at"] == GEN


def test_sort_order(cfg):
    items = [mk("a", rel=5, pub=date(2026, 11, 9)), mk("b", rel=7, pub=date(2026, 11, 10)),
             mk("c", rel=9, pub=date(2026, 11, 10)), mk("d", rel=9, pub=date(2026, 11, 10)),
             mk("e", scored=False, pub=date(2026, 11, 12))]
    out = export_news(items, cfg, generated_at=GEN)
    titles = [r["title"] for r in out["items"]]
    c, d = sorted(["Tc", "Td"], key=lambda t: make_news_id(f"https://news.example/{t[1:]}"))
    assert titles == ["Te", c, d, "Tb", "Ta"]
    assert out["items"][0]["date"] == "2026-11-12"


def test_exact_keys_and_event_types(cfg):
    out = export_news([mk("a")], cfg, generated_at=GEN)
    assert set(out) == {"generated_at", "count", "counts", "min_relevance", "related_min_relevance", "event_types", "items", "weeks", "funding_quarters"}
    assert set(out["items"][0]) == set(NEWS_ITEM_KEYS)
    assert [e["id"] for e in out["event_types"]] == [
        "funding", "partnership", "m_and_a", "model_or_data_release", "clinical", "people", "other"]
    assert out["event_types"][0]["label"] == "融资"


def test_weeks_and_quarters(cfg):
    items = [mk("a", pub=date(2026, 11, 10)), mk("b", pub=date(2026, 11, 2)),
             mk("c", pub=date(2026, 11, 11)), mk("d", pub=date(2026, 9, 30))]
    out = export_news(items, cfg, generated_at=GEN)
    assert [(w["id"], w["count"]) for w in out["weeks"]] == [("2026-W46", 2), ("2026-W45", 1), ("2026-W40", 1)]
    w = out["weeks"][0]
    assert (w["start"], w["end"]) == ("2026-11-09", "2026-11-15")
    q = {r["quarter"] for r in out["items"]}
    assert q == {"2026Q4", "2026Q3"}


def test_funding_quarters(cfg):
    items = [
        mk("a", amt=1e8, cos=["Zorbex"]), mk("b", amt=5e7), mk("c", amt=None), mk("d", amt=2e7),
        mk("e", amt=3e7),
        mk("f", et="partnership", amt=9e9),            # 非融资不计
        mk("g", scored=False, amt=9e9),                # 未打分不计
        mk("h", amt=1e6, pub=date(2026, 7, 1)),
    ]
    out = export_news(items, cfg, generated_at=GEN)
    fq = {r["quarter"]: r for r in out["funding_quarters"]}
    assert [r["quarter"] for r in out["funding_quarters"]] == ["2026Q3", "2026Q4"]
    q4 = fq["2026Q4"]
    assert q4["total_usd"] == 1e8 + 5e7 + 2e7 + 3e7
    assert q4["n_events"] == 5 and q4["n_undisclosed"] == 1
    assert [t["amount_usd"] for t in q4["top"]] == [1e8, 5e7, 3e7]
    assert q4["top"][0]["companies"] == ["Zorbex"]
    assert set(q4["top"][0]) == {"id", "title", "amount_usd", "companies"}


def test_company_ids_and_news_attribution(cfg):
    items = [
        mk("a", cos=["zorbex bio"], amt=1e8, rnd="B", pub=date(2026, 11, 1)),
        mk("b", cos=["QLN", "Unknown"], et="partnership", pub=date(2026, 11, 8)),
        mk("c", cos=["Zorbex"], amt=None, pub=date(2026, 11, 9)),
        mk("d", cos=["Zorbex"], rel=2),          # 低相关度不收录
        mk("e", scored=False, cos=["Zorbex"]),   # 未打分不统计
    ]
    n = export_news(items, cfg, generated_at=GEN)
    by = {r["title"]: r for r in n["items"]}
    assert by["Tb"]["company_ids"] == ["quillon"] and by["Tb"]["companies"] == ["QLN", "Unknown"]
    comp = export_companies(items, [], cfg, generated_at=GEN)
    z = {c["id"]: c for c in comp["companies"]}["zorbex"]
    assert z["n_news"] == 2
    assert z["news_ids"] == [by["Tc"]["id"], by["Ta"]["id"]]  # 日期倒序
    assert z["funding_total_usd"] == 1e8
    assert z["funding_events"] == [{"id": by["Ta"]["id"], "date": "2026-11-01", "amount_usd": 1e8, "round": "B"}]
    assert z["last_event_date"] == "2026-11-09"
    assert set(comp) == {"generated_at", "categories", "companies", "paper_companies"}
    assert comp["categories"]["biotech"]


def _p(pid, title, abstract=None, aff=()):
    return Paper(id=pid, title=title, abstract=abstract, affiliations=list(aff), source="arxiv",
                 first_seen=date(2026, 11, 1))


def test_paper_links_terms_models_case_sensitive(cfg):
    papers = [
        _p("p1", "Scaling ZetaNet for cells"),
        _p("p2", "We thank Zorbex Bio for data"),
        _p("p3", "zetanet in lowercase", "and zorbex bio too"),     # 大小写不同不命中
        _p("p4", "About Zorbex only"),                              # 别名/名称片段不用
        _p("p5", "Intro", aff=["Zorbex Bio, Inc."]),
        _p("p6", "ZetaNetwork is different"),                       # 词边界
        _p("p7", "Quillon and NoLink and QLN"),                     # name/aliases 不参与
    ]
    comp = export_companies([], papers, cfg, generated_at=GEN)
    c = {x["id"]: x for x in comp["companies"]}
    assert sorted(c["zorbex"]["paper_ids"]) == ["p1", "p2", "p5"]
    assert c["zorbex"]["n_papers"] == 3
    assert c["quillon"]["paper_ids"] == [] and c["nolink"]["paper_ids"] == []
    assert comp["paper_companies"] == {"p1": ["zorbex"], "p2": ["zorbex"], "p5": ["zorbex"]}


def test_sentence_initial_common_word_not_linked():
    from aivc.config import load_config
    c = load_config(Path(__file__).resolve().parent.parent / "config")
    papers = [_p("s1", "State of the art in cell modeling"), _p("s2", "Cell-State-Dependent response"),
              _p("s3", "Evo of genes", "Evolution matters"), _p("s4", "Tahoe-100M atlas")]
    comp = export_companies([], papers, c, generated_at=GEN)
    arc = next(x for x in comp["companies"] if x["id"] == "arc-institute")
    assert arc["paper_ids"] == []


def test_empty_library(cfg):
    n = export_news([], cfg, generated_at=GEN)
    assert n["items"] == [] and n["weeks"] == [] and n["funding_quarters"] == [] and n["count"] == 0
    assert len(n["event_types"]) == 7
    c = export_companies([], [], cfg, generated_at=GEN)
    assert len(c["companies"]) == 3 and c["paper_companies"] == {}
    assert all(x["n_news"] == 0 and x["last_event_date"] is None and x["funding_total_usd"] == 0
               for x in c["companies"])


def test_rolling_cap_in_export(cfg):
    # 同一公司分布在 5 个不同自然周、30 天内共 5 条：滚动上限 4 条，丢掉相关度最低的
    items = [mk(f"r{i}", rel=5 + i, pub=date(2026, 10, 20) + timedelta(days=7 * i), cos=["Quillon"]) for i in range(5)]
    items.append(mk("other", rel=6, pub=date(2026, 10, 21), cos=["Zorbex"]))
    out = export_news(items, cfg, generated_at=GEN)
    quillon = [r for r in out["items"] if r["companies"] == ["Quillon"]]
    assert len(quillon) == 4 and "Tr0" not in {r["title"] for r in quillon}
    assert any(r["companies"] == ["Zorbex"] for r in out["items"])
    cfg.pipeline.news.max_per_company_per_30d = 0
    assert len([r for r in export_news(items, cfg, generated_at=GEN)["items"] if r["companies"] == ["Quillon"]]) == 5


def test_undated_items_excluded_from_export_and_funding(cfg):
    items = [mk("a", amt=5_000_000), mk("b", pub=None, amt=9_000_000)]
    out = export_news(items, cfg, generated_at=GEN)
    assert [r["title"] for r in out["items"]] == ["Ta"]
    assert sum(q["total_usd"] for q in out["funding_quarters"]) == 5_000_000


@pytest.fixture
def tcfg(cfg):
    """site_min=6、related_min=5 的分层配置；公司上限放宽，单测按需调整。"""
    news = cfg.pipeline.news.model_copy(update={"site_min_relevance": 6, "site_related_min_relevance": 5})
    return cfg.model_copy(update={"pipeline": cfg.pipeline.model_copy(update={"news": news})})


def test_tier_counts_and_unscored(tcfg):
    items = [mk("a", rel=7), mk("b", rel=6), mk("c", rel=5), mk("d", rel=4), mk("e", scored=False)]
    out = export_news(items, tcfg, generated_at=GEN)
    tiers = {r["title"]: r["tier"] for r in out["items"]}
    assert tiers == {"Ta": "core", "Tb": "core", "Tc": "related", "Te": None}
    assert out["counts"] == {"core": 2, "related": 1, "unscored": 1}
    assert out["count"] == 4
    assert out["min_relevance"] == 6 and out["related_min_relevance"] == 5
    assert all(set(r) == set(NEWS_ITEM_KEYS) for r in out["items"])


def test_no_related_tier_when_related_min_not_below_site_min(cfg):
    news = cfg.pipeline.news.model_copy(update={"site_min_relevance": 6, "site_related_min_relevance": 6})
    c6 = cfg.model_copy(update={"pipeline": cfg.pipeline.model_copy(update={"news": news})})
    out = export_news([mk("a", rel=6), mk("b", rel=5)], c6, generated_at=GEN)
    assert [r["title"] for r in out["items"]] == ["Ta"] and out["counts"]["related"] == 0
    news = news.model_copy(update={"site_related_min_relevance": 8})  # 大于 site_min 时取 min
    c8 = cfg.model_copy(update={"pipeline": cfg.pipeline.model_copy(update={"news": news})})
    out = export_news([mk("a", rel=6), mk("b", rel=5)], c8, generated_at=GEN)
    assert out["related_min_relevance"] == 6 and out["counts"] == {"core": 1, "related": 0, "unscored": 0}


def test_weeks_and_funding_exclude_related(tcfg):
    items = [mk("a", rel=6, amt=1_000_000, pub=date(2026, 11, 10)),
             mk("b", rel=5, amt=9_000_000, pub=date(2026, 11, 10)),
             mk("c", rel=5, pub=date(2026, 10, 1))]
    out = export_news(items, tcfg, generated_at=GEN)
    assert len(out["items"]) == 3
    assert [(w["id"], w["count"]) for w in out["weeks"]] == [("2026-W46", 1)]
    assert sum(q["total_usd"] for q in out["funding_quarters"]) == 1_000_000
    assert sum(q["n_events"] for q in out["funding_quarters"]) == 1


def test_companies_only_count_core(tcfg):
    items = [mk("a", rel=6, cos=["Zorbex"], amt=1_000_000), mk("b", rel=5, cos=["Zorbex"], amt=5_000_000)]
    co = {c["id"]: c for c in export_companies(items, [], tcfg, generated_at=GEN)["companies"]}["zorbex"]
    assert co["n_news"] == 1 and co["news_ids"] == [items[0].id]
    assert co["funding_total_usd"] == 1_000_000 and len(co["funding_events"]) == 1


def test_company_caps_keep_core_over_related(tcfg):
    news = tcfg.pipeline.news.model_copy(update={"max_per_company_per_week": 3, "max_per_company_per_30d": 0})
    c = tcfg.model_copy(update={"pipeline": tcfg.pipeline.model_copy(update={"news": news})})
    items = [mk(f"c{i}", rel=6, cos=["Zorbex"]) for i in range(3)] + \
            [mk(f"r{i}", rel=5, cos=["Zorbex"], pub=date(2026, 11, 11)) for i in range(2)]
    out = export_news(items, c, generated_at=GEN)
    assert sorted(r["title"] for r in out["items"]) == ["Tc0", "Tc1", "Tc2"]
    assert {r["tier"] for r in out["items"]} == {"core"}
    # 滚动上限同样优先保留核心
    news = news.model_copy(update={"max_per_company_per_week": 0, "max_per_company_per_30d": 3})
    c = tcfg.model_copy(update={"pipeline": tcfg.pipeline.model_copy(update={"news": news})})
    out = export_news(items, c, generated_at=GEN)
    assert sorted(r["title"] for r in out["items"]) == ["Tc0", "Tc1", "Tc2"]
