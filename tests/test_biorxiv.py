import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from aivc.fetchers.base import FetchError
from aivc.fetchers.biorxiv import BiorxivFetcher, parse_biorxiv_page

FIX = Path(__file__).parent / "fixtures" / "biorxiv"
TODAY = date(2026, 11, 14)


def load(name):
    return json.loads((FIX / name).read_text())


def make_cfg(server="biorxiv", categories=("systems biology",), max_pages=400):
    return SimpleNamespace(
        server=server, base_url="https://api.biorxiv.org", categories=list(categories),
        delay_seconds=0.5, max_pages_per_category=max_pages,
    )


HTTP = SimpleNamespace(max_retries=2, user_agent="t", timeout=5)


def fetcher(handler, **kw):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    sleeps = []
    f = BiorxivFetcher(make_cfg(**kw), HTTP, client=client, sleep=sleeps.append, today=TODAY)
    return f, sleeps


def paged_handler(requests):
    pages = {0: load("page1.json"), 3: load("page2.json")}

    def h(req):
        requests.append(req)
        cursor = int(req.url.path.rsplit("/", 1)[1])
        return httpx.Response(200, json=pages.get(cursor, load("empty.json")))
    return h


def test_parse_fields():
    papers, total = parse_biorxiv_page(load("page1.json"), "biorxiv", TODAY)
    assert total == 5 and len(papers) == 3
    p = papers[0]
    assert p.id == "biorxiv:2026.11.12.700001"
    assert p.doi == "10.64898/2026.11.12.700001"
    assert p.authors == ["Schmirler, R.", "Brenner, M.", "Doe, J."]
    assert p.affiliations == ["Example University"]
    assert p.source == "biorxiv" and p.version == "v1"
    assert p.published == date(2026, 11, 12) and p.updated == date(2026, 11, 12)
    assert p.url == "https://www.biorxiv.org/content/10.64898/2026.11.12.700001v1"
    assert p.pdf_url == p.url + ".full.pdf"
    assert p.categories == ["systems biology"]
    assert p.venue is None and p.first_seen == TODAY
    assert p.code_url == "https://github.com/example/perturb-fm" and p.has_code is True


def test_both_prefixes_and_published_date():
    papers, _ = parse_biorxiv_page(load("page1.json"), "biorxiv", TODAY)
    assert papers[0].biorxiv_id == "2026.11.12.700001"
    p = papers[1]
    assert p.doi.startswith("10.1101/") and p.biorxiv_id == "2026.11.10.600002"
    assert p.published == date(2026, 11, 10) and p.updated == date(2026, 11, 12)
    assert p.venue == "10.1038/s41586-026-00001-x"
    assert p.affiliations == [] and p.has_code is None


def test_pagination_total_string_and_latest_version():
    reqs = []
    f, sleeps = fetcher(paged_handler(reqs))
    papers = f.fetch(date(2026, 11, 12), date(2026, 11, 13))
    assert [r.url.path.rsplit("/", 1)[1] for r in reqs] == ["0", "3"]
    assert len(papers) == 4
    grn = next(p for p in papers if p.biorxiv_id == "2026.11.10.600002")
    assert grn.version == "v3" and grn.updated == date(2026, 11, 13)
    assert sleeps == [0.5]


def test_category_underscore_and_url():
    reqs = []
    f, _ = fetcher(paged_handler(reqs))
    f.fetch(date(2026, 11, 12), date(2026, 11, 13))
    r = reqs[0]
    assert r.url.params["category"] == "systems_biology"
    assert r.url.path == "/details/biorxiv/2026-11-12/2026-11-13/0"


def test_multi_category_dedup():
    reqs = []
    f, sleeps = fetcher(paged_handler(reqs), categories=("systems biology", "bioinformatics"))
    papers = f.fetch(date(2026, 11, 12), date(2026, 11, 13))
    assert len(reqs) == 4
    assert len(papers) == 4
    assert len({p.id for p in papers}) == 4
    assert len(sleeps) == 3


def test_empty():
    f, _ = fetcher(lambda req: httpx.Response(200, json=load("empty.json")))
    assert f.fetch(date(2026, 11, 12), date(2026, 11, 13)) == []


def test_error_status_raises():
    f, _ = fetcher(lambda req: httpx.Response(200, json={"messages": [{"status": "error"}], "collection": []}))
    with pytest.raises(FetchError):
        f.fetch(date(2026, 11, 12), date(2026, 11, 13))


def test_persistent_500():
    f, _ = fetcher(lambda req: httpx.Response(500))
    with pytest.raises(FetchError):
        f.fetch(date(2026, 11, 12), date(2026, 11, 13))


def test_max_pages():
    reqs = []
    f, _ = fetcher(paged_handler(reqs), max_pages=1)
    f.fetch(date(2026, 11, 12), date(2026, 11, 13))
    assert len(reqs) == 1


def test_medrxiv():
    reqs = []
    f, _ = fetcher(paged_handler(reqs), server="medrxiv")
    papers = f.fetch(date(2026, 11, 12), date(2026, 11, 13))
    assert f.name == "medrxiv"
    assert reqs[0].url.path.startswith("/details/medrxiv/")
    assert all(p.source == "medrxiv" and p.url.startswith("https://www.medrxiv.org/content/") for p in papers)


def test_license_kept_verbatim_or_none():
    payload = load("page1.json")
    payload["collection"][2]["license"] = "  "
    del payload["collection"][1]["license"]
    papers, _ = parse_biorxiv_page(payload, "biorxiv", TODAY)
    assert papers[0].license == "cc_by"
    assert papers[1].license is None and papers[2].license is None
    assert parse_biorxiv_page(load("page1.json"), "biorxiv", TODAY)[0][1].license == "cc_no"
