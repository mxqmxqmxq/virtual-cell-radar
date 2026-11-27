from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from aivc.fetchers.arxiv import ArxivFetcher, parse_arxiv_feed
from aivc.fetchers.base import FetchError

FIX = Path(__file__).parent / "fixtures" / "arxiv"
SAMPLE = (FIX / "sample_feed.xml").read_text()
EMPTY = (FIX / "empty_feed.xml").read_text()
TODAY = date(2026, 11, 14)


def cfg(**kw):
    d = dict(
        base_url="https://export.arxiv.org/api/query",
        bio_categories=["q-bio.GN", "q-bio.QM"],
        ml_categories=["cs.LG"],
        query_terms=["virtual cell", "perturbation prediction"],
        page_size=100,
        delay_seconds=3.0,
        max_results_per_window=2000,
    )
    d.update(kw)
    return SimpleNamespace(**d)


HTTP = SimpleNamespace(user_agent="virtual-cell-radar/0.1 (+https://github.com/mxqmxqmxq/virtual-cell-radar)", timeout=10, max_retries=1)


def make(handler, **kw):
    sleeps = []
    client = httpx.Client(transport=httpx.MockTransport(handler))
    f = ArxivFetcher(cfg(**kw), HTTP, client=client, sleep=sleeps.append, today=TODAY)
    return f, sleeps


def feed_with(n_entries, total, start_id=0):
    entries = "".join(
        f"""<entry><id>http://arxiv.org/abs/2611.{start_id + i:05d}v1</id><title>T{start_id + i}</title>
<updated>2026-11-08T00:00:00Z</updated><published>2026-11-08T00:00:00Z</published><summary>s</summary>
<category term="q-bio.GN"/><arxiv:primary_category xmlns:arxiv="http://arxiv.org/schemas/atom" term="q-bio.GN"/>
<author><name>A</name></author></entry>"""
        for i in range(n_entries)
    )
    return f"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"
xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"><opensearch:totalResults>{total}</opensearch:totalResults>
{entries}</feed>"""


def test_parse_fields():
    papers, total = parse_arxiv_feed(SAMPLE, TODAY)
    assert total == 3 and len(papers) == 3
    a, b, c = papers
    assert a.id == "arxiv:2611.11217" and a.arxiv_id == "2611.11217" and a.version == "v1"
    assert a.url == "https://arxiv.org/abs/2611.11217"
    assert a.pdf_url == "https://arxiv.org/pdf/2611.11217"
    assert a.categories == ["cs.LG", "q-bio.GN", "q-bio.QM"]
    assert a.authors == ["Alice Zhang", "Bob Li", "Carol Smith"]
    assert a.published == date(2026, 11, 8) and a.updated == date(2026, 11, 9)
    assert a.code_url == "https://github.com/example-lab/scbig" and a.has_code
    assert a.source == "arxiv" and a.first_seen == TODAY
    assert b.title == "A Virtual Cell Foundation Model for Drug Response Prediction"
    assert "\n" not in b.abstract and b.version == "v2"
    assert b.code_url == "https://github.com/org/vcell"
    assert b.doi == "10.1038/s41592-026-00001-x" and b.venue == "Nature Methods 2026"
    assert c.code_url is None and not c.has_code and c.doi is None


def test_parse_empty():
    assert parse_arxiv_feed(EMPTY, TODAY) == ([], 0)


def test_fetch_basic_dedup_across_groups_and_query():
    reqs = []

    def handler(req):
        reqs.append(req)
        return httpx.Response(200, text=SAMPLE)

    f, sleeps = make(handler)
    papers = f.fetch(date(2026, 11, 1), date(2026, 11, 8))
    assert len(reqs) == 2
    assert [p.arxiv_id for p in papers] == ["2611.11217", "2611.10001", "2611.09999"]
    for p in papers:
        assert p.id.startswith("arxiv:") and "v" not in p.arxiv_id.split(".")[-1]
        assert "\n" not in p.title and "\n" not in (p.abstract or "")
    q0 = reqs[0].url.params["search_query"]
    q1 = reqs[1].url.params["search_query"]
    assert "submittedDate:[202611010000 TO 202611082359]" in q0
    assert "cat:q-bio.GN OR cat:q-bio.QM" in q0 and "abs:" not in q0
    assert 'cat:cs.LG' in q1 and 'abs:"virtual cell"' in q1 and 'ti:"virtual cell"' in q1 and "202611082359" in q1
    assert reqs[0].url.params["sortBy"] == "submittedDate"
    assert reqs[0].url.params["max_results"] == "100"
    assert sleeps and all(s == 3.0 for s in sleeps)


def test_pagination_two_pages():
    starts = []

    def handler(req):
        s = int(req.url.params["start"])
        q = req.url.params["search_query"]
        starts.append((("abs:" in q), s))
        if "abs:" in q:
            return httpx.Response(200, text=feed_with(0, 0))
        return httpx.Response(200, text=feed_with(2 if s == 0 else 1, 3, start_id=s * 10))

    f, sleeps = make(handler, page_size=2)
    papers = f.fetch(date(2026, 11, 1), date(2026, 11, 8))
    assert len(papers) == 3
    assert (False, 0) in starts and (False, 2) in starts and (False, 4) not in starts
    assert sleeps.count(3.0) == len(sleeps) and len(sleeps) >= 2


def test_max_results_cap():
    def handler(req):
        s = int(req.url.params["start"])
        return httpx.Response(200, text=feed_with(2, 1000, start_id=s * 10 + (7 if "abs:" in req.url.params["search_query"] else 0)))

    f, _ = make(handler, page_size=2, max_results_per_window=4, query_terms=[])
    papers = f.fetch(date(2026, 11, 1), date(2026, 11, 8))
    assert len(papers) == 4


def test_empty_page_retry():
    calls = {"n": 0}

    def handler(req):
        if "abs:" in req.url.params["search_query"]:
            return httpx.Response(200, text=EMPTY)
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(200, text=feed_with(0, 3))
        return httpx.Response(200, text=SAMPLE)

    f, sleeps = make(handler)
    papers = f.fetch(date(2026, 11, 1), date(2026, 11, 8))
    assert calls["n"] == 3 and len(papers) == 3
    assert sleeps.count(3.0) >= 2


def test_empty_page_gives_up_after_retries():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, text=feed_with(0, 5))

    f, _ = make(handler, ml_categories=[])
    assert f.fetch(date(2026, 11, 1), date(2026, 11, 8)) == []
    assert calls["n"] == 3  # initial + 2 retries


def test_http_500_raises_fetcherror():
    f, sleeps = make(lambda req: httpx.Response(500))
    with pytest.raises(FetchError):
        f.fetch(date(2026, 11, 1), date(2026, 11, 8))


def test_window_over_limit_is_split():
    """结果超过上限时按日期二分，而不是静默丢掉最早的论文。"""
    seen = []

    def handler(req):
        q = req.url.params["search_query"]
        seen.append(q)
        if "q-bio" not in q:
            return httpx.Response(200, text=feed_with(0, 0))
        # 全窗口 5 条 > 上限 3；拆开后每半 <= 3
        if "202611010000 TO 202611042359" in q:
            return httpx.Response(200, text=feed_with(3, 5))
        if "202611010000 TO 202611022359" in q:
            return httpx.Response(200, text=feed_with(2, 2, start_id=0))
        if "202611030000 TO 202611042359" in q:
            return httpx.Response(200, text=feed_with(3, 3, start_id=10))
        raise AssertionError(q)

    f, _ = make(handler, page_size=10, max_results_per_window=3)
    papers = f.fetch(date(2026, 11, 1), date(2026, 11, 4))
    assert len(papers) == 5
    assert any("202611010000 TO 202611022359" in q for q in seen)


def test_weights_url_from_abstract():
    xml = SAMPLE.replace("https://github.com/example-lab/scbig",
                         "https://huggingface.co/acme/scbig-model")
    papers, _ = parse_arxiv_feed(xml, TODAY)
    a = papers[0]
    assert a.weights_url == "https://huggingface.co/acme/scbig-model"
    assert a.has_weights is True and a.sources == ["arxiv"]
    assert papers[2].has_weights is None


def test_html_200_raises_fetcherror():
    f, _ = make(lambda req: httpx.Response(200, text="<html><body>Service busy</body></html>"))
    with pytest.raises(FetchError, match="Atom"):
        f.fetch(date(2026, 11, 1), date(2026, 11, 8))


def test_empty_atom_feed_still_ok():
    assert parse_arxiv_feed(EMPTY, TODAY) == ([], 0)


def test_empty_page_exhausted_marks_incomplete_keeps_results():
    def handler(req):
        s = int(req.url.params["start"])
        if s == 0:
            return httpx.Response(200, text=feed_with(2, 5))
        return httpx.Response(200, text=feed_with(0, 5))

    f, _ = make(handler, page_size=2, ml_categories=[])
    papers = f.fetch(date(2026, 11, 1), date(2026, 11, 8))
    assert len(papers) == 2
    assert f.last_incomplete is True and "start=2" in f.last_warning
    # 下一次 fetch 复位
    f.fetch(date(2026, 11, 1), date(2026, 11, 8))
    assert f.last_incomplete is True  # 同样的响应再次不完整


def test_single_day_over_limit_marks_incomplete():
    def handler(req):
        s = int(req.url.params["start"])
        return httpx.Response(200, text=feed_with(2, 1000, start_id=s * 10))

    f, _ = make(handler, page_size=2, max_results_per_window=4, ml_categories=[])
    papers = f.fetch(date(2026, 11, 3), date(2026, 11, 3))
    assert len(papers) == 4
    assert f.last_incomplete is True and "单日" in f.last_warning


def test_normal_pagination_not_incomplete():
    def handler(req):
        s = int(req.url.params["start"])
        return httpx.Response(200, text=feed_with(2 if s == 0 else 1, 3, start_id=s * 10))

    f, _ = make(handler, page_size=2, ml_categories=[])
    assert len(f.fetch(date(2026, 11, 1), date(2026, 11, 8))) == 3
    assert f.last_incomplete is False and f.last_warning is None
