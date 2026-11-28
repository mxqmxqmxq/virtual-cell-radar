import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from aivc.config import JournalIssn
from aivc.fetchers.base import FetchError
from aivc.fetchers.openalex import (
    OpenAlexFetcher, reconstruct_abstract, short_openalex_id, work_signals, work_to_paper,
)

FIX = Path(__file__).parent / "fixtures" / "openalex"
TODAY = date(2026, 11, 14)
HTTP = SimpleNamespace(max_retries=1, user_agent="t", timeout=5)


def load(name):
    return json.loads((FIX / name).read_text())


def make_cfg(**kw):
    d = dict(
        base_url="https://api.openalex.org/works",
        journal_issns=[JournalIssn(name="Nature Methods", issn=["1548-7091", "1548-7105"]),
                       JournalIssn(name="Cell", issn=["0092-8674"])],
        domain_terms=["virtual cell", "single-cell"], queries=[], mailto="",
        api_key_env="OPENALEX_API_KEY", per_page=50, delay_seconds=0.5, max_pages=10,
    )
    d.update(kw)
    return SimpleNamespace(**d)


def fetcher(handler, api_key=None, **kw):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    sleeps = []
    f = OpenAlexFetcher(make_cfg(**kw), HTTP, client=client, sleep=sleeps.append,
                        today=TODAY, api_key=api_key)
    return f, sleeps


def paged(requests):
    def h(req):
        requests.append(req)
        cur = req.url.params.get("cursor")
        name = "works_page1.json" if cur == "*" else "works_page2.json" if cur == "CUR2" else "works_empty.json"
        return httpx.Response(200, json=load(name))
    return h


def test_reconstruct_abstract_order():
    assert reconstruct_abstract({"world": [1], "hello": [0], "again": [2, 3]}) == "hello world again again"
    assert reconstruct_abstract({}) is None
    assert reconstruct_abstract(None) is None


def test_short_id():
    assert short_openalex_id("https://openalex.org/W123") == "W123"
    assert short_openalex_id("W9") == "W9"
    assert short_openalex_id(None) is None


def test_work_signals():
    s = work_signals(load("works_page1.json")["results"][0])
    assert s["openalex_id"] == "W1001"
    assert s["doi"] == "10.1038/s41592-026-00001-1"
    assert s["pmid"] == "39000001"
    assert s["venue"] == "Nature Methods" and s["venue_type"] == "journal"
    assert s["citation_count"] == 42
    assert s["affiliations"] == ["Example University", "Example Institute"]
    assert s["license"] == "cc-by"
    assert s["is_journal_article"] is True
    assert s["arxiv_id"] is None
    s3 = work_signals(load("works_page2.json")["results"][0])
    assert s3["arxiv_id"] == "2611.01234"
    assert s3["is_journal_article"] is False


def test_journal_article_promotion_fields():
    p = work_to_paper(load("works_page1.json")["results"][0], TODAY)
    assert p.source == "openalex" and p.sources == ["openalex"]
    assert p.title == "A foundation model for virtual cell perturbation"
    assert p.published_venue == "Nature Methods"
    assert p.published_doi == "10.1038/s41592-026-00001-1"
    assert p.id == "doi:10.1038/s41592-026-00001-1"
    assert p.pmid == "39000001" and p.openalex_id == "W1001"
    assert p.authors == ["Alice Example", "Bob Sample"]
    assert p.code_url == "https://github.com/example-lab/vcmodel" and p.has_code is True
    assert p.weights_url == "https://huggingface.co/example-lab/vcmodel-base" and p.has_weights is True
    assert p.pdf_url == "https://example.org/w1001.pdf"
    assert p.published == date(2026, 11, 10) and p.first_seen == TODAY
    assert p.citation_count == 42


def test_preprint_not_promoted():
    p = work_to_paper(load("works_page1.json")["results"][1], TODAY)
    assert p.published_venue is None and p.published_doi is None
    assert p.id == "biorxiv:2026.01.02.000001"
    assert p.has_code is None and p.has_weights is None
    assert p.pdf_url.endswith(".full.pdf")


