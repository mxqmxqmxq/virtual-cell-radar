import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx

from aivc.config import NewsFeed, NewsSourceConfig
from aivc.fetchers.news_dates import ArticleDateCache, extract_published_date
from aivc.fetchers.news_rss import NewsRSSFetcher

FIX = Path(__file__).parent / "fixtures" / "news"
UA = "aivc-test/0.1 (+https://github.com/mxqmxqmxq/virtual-cell-radar)"
HTTP = SimpleNamespace(user_agent="x", timeout=10, max_retries=0)
SINCE, UNTIL = date(2026, 11, 1), date(2026, 11, 14)


def page(name):
    return (FIX / name).read_text()


def test_extract_priority_and_forms():
    # meta article:published_time 优先于 JSON-LD 与 <time>
    assert extract_published_date(page("article_og.html")) == date(2026, 11, 4)
    assert extract_published_date(page("article_jsonld.html")) == date(2026, 11, 3)
    assert extract_published_date(page("article_itemprop.html")) == date(2026, 11, 5)
    assert extract_published_date(page("article_namemeta.html")) == date(2026, 11, 6)
    assert extract_published_date(page("article_time.html")) == date(2026, 11, 4)


def test_extract_none_cases():
    assert extract_published_date("") is None
    assert extract_published_date("<html><body>nothing</body></html>") is None
    assert extract_published_date(page("article_modified_only.html")) is None
    bad = '<meta property="article:published_time" content="2026-13-45T00:00:00Z">'
    assert extract_published_date(bad) is None
    assert extract_published_date('<meta property="article:published_time" content="Nov 4">') is None


def test_extract_future_date_invalid_with_today():
    html = '<meta property="article:published_time" content="2027-01-01T00:00:00Z">'
    assert extract_published_date(html, today=date(2026, 11, 15)) is None
    assert extract_published_date(html) == date(2027, 1, 1)
    near = '<meta property="article:published_time" content="2026-11-16T00:00:00Z">'
    assert extract_published_date(near, today=date(2026, 11, 15)) == date(2026, 11, 16)


def test_cache_roundtrip_null_retry_and_corrupt(tmp_path):
    p = tmp_path / "c" / "d.json"
    c = ArticleDateCache(p)
    c.save()
    assert not p.exists()  # 无变化不写
    c.put("https://a.example/x/?utm_source=z", date(2026, 11, 3), today=date(2026, 11, 10))
    c.put("https://a.example/y", None, today=date(2026, 11, 10))
    c.save()
    raw = json.loads(p.read_text())
    assert raw["https://a.example/x"] == {"date": "2026-11-03", "checked": "2026-11-10"}
    assert not list(p.parent.glob("*.tmp"))
    c2 = ArticleDateCache(p)
    assert c2.get("https://a.example/x") == (True, date(2026, 11, 3))
    assert c2.get("https://a.example/y", today=date(2026, 11, 16)) == (True, None)
    assert c2.get("https://a.example/y", today=date(2026, 11, 17)) == (False, None)
    assert c2.get("https://a.example/z") == (False, None)
    p.write_text("{not json")
    assert ArticleDateCache(p).get("https://a.example/x") == (False, None)


# ---- 抓取器 ----
FEED = '''<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Item one without date</title><link>https://nodate.example.test/post/1-a/</link></item>
<item><title>Item two without date</title><link>https://nodate.example.test/post/2-b/</link></item>
<item><title>Item three without date</title><link>https://nodate.example.test/post/3-c/</link></item>
</channel></rss>'''
FURL = "https://nodate.example.test/feed.xml"
ROBOTS_OK = "User-agent: *\nDisallow: /admin/\n"


def mk(tmp_path, routes, *, feed_kw=None, cfg_kw=None, cache=True):
    seen: list[httpx.Request] = []

    def handler(req):
        seen.append(req)
        r = routes.get(str(req.url))
        if r is None:
            return httpx.Response(404, text="nope")
        return r if isinstance(r, httpx.Response) else httpx.Response(200, text=r)

    feed = NewsFeed(name="ND", url=FURL, **{"date_from_article": True, "require_date": True, **(feed_kw or {})})
    cfg = NewsSourceConfig(feeds=[feed], user_agent=UA, **(cfg_kw or {}))
    sleeps: list[float] = []
    dc = ArticleDateCache(tmp_path / "news_dates.json") if cache else None
    f = NewsRSSFetcher(cfg, HTTP, client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
                       sleep=sleeps.append, date_cache=dc)
    return f, sleeps, seen, dc


def art(day):
    return f'<html><head><meta property="article:published_time" content="2026-11-{day:02d}T10:00:00+00:00"></head><body>SECRET BODY TEXT</body></html>'


