import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from aivc.config import HttpConfig, OpenReviewConfig, OpenReviewVenue
from aivc.fetchers.base import FetchError
from aivc.fetchers.openreview import OpenReviewFetcher, note_to_paper

FIX = Path(__file__).parent / "fixtures" / "openreview"
P1 = json.loads((FIX / "notes_page1.json").read_text())
P2 = json.loads((FIX / "notes_page2.json").read_text())
SINCE, UNTIL, TODAY = date(2026, 11, 20), date(2026, 11, 27), date(2026, 11, 28)


def make(venues, handler, token="", page_size=2, **kw):
    reqs = []

    def h(req):
        reqs.append(req)
        return handler(req)

    client = httpx.Client(transport=httpx.MockTransport(h))
    sleeps = []
    cfg = OpenReviewConfig(enabled=True, venues=venues, page_size=page_size, **kw)
    f = OpenReviewFetcher(cfg, HttpConfig(max_retries=0), client=client,
                          sleep=sleeps.append, today=TODAY, token=token)
    return f, reqs, sleeps


def paged(req):
    off = int(req.url.params["offset"])
    return httpx.Response(200, json=P1 if off == 0 else P2)


V = OpenReviewVenue(name="ICLR 2027", venueid="ICLR.cc/2027/Conference")


def test_paging_and_filters():
    f, reqs, sleeps = make([V], paged)
    papers = f.fetch(SINCE, UNTIL)
    assert [int(r.url.params["offset"]) for r in reqs] == [0, 2]
    assert len(reqs) == 2
    assert reqs[0].url.params["content.venueid"] == "ICLR.cc/2027/Conference"
    assert reqs[0].url.params["limit"] == "2"
    assert {p.openreview_id for p in papers} == {"AAA111", "CCC333", "EEE555"}  # BBB222 outside window, DDD444 restricted
    a = next(p for p in papers if p.openreview_id == "AAA111")
    assert a.code_url == "https://github.com/acme/flowpert" and a.has_code is True
    assert a.published == date(2026, 11, 25) and a.venue == "ICLR 2027 Poster"
    assert a.sources == ["openreview"] and a.first_seen == TODAY
    assert a.url == "https://openreview.net/forum?id=AAA111"
    c = next(p for p in papers if p.openreview_id == "CCC333")
    assert c.authors == [] and c.published == date(2026, 11, 22)  # odate fallback
    assert c.venue == "ICLR 2027" and c.weights_url == "https://huggingface.co/acme/cellmodel"
    assert sleeps and min(sleeps) >= 1.0


def test_invitation_param():
    v = OpenReviewVenue(name="X", invitation="ICLR.cc/2027/Conference/-/Submission")
    f, reqs, _ = make([v], lambda r: httpx.Response(200, json={"notes": [], "count": 0}))
    assert f.fetch(SINCE, UNTIL) == []
    assert reqs[0].url.params["invitation"].endswith("/-/Submission")
    assert "content.venueid" not in reqs[0].url.params


def test_auth_header():
    f, reqs, _ = make([V], lambda r: httpx.Response(200, json={"notes": [], "count": 0}), token="tok")
    f.fetch(SINCE, UNTIL)
    assert reqs[0].headers["Authorization"] == "Bearer tok"
    f, reqs, _ = make([V], lambda r: httpx.Response(200, json={"notes": [], "count": 0}))
    f.fetch(SINCE, UNTIL)
    assert "Authorization" not in reqs[0].headers


def test_token_from_env(monkeypatch):
    monkeypatch.setenv("OPENREVIEW_TOKEN", "envtok")
    f, reqs, _ = make([V], lambda r: httpx.Response(200, json={"notes": [], "count": 0}), token=None)
    f.fetch(SINCE, UNTIL)
    assert reqs[0].headers["Authorization"] == "Bearer envtok"


def test_max_notes_stops():
    f, reqs, _ = make([V], paged, max_notes_per_venue=2)
    f.fetch(SINCE, UNTIL)
    assert len(reqs) == 1


def test_string_venue_ignored():
    f, reqs, _ = make(["ICLR 2027", OpenReviewVenue(name="n")], paged)
    assert f.fetch(SINCE, UNTIL) == []
    assert reqs == []


def test_delay_floor():
    f, _, sleeps = make([V], paged, delay_seconds=0.1)
    f.fetch(SINCE, UNTIL)
    assert min(sleeps) >= 1.0


def test_500_raises():
    f, _, _ = make([V], lambda r: httpx.Response(500))
    with pytest.raises(FetchError):
        f.fetch(SINCE, UNTIL)


