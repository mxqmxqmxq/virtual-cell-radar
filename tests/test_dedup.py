from datetime import date

from aivc.dedup import DedupIndex, dedup_batch, merge_papers
from aivc.models import LLMAnnotation, Paper

T = "Learning perturbation responses of single cells with flow matching"


def mk(id, title=T, **kw):
    kw.setdefault("source", "arxiv")
    kw.setdefault("first_seen", date(2026, 1, 1))
    return Paper(id=id, title=title, **kw)


def test_arxiv_versions():
    a = mk("arxiv:2606.01042", arxiv_id="2606.01042v1", version="v1")
    b = mk("arxiv:2606.01042", title="Different", arxiv_id="2606.01042v2", version="v2")
    assert DedupIndex([a]).find(b) is a


def test_biorxiv_prefixes():
    a = mk("biorxiv:2026.07.15.738608", source="biorxiv", doi="10.1101/2026.07.15.738608",
           title="Alpha beta gamma")
    b = mk("doi:10.64898/2026.07.15.738608", source="biorxiv", doi="10.64898/2026.07.15.738608",
           title="Totally other")
    assert DedupIndex([a]).find(b) is a


def test_cross_source_title_merge_alt_ids():
    a = mk("arxiv:2606.01042", arxiv_id="2606.01042", url="http://arxiv/abs")
    b = mk("biorxiv:2026.01.01.000001", source="biorxiv", title=T.upper() + "!",
           biorxiv_id="2026.01.01.000001", url="http://bio/x", doi="10.1101/2026.01.01.000001")
    r = dedup_batch([b], [a])
    assert r.new == [] and r.n_duplicates == 1 and len(r.updated) == 1
    m = r.updated[0]
    assert m.id == a.id and m.alt_ids == [b.id] and m.alt_urls == ["http://bio/x"]
    assert m.biorxiv_id == "2026.01.01.000001" and m.doi == "10.1101/2026.01.01.000001"


def test_fuzzy_match_one_word():
    a = mk("arxiv:1", arxiv_id="1")
    b = mk("biorxiv:2026.01.01.000002", source="biorxiv", title=T + " models")
    assert DedupIndex([a], 90).find(b) is a


def test_short_title_no_fuzzy():
    a = mk("arxiv:1", title="Cell models", arxiv_id="1")
    b = mk("other:2", title="Cell model", source="other")
    assert DedupIndex([a]).find(b) is None


def test_different_arxiv_ids_not_merged():
    a = mk("arxiv:2606.00001", arxiv_id="2606.00001")
    b = mk("arxiv:2606.00002", arxiv_id="2606.00002", title=T + " models")
    assert DedupIndex([a]).find(b) is None
    # even exact title with no shared author
    c = mk("arxiv:2606.00003", arxiv_id="2606.00003", authors=["Z"])
    assert DedupIndex([mk("arxiv:2606.00001", arxiv_id="2606.00001", authors=["A"])]).find(c) is None
    # exact title with shared author is the same paper
    d = mk("arxiv:2606.00004", arxiv_id="2606.00004", authors=["A"])
    assert DedupIndex([mk("arxiv:2606.00001", arxiv_id="2606.00001", authors=["A"])]).find(d)


def test_merge_keeps_llm_and_first_seen():
    ann = LLMAnnotation(relevance=8, model="m", prompt_version="1", scored_at="2026-01-02T00:00:00")
    a = mk("arxiv:1", arxiv_id="1", llm=ann, llm_status="scored", final_score=8.0, must_read=True,
           local_file="a.pdf", authors=["A"], version="v1")
    b = mk("arxiv:1", arxiv_id="1", first_seen=date(2026, 5, 5), authors=["B"], version="v2",
           abstract="new abs", title="New title", keyword_score=3.0, circle="core",
           matched_keywords=["x"])
    m = merge_papers(a, b.model_copy(update={"abstract": None}))
    assert m.llm == ann and m.llm_status == "scored" and m.final_score == 8.0 and m.must_read
    assert m.first_seen == date(2026, 1, 1) and m.local_file == "a.pdf"
    assert m.authors == ["A"] and m.title == "New title" and m.version == "v2"
    assert m.circle == "core"
    assert merge_papers(a, a.model_copy()) == a