def routes_all():
    return {
        "https://nodate.example.test/robots.txt": ROBOTS_OK,
        FURL: FEED,
        "https://nodate.example.test/post/1-a/": art(4),
        "https://nodate.example.test/post/2-b/": art(5),
        "https://nodate.example.test/post/3-c/": art(6),
    }


def article_reqs(seen):
    return [r for r in seen if "/post/" in r.url.path]


def test_fills_dates_with_pacing_and_cache_content(tmp_path):
    f, sleeps, seen, _ = mk(tmp_path, routes_all())
    res = f.fetch(SINCE, UNTIL)
    assert [e.published for e in res.entries] == [date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6)]
    assert res.date_lookups == 3 and len(article_reqs(seen)) == 3
    # 每次请求前都节流：总请求数 N -> 至少 N-1 次 sleep
    assert len(sleeps) >= len(seen) - 1 and all(s >= 1.0 for s in sleeps)
    raw = (tmp_path / "news_dates.json").read_text()
    assert "SECRET" not in raw and "BODY" not in raw
    data = json.loads(raw)
    assert set(data) == {"https://nodate.example.test/post/1-a", "https://nodate.example.test/post/2-b",
                         "https://nodate.example.test/post/3-c"}
    assert all(set(v) == {"date", "checked"} for v in data.values())
    assert all("SECRET" not in e.snippet for e in res.entries)


def test_cache_hit_no_request(tmp_path):
    f, _, seen, dc = mk(tmp_path, routes_all())
    for i, d in ((1, 4), (2, 5), (3, 6)):
        dc.put(f"https://nodate.example.test/post/{i}-{'abc'[i-1]}/", date(2026, 11, d), today=UNTIL)
    res = f.fetch(SINCE, UNTIL)
    assert len(res.entries) == 3 and article_reqs(seen) == [] and res.date_lookups == 0


def test_robots_disallow_skips_without_cache(tmp_path):
    routes = routes_all()
    routes["https://nodate.example.test/robots.txt"] = "User-agent: *\nDisallow: /post/\n"
    f, _, seen, _ = mk(tmp_path, routes)
    res = f.fetch(SINCE, UNTIL)
    assert article_reqs(seen) == [] and res.entries == []  # require_date 丢弃
    assert not (tmp_path / "news_dates.json").exists()


def test_max_lookups_cap(tmp_path):
    f, _, seen, _ = mk(tmp_path, routes_all(), cfg_kw={"max_date_lookups_per_run": 2})
    res = f.fetch(SINCE, UNTIL)
    assert len(article_reqs(seen)) == 2 and res.date_lookups == 2 and len(res.entries) == 2


def test_redirect_followed(tmp_path):
    routes = routes_all()
    routes["https://nodate.example.test/post/1-a/"] = httpx.Response(
        301, headers={"location": "https://nodate.example.test/news/a/"})
    routes["https://nodate.example.test/news/a/"] = art(7)
    f, _, _, _ = mk(tmp_path, routes)
    res = f.fetch(SINCE, UNTIL)
    assert res.entries[0].published == date(2026, 11, 7)


def test_failure_not_cached_and_isolated(tmp_path):
    routes = routes_all()
    routes["https://nodate.example.test/post/1-a/"] = httpx.Response(500, text="boom")
    f, _, _, _ = mk(tmp_path, routes)
    res = f.fetch(SINCE, UNTIL)
    assert [e.title for e in res.entries] == ["Item two without date", "Item three without date"]
    data = json.loads((tmp_path / "news_dates.json").read_text())
    assert "https://nodate.example.test/post/1-a" not in data and len(data) == 2


def test_null_result_not_retried_within_7_days(tmp_path):
    routes = routes_all()
    routes["https://nodate.example.test/post/1-a/"] = "<html><body>no date</body></html>"
    f, _, seen, dc = mk(tmp_path, routes)
    f.fetch(SINCE, UNTIL)
    n = len(article_reqs(seen))
    f2 = NewsRSSFetcher(f.cfg, HTTP, client=f._client, sleep=lambda s: None,
                        date_cache=ArticleDateCache(tmp_path / "news_dates.json"))
    f2.fetch(SINCE, UNTIL)
    assert len(article_reqs(seen)) == n  # 缓存全部命中（含 null）


def test_require_date_false_keeps_undated_and_default_off(tmp_path):
    f, _, seen, _ = mk(tmp_path, routes_all(), feed_kw={"date_from_article": False, "require_date": False})
    res = f.fetch(SINCE, UNTIL)
    assert article_reqs(seen) == [] and len(res.entries) == 3
    assert all(e.published is None for e in res.entries)
    assert NewsFeed(name="x", url="https://x.test/f").date_from_article is False
