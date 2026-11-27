import json
from datetime import date
from pathlib import Path

import httpx

from aivc.config import load_config
from aivc.enrich import enrich_offline, enrich_via_openalex, load_lab_names
from aivc.models import Paper

FIX = Path(__file__).parent / "fixtures" / "openalex"
CONFIG = Path(__file__).parent.parent / "config"


def cfg():
    return load_config(CONFIG)


def mk(pid="p1", **kw):
    d = dict(id=pid, title="T", source="arxiv", first_seen=date(2026, 11, 1), sources=["arxiv"])
    d.update(kw)
    return Paper(**d)


def test_affiliation_match_and_idempotent():
    c = cfg()
    p = mk(affiliations=["Arc Institute, Palo Alto"])
    [q] = enrich_offline([p], c)
    assert q.matched_orgs == ["Arc Institute"]
    assert p.matched_orgs == []
    [r] = enrich_offline([q], c)
    assert r is q


def test_abstract_paper_terms_only():
    c = cfg()
    [a] = enrich_offline([mk(abstract="We release Tahoe-100M, a perturbation atlas.")], c)
    assert a.matched_orgs == [] and a.mentioned_orgs == ["Tahoe Therapeutics"]
    [b] = enrich_offline([mk(abstract="An arc of Arc cells along the Arc.")], c)
    assert b.matched_orgs == []


def test_labs_in_affiliations_only():
    c = cfg()
    [a] = enrich_offline([mk(affiliations=["Theis Lab, Munich"])], c, labs=["Theis Lab"])
    assert a.matched_orgs == ["Theis Lab"]
    [b] = enrich_offline([mk(abstract="Theis Lab did this")], c, labs=["Theis Lab"])
    assert b.matched_orgs == []


def test_unchanged_returns_same_object():
    p = mk()
    [q] = enrich_offline([p], cfg())
    assert q is p


def test_links_and_sources():
    p = mk(source="biorxiv", sources=[],
           abstract="Code: https://github.com/foo/bar and weights https://huggingface.co/foo/bar-model.")
    [q] = enrich_offline([p], cfg())
    assert q.code_url == "https://github.com/foo/bar" and q.has_code is True
    assert q.weights_url == "https://huggingface.co/foo/bar-model" and q.has_weights is True
    assert q.sources == ["biorxiv"]
    keep = mk(code_url="https://github.com/x/y", abstract="https://github.com/foo/bar")
    [k] = enrich_offline([keep], cfg())
    assert k.code_url == "https://github.com/x/y"


def test_load_lab_names(tmp_path):
    assert load_lab_names(tmp_path) == []
    (tmp_path / "resources").mkdir()
    (tmp_path / "resources" / "labs.yaml").write_text(
        "resources:\n  - {id: a, name: 'Theis Lab（Helmholtz Munich）', kind: lab}\n"
        "  - {id: b, name: 'Some Tool', kind: tool}\n", encoding="utf-8")
    assert load_lab_names(tmp_path) == ["Theis Lab", "Helmholtz Munich"]


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _papers():
    return [
        mk("a", doi="10.1038/S41592-026-00002-2", affiliations=["Arc Institute"], citation_count=5),
        mk("b", source="biorxiv", doi="10.1101/2026.10.01.123456"),
        mk("c"),
    ]


def test_openalex_refresh():
    reqs = []

    def h(req):
        reqs.append(req)
        return httpx.Response(200, json=json.loads((FIX / "enrich_batch.json").read_text()))

    res = enrich_via_openalex(_papers(), cfg(), client=_client(h), sleep=lambda s: None)
    assert res.n_requests == 1 and res.n_matched == 2 and res.n_changed == 2
    f = reqs[0].url.params["filter"]
    assert f == "doi:https://doi.org/10.1038/s41592-026-00002-2|https://doi.org/10.1101/2026.10.01.123456"
    a, b, c = res.papers
    assert a.citation_count == 77 and a.openalex_id == "W2001" and a.pmid == "39000002"
    assert a.affiliations == ["Arc Institute", "Stanford University"]
    assert a.published_venue == "Nature Methods" and a.published_doi == "10.1038/s41592-026-00002-2"
    assert b.citation_count == 3 and b.published_venue is None
    assert c.doi is None and c.citation_count is None


