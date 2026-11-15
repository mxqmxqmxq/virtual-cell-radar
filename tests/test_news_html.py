from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx

from aivc.config import NewsFeed, NewsHtmlSource, NewsSourceConfig
from aivc.fetchers.news_html import CompositeNewsFetcher, NewsHTMLFetcher, find_date, parse_list_page
from aivc.fetchers.news_rss import NewsRSSFetcher

FIX = Path(__file__).parent / "fixtures"
NEWS = FIX / "news_html"
CARDS = (NEWS / "cards.html").read_text()
TEXTD = (NEWS / "list_text_date.html").read_text()
BEFORE = (NEWS / "date_before.html").read_text()
URLD = (NEWS / "url_dated.html").read_text()
RSS = (FIX / "news" / "rss2.xml").read_text()
UA = "aivc-test/0.1 (+https://github.com/mxqmxqmxq/virtual-cell-radar)"
HTTP = SimpleNamespace(user_agent="x", timeout=10, max_retries=0)
SINCE, UNTIL = date(2026, 9, 1), date(2026, 11, 14)

S_CARDS = NewsHtmlSource(name="Cards", list_url="https://cards.example.test/news/", company="alpha-bio",
                         link_pattern=r"/news/")
S_TEXT = NewsHtmlSource(name="Text", list_url="https://text.example.test/blog/", link_pattern=r"/blog/")
S_BEFORE = NewsHtmlSource(name="Before", list_url="https://before.example.test/n/", link_pattern=r"/n/p")
S_URL = NewsHtmlSource(name="UrlD", list_url="https://blog.example.test/", link_pattern=r"/\d{4}/\d{2}/")


def by_url(es):
    return {e.url: e for e in es}


def test_cards_filters_titles_dates():
    es = parse_list_page(CARDS, S_CARDS)
    m = by_url(es)
    base = "https://cards.example.test"
    assert set(m) == {f"{base}/news/alpha-launch", f"{base}/news/beta-raise", f"{base}/news/gamma-note"}
    a = m[f"{base}/news/alpha-launch"]
    assert a.title == "Alpha Bio launches a virtual cell platform"  # 标题标签优先，不含 time/段落
    assert a.published == date(2026, 11, 10)
    assert a.feed_company == "alpha-bio" and a.feed_kind == "company" and a.source == "Cards"
    # 同一 URL 的 "Read more" 锚点被合并，标题取较长文本
    assert m[f"{base}/news/beta-raise"].title.startswith("Beta Labs raises Series B")
    assert m[f"{base}/news/beta-raise"].published == date(2026, 11, 8)
    # 锚点文本过短时回退 aria-label；日期来自锚点内的 time
    g = m[f"{base}/news/gamma-note"]
    assert g.title == "Gamma note on foundation model benchmarks" and g.published == date(2026, 11, 6)
    # 脚本里的伪日期、正文段落都不出现
    assert all(e.snippet == "" for e in es)
    assert not any("Body paragraph" in e.title for e in es)


def test_filters_same_host_exclude_and_list_self():
    es = parse_list_page(CARDS, S_CARDS)
    urls = [e.url for e in es]
    assert not any("other.example.test" in u for u in urls)
    assert not any(u.endswith("/news") or u.endswith("/news/") for u in urls)
    open_src = S_CARDS.model_copy(update={"same_host_only": False, "link_pattern": r"/news/ext"})
    assert [e.url for e in parse_list_page(CARDS, open_src)] == ["https://other.example.test/news/ext"]
    excl = S_CARDS.model_copy(update={"exclude_pattern": r"gamma|beta"})
    got = {e.url.rsplit("/", 1)[1] for e in parse_list_page(CARDS, excl)}
    assert "alpha-launch" in got and not got & {"gamma-note", "beta-raise"}


def test_text_date_formats_after_layout():
    es = parse_list_page(TEXTD, S_TEXT)
    d = {e.url.rsplit("/", 1)[1]: e.published for e in es}
    assert d == {
        "one": date(2026, 11, 5), "two": date(2026, 10, 12), "three": date(2026, 11, 3),
        "four": date(2026, 11, 2), "five": date(2026, 11, 1), "six": date(2026, 9, 30),
    }
    assert es[1].title == "Second post on dataset releases & benchmarks"
    assert all(e.feed_kind == "company" and e.feed_company is None for e in es)


