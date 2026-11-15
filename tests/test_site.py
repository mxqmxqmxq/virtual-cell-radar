import json
from datetime import date, datetime, timedelta

import pytest

from aivc.config import load_config
from aivc.models import LLMAnnotation, Paper
from aivc.site import build_site, make_server, week_id
from aivc.storage import PaperStore


def _llm(**kw):
    base = dict(
        tasks=["perturbation_prediction"], paradigms=["foundation_model"], datasets_used=["Norman", " norman ", "Replogle"],
        releases_new_dataset=False, new_dataset_names=[], circle="core", relevance=9,
        novelty_zh="新颖", summary_zh="摘要", model="m", prompt_version="v1",
        scored_at=datetime(2026, 11, 1, 12, 0),
    )
    base.update(kw)
    return LLMAnnotation(**base)


@pytest.fixture
def env(tmp_path, config_dir):
    cfg = load_config(config_dir)
    data = tmp_path / "data"
    papers = [
        Paper(id="p1", title="Scored A", source="arxiv", published=date(2026, 11, 10), first_seen=date(2026, 11, 11),
              url="https://x/1", llm_status="scored", llm=_llm(), final_score=8.5, must_read=True, circle="peripheral"),
        Paper(id="p2", title="Scored B <b>", source="biorxiv", published=date(2026, 10, 30), first_seen=date(2026, 11, 2),
              llm_status="scored", llm=_llm(releases_new_dataset=True, new_dataset_names=["NewSet"], datasets_used=["Norman"]),
              final_score=6.0),
        Paper(id="p3", title="Unscored", source="openreview", first_seen=date(2026, 12, 31), keyword_score=3.0,
              circle="core", local_file="rel/a.pdf"),
        Paper(id="p4", title="Scored no names", source="arxiv", published=date(2026, 9, 1), first_seen=date(2026, 11, 12),
              llm_status="scored", llm=_llm(releases_new_dataset=True, new_dataset_names=[], datasets_used=[]), final_score=7.0),
    ]
    PaperStore(data).upsert(papers)
    reports = tmp_path / "reports"
    (reports / "weekly").mkdir(parents=True)
    (reports / "weekly" / "2026-W45.md").write_text(
        "# AIVC 周报 · 2026-W45（2026-11-02 ~ 2026-11-08）\n\n- **a**\n\n<script>x</script>\n", encoding="utf-8")
    (reports / "weekly" / "2026-W46.md").write_text("# W46\n", encoding="utf-8")
    (reports / "weekly" / "notes.md").write_text("# no\n", encoding="utf-8")
    assets = tmp_path / "assets_src"
    (assets / "assets").mkdir(parents=True)
    (assets / "index.html").write_text("<html></html>", encoding="utf-8")
    (assets / "assets" / "app.js").write_text("//", encoding="utf-8")
    return cfg, data, reports, assets, tmp_path / "site"


def _build(env, **kw):
    cfg, data, reports, assets, site = env
    return build_site(cfg, data_dir=data, reports_dir=reports, site_dir=site, today=date(2026, 11, 12),
                      assets_dir=assets, **kw)


def _j(site, name):
    return json.loads((site / "data" / name).read_text(encoding="utf-8"))


def test_papers_json(env):
    res = _build(env)
    site = env[4]
    assert res.n_papers == 4 and res.n_weekly == 2
    d = _j(site, "papers.json")
    assert d["count"] == 4 and len(d["papers"]) == 4 and "generated_at" in d
    by = {p["id"]: p for p in d["papers"]}
    keys = {"id", "title", "abstract", "authors", "source", "venue", "published", "first_seen", "date", "month", "week",
            "url", "pdf_url", "code_url", "doi", "license", "categories", "keywords", "tags", "matched_keywords",
            "keyword_score", "circle", "scored", "llm_status", "final_score", "must_read", "relevance",
            "tasks", "paradigms", "datasets_used", "releases_new_dataset", "new_dataset_names", "novelty_zh", "summary_zh"}
    assert all(set(p) == keys for p in d["papers"])
    p1, p3 = by["p1"], by["p3"]
    assert p1["scored"] is True and p1["circle"] == "core" and p1["week"] == "2026-W46"
    assert p1["date"] == "2026-11-10" and p1["month"] == "2026-11" and p1["tasks"] == ["perturbation_prediction"]
    assert p3["scored"] is False and p3["date"] == "2026-12-31" and p3["published"] is None
    assert p3["week"] == "2026-W53" and p3["circle"] == "core"
    assert p3["tasks"] == [] and p3["releases_new_dataset"] is False and p3["novelty_zh"] == ""
    assert p3["final_score"] is None and p3["relevance"] is None and "local_file" not in p3
    assert all("license" in p and p["license"] is None for p in d["papers"])
    assert week_id(date(2027, 1, 1)) == "2026-W53"


