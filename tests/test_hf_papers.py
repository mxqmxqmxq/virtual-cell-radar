import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from aivc.fetchers.base import FetchError
from aivc.fetchers.hf_papers import HFPapersFetcher, item_to_paper

FIX = Path(__file__).parent / "fixtures" / "hf_papers"
DAILY = json.loads((FIX / "daily_2026-11-10.json").read_text())
EMPTY = (FIX / "daily_empty.json").read_text()
TODAY = date(2026, 11, 14)
HTTP = SimpleNamespace(user_agent="test", timeout=10, max_retries=0)


def make(handler, **kw):
    cfg = SimpleNamespace(
        base_url="https://huggingface.co/api/daily_papers",
        delay_seconds=1.0,
        max_days=31,
    )
    for k, v in kw.items():
        setattr(cfg, k, v)
    sleeps = []
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return HFPapersFetcher(cfg, HTTP, client=client, sleep=sleeps.append, today=TODAY), sleeps


def test_item_fields():
    p = item_to_paper(DAILY[0], TODAY)
    assert p.arxiv_id == "2611.01001"
    assert p.id.startswith("arxiv:") or "2611.01001" in p.id
    assert p.source == "hf_papers" and p.sources == ["hf_papers"]
    assert p.authors == ["Alice Zhang", "Bob Li"]
    assert p.hf_upvotes == 42 and p.github_stars == 310
    assert p.code_url == "https://github.com/example-lab/cellflow"  # githubRepo wins, cleaned
    assert p.has_code is True
    assert p.weights_url == "https://huggingface.co/example-lab/cellflow-base"
    assert p.published == date(2026, 11, 9)
    assert p.url == "https://arxiv.org/abs/2611.01001"
    assert p.pdf_url == "https://arxiv.org/pdf/2611.01001"
    assert p.alt_urls == ["https://huggingface.co/papers/2611.01001"]
    assert p.first_seen == TODAY


def test_code_url_from_abstract_and_none():
    p = item_to_paper(DAILY[1], TODAY)
    assert p.code_url == "https://github.com/vc-lab/vct"
    assert p.has_code is True
    assert p.hf_upvotes == 3 and p.github_stars is None
    assert p.weights_url is None


def test_version_stripped_and_string_authors():
    p = item_to_paper(DAILY[2], TODAY)
    assert p.arxiv_id == "2611.01003"
    assert p.authors == ["Dan Roe", "Eve Poe"]
    assert p.code_url is None and p.has_code is None
    assert p.hf_upvotes is None


def test_no_id_returns_none():
    assert item_to_paper({"paper": {"title": "x"}}, TODAY) is None


def test_window_three_days_desc_and_sleeps():
    seen = []

    def h(req):
        seen.append(req.url.params["date"])
        return httpx.Response(200, text=EMPTY)

    f, sleeps = make(h)
    assert f.fetch(date(2026, 11, 8), date(2026, 11, 10)) == []
    assert seen == ["2026-11-10", "2026-11-09", "2026-11-08"]
    assert sleeps == [1.0, 1.0]


def test_404_skipped():
    def h(req):
        if req.url.params["date"] == "2026-11-10":
            return httpx.Response(404)
        return httpx.Response(200, json=DAILY)

    f, _ = make(h)
    papers = f.fetch(date(2026, 11, 9), date(2026, 11, 10))
    assert len(papers) == 3


def test_400_future_utc_date_skipped():
    def h(req):
        if req.url.params["date"] == "2026-11-15":
            return httpx.Response(400, json={"error": "date must be <= today UTC"})
        return httpx.Response(200, json=DAILY)

    f, _ = make(h)
    papers = f.fetch(date(2026, 11, 14), date(2026, 11, 15))
    assert len(papers) == 3
    assert f.last_incomplete is False


def test_400_weekend_today_skipped():
    # TODAY=2026-11-14 is Saturday; HF daily_papers returns 400 on weekends
    def h(req):
        if req.url.params["date"] == "2026-11-14":
            return httpx.Response(400, json={"error": "date must be <= 2026-11-13"})
        return httpx.Response(200, json=DAILY)

    f, _ = make(h)
    papers = f.fetch(date(2026, 11, 13), date(2026, 11, 14))
    assert len(papers) == 3
    assert f.last_incomplete is False