def test_date_before_layout_auto_and_explicit():
    es = parse_list_page(BEFORE, S_BEFORE)
    assert [e.published for e in es] == [date(2026, 11, 12), date(2026, 11, 4), date(2026, 10, 20)]
    wrong = parse_list_page(BEFORE, S_BEFORE.model_copy(update={"date_scope": "after"}))
    assert [e.published for e in wrong][:2] == [date(2026, 11, 4), date(2026, 10, 20)]


def test_url_date_and_require_date():
    es = parse_list_page(URLD, S_URL)
    assert [(e.url.rsplit("/", 1)[1], e.published) for e in es] == [
        ("lab-update-on-atlas", date(2026, 11, 9)), ("older-lab-update-note", date(2026, 10, 30)),
    ]
    # 无日期的条目：默认丢弃，require_date=False 时保留且 published 为空
    assert not any("undated" in e.url for e in parse_list_page(CARDS, S_CARDS))
    keep = parse_list_page(CARDS, S_CARDS.model_copy(update={"require_date": False}))
    undated = [e for e in keep if "undated" in e.url]
    assert len(undated) == 1 and undated[0].published is None


def test_min_title_and_max_items():
    es = parse_list_page(TEXTD, S_TEXT.model_copy(update={"max_items": 2}))
    assert len(es) == 2
    es = parse_list_page(TEXTD, S_TEXT.model_copy(update={"min_title_chars": 100}))
    assert es == []


def test_find_date_variants():
    assert find_date("posted 2026-11-03T10:00:00Z") == date(2026, 11, 3)
    assert find_date("Nov 3, 2026") == date(2026, 11, 3)
    assert find_date("3rd November 2026") == date(2026, 11, 3)
    assert find_date("2026-13-45 then 2026-02-03") == date(2026, 2, 3)
    assert find_date("no date, version 1.2.3") is None


def make(srcs, routes, feeds=(), cls=NewsHTMLFetcher, **kw):
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        r = routes.get(str(req.url))
        if r is None:
            return httpx.Response(404, text="nope")
        if isinstance(r, httpx.Response):
            return r
        if isinstance(r, Exception):
            raise r
        return httpx.Response(200, text=r)

    cfg = NewsSourceConfig(feeds=list(feeds), html_sources=list(srcs), user_agent=UA, **kw)
    sleeps: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return cls(cfg, HTTP, client=client, sleep=sleeps.append), sleeps, seen


def test_fetch_robots_disallow_makes_no_list_request():
    f, _, seen = make([S_CARDS], {
        "https://cards.example.test/robots.txt": "User-agent: *\nDisallow: /news/\n",
        "https://cards.example.test/news/": CARDS,
    })
    r = f.fetch(SINCE, UNTIL)
    assert r.errors == {"Cards": "robots disallow"} and r.entries == []
    assert [str(x.url) for x in seen] == ["https://cards.example.test/robots.txt"]


def test_fetch_never_requests_item_links_and_paces():
    f, sleeps, seen = make([S_CARDS, S_TEXT], {
        "https://cards.example.test/news/": CARDS,
        "https://text.example.test/blog/": TEXTD,
    })
    r = f.fetch(SINCE, UNTIL)
    assert r.per_feed == {"Cards": 3, "Text": 6}
    assert r.requests == len(seen) == 4  # 两个 robots + 两个列表页
    paths = {x.url.path for x in seen}
    assert paths == {"/robots.txt", "/news/", "/blog/"}
    assert len(sleeps) == 3 and all(s >= 1.0 for s in sleeps)
    assert all(e.snippet == "" for e in r.entries)


def test_fetch_window_filter_and_disabled():
    off = S_TEXT.model_copy(update={"name": "Off", "enabled": False})
    f, _, seen = make([S_TEXT, off], {"https://text.example.test/blog/": TEXTD})
    r = f.fetch(date(2026, 11, 1), date(2026, 11, 4))
    assert sorted(e.published for e in r.entries) == [date(2026, 11, 1), date(2026, 11, 2), date(2026, 11, 3)]
    assert "Off" not in r.per_feed


