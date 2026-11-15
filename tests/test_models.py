from datetime import date, datetime

import pytest
from pydantic import ValidationError

from aivc.models import (
    LLMAnnotation, NewsEntry, NewsItem, Paper, make_news_id, make_paper_id, normalize_news_url,
)


def test_paper_defaults():
    p = Paper(id="x", title="t", source="other", first_seen=date(2026, 1, 1))
    assert p.authors == [] and p.circle == "none" and p.llm_status == "unscored"
    assert p.schema_version == 1 and p.must_read is False


def test_llm_relevance_bounds():
    kw = dict(model="m", prompt_version="v1", scored_at=datetime(2026, 1, 1))
    LLMAnnotation(relevance=10, **kw)
    with pytest.raises(ValidationError):
        LLMAnnotation(relevance=11, **kw)


def test_make_paper_id_priority():
    assert make_paper_id(arxiv_id="2606.01042v2", doi="10.1/x") == "arxiv:2606.01042"
    assert make_paper_id(biorxiv_id="2026.07.15.738608", doi="10.1/x") == "biorxiv:2026.07.15.738608"
    assert make_paper_id(doi="10.1101/ABC") == "doi:10.1101/abc"
    assert make_paper_id(openreview_id="AbC") == "openreview:AbC"
    a = make_paper_id(title="Hello, World!")
    assert a == make_paper_id(title="hello world") and a.startswith("title:") and len(a) == 6 + 16
    with pytest.raises(ValueError):
        make_paper_id()


def test_legacy_highlight_key_ignored():
    p = Paper.model_validate(
        {"id": "x", "source": "arxiv", "title": "t", "first_seen": "2026-10-11", "highlight": ["A"], "keyword_score": 1.0}
    )
    assert "highlight" not in p.model_dump()
    assert "highlight" not in p.model_dump_json()


def test_legacy_row_without_license():
    p = Paper.model_validate({"id": "x", "source": "arxiv", "title": "t", "first_seen": "2026-10-11"})
    assert p.license is None
    assert Paper.model_validate({"id": "x", "source": "arxiv", "title": "t", "first_seen": "2026-10-11", "license": "cc_by"}).license == "cc_by"


def test_news_id_normalizes_url():
    a = make_news_id("HTTPS://Example.com/a/b/?utm_source=x&id=1#frag")
    b = make_news_id("https://example.com/a/b?id=1")
    assert a == b and a.startswith("news:") and len(a) == len("news:") + 16
    assert make_news_id("https://example.com/other") != a
    assert normalize_news_url("https://Example.com/p/?utm_medium=m") == "https://example.com/p"


def test_news_item_defaults_and_no_text_fields():
    n = NewsItem(id="news:1", title="t", url="https://e.com/x", source="S", first_seen=date(2026, 11, 1))
    assert n.llm_status == "unscored" and n.sources == [] and n.major is False and n.relevance is None
    fields = set(NewsItem.model_fields)
    assert not fields & {"company", "snippet", "content", "body", "text"}
    with pytest.raises(ValidationError):
        NewsItem(id="n", title="t", url="u", source="s", first_seen=date(2026, 11, 1), relevance=11)


def test_news_entry_transient():
    e = NewsEntry(title="t", url="https://e.com", source="S")
    assert e.snippet == "" and e.feed_kind == "media" and e.feed_company is None
