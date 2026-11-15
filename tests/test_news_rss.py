from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx

from aivc.config import NewsFeed, NewsSourceConfig
from aivc.fetchers.news_rss import NewsRSSFetcher, parse_feed

FIX = Path(__file__).parent / "fixtures" / "news"
RSS = (FIX / "rss2.xml").read_text()
ATOM = (FIX / "atom.xml").read_text()
EMPTY = (FIX / "empty.xml").read_text()
HTML = (FIX / "html_content.xml").read_text()
UA = "aivc-test/0.1 (+https://github.com/mxqmxqmxq/virtual-cell-radar)"
HTTP = SimpleNamespace(user_agent="x", timeout=10, max_retries=0)
SINCE, UNTIL = date(2026, 11, 1), date(2026, 11, 14)

F_RSS = NewsFeed(name="Wire", url="https://wire.example.test/feed.xml", kind="wire")
F_ATOM = NewsFeed(name="Lab", url="https://blog.example.test/atom.xml", kind="company", company="sample-lab")
F_EMPTY = NewsFeed(name="Empty", url="https://empty.example.test/feed", kind="media")
F_HTML = NewsFeed(name="Html", url="https://html.example.test/feed", kind="media")


def make(feeds, routes, **kw):
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        r = routes.get(str(req.url))
        if r is None:
            return httpx.Response(404, text="nope")
        if isinstance(r, httpx.Response):
            return r
        return httpx.Response(200, text=r)

    cfg = NewsSourceConfig(feeds=feeds, user_agent=UA, **kw)
    sleeps: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return NewsRSSFetcher(cfg, HTTP, client=client, sleep=sleeps.append), sleeps, seen


def test_parse_rss2():
    es = parse_feed(RSS, F_RSS)
    titles = [e.title for e in es]
    assert titles[0].startswith("Example Bio raises $120M")
    assert es[0].published == date(2026, 11, 11)
    assert es[0].source == "Wire" and es[0].feed_kind == "wire" and es[0].feed_company is None
    assert "fictional financing" in es[0].snippet
    # 缺标题、坏链接、无链接的条目被丢弃；无日期的保留
    assert len(es) == 4
    assert not any("broken" in t or "no link" in t for t in titles)
    undated = [e for e in es if e.title.startswith("Item without")][0]
    assert undated.published is None


def test_parse_atom():
    es = parse_feed(ATOM, F_ATOM)
    assert len(es) == 2
    assert es[0].url == "https://blog.example.test/posts/perturbation-atlas"
    assert es[0].published == date(2026, 11, 12)
    assert es[0].feed_kind == "company" and es[0].feed_company == "sample-lab"
    assert es[1].published == date(2026, 11, 4)


def test_parse_empty():
    assert parse_feed(EMPTY, F_EMPTY) == []
    assert parse_feed("", F_EMPTY) == []


def test_html_cleaning_and_no_content_encoded():
    es = parse_feed(HTML, F_HTML)
    a, b, c = es
    assert a.title == "Fiction & Co. unveils model"
    assert a.snippet == "Hello bold & world. Second paragraph"
    assert "<" not in a.snippet and "alert" not in a.snippet
    assert "FULLTEXT-SENTINEL" not in "".join(e.snippet for e in es)
    assert b.snippet == ""
    t = parse_feed(HTML, F_HTML, snippet_max_chars=40)[2]
    assert len(t.snippet) == 40 and set(t.snippet) == {"X"}


def test_window_filter_and_cap():
    f, _, _ = make([F_RSS], {F_RSS.url: RSS, "https://wire.example.test/robots.txt": "User-agent: *\nAllow: /"})
    res = f.fetch(SINCE, UNTIL)
    titles = [e.title for e in res.entries]
    assert not any("Old fictional" in t for t in titles)
    assert any("undated" in e.url for e in res.entries)
    assert res.per_feed == {"Wire": 3}
    f2, _, _ = make([F_RSS], {F_RSS.url: RSS}, max_items_per_feed=1)
    assert len(f2.fetch(SINCE, UNTIL).entries) == 1