def test_single_source_failure_isolated():
    f, _, _ = make([S_URL, S_CARDS, S_TEXT, S_BEFORE], {
        "https://blog.example.test/": httpx.Response(500, text="boom"),
        "https://cards.example.test/news/": httpx.ConnectError("down"),
        "https://text.example.test/blog/": TEXTD,
        "https://before.example.test/n/": BEFORE,
    })
    r = f.fetch(SINCE, UNTIL)
    assert set(r.errors) == {"UrlD", "Cards"}
    assert r.per_feed == {"Text": 6, "Before": 3}


def test_composite_merges_and_shares_state():
    feed = NewsFeed(name="Wire", url="https://wire.example.test/feed.xml", kind="wire")
    f, sleeps, seen = make([S_TEXT, S_BEFORE], {
        "https://wire.example.test/feed.xml": RSS,
        "https://text.example.test/blog/": TEXTD,
        "https://before.example.test/n/": BEFORE,
    }, feeds=[feed], cls=CompositeNewsFetcher)
    r = f.fetch(date(2026, 1, 1), UNTIL)
    assert r.per_feed["Wire"] >= 1 and r.per_feed["Text"] == 6 and r.per_feed["Before"] == 3
    assert {e.source for e in r.entries} >= {"Wire", "Text", "Before"}
    assert r.requests == len(seen) == 6  # 3 个 robots + 3 个列表/feed
    assert len(sleeps) == 5 and all(s >= 1.0 for s in sleeps)  # 共用间隔：任意两次请求之间都 sleep


def test_composite_without_html_sources_matches_rss_only():
    feed = NewsFeed(name="Wire", url="https://wire.example.test/feed.xml", kind="wire")
    routes = {"https://wire.example.test/feed.xml": RSS}
    c, _, cseen = make([], routes, feeds=[feed], cls=CompositeNewsFetcher)
    n, _, nseen = make([], routes, feeds=[feed], cls=NewsRSSFetcher)
    rc, rn = c.fetch(date(2026, 1, 1), UNTIL), n.fetch(date(2026, 1, 1), UNTIL)
    assert [e.url for e in rc.entries] == [e.url for e in rn.entries]
    assert rc.requests == rn.requests and len(cseen) == len(nseen)


def test_run_news_counts_html_sources(config_dir, tmp_path, monkeypatch):
    from aivc import news_pipeline
    from aivc.config import load_config
    from aivc.fetchers.news_rss import NewsFetchResult

    cfg = load_config(config_dir)
    for fd in cfg.sources.news.feeds:
        fd.enabled = False
    cfg.sources.news.html_sources = [S_TEXT]
    made = []

    class FakeComposite:
        def __init__(self, src, http, **kw):
            made.append(src)

        def fetch(self, since, until):
            es = parse_list_page(TEXTD, S_TEXT)
            return NewsFetchResult(entries=es, per_feed={"Text": len(es)}, requests=2)

    monkeypatch.setattr(news_pipeline, "CompositeNewsFetcher", FakeComposite)
    s = news_pipeline.run_news(cfg, data_dir=tmp_path, today=date(2026, 11, 14), use_llm=False, days=60)
    assert made and s.attempted == ["Text"] and s.n_fetched == 6
    assert not any("没有启用" in n for n in s.notes)
    # 条目来自 HTML 源：只有标题/链接/日期，进入预筛流程不报错
    assert s.requests == 2 and not s.fetch_errors


# ---------------------------------------------------------------------------
# 2026-11 新增源：参数与 config/sources.yaml 完全一致；fixture 为真实列表页裁剪（只含标题/链接/日期外层结构）
# ---------------------------------------------------------------------------
S_DYNO = NewsHtmlSource(
    name="Dyno Therapeutics News", list_url="https://www.dynotx.com/news", kind="company",
    company="Dyno Therapeutics", enabled=False,
    link_pattern=r"^https://www\.dynotx\.com/news/[^/?#]+$", max_items=30,
)
S_NOETIK = NewsHtmlSource(
    name="Noetik News", list_url="https://www.noetik.ai/news", kind="company", company="Noetik", enabled=False,
    link_pattern=r"^https://www\.noetik\.ai/[A-Za-z0-9_-]+$",
    title_strip_patterns=[r"^(?:News|Blog|Research)\s+", r"\s+By\s+Noetik\b.*$"], max_items=30,
)


