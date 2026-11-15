from datetime import date

import pytest

from aivc.config import load_config
from aivc.models import NewsItem, NewsSourceRef, make_news_id
from aivc.news_report import fmt_usd, render_major_section, render_weekly_news

START, END = date(2026, 11, 9), date(2026, 11, 15)


@pytest.fixture
def cfg(config_dir):
    return load_config(config_dir)


def mk(i, **kw):
    d = dict(id=make_news_id(f"https://x.example/{i}"), title=f"虚构公司 {i} 事件", url=f"https://x.example/{i}",
             source="Fake Media", published=date(2026, 11, 12), first_seen=date(2026, 11, 12),
             llm_status="scored", event_type="funding", relevance=7, companies=["Recursion"],
             summary_zh="虚构摘要。", significance_zh="虚构意义。", amount_usd=1.2e8, round="Series B",
             investors=["VC One"])
    d.update(kw)
    return NewsItem(**d)


def test_fmt_usd():
    assert fmt_usd(None) == "未披露"
    assert fmt_usd(1.2e9) == "$1.2B"
    assert fmt_usd(120e6) == "$120M"
    assert fmt_usd(850e3) == "$850K"


def test_major_empty():
    assert render_major_section([]) == []


def test_major_section():
    lines = render_major_section([mk(1, major=True)])
    assert lines[0] == "## 重大行业事件"
    row = next(x for x in lines if x.startswith("- **[重大]"))
    assert "融资" in row and "$120M" in row and "Recursion" in row and "[原文](https://x.example/1)" in row
    assert "2026-11-12" in row and "Fake Media" in row
    assert "  虚构意义。" in lines


def test_weekly_empty(cfg):
    out = render_weekly_news([mk(1, published=date(2026, 10, 1))], START, END, cfg)
    assert out[0] == "## 行业动态" and "本周暂无已收录的行业动态。" in out


def test_weekly_filters_unscored_and_low_relevance(cfg):
    items = [mk(1, llm_status="unscored", relevance=None), mk(2, relevance=1)]
    assert "本周暂无已收录的行业动态。" in render_weekly_news(items, START, END, cfg)


def test_weekly_excludes_undated_high_score(cfg):
    items = [mk(1, published=None, major=True, relevance=9)]
    assert "本周暂无已收录的行业动态。" in render_weekly_news(items, START, END, cfg)


def test_weekly_sections(cfg):
    items = [mk(1, major=True, major_reasons=["融资 ≥ 1 亿美元"]),
             mk(2, event_type="partnership", amount_usd=None, relevance=5, round=None, investors=[]),
             mk(3, amount_usd=None, relevance=6)]
    text = "\n".join(render_weekly_news(items, START, END, cfg))
    assert "### 重大事件" in text and "### 本周重要事件" in text and "### 融资汇总" in text
    assert text.index("### 重大事件") < text.index("### 本周重要事件") < text.index("### 融资汇总")
    assert "| 公司 | 轮次 | 金额 | 投资方 | 来源 |" in text
    assert "共 2 笔，未披露金额 1 笔" in text and "**$120M**" in text


def test_weekly_no_major_section_when_none(cfg):
    text = "\n".join(render_weekly_news([mk(1)], START, END, cfg))
    assert "### 重大事件" not in text and "### 本周重要事件" in text


def test_weekly_multi_source_links(cfg):
    it = mk(1, sources=[NewsSourceRef(name="Media A", url="https://a.example/1"),
                        NewsSourceRef(name="Media B", url="https://b.example/1")])
    text = "\n".join(render_weekly_news([it], START, END, cfg))
    assert "[Media A](https://a.example/1)" in text and "[Media B](https://b.example/1)" in text


def test_weekly_top_n(cfg):
    cfg.pipeline.news.weekly_top_n = 1
    items = [mk(1, relevance=5, event_type="other"), mk(2, relevance=9, event_type="other")]
    text = "\n".join(render_weekly_news(items, START, END, cfg))
    top = text.split("### 本周重要事件")[1]
    assert "虚构公司 2" in top and "虚构公司 1" not in top


def test_weekly_no_funding(cfg):
    text = "\n".join(render_weekly_news([mk(1, event_type="other")], START, END, cfg))
    assert "本周无已收录的融资事件。" in text


def test_weekly_excludes_related_tier_five(cfg):
    assert cfg.pipeline.news.site_related_min_relevance == 5 and cfg.pipeline.news.site_min_relevance > 5
    items = [mk(1, relevance=5)]
    assert "本周暂无已收录的行业动态。" in render_weekly_news(items, START, END, cfg)