def test_merge_abstract_change_resets_score():
    ann = LLMAnnotation(relevance=8, model="m", prompt_version="1", scored_at="2026-01-02T00:00:00")
    a = mk("arxiv:1", arxiv_id="1", llm=ann, llm_status="scored", final_score=8.0, must_read=True)
    b = mk("arxiv:1", arxiv_id="1", abstract="new abs", version="v2")
    m = merge_papers(a, b)
    assert m.abstract == "new abs"
    assert m.llm_status == "unscored" and m.llm is None and m.final_score is None and not m.must_read
    assert m.llm_skip_reason == "abstract_changed"


def test_in_batch_duplicates():
    a = mk("arxiv:1", arxiv_id="1v1")
    b = mk("arxiv:1", arxiv_id="1v2", version="v2", abstract="abs")
    r = dedup_batch([a, b], [])
    assert len(r.new) == 1 and r.n_duplicates == 1 and r.updated == []
    assert r.new[0].abstract == "abs"


def test_updated_only_real_changes_and_idempotent():
    existing = [mk("arxiv:1", arxiv_id="1", abstract="x")]
    same = mk("arxiv:1", arxiv_id="1", abstract="x")
    r = dedup_batch([same], existing)
    assert r.new == [] and r.updated == [] and r.n_duplicates == 1

    batch = [mk("arxiv:5", arxiv_id="5", title="Brand new paper about nothing at all else"),
             mk("biorxiv:2026.01.01.000009", source="biorxiv", title=T + "!", doi="10.1101/2026.01.01.000009")]
    ex = [mk("arxiv:1", arxiv_id="1")]
    r1 = dedup_batch(batch, ex)
    assert len(r1.new) == 1 and len(r1.updated) == 1
    store = [r1.updated[0], *r1.new]
    r2 = dedup_batch(batch, store)
    assert r2.new == [] and r2.updated == []


def test_merge_license_fills_but_does_not_overwrite():
    a = mk("arxiv:1", arxiv_id="1")
    b = mk("arxiv:1", arxiv_id="1", license="cc_by")
    assert merge_papers(a, b).license == "cc_by"
    c = mk("arxiv:1", arxiv_id="1", license="cc_no")
    assert merge_papers(c, b).license == "cc_no"


def test_pmid_openalex_keys_dedup():
    a = mk("pmid:1", source="europepmc", pmid="123", title="Aaa bbb")
    b = mk("x:2", source="openalex", pmid="123", title="Totally different words here")
    assert DedupIndex([a]).find(b) is a
    c = mk("oa:1", source="openalex", openalex_id="W1", title="Qqq")
    d = mk("y:3", source="openalex", openalex_id="W1", title="Zzz unrelated title entirely")
    assert DedupIndex([c]).find(d) is c


def test_doi_published_doi_cross_hit():
    a = mk("arxiv:1", arxiv_id="1", doi="10.1/pre", published_doi="10.2/journal")
    b = mk("openalex:W9", source="openalex", doi="10.2/JOURNAL", title="Unrelated other words")
    assert DedupIndex([a]).find(b) is a
    c = mk("arxiv:2", arxiv_id="2", doi="10.3/x", title="Foo bar baz")
    d = mk("openalex:W8", source="openalex", doi="10.4/y", published_doi="10.3/x", title="Qux")
    assert DedupIndex([c]).find(d) is c


def test_merge_sources_union_and_max_counts():
    a = mk("arxiv:1", arxiv_id="1", citation_count=3, hf_upvotes=None, github_stars=10,
           semantic_score=0.1)
    b = mk("hf:1", source="hf_papers", citation_count=1, hf_upvotes=5, github_stars=50,
           semantic_score=0.3, sources=["hf_papers", "openalex"])
    m = merge_papers(a, b)
    assert m.sources == ["arxiv", "hf_papers", "openalex"]
    assert (m.citation_count, m.hf_upvotes, m.github_stars) == (3, 5, 50)
    assert m.semantic_score == 0.3
    assert merge_papers(a, a.model_copy()) == a


def _journal(**kw):
    kw.setdefault("source", "openalex")
    kw.setdefault("venue", "Nature Methods")
    kw.setdefault("doi", "10.1038/x")
    kw.setdefault("published_venue", kw["venue"])
    kw.setdefault("published_doi", kw["doi"])
    return mk("openalex:W1", openalex_id="W1", **kw)