def test_dyno_fixture_titles_dates_and_filters():
    es = parse_list_page((NEWS / "dyno.html").read_text(), S_DYNO)
    base = "https://www.dynotx.com/news/"
    got = {e.url.removeprefix(base): (e.title, e.published) for e in es}
    assert len(es) == 4  # 3 条公告 + 1 条 Publications；外链（Technology Networks）被 same_host_only 滤掉
    assert got["dyno-therapeutics-to-present-new-aav-capsid-data-across-three-posters-at-the-33rd-annual-"
               "congress-of-the-european-society-of-gene-cell-therapy"] == (
        "Dyno Therapeutics to Present New AAV Capsid Data Across Three Posters at the 33rd Annual Congress "
        "of the European Society of Gene & Cell Therapy", date(2026, 10, 27))
    assert got["dyno-therapeutics-presents-new-multi-mechanism-muscle-capsid-data-at-the-31st-international-"
               "congress-of-the-world-muscle-society"][1] == date(2026, 10, 5)
    assert got["dyno-therapeutics-launches-dyno-psi-phi-an-agentic-ai-suite-for-protein-binder-design-at-"
               "nvidia-gtc-2026"] == (
        "Dyno Therapeutics Launches Dyno Psi-Phi, an Agentic AI Suite for Protein Binder Design, at NVIDIA GTC 2026",
        date(2026, 3, 18))
    assert got["low-n-openfold-fine-tuning-improves-peptide-design-without-additional-structures"][1] == date(2024, 10, 4)
    assert not any("technologynetworks" in e.url for e in es)
    assert all(e.snippet == "" and e.feed_company == "Dyno Therapeutics" and e.source == S_DYNO.name for e in es)


def test_noetik_fixture_strips_category_and_byline():
    es = parse_list_page((NEWS / "noetik.html").read_text(), S_NOETIK)
    assert [(e.url, e.title, e.published) for e in es] == [
        ("https://www.noetik.ai/tario2",
         "Noetik’s TARIO-2 Foundation Model Identifies Patients with Improved Response to Next-Generation "
         "Immune Checkpoint Inhibitors BOT+BAL Using Only Routine Pathology Images", date(2026, 6, 15)),
        ("https://www.noetik.ai/asco2026",
         "Noetik and Agenus Present Use of TARIO-2 Foundation Model to Predict Patient Responses to BOT+BAL "
         "at ASCO 2026", date(2026, 5, 31)),
        ("https://www.noetik.ai/sitc-2025",
         "Noetik Presents Virtual Cell Foundation Models at SITC 2025", date(2025, 11, 10)),
    ]
    # Business Wire 外链不放开 same_host_only；无日期的导航链接被 require_date 丢弃
    assert not any("businesswire" in e.url for e in es)
    assert all(e.snippet == "" for e in es)
    # 不带清洗规则时标题仍混有类别前缀和署名后缀（证明清洗确由配置驱动）
    raw = parse_list_page((NEWS / "noetik.html").read_text(), S_NOETIK.model_copy(update={"title_strip_patterns": []}))
    assert raw[0].title.startswith("News ") and "By Noetik" in raw[0].title


def test_title_strip_patterns_default_off_and_min_title_after_strip():
    page = (
        '<ul><li><a href="https://s.example.test/n/a1">Tag: Alpha Bio launches virtual cell platform</a>'
        '<time datetime="2026-11-01"></time></li>'
        '<li><a href="https://s.example.test/n/a2">Tag: Tiny title</a><time datetime="2026-11-02"></time></li></ul>'
    )
    base = NewsHtmlSource(name="Strip", list_url="https://s.example.test/n/", link_pattern=r"/n/a")
    off = parse_list_page(page, base)
    assert [e.title for e in off] == ["Tag: Alpha Bio launches virtual cell platform", "Tag: Tiny title"]
    on = parse_list_page(page, base.model_copy(update={"title_strip_patterns": [r"^Tag:\s*"]}))
    # 清洗后短于 min_title_chars(15) 的标题仍按原规则丢弃
    assert [e.title for e in on] == ["Alpha Bio launches virtual cell platform"]
    # 多条规则按顺序依次应用
    two = parse_list_page(page, base.model_copy(update={"title_strip_patterns": [r"^Tag:\s*", r"\s+platform$"]}))
    assert [e.title for e in two] == ["Alpha Bio launches virtual cell"]