def test_id_priority():
    w = load("works_page2.json")["results"][0]
    assert work_to_paper(w, TODAY).id == "arxiv:2611.01234"
    w = dict(w, locations=[])
    assert work_to_paper(w, TODAY).id == "doi:10.9999/example.3"
    w = dict(w, doi=None, ids={"openalex": w["id"], "pmid": "https://pubmed.ncbi.nlm.nih.gov/55"})
    assert work_to_paper(w, TODAY).id == "pmid:55"
    w = dict(w, ids={"openalex": w["id"]})
    assert work_to_paper(w, TODAY).id == "openalex:W1003"


def test_no_title_returns_none():
    assert work_to_paper({"id": "https://openalex.org/W1", "display_name": ""}, TODAY) is None


def test_fetch_mode_a_params_and_paging():
    reqs = []
    f, sleeps = fetcher(paged(reqs))
    papers = f.fetch(date(2026, 11, 1), date(2026, 11, 14))
    assert len(papers) == 3 and len(reqs) == 2
    q1 = reqs[0].url.params
    assert "primary_location.source.issn:1548-7091|1548-7105|0092-8674" in q1["filter"]
    assert "from_publication_date:2026-11-01" in q1["filter"]
    assert "to_publication_date:2026-11-14" in q1["filter"]
    assert q1["search"] == '"virtual cell" OR "single-cell"'
    assert q1["per-page"] == "50" and q1["cursor"] == "*"
    assert reqs[1].url.params["cursor"] == "CUR2"
    assert sleeps == [0.5]
    assert "mailto" not in q1 and "api_key" not in q1


def test_mailto_and_api_key():
    reqs = []
    f, _ = fetcher(paged(reqs), api_key="K", mailto="me@example.org")
    f.fetch(date(2026, 11, 1), date(2026, 11, 14))
    assert reqs[0].url.params["mailto"] == "me@example.org"
    assert reqs[0].url.params["api_key"] == "K"


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "ENVK")
    f, _ = fetcher(lambda r: httpx.Response(200, json=load("works_empty.json")))
    assert f.api_key == "ENVK"


def test_mode_b_one_request_per_query():
    reqs = []

    def h(req):
        reqs.append(req)
        return httpx.Response(200, json=load("works_empty.json"))
    f, sleeps = fetcher(h, journal_issns=[], domain_terms=[], queries=["virtual cell", "perturbation"])
    assert f.fetch(date(2026, 11, 1), date(2026, 11, 14)) == []
    assert [r.url.params["search"] for r in reqs] == ["virtual cell", "perturbation"]
    assert "primary_location" not in reqs[0].url.params["filter"]
    assert sleeps == [0.5]


def test_anonymous_cursor_401_keeps_first_page():
    def h(req):
        if req.url.params.get("cursor") == "*":
            return httpx.Response(200, json=load("works_page1.json"))
        return httpx.Response(401, json={"error": "API key required for cursor paging"})
    f, _ = fetcher(h, api_key=None)
    papers = f.fetch(date(2026, 11, 1), date(2026, 11, 14))
    assert len(papers) == 2
    assert f.last_incomplete is True
    assert "OPENALEX_API_KEY" in f.last_warning


def test_large_issn_list_chunked():
    reqs = []
    issns = [JournalIssn(name=f"J{i}", issn=[f"1000-{i:04d}"]) for i in range(20)]
    f, _ = fetcher(lambda req: (reqs.append(req), httpx.Response(200, json=load("works_empty.json")))[1],
                   journal_issns=issns)
    f.fetch(date(2026, 11, 1), date(2026, 11, 14))
    assert len(reqs) == 2
    assert "1000-0000" in reqs[0].url.params["filter"]
    assert "1000-0019" in reqs[1].url.params["filter"]
    assert f.last_incomplete is False


def test_max_pages_truncates():
    reqs = []
    f, _ = fetcher(paged(reqs), max_pages=1)
    assert len(f.fetch(date(2026, 11, 1), date(2026, 11, 14))) == 2
    assert len(reqs) == 1


def test_dedup_by_id():
    def h(req):
        return httpx.Response(200, json=dict(load("works_page1.json"), meta={"next_cursor": None}))
    f, _ = fetcher(h, queries=["a", "b"], journal_issns=[])
    assert len(f.fetch(date(2026, 11, 1), date(2026, 11, 14))) == 2