def test_papers_json_include_local(env):
    _build(env, include_local=True)
    by = {p["id"]: p for p in _j(env[4], "papers.json")["papers"]}
    assert by["p3"]["local_file"] == "rel/a.pdf" and by["p1"]["local_file"] is None
    assert all("highlight" not in p for p in by.values())


def test_topics_json(env):
    _build(env)
    d = _j(env[4], "topics.json")
    assert d["version"] == 1 and isinstance(d["presets"], list) and len(d["presets"]) == len(env[0].topics.topics)
    for pr in d["presets"]:
        assert set(pr) == {"id", "name", "terms", "default_enabled"}


def test_topics_json_empty_and_param(env):
    _build(env, topics=[])
    assert _j(env[4], "topics.json") == {"version": 1, "presets": []}
    from aivc.topics import Topic
    _build(env, topics=[Topic(id="t-1", name="T", terms=["abc", "x*y"], default_enabled=True)])
    assert _j(env[4], "topics.json")["presets"] == [
        {"id": "t-1", "name": "T", "terms": ["abc", "x*y"], "default_enabled": True}]


def test_build_site_papers_param(env):
    only = [Paper(id="z1", title="Only", source="arxiv", first_seen=date(2026, 11, 11))]
    res = _build(env, papers=only)
    assert res.n_papers == 1
    assert [p["id"] for p in _j(env[4], "papers.json")["papers"]] == ["z1"]


def test_meta_and_datasets(env):
    _build(env)
    site = env[4]
    m = _j(site, "meta.json")
    assert m["current_week"] == {"id": "2026-W46", "start": "2026-11-09", "end": "2026-11-15"}
    s = m["stats"]
    assert (s["total"], s["scored"], s["unscored"], s["must_read"], s["new_datasets"]) == (4, 3, 1, 1, 2)
    assert s["this_week_new"] == 2 and s["this_week_must_read"] == 1
    assert s["by_source"] == {"arxiv": 2, "biorxiv": 1, "openreview": 1}
    assert s["by_circle"] == {"core": 4}
    assert m["latest_weekly"]["id"] == "2026-W46"
    assert m["months"] == sorted(m["months"]) and "2026-09" in m["months"]
    assert m["taxonomy"]["tasks"] and {"id", "label"} == set(m["taxonomy"]["tasks"][0])
    assert m["circle_labels"]["core"] == "核心圈" and m["must_read_threshold"] == env[0].llm.must_read_threshold
    ds = _j(site, "datasets.json")
    names = [r["name"] for r in ds["new_datasets"]]
    assert sorted(names, key=str) == sorted([None, "NewSet"], key=str)
    assert [r["date"] for r in ds["new_datasets"]] == sorted((r["date"] for r in ds["new_datasets"]), reverse=True)
    top = {r["name"]: r for r in ds["used_top"]}
    assert top["Norman"]["count"] == 2 and set(top["Norman"]["paper_ids"]) == {"p1", "p2"}
    assert top["Replogle"]["count"] == 1
    assert len([r for r in ds["used_top"] if r["name"].casefold() == "norman"]) == 1


def test_weekly_json(env):
    _build(env)
    w = _j(env[4], "weekly.json")["reports"]
    assert [r["id"] for r in w] == ["2026-W46", "2026-W45"]
    r45 = w[1]
    assert r45["start"] == "2026-11-02" and r45["end"] == "2026-11-08"
    assert r45["title"].startswith("AIVC 周报")
    assert "<li><strong>a</strong></li>" in r45["html"] and "<script" not in r45["html"]