def test_real_sources_yaml_regexes_compile_and_companies_exist():
    import re

    from aivc.config import load_config

    cfg = load_config(Path(__file__).parent.parent / "config")
    names = {c.name for c in cfg.companies.companies}
    for s in cfg.sources.news.html_sources:
        for pat in [s.link_pattern, s.exclude_pattern, *s.title_strip_patterns]:
            if pat:
                re.compile(pat)
    new = {"Valence Labs", "Enveda", "Dyno Therapeutics News", "Noetik News"}
    for s in [*cfg.sources.news.html_sources, *cfg.sources.news.feeds]:
        if s.name in new:
            assert s.company in names, f"{s.name}: 公司 {s.company!r} 不在 companies.yaml"
    # 本次新增源与上面的测试参数一致
    html = {s.name: s for s in cfg.sources.news.html_sources}
    for want in (S_DYNO, S_NOETIK):
        assert html[want.name] == want


# ---- date_scope="before_nearest"（卡片内日期在锚点之前，卡片之间夹着被过滤的条目）----
BIOHUB = (FIX / "news" / "biohub_news_list.html").read_text()
S_BIOHUB = NewsHtmlSource(
    name="Biohub News", list_url="https://biohub.org/news/", kind="company", company="CZI",
    link_pattern=r"^https://biohub\.org/news/[^/?#]+/?$", date_scope="before_nearest",
    title_strip_patterns=[r"^(?:News|Blog|Read more)\s*[–-]\s*"],
)
_BIOHUB_EXPECT = {
    "https://biohub.org/news/alpha-consortium-data-release/":
        ("Alpha consortium releases open reference dataset for cell models", date(2026, 10, 7)),
    "https://biohub.org/news/beta-imaging-facility-opens/":
        ("Beta institute opens new imaging facility for researchers", date(2026, 10, 14)),
    "https://biohub.org/news/gamma-early-career-prize/":
        ("Gamma lab announces fictional prize for early career scientists", date(2026, 8, 27)),
}


def test_before_nearest_biohub_cards():
    es = parse_list_page(BIOHUB, S_BIOHUB)
    got = by_url(es)
    assert set(got) == set(_BIOHUB_EXPECT)  # 博客、外链、分页都不在
    assert len(es) == 3
    for url, (title, d) in _BIOHUB_EXPECT.items():
        assert got[url].title == title
        assert got[url].published == d
        assert got[url].snippet == ""
        assert got[url].feed_company == "CZI"
    assert all("News –" not in e.title and "Read more –" not in e.title for e in es)


def test_before_nearest_differs_from_auto_and_lookback_limit():
    auto = by_url(parse_list_page(BIOHUB, S_BIOHUB.model_copy(update={"date_scope": "auto"})))
    hero = "https://biohub.org/news/alpha-consortium-data-release/"
    assert auto[hero].published != date(2026, 10, 7)  # auto 取到了相邻卡片的日期
    # 回看窗口太小时，摘要把日期挤出窗口，不会误取别的卡片：要么无日期（被 require_date 丢弃）
    tiny = parse_list_page(BIOHUB, S_BIOHUB.model_copy(update={"date_lookback_chars": 20}))
    assert hero not in by_url(tiny)


def test_find_date_last_and_before_unchanged():
    assert find_date("a 2026-01-02 b 2026-03-04") == date(2026, 1, 2)
    assert find_date("a 2026-01-02 b 2026-03-04", last=True) == date(2026, 3, 4)
    assert find_date("nothing", last=True) is None
    # 既有 before 场景不变
    es = parse_list_page(BEFORE, S_BEFORE.model_copy(update={"date_scope": "before"}))
    assert [e.published for e in es] == [date(2026, 11, 12), date(2026, 11, 4), date(2026, 10, 20)]