def test_500_raises():
    f, _ = fetcher(lambda r: httpx.Response(500))
    with pytest.raises(FetchError):
        f.fetch(date(2026, 11, 1), date(2026, 11, 14))


def test_non_json_raises():
    f, _ = fetcher(lambda r: httpx.Response(200, text="<html>"))
    with pytest.raises(FetchError):
        f.fetch(date(2026, 11, 1), date(2026, 11, 14))


def test_issn_and_queries_both_run():
    reqs = []

    def h(req):
        reqs.append(req)
        return httpx.Response(200, json=load("works_empty.json"))
    f, _ = fetcher(h, queries=["virtual cell", "perturbation"])
    f.fetch(date(2026, 11, 1), date(2026, 11, 14))
    assert len(reqs) == 3
    assert "primary_location.source.issn" in reqs[0].url.params["filter"]
    assert [r.url.params["search"] for r in reqs[1:]] == ["virtual cell", "perturbation"]


def _work(wid, title="Same Paper", **kw):
    w = {"id": f"https://openalex.org/{wid}", "display_name": title, "type": "preprint",
         "publication_date": "2026-11-01", "ids": {}, "primary_location": {
             "landing_page_url": "https://arxiv.org/abs/2611.00001v2",
             "source": {"display_name": "arXiv", "type": "repository"}}}
    w.update(kw)
    return w


def _journal_work():
    return _work("W2", type="article", primary_location={
        "landing_page_url": "https://doi.org/10.1/x",
        "source": {"display_name": "Nature Methods", "type": "journal"}},
        locations=[{"landing_page_url": "https://arxiv.org/abs/2611.00001"}])


def _fetch_works(works):
    def h(req):
        return httpx.Response(200, json={"results": works, "meta": {"next_cursor": None}})
    f, _ = fetcher(h, journal_issns=[], queries=["q"])
    return f.fetch(date(2026, 11, 1), date(2026, 11, 14))


@pytest.mark.parametrize("order", [0, 1])
def test_same_id_prefers_journal_version_any_order(order):
    ws = [_work("W1", locations=[]), _journal_work()]
    if order:
        ws.reverse()
    out = _fetch_works(ws)
    assert len(out) == 1
    assert out[0].published_venue == "Nature Methods"


def test_same_id_prefers_richer_preprint():
    poor = _work("W1")
    rich = _work("W3", cited_by_count=9, abstract_inverted_index={"An": [0], "abstract.": [1]})
    for ws in ([poor, rich], [rich, poor]):
        out = _fetch_works(ws)
        assert len(out) == 1
        assert out[0].abstract == "An abstract." and out[0].citation_count == 9


def test_arxiv_id_from_pdf_and_variants():
    def sig(url):
        return work_signals({"primary_location": {"landing_page_url": url}})["arxiv_id"]
    assert sig("https://arxiv.org/pdf/2301.12345v2.pdf") == "2301.12345"
    assert sig("https://arxiv.org/abs/2301.12345") == "2301.12345"
    assert sig("https://export.arxiv.org/abs/q-bio/0601001v1") == "q-bio/0601001"
    assert work_signals({"primary_location": {"landing_page_url": "https://x.org/a",
                                              "pdf_url": "https://arxiv.org/pdf/2301.12345"}})["arxiv_id"] == "2301.12345"


def test_arxiv_id_rejects_untrusted_host():
    for url in ("https://evil.com/arxiv.org/abs/2301.12345",
                "https://arxiv.org.evil.com/abs/2301.12345",
                "https://evil.com/abs/2301.12345"):
        assert work_signals({"primary_location": {"landing_page_url": url}})["arxiv_id"] is None


def test_pmid_trailing_slash():
    assert work_signals({"ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/12345/"}})["pmid"] == "12345"


def test_max_pages_marks_incomplete_and_clean_run_not():
    f, _ = fetcher(paged([]), max_pages=1)
    f.fetch(date(2026, 11, 1), date(2026, 11, 14))
    assert f.last_incomplete is True
    f2, _ = fetcher(paged([]))
    f2.fetch(date(2026, 11, 1), date(2026, 11, 14))
    assert f2.last_incomplete is False and f2.last_warning is None