def test_no_weekly_dir(env, tmp_path):
    cfg, data, _, assets, site = env
    build_site(cfg, data_dir=data, reports_dir=tmp_path / "nope", site_dir=site, today=date(2026, 11, 12), assets_dir=assets)
    assert _j(site, "weekly.json") == {"reports": []}
    assert _j(site, "meta.json")["latest_weekly"] is None


def test_assets_copied_and_unrelated_kept_and_rebuild(env):
    site = env[4]
    site.mkdir()
    (site / "keep.txt").write_text("mine", encoding="utf-8")
    _build(env)
    res = _build(env)
    assert (site / "index.html").is_file() and (site / "assets" / "app.js").is_file()
    assert (site / "keep.txt").read_text(encoding="utf-8") == "mine"
    assert site / "data" / "papers.json" in res.files
    assert not list(site.rglob("*.tmp"))


def test_missing_assets(env, tmp_path):
    cfg, data, reports, _, site = env
    with pytest.raises(FileNotFoundError, match="静态资源"):
        build_site(cfg, data_dir=data, reports_dir=reports, site_dir=site, assets_dir=tmp_path / "missing")


def test_make_server(tmp_path):
    srv = make_server(tmp_path, port=0)
    try:
        assert srv.server_address[0] == "127.0.0.1" and srv.server_address[1] > 0
    finally:
        srv.server_close()


# ---------- home.json ----------
from aivc.site import _plain_lines, build_home  # noqa: E402

TODAY = date(2026, 11, 12)  # ISO week 2026-W46 (Mon 2026-11-09)


def _tax(cfg):
    return cfg.scope.taxonomy.paradigms, cfg.scope.taxonomy.tasks


def _sp(pid, score, *, must=False, first=date(2026, 11, 10), cfg=None, **kw):
    llm_kw = kw.pop("llm_kw", {})
    return Paper(id=pid, title=pid, source="arxiv", first_seen=first, llm_status="scored",
                 llm=_llm(**llm_kw), final_score=score, must_read=must, **kw)


def _up(pid, kscore, first=date(2026, 11, 10), **kw):
    return Paper(id=pid, title=pid, source="openreview", first_seen=first, keyword_score=kscore, **kw)


def test_home_json_keys(env):
    _build(env)
    h = _j(env[4], "home.json")
    assert set(h) == {"generated_at", "featured", "progress", "sources", "heat", "keywords_top", "weeks", "demo", "weekly", "digest"}
    assert set(h["featured"]) == {"mode", "week", "items"} and h["featured"]["week"] == "2026-W46"
    assert set(h["progress"]) == {"total", "scored", "unscored", "must_read", "new_datasets", "core", "peripheral",
                                  "none", "this_week_new", "news_7d"}
    assert all(isinstance(v, int) for v in h["progress"].values())
    assert h["progress"]["total"] == 4 and h["progress"]["scored"] == 3 and h["progress"]["this_week_new"] == 2
    assert set(h["heat"]) == {"paradigms", "tasks", "cells", "max", "n_scored"}
    assert set(h["demo"]) == {"pipeline_ids", "tag_id", "tag_scored"}
    assert h["featured"]["mode"] == "week_must" and h["featured"]["items"][0] == {"id": "p1", "reason": "week_must"}
    assert h["weekly"]["id"] == "2026-W46" and h["weekly"]["count"] == 2
    assert set(h["weekly"]) == {"id", "title", "start", "end", "lines", "count"}
    for s in h["sources"]:
        assert set(s) == {"id", "label", "kind", "status", "count"}
    labels = {s["id"]: s for s in h["sources"] if s["kind"] == "source"}
    assert labels["arxiv"]["label"] == "arXiv" and labels["arxiv"]["count"] == 2 and labels["arxiv"]["status"] == "active"
    assert "medrxiv" in labels
    # existing JSON files keep their keys
    assert set(_j(env[4], "weekly.json")) == {"reports"} and set(_j(env[4], "datasets.json")) == {"new_datasets", "used_top"}