def test_promotion_venue_none():
    a = mk("arxiv:1", arxiv_id="1", doi="10.1101/pre", abstract="abs", llm_status="scored")
    m = merge_papers(a, _journal())
    assert m.published_venue == "Nature Methods" and m.published_doi == "10.1038/x"
    assert m.venue == "Nature Methods" and m.openalex_id == "W1"
    assert m.llm_status == "scored"


def test_promotion_replaces_submission_venue():
    a = mk("arxiv:1", arxiv_id="1", venue="ICLR 2027 (submission, under review)")
    m = merge_papers(a, _journal())
    assert m.venue == "Nature Methods" and m.published_venue == "Nature Methods"


def test_promotion_keeps_existing_published_and_real_venue():
    a = mk("arxiv:1", arxiv_id="1", venue="NeurIPS 2026", published_venue="Cell",
           published_doi="10.1016/c")
    m = merge_papers(a, _journal())
    assert m.published_venue == "Cell" and m.published_doi == "10.1016/c"
    assert m.venue == "NeurIPS 2026"


def test_non_preprint_existing_promoted_by_explicit_evidence():
    a = mk("openreview:1", source="openreview", openreview_id="1")
    m = merge_papers(a, _journal())
    assert m.published_venue == "Nature Methods" and m.published_doi == "10.1038/x"


def _pair():
    A = mk("arxiv:1", arxiv_id="1", doi="10.1101/x", abstract="abs")
    B = mk("openalex:W1", source="openalex", openalex_id="W1", doi="10.1038/y",
           venue="Nature Methods", published_venue="Nature Methods", published_doi="10.1038/y")
    return A, B


def test_merge_both_orders_consistent():
    A, B = _pair()
    ab, ba = merge_papers(A, B), merge_papers(B, A)
    assert ab.published_venue == ba.published_venue == "Nature Methods"
    assert ab.published_doi == ba.published_doi == "10.1038/y"
    assert set(ab.sources) == set(ba.sources) == {"arxiv", "openalex"}
    assert ab.doi == "10.1101/x" and ba.doi == "10.1038/y"
    ids_ab = (set(ab.alt_ids) | {ab.id}) - {"doi:10.1038/y", "doi:10.1101/x"}
    ids_ba = (set(ba.alt_ids) | {ba.id}) - {"doi:10.1038/y", "doi:10.1101/x"}
    assert ids_ab == ids_ba == {"arxiv:1", "openalex:W1"}
    assert "doi:10.1038/y" in ab.alt_ids and "doi:10.1101/x" in ba.alt_ids


def test_replay_idempotent():
    A, B = _pair()
    ab = merge_papers(A, B)
    assert merge_papers(ab, B) == ab
    ba = merge_papers(B, A)
    assert merge_papers(ba, A) == ba


def test_dedup_batch_both_orders_single_record():
    A, B = _pair()
    for batch in ([A, B], [B, A]):
        r = dedup_batch(batch, [])
        got = r.new + r.updated
        assert len(got) == 1 and got[0].published_doi == "10.1038/y"


def test_existing_published_doi_not_overwritten():
    A, B = _pair()
    A = A.model_copy(update={"published_doi": "10.1016/c", "published_venue": "Cell"})
    m = merge_papers(A, B)
    assert m.published_doi == "10.1016/c" and m.published_venue == "Cell"


def test_repository_record_does_not_promote():
    # OpenAlex preprint record: venue is a preprint server, no published_* set.
    a = mk("biorxiv:1", source="biorxiv", biorxiv_id="1", doi="10.1101/pre")
    inc = mk("openalex:W2", source="openalex", openalex_id="W2", venue="bioRxiv", doi="10.1101/pre")
    m = merge_papers(a, inc)
    assert m.published_venue is None and m.published_doi is None


def test_preprint_server_venue_replaced_on_promotion():
    existing = mk("biorxiv:10.1101/2026.01.01.000001", source="biorxiv", venue="bioRxiv")
    incoming = existing.model_copy(update={"source": "openalex", "venue": "Nature Methods",
                                           "published_venue": "Nature Methods",
                                           "published_doi": "10.1038/s41592-026-00001-1"})
    merged = merge_papers(existing, incoming)
    assert merged.published_venue == "Nature Methods"
    assert merged.venue == "Nature Methods"
