import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from aivc.fetchers.base import FetchError
from aivc.fetchers.europepmc import EuropePMCFetcher, result_to_paper

FIX = Path(__file__).parent / "fixtures" / "europepmc"
TODAY = date(2026, 11, 14)
HTTP = SimpleNamespace(max_retries=1, user_agent="t", timeout=5)


def load(name):
    return json.loads((FIX / name).read_text())


def make_cfg(queries=("virtual cell",), max_pages=10):
    return SimpleNamespace(
        base_url="https://example.test/search", queries=list(queries),
        page_size=2, delay_seconds=1.0, max_pages=max_pages,
    )


def fetcher(handler, **kw):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    sleeps = []
    return EuropePMCFetcher(make_cfg(**kw), HTTP, client=client, sleep=sleeps.append, today=TODAY), sleeps


def paged(requests):
    def h(req):
        requests.append(req)
        if req.url.params["cursorMark"] == "*":
            return httpx.Response(200, json=load("search_page1.json"))
        return httpx.Response(200, json=load("search_page2.json"))
    return h


def test_journal_article_fields():
    r = load("search_page1.json")["resultList"]["result"][0]
    p = result_to_paper(r, TODAY)
    assert p.id == "doi:10.1038/s41592-026-00001-x"
    assert p.pmid == "39000001" and p.pmcid == "PMC9000001"
    assert p.abstract == "Background We present a model. Code is at https://github.com/example-lab/vcell-model. Results It works well."
    assert "<" not in p.abstract
    assert p.authors == ["Doe J", "Roe R"]
    assert p.affiliations == ["Example Institute, Cambridge, UK.", "Other University, Boston, USA."]
    assert p.venue == "Nature Methods" and p.citation_count == 12
    assert p.published == date(2026, 11, 5) and p.license == "cc by"
    assert p.url == "https://europepmc.org/article/MED/39000001"
    assert p.pdf_url == "https://europepmc.org/articles/PMC9000001?pdf=render"
    assert p.source == "europepmc" and p.sources == ["europepmc"]
    assert p.code_url == "https://github.com/example-lab/vcell-model" and p.has_code
    assert p.published_venue == "Nature Methods"
    assert p.published_doi == "10.1038/s41592-026-00001-x"


def test_preprint_has_no_published_fields():
    r = load("search_page1.json")["resultList"]["result"][1]
    p = result_to_paper(r, TODAY)
    assert p.biorxiv_id and p.id == f"biorxiv:{p.biorxiv_id}"
    assert p.published_venue is None and p.published_doi is None
    assert p.authors == ["Smith A", "Lee B"]


def test_id_priority_and_authorstring_fallback():
    p = result_to_paper({"id": "1", "source": "MED", "pmid": "1", "title": "T",
                         "authorString": "A B, C D."}, TODAY)
    assert p.id == "pmid:1" and p.authors == ["A B", "C D"]
    p = result_to_paper({"id": "1", "source": "MED", "pmid": "1", "doi": "10.1/XY", "title": "T"}, TODAY)
    assert p.id == "doi:10.1/xy"
    assert result_to_paper({"id": "1", "title": "  "}, TODAY) is None


def test_query_and_paging():
    reqs = []
    f, sleeps = fetcher(paged(reqs))
    papers = f.fetch(date(2026, 11, 1), date(2026, 11, 10))
    assert len(papers) == 3
    assert len(reqs) == 2
    q = reqs[0].url.params["query"]
    assert q == "(virtual cell) AND FIRST_PDATE:[2026-11-01 TO 2026-11-10]"
    p0 = reqs[0].url.params
    assert p0["format"] == "json" and p0["resultType"] == "core" and p0["pageSize"] == "2"
    assert reqs[1].url.params["cursorMark"] == "AoIIP4AAACgxMjM0"
    assert sleeps == [1.0]


def test_empty_stops():
    reqs = []

    def h(req):
        reqs.append(req)
        return httpx.Response(200, json=load("search_empty.json"))
    f, _ = fetcher(h)
    assert f.fetch(date(2026, 11, 1), date(2026, 11, 2)) == []
    assert len(reqs) == 1


def test_max_pages():
    def h(req):
        d = load("search_page1.json")
        d["nextCursorMark"] = req.url.params["cursorMark"] + "x"
        return httpx.Response(200, json=d)
    f, sleeps = fetcher(h, max_pages=3)
    f.fetch(date(2026, 11, 1), date(2026, 11, 2))
    assert len(sleeps) == 2


def test_multi_query_dedup_and_sleep():
    reqs = []
    f, sleeps = fetcher(paged(reqs), queries=("a", "b"))
    papers = f.fetch(date(2026, 11, 1), date(2026, 11, 10))
    assert len(papers) == 3 and len(reqs) == 4
    assert sleeps == [1.0, 1.0, 1.0]


def test_500_raises():
    f, _ = fetcher(lambda req: httpx.Response(500))
    with pytest.raises(FetchError):
        f.fetch(date(2026, 11, 1), date(2026, 11, 2))


def test_non_json_raises():
    f, _ = fetcher(lambda req: httpx.Response(200, text="<html>"))
    with pytest.raises(FetchError):
        f.fetch(date(2026, 11, 1), date(2026, 11, 2))