def test_home_featured_fallbacks(env):
    cfg = env[0]
    rd = env[2]
    # (a) week must reads first, then top_final; no dupes, max 6
    papers = [_sp(f"s{i}", 5 + i * 0.1) for i in range(8)]
    papers.append(_sp("m1", 8.0, must=True))
    papers.append(_sp("m_old", 9.9, must=True, first=date(2026, 10, 1)))
    papers.append(_up("u1", 4.0))
    h = build_home(cfg, papers, [], TODAY, "t", rd)["featured"]
    ids = [i["id"] for i in h["items"]]
    assert h["mode"] == "week_must" and ids[0] == "m1" and len(ids) == 6 == len(set(ids))
    assert [i["reason"] for i in h["items"]] == ["week_must"] + ["top_final"] * 5
    assert ids[1] == "m_old"
    # (b) no week must-read but scored
    h = build_home(cfg, [_sp("a", 6.0), _sp("b", 7.0), _up("u", 9)], [], TODAY, "t", rd)["featured"]
    assert h["mode"] == "top_final" and [i["id"] for i in h["items"]] == ["b", "a", "u"]
    assert h["items"][-1]["reason"] == "top_keyword"
    # (c) all unscored
    h = build_home(cfg, [_up("x", 1.0), _up("y", 3.0), _up("z", 3.0, first=date(2026, 11, 11))], [], TODAY, "t", rd)["featured"]
    assert h["mode"] == "top_keyword" and [i["id"] for i in h["items"]] == ["z", "y", "x"]


def test_home_empty_library(env):
    h = build_home(env[0], [], [], TODAY, "t", env[2])
    assert h["featured"] == {"mode": "empty", "week": "2026-W46", "items": []}
    assert h["demo"] == {"pipeline_ids": [], "tag_id": None, "tag_scored": False}
    assert h["weekly"] is None and h["heat"]["max"] == 0 and h["heat"]["cells"] == [] and len(h["weeks"]) == 12
    assert h["progress"]["total"] == 0 and h["keywords_top"] == []


def test_home_heat(env):
    cfg = env[0]
    ps, ts = _tax(cfg)
    p0, p1, t0, t1 = ps[0].id, ps[1].id, ts[0].id, ts[1].id
    papers = [
        _sp("a", 8, llm_kw={"paradigms": [p0, p1], "tasks": [t0, t1, "bogus"]}),
        _sp("b", 7, llm_kw={"paradigms": [p0], "tasks": [t0]}),
        _sp("c", 6, llm_kw={"paradigms": ["bogus"], "tasks": [t0]}),
        _up("u", 1),
    ]
    heat = build_home(cfg, papers, [], TODAY, "t", env[2])["heat"]
    cells = {(c["p"], c["t"]): c["n"] for c in heat["cells"]}
    assert cells == {(p0, t0): 2, (p0, t1): 1, (p1, t0): 1, (p1, t1): 1}
    assert heat["max"] == 2 and heat["n_scored"] == 3
    assert [x["id"] for x in heat["paradigms"]] == [x.id for x in ps]
    assert {x["id"]: x["count"] for x in heat["paradigms"]}[p0] == 2
    assert {x["id"]: x["count"] for x in heat["tasks"]}[t0] == 3
    demo = build_home(cfg, papers, [], TODAY, "t", env[2])["demo"]
    assert demo["tag_id"] == "a" and demo["tag_scored"] is True


def test_home_weeks_and_keywords_and_demo(env):
    cfg = env[0]
    papers = [
        _sp("a", 8, matched_keywords=["k1", "k2"]),
        _up("b", 2, first=date(2026, 8, 25), matched_keywords=["k1"]),
        _up("c", 5, first=date(2026, 11, 11), matched_keywords=["k2", "k3"]),
        _up("d", 1, first=date(2025, 1, 1)),
    ]
    h = build_home(cfg, papers, [], TODAY, "t", env[2])
    w = h["weeks"]
    assert len(w) == 12 and w[-1]["id"] == week_id(TODAY) and w[-1]["start"] == "2026-11-09"
    assert [x["id"] for x in w] == sorted(x["id"] for x in w)
    assert w[-1]["count"] == 2 and w[-1]["scored"] == 1 and w[0]["start"] == "2026-08-24" and w[0]["count"] == 1
    assert h["keywords_top"][:2] == [{"name": "k1", "count": 2}, {"name": "k2", "count": 2}]
    assert h["demo"]["pipeline_ids"] == ["c", "a", "b", "d"]
    # no scored with tags -> keyword fallback
    h2 = build_home(cfg, [_up("p", 1), _up("q", 4)], [], TODAY, "t", env[2])
    assert h2["demo"]["tag_id"] == "q" and h2["demo"]["tag_scored"] is False


