import shutil

import pytest

from aivc.config import load_config


def test_real_config_loads(config_dir):
    c = load_config(config_dir)
    assert c.llm.model == "claude-haiku-5-5"
    assert c.sources.arxiv.enabled and c.sources.arxiv.page_size == 100
    assert c.sources.medrxiv.enabled is False
    assert c.scope.circles["core"].weight == 1.0 and c.scope.circles["peripheral"].weight == 0.6
    assert any(g.circle == "core" for g in c.keywords.groups)
    assert len(c.companies.companies) >= 14
    assert c.sources.news.enabled is True and len(c.sources.news.feeds) >= 3
    assert c.sources.news.effective_delay >= 1.0
    n = c.pipeline.news
    assert n.run_with_update and n.prompt_version == "news-v6.1" and n.default_days == 7
    assert n.site_min_relevance == 6 and n.site_related_min_relevance == 5
    assert [r.id for r in n.major] == ["big_funding", "top_model_release"]
    assert all(co.id for co in c.companies.companies)
    assert c.pipeline.dedup.title_threshold == 93
    assert c.sources.seed.source_dir is None


def test_missing_file(config_dir, tmp_path):
    shutil.copytree(config_dir, tmp_path / "c")
    (tmp_path / "c" / "llm.yaml").unlink()
    with pytest.raises(FileNotFoundError, match="llm.yaml"):
        load_config(tmp_path / "c")


def test_invalid_file(config_dir, tmp_path):
    shutil.copytree(config_dir, tmp_path / "c")
    (tmp_path / "c" / "pipeline.yaml").write_text("update: 5\n")
    with pytest.raises(ValueError, match="pipeline.yaml"):
        load_config(tmp_path / "c")


def test_topics_loaded_and_optional(config_dir, tmp_path):
    c = load_config(config_dir)
    assert len(c.topics.topics) == 3
    shutil.copytree(config_dir, tmp_path / "c")
    (tmp_path / "c" / "topics.yaml").unlink()
    assert load_config(tmp_path / "c").topics.topics == []
    assert c.pipeline.weekly.max_per_topic == 10


def test_company_id_slug():
    from aivc.config import Company

    assert Company(name="Isomorphic Labs").id == "isomorphic-labs"
    assert Company(name="Genentech/Roche").id == "genentech-roche"
    assert Company(name="晶泰科技").id.startswith("c-")
    assert Company(name="X", id="custom").id == "custom"
    assert Company(name="X").tier == "normal" and Company(name="X").category == "other"


def test_news_date_lookup_defaults(config_dir):
    c = load_config(config_dir)
    assert c.sources.news.max_date_lookups_per_run == 20
    assert [f.name for f in c.sources.news.feeds if f.date_from_article] == ["BiopharmaTrend"]


def test_news_zh_and_biohub_sources(config_dir):
    news = load_config(config_dir).sources.news
    feeds = {f.name: f for f in news.feeds}
    htmls = {h.name: h for h in news.html_sources}
    bh = htmls["Biohub News"]
    assert bh.enabled and bh.date_scope == "before_nearest" and bh.prefilter == "none"
    assert bh.list_url == "https://biohub.org/news/"
    strict_enabled = sorted(f.name for f in news.feeds if f.enabled and f.prefilter == "strict")
    assert strict_enabled == sorted(["量子位", "IT之家", "雷峰网", "36氪", "Latent Space"])
    assert all(feeds[n].require_date for n in strict_enabled if n != "Latent Space")
    # Latent Space 2026-11-15 作者确认启用；播客 feed 与之二选一，保持停用
    assert feeds["Latent Space"].enabled and feeds["Latent Space"].strict_terms
    assert not feeds["Latent Space Podcast"].enabled
    assert not htmls["生物谷"].enabled


def test_news_strict_keywords_loaded(config_dir):
    n = load_config(config_dir).pipeline.news
    assert "虚拟细胞" in n.strict_keywords and "virtual cell" in n.strict_keywords
    assert "扰动" not in n.strict_keywords and "AI" not in n.strict_keywords
    assert "AI制药" in n.keywords
    assert "AI for Science" not in n.keywords and "AI for Science" in n.strict_keywords
    assert "扰动" not in n.keywords and "细胞扰动" in n.keywords and "基因扰动" in n.keywords