def test_openalex_dry_run_and_limit():
    calls = []

    def h(req):
        calls.append(req)
        return httpx.Response(200, json=json.loads((FIX / "enrich_batch.json").read_text()))

    papers = _papers()
    res = enrich_via_openalex(papers, cfg(), client=_client(h), sleep=lambda s: None, dry_run=True)
    assert res.papers == papers and res.n_changed == 2
    assert any("dry-run" in n for n in res.notes)
    calls.clear()
    res = enrich_via_openalex(papers, cfg(), client=_client(h), sleep=lambda s: None, limit=1)
    assert "10.1101" not in calls[0].url.params["filter"]
    assert res.papers[1] == papers[1]


def test_openalex_failure_noted():
    papers = _papers()
    res = enrich_via_openalex(papers, cfg(), client=_client(lambda r: httpx.Response(500)),
                              sleep=lambda s: None)
    assert res.papers == papers and res.n_changed == 0
    assert any("失败" in n for n in res.notes)


def test_cellxgene_mention_not_affiliation():
    c = cfg()
    [a] = enrich_offline([mk(abstract="We release Tahoe-100M and use CELLxGENE data.")], c)
    assert a.matched_orgs == []
    assert "Tahoe Therapeutics" in a.mentioned_orgs


def test_affiliation_is_matched_not_mentioned():
    c = cfg()
    [a] = enrich_offline([mk(affiliations=["Arc Institute"], abstract="Tahoe-100M atlas.")], c)
    assert a.matched_orgs == ["Arc Institute"]
    assert "Arc Institute" not in a.mentioned_orgs
    [b] = enrich_offline([mk(affiliations=["Tahoe Therapeutics"], abstract="Tahoe-100M atlas.")], c)
    assert b.matched_orgs == ["Tahoe Therapeutics"] and b.mentioned_orgs == []


def test_author_names_not_matched():
    c = cfg()
    [a] = enrich_offline([mk(authors=["Arc Institute", "Tahoe Therapeutics"])], c)
    assert a.matched_orgs == [] and a.mentioned_orgs == []


def test_code_url_without_release_evidence():
    p = mk(abstract="Our method is built on https://github.com/x/y for training.")
    [q] = enrich_offline([p], cfg())
    assert q.code_url == "https://github.com/x/y" and q.has_code is None


def _long_papers(n=120):
    return [mk(f"p{i}", doi=f"10.1101/2026.10.01.{i:06d}" + "x" * 60) for i in range(n)]


def test_openalex_batches_by_url_length():
    import urllib.parse
    reqs = []

    def h(req):
        reqs.append(req)
        return httpx.Response(200, json={"results": []})

    res = enrich_via_openalex(_long_papers(), cfg(), client=_client(h), sleep=lambda s: None)
    assert len(reqs) >= 3 and res.n_batches == len(reqs) and res.n_failed_batches == 0
    assert all(len(str(r.url)) <= 1800 + 50 for r in reqs)
    assert sum(r.url.params["filter"].count("https://doi.org/") for r in reqs) == 120


def test_openalex_414_bisects():
    papers = [mk(f"p{i}", doi=f"10.1101/2026.10.01.{i:06d}") for i in range(8)]
    seen = []

    def h(req):
        n = req.url.params["filter"].count("https://doi.org/")
        seen.append(n)
        if n > 3:
            return httpx.Response(414)
        return httpx.Response(200, json={"results": []})

    res = enrich_via_openalex(papers, cfg(), client=_client(h), sleep=lambda s: None)
    assert seen[0] == 8
    assert res.n_failed_batches == 0 and res.n_batches == 1
    assert seen == [8, 4, 2, 2, 4, 2, 2]


def test_openalex_414_single_counts_failed():
    papers = [mk("a", doi="10.1101/2026.10.01.000001")]
    res = enrich_via_openalex(papers, cfg(), client=_client(lambda r: httpx.Response(414)),
                              sleep=lambda s: None)
    assert res.n_batches == 1 and res.n_failed_batches == 1