def test_400_historical_date_raises():
    def h(req):
        if req.url.params["date"] == "2026-11-10":
            return httpx.Response(400, json={"error": "bad"})
        return httpx.Response(200, json=DAILY)

    f, _ = make(h)
    with pytest.raises(FetchError, match="2026-11-10"):
        f.fetch(date(2026, 11, 9), date(2026, 11, 10))


def test_200_dict_raises():
    f, _ = make(lambda req: httpx.Response(200, json={"error": "x"}))
    with pytest.raises(FetchError, match="2026-11-10"):
        f.fetch(date(2026, 11, 10), date(2026, 11, 10))


def test_github_repo_explicit_has_code_without_context():
    item = {"paper": {"id": "2611.09001", "title": "T", "summary": "No release words here.",
                      "githubRepo": "https://github.com/x/y"}}
    p = item_to_paper(item, TODAY)
    assert p.code_url == "https://github.com/x/y" and p.has_code is True


def test_abstract_repo_without_release_context_not_has_code():
    item = {"paper": {"id": "2611.09002", "title": "T",
                      "summary": "Our method is built on https://github.com/x/y and extends it."}}
    p = item_to_paper(item, TODAY)
    assert p.code_url == "https://github.com/x/y"
    assert p.has_code is None


def test_max_days_truncates():
    seen = []

    def h(req):
        seen.append(req.url.params["date"])
        return httpx.Response(200, text=EMPTY)

    f, _ = make(h, max_days=2)
    f.fetch(date(2026, 11, 1), date(2026, 11, 10))
    assert seen == ["2026-11-10", "2026-11-09"]
    assert f.last_incomplete is True and "8" in f.last_warning


def test_complete_window_resets_incomplete_flag():
    f, _ = make(lambda req: httpx.Response(200, text=EMPTY), max_days=1)
    f.fetch(date(2026, 11, 8), date(2026, 11, 10))
    assert f.last_incomplete is True
    f.fetch(date(2026, 11, 10), date(2026, 11, 10))
    assert f.last_incomplete is False and f.last_warning is None


def test_500_raises():
    f, _ = make(lambda req: httpx.Response(500))
    with pytest.raises(FetchError):
        f.fetch(date(2026, 11, 10), date(2026, 11, 10))


def test_dedup_across_days():
    f, _ = make(lambda req: httpx.Response(200, json=DAILY))
    papers = f.fetch(date(2026, 11, 8), date(2026, 11, 10))
    assert len(papers) == 3


def test_400_utc_not_local_today_skipped():
    """本机「今天」在 UTC 下仍是未来（如 UTC+8 早晨）：400 应跳过而非抛错；比较基准为 UTC。"""
    from datetime import datetime, timezone

    local_today = date(2026, 11, 13)  # 周五；UTC 此刻仍为周四 11-12

    def h(req):
        if req.url.params["date"] == "2026-11-13":
            return httpx.Response(400, json={"error": "date must be <= today UTC"})
        return httpx.Response(200, json=DAILY)

    cfg = SimpleNamespace(base_url="https://huggingface.co/api/daily_papers", delay_seconds=0, max_days=31)
    client = httpx.Client(transport=httpx.MockTransport(h))
    f = HFPapersFetcher(cfg, HTTP, client=client, sleep=lambda s: None,
                        now=lambda: datetime(2026, 11, 12, 23, 0, tzinfo=timezone.utc))
    papers = f.fetch(date(2026, 11, 12), local_today)
    assert len(papers) == 3 and f.last_incomplete is False
    # 反例：UTC 已到工作日 11-13 时，同一 400 不应跳过 -> 抛错
    f2 = HFPapersFetcher(cfg, HTTP, client=client, sleep=lambda s: None,
                         now=lambda: datetime(2026, 11, 13, 1, 0, tzinfo=timezone.utc))
    with pytest.raises(FetchError, match="2026-11-13"):
        f2.fetch(date(2026, 11, 12), local_today)