def test_note_to_paper_edge_cases():
    n = {"id": "Z", "readers": ["everyone"], "pdate": P1["notes"][0]["pdate"], "content": {"title": "  ", "abstract": "a"}}
    assert note_to_paper(n, "v", SINCE, UNTIL, TODAY) is None
    n["content"] = {"title": "Plain value title", "authors": ["A B", "Anonymous"]}
    p = note_to_paper(n, "v", SINCE, UNTIL, TODAY)
    assert p.title == "Plain value title" and p.authors == ["A B"] and p.has_code is None


# ---- B1 visibility gate / M10 paging ----

def _note(nid, **kw):
    n = {"id": nid, "readers": ["everyone"], "pdate": P1["notes"][0]["pdate"],
         "tmdate": P1["notes"][0]["pdate"], "content": {"title": {"value": f"T {nid}"}}}
    n.update(kw)
    return n


def test_restricted_note_dropped_with_token():
    f, _, _ = make([V], paged, token="tok")
    ids = {p.openreview_id for p in f.fetch(SINCE, UNTIL)}
    assert "DDD444" not in ids and "AAA111" in ids


def test_field_level_restricted_fields_stripped():
    f, _, _ = make([V], paged, token="tok")
    e = next(p for p in f.fetch(SINCE, UNTIL) if p.openreview_id == "EEE555")
    assert e.authors == [] and e.title == "Public paper with hidden authors"
    n = _note("Q", content={
        "title": {"value": "Kept"},
        "abstract": {"value": "hidden", "readers": ["Reviewers"]},
        "keywords": {"value": ["k"], "readers": ["Reviewers"]},
        "venue": {"value": "Secret", "readers": ["Reviewers"]},
        "authors": {"value": ["A"], "readers": ["everyone", "x"]}})
    p = note_to_paper(n, "VN", SINCE, UNTIL, TODAY)
    assert p.abstract is None and p.keywords == [] and p.venue == "VN" and p.authors == ["A"]
    n["content"]["title"]["readers"] = ["Reviewers"]
    assert note_to_paper(n, "VN", SINCE, UNTIL, TODAY) is None


def test_public_note_kept():
    p = note_to_paper(_note("OK"), "VN", SINCE, UNTIL, TODAY)
    assert p is not None and p.openreview_id == "OK"


def test_missing_or_bad_readers_dropped():
    n = _note("NR")
    del n["readers"]
    assert note_to_paper(n, "VN", SINCE, UNTIL, TODAY) is None
    assert note_to_paper(_note("S", readers="everyone"), "VN", SINCE, UNTIL, TODAY) is None
    assert note_to_paper(_note("E", readers=[]), "VN", SINCE, UNTIL, TODAY) is None


def test_sort_param_tmdate():
    f, reqs, _ = make([V], paged)
    f.fetch(SINCE, UNTIL)
    assert all(r.url.params["sort"] == "tmdate:desc" for r in reqs)


def test_early_created_recently_public_kept():
    n = _note("EARLY", cdate=1700000000000, pdate=None, odate=P1["notes"][0]["pdate"])
    n.pop("pdate")
    f, _, _ = make([V], lambda r: httpx.Response(200, json={"notes": [n], "count": 1}))
    assert [p.openreview_id for p in f.fetch(SINCE, UNTIL)] == ["EARLY"]


def test_sleep_between_venues():
    v2 = OpenReviewVenue(name="V2", venueid="X/2027")
    f, reqs, sleeps = make([V, v2], lambda r: httpx.Response(200, json={"notes": [], "count": 0}))
    f.fetch(SINCE, UNTIL)
    assert len(reqs) == 2 and len(sleeps) == 1 and sleeps[0] >= 1.0


def test_incomplete_when_cap_reached():
    f, reqs, _ = make([V], paged, max_notes_per_venue=2)
    assert f.last_incomplete is False and f.last_warning is None
    f.fetch(SINCE, UNTIL)
    assert f.last_incomplete is True and "max_notes_per_venue=2" in f.last_warning
    f2, _, _ = make([V], paged)
    f2.fetch(SINCE, UNTIL)
    assert f2.last_incomplete is False and f2.last_warning is None


def test_stops_after_crossing_since():
    old = _note("OLD", tmdate=1700000000000, pdate=1700000000000)
    f, reqs, _ = make([V], lambda r: httpx.Response(200, json={"notes": [old], "count": 99}))
    f.fetch(SINCE, UNTIL)
    assert len(reqs) == 1 and f.last_incomplete is False