def test_home_weekly_lines(env):
    _build(env)
    h = _j(env[4], "home.json")
    assert h["weekly"]["lines"] == []  # W46 is "# W46" only
    cfg, _, reports, _, _ = env
    md = ("# T\n\n> quote\n## Sec\n- **bold** and `code` [link](http://x)\n1. item\n<script>x</script>\n" + "z" * 200 + "\n"
          + "\n".join(f"l{i}" for i in range(20)))
    lines = _plain_lines(md)
    assert len(lines) == 8 and lines[0] == "Sec"
    assert lines[1] == "bold and code link" and lines[2] == "item"
    assert all("<" not in x and "*" not in x and "`" not in x and "](" not in x and len(x) <= 120 for x in lines)
    (reports / "weekly" / "2026-W46.md").write_text(md, encoding="utf-8")
    from aivc.site import collect_weekly
    wk = build_home(cfg, [], collect_weekly(reports), TODAY, "t", reports)["weekly"]
    assert wk["lines"] == lines and "script" not in " ".join(wk["lines"])
    (reports / "weekly" / "2026-W46.md").unlink()
    wk = build_home(cfg, [], [{"id": "2026-W46", "title": "t", "start": "a", "end": "b"}], TODAY, "t", reports)["weekly"]
    assert wk["lines"] == []


def test_home_no_weekly_dir(env, tmp_path):
    cfg, data, _, assets, site = env
    build_site(cfg, data_dir=data, reports_dir=tmp_path / "nope", site_dir=site, today=TODAY, assets_dir=assets)
    assert _j(site, "home.json")["weekly"] is None


def _news_item(n, d, rel=7, et="funding", amt=None, scored=True):
    from aivc.models import NewsItem, NewsLLM, NewsSourceRef, make_news_id
    url = f"https://news.example/{n}"
    return NewsItem(
        id=make_news_id(url), title=f"N{n}", url=url, source="Src", sources=[NewsSourceRef(name="Src", url=url)],
        published=d, event_type=et if scored else None, amount_usd=amt, relevance=rel if scored else None,
        llm=NewsLLM(model="m", prompt_version="news-v1", scored_at=datetime(2026, 11, 11)) if scored else None,
        llm_status="scored" if scored else "unscored", first_seen=d)


def test_news_and_companies_json_empty(env):
    res = _build(env)
    n = _j(env[4], "news.json")
    assert n["items"] == [] and n["count"] == 0 and res.n_news == 0
    c = _j(env[4], "companies.json")
    assert isinstance(c["companies"], list) and c["paper_companies"] == {}
    m = _j(env[4], "meta.json")["stats"]
    assert m["news_total"] == 0 and m["news_7d"] == 0


def test_news_and_companies_json_with_data(env):
    from aivc.news import NewsStore
    NewsStore(env[1]).upsert([
        _news_item(1, date(2026, 11, 10), amt=1e8), _news_item(2, date(2026, 10, 1)),
        _news_item(3, date(2026, 11, 11), rel=1), _news_item(4, date(2026, 11, 11), scored=False),
    ])
    res = _build(env)
    n = _j(env[4], "news.json")
    assert res.n_news == 3 and n["count"] == 3
    assert (env[4] / "data" / "companies.json").is_file()
    s = _j(env[4], "meta.json")["stats"]
    assert s["news_total"] == 2 and s["news_7d"] == 1  # 2026-11-12 往前 7 天：11-06..11-12；未打分不计


# ---------- home.json digest ----------

def _dg(env, papers, news=None, weekly=None):
    exp = {"items": news} if news is not None else None
    return build_home(env[0], papers, weekly or [], TODAY, "t", env[2], exp)["digest"]


def _nrow(i, d, rel=5, major=False, scored=True, co=None):
    return {"id": i, "date": d.isoformat(), "relevance": rel, "major": major, "scored": scored,
            "companies": [co] if co else [], "company_ids": [co] if co else []}


