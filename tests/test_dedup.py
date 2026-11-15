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