def test_robots_disallow_does_not_request_feed():
    routes = {
        "https://wire.example.test/robots.txt": f"User-agent: *\nDisallow: /feed.xml\n",
        F_RSS.url: RSS,
    }
    f, _, seen = make([F_RSS], routes)
    res = f.fetch(SINCE, UNTIL)
    assert res.errors == {"Wire": "robots disallow"}
    assert res.entries == []
    assert [str(r.url) for r in seen] == ["https://wire.example.test/robots.txt"]
    assert res.requests == 1


def test_robots_404_is_allowed_and_cached_per_host():
    other = NewsFeed(name="Wire2", url="https://wire.example.test/other.xml")
    f, _, seen = make([F_RSS, other], {F_RSS.url: RSS, other.url: RSS})
    res = f.fetch(SINCE, UNTIL)
    assert res.errors == {}
    urls = [str(r.url) for r in seen]
    assert urls.count("https://wire.example.test/robots.txt") == 1
    assert urls.count(F_RSS.url) == 1 and urls.count(other.url) == 1
    assert res.requests == 3


def test_respect_robots_off_skips_robots():
    f, _, seen = make([F_RSS], {F_RSS.url: RSS}, respect_robots=False)
    f.fetch(SINCE, UNTIL)
    assert [str(r.url) for r in seen] == [F_RSS.url]


def test_request_spacing_at_least_one_second():
    f, sleeps, seen = make([F_RSS, F_ATOM], {F_RSS.url: RSS, F_ATOM.url: ATOM}, delay_seconds=0.2)
    f.fetch(SINCE, UNTIL)
    assert len(seen) == 4  # 2 robots + 2 feeds
    assert len(sleeps) == len(seen) - 1
    assert all(s >= 1.0 for s in sleeps)
    f2, sleeps2, _ = make([F_RSS, F_ATOM], {F_RSS.url: RSS, F_ATOM.url: ATOM}, delay_seconds=2.5)
    f2.fetch(SINCE, UNTIL)
    assert sleeps2 and all(s == 2.5 for s in sleeps2)


def test_single_feed_failure_isolated():
    bad = NewsFeed(name="Bad", url="https://bad.example.test/feed")
    f, _, _ = make([bad, F_ATOM], {bad.url: httpx.Response(500), F_ATOM.url: ATOM})
    res = f.fetch(SINCE, UNTIL)
    assert "Bad" in res.errors and "500" in res.errors["Bad"]
    assert res.per_feed == {"Lab": 2}
    assert len(res.entries) == 2


def test_user_agent_and_single_get_per_feed():
    f, _, seen = make([F_RSS, F_EMPTY], {F_RSS.url: RSS, F_EMPTY.url: EMPTY})
    res = f.fetch(SINCE, UNTIL)
    assert all(r.headers["User-Agent"] == UA for r in seen)
    feed_gets = [r for r in seen if r.url.path != "/robots.txt"]
    assert len(feed_gets) == 2 and all(r.method == "GET" for r in feed_gets)
    assert res.per_feed["Empty"] == 0


def test_disabled_feed_skipped():
    off = NewsFeed(name="Off", url="https://off.example.test/feed", enabled=False)
    f, _, seen = make([off], {})
    res = f.fetch(SINCE, UNTIL)
    assert seen == [] and res.requests == 0 and res.entries == []


def test_require_date_drops_undated():
    feed = NewsFeed(name="Wire", url=F_RSS.url, kind="wire", require_date=True)
    f, _, _ = make([feed], {F_RSS.url: RSS})
    res = f.fetch(SINCE, UNTIL)
    assert res.entries and all(e.published is not None for e in res.entries)
    assert not any("undated" in e.url for e in res.entries)