def test_digest_window_boundary_and_fallback(env):
    ps = [_up("in0", 1, first=TODAY), _up("in6", 1, first=date(2026, 11, 6)), _up("out7", 1, first=date(2026, 11, 5)),
          _up("fut", 1, first=date(2026, 11, 13)), _up("in3", 1, first=date(2026, 11, 9))]
    d = _dg(env, ps)
    assert d["window"] == {"start": "2026-11-06", "end": "2026-11-12", "days": 7, "label": d["window"]["label"]}
    assert d["fallback"] is False and d["n_papers"] == 3 and set(d["paper_ids"]) == {"in0", "in6", "in3"}
    assert "近 7 天" in d["window"]["label"]
    # fewer than 3 in window -> fallback to latest 30 (never future)
    ps2 = [_up("a", 1, first=TODAY), _up("b", 2, first=date(2026, 10, 1)), _up("c", 3, first=date(2026, 11, 13))]
    d2 = _dg(env, ps2)
    assert d2["fallback"] is True and d2["n_papers"] == 1 and set(d2["paper_ids"]) == {"a", "b"}
    assert "最近入库" in d2["window"]["label"]
    d3 = _dg(env, [_up(f"x{i:02d}", 1, first=date(2026, 9, 1) + timedelta(days=i)) for i in range(40)])
    assert d3["fallback"] and len(d3["paper_ids"]) == 30


def test_digest_paper_order_and_cap(env):
    ps = [_up("u_hi", 9.0), _up("u_lo", 1.0), _sp("s_lo", 5.0), _sp("s_hi", 8.0, must=True),
          _sp("s_tie_b", 6.0), _sp("s_tie_a", 6.0)]
    d = _dg(env, ps)
    assert d["paper_ids"] == ["s_hi", "s_tie_a", "s_tie_b", "s_lo", "u_hi", "u_lo"]
    assert d["n_papers"] == 6 and d["n_must_read"] == 1
    many = [_sp(f"m{i:02d}", 5.0, first=date(2026, 11, 10)) for i in range(35)]
    dm = _dg(env, many)
    assert len(dm["paper_ids"]) == 30 and dm["n_papers"] == 35


def test_digest_new_datasets(env):
    ps = [_sp("d1", 7.0, llm_kw={"releases_new_dataset": True, "new_dataset_names": ["Alpha", "alpha", " Beta "]}),
          _sp("d2", 7.0, first=date(2026, 11, 11), llm_kw={"releases_new_dataset": True, "new_dataset_names": ["Gamma"]}),
          _sp("d_old", 7.0, first=date(2026, 11, 1), llm_kw={"releases_new_dataset": True, "new_dataset_names": ["Old"]}),
          _sp("d_no", 7.0, llm_kw={"releases_new_dataset": False, "new_dataset_names": ["Nope"]}),
          _up("d_un", 1.0)]
    nd = _dg(env, ps)["new_datasets"]
    assert [x["name"] for x in nd] == ["Gamma", "Alpha", "Beta"]
    assert nd[0] == {"name": "Gamma", "paper_id": "d2", "paper_title": "d2", "date": "2026-11-11"}
    many = [_sp(f"e{i}", 5.0, llm_kw={"releases_new_dataset": True, "new_dataset_names": [f"N{i}"]}) for i in range(9)]
    assert len(_dg(env, many)["new_datasets"]) == 6
    assert _dg(env, [_sp("z", 5.0)])["new_datasets"] == []


def test_digest_movers(env):
    ps_, _ = _tax(env[0])
    a, b, c = ps_[0].id, ps_[1].id, ps_[2].id
    old = date(2026, 11, 3)  # previous window
    ps = [_sp(f"c{i}", 5.0, llm_kw={"paradigms": [a]}) for i in range(3)]
    ps += [_sp("cb", 5.0, llm_kw={"paradigms": [b, "bogus"]})]
    ps += [_sp(f"p{i}", 5.0, first=old, llm_kw={"paradigms": [b]}) for i in range(2)]
    ps += [_sp("pc", 5.0, first=old, llm_kw={"paradigms": [c]}), _sp("ancient", 5.0, first=date(2026, 10, 1), llm_kw={"paradigms": [c]})]
    mv = _dg(env, ps)["movers"]
    assert [(m["id"], m["cur"], m["prev"], m["delta"]) for m in mv] == [(a, 3, 0, 3), (b, 1, 2, -1), (c, 0, 1, -1)]
    assert all(set(m) == {"id", "label", "cur", "prev", "delta"} for m in mv)
    assert _dg(env, ps[:3] + ps[4:5])["movers"] == []  # 4 scored papers in two windows -> too few
    assert _dg(env, [])["movers"] == []


def test_digest_news_and_weekly(env):
    news = [_nrow("minor_hi", date(2026, 11, 12), rel=9), _nrow("major_lo", date(2026, 11, 1), rel=4, major=True),
            _nrow("edge", date(2026, 10, 30), rel=10), _nrow("old", date(2026, 10, 29), rel=10, major=True),
            _nrow("same_b", date(2026, 11, 10), rel=6), _nrow("same_a", date(2026, 11, 10), rel=6),
            _nrow("un", date(2026, 11, 12), rel=10, scored=False), _nrow("fut", date(2026, 11, 13), rel=10),
            _nrow("w7", date(2026, 11, 6), rel=1)]
    d = _dg(env, [], news, weekly=[{"id": "2026-W46", "title": "T", "start": "s", "end": "e"}])
    assert d["news_ids"] == ["major_lo", "edge", "minor_hi", "same_a", "same_b", "w7"]
    assert d["n_news_7d"] == 4  # minor_hi, same_a, same_b, w7 (scored, today-6 inclusive)
    assert d["weekly"] == {"id": "2026-W46", "title": "T"}
    many = [_nrow(f"n{i:02d}", TODAY, rel=5) for i in range(12)]
    assert len(_dg(env, [], many)["news_ids"]) == 8


def test_digest_news_interleaves_companies_on_ties(env):
    news = [_nrow("a1", date(2026, 11, 12), rel=7, co="a"), _nrow("a2", date(2026, 11, 11), rel=7, co="a"),
            _nrow("a3", date(2026, 11, 10), rel=7, co="a"), _nrow("b1", date(2026, 11, 9), rel=7, co="b"),
            _nrow("c1", date(2026, 11, 8), rel=7, co="c"), _nrow("hi", date(2026, 11, 1), rel=9, co="a"),
            _nrow("lo", date(2026, 11, 12), rel=6, co="a")]
    assert _dg(env, [], news)["news_ids"] == ["hi", "a1", "b1", "c1", "a2", "a3", "lo"]


def test_digest_without_news(env):
    d = _dg(env, [_sp("a", 5.0)])
    assert d["news_ids"] == [] and d["n_news_7d"] == 0 and d["weekly"] is None
    assert set(d) == {"window", "fallback", "paper_ids", "n_papers", "n_must_read", "new_datasets", "movers",
                      "news_ids", "n_news_7d", "weekly"}
    h = build_home(env[0], [], [], TODAY, "t", env[2])
    assert h["digest"]["paper_ids"] == [] and h["progress"]["news_7d"] == 0


def test_resources_json_and_meta(env):
    from aivc.resources import ResourceStore
    cfg, data, *_ = env
    st = ResourceStore(data)
    base = dict(kind="repo", description_zh="说明", added=date(2026, 11, 1))
    from aivc.models import Resource
    st.save("repos.yaml", [Resource(id="r1", name="R1", url="https://github.com/o/r1", paper_ids=["p1", "nope"], **base)])
    st.save_candidates([Resource(id="c1", name="C1", url="https://github.com/o/c1", status="candidate", **base)])
    r = _build(env)
    site = env[4]
    rj = _j(site, "resources.json")
    assert r.n_resources == 1 and rj["count"] == 1 and rj["candidates_count"] == 1
    assert [i["id"] for i in rj["items"]] == ["r1"] and rj["items"][0]["paper_ids"] == ["p1"]
    assert _j(site, "meta.json")["stats"]["resources_total"] == 1


def test_resources_json_empty_without_files(env):
    r = _build(env)
    rj = _j(env[4], "resources.json")
    assert r.n_resources == 0 and rj["count"] == 0 and rj["items"] == []
    assert _j(env[4], "meta.json")["stats"]["resources_total"] == 0
