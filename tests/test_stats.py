import json
from datetime import date, datetime, timedelta

from aivc.config import load_config
from aivc.models import LLMAnnotation, Paper
from aivc.state import FetchState
from aivc.stats import compute_stats, enabled_source_ids, render_stats

TODAY = date(2026, 11, 28)


def ann(circle="core", rel=8, pv="v4", four=True):
    kw = dict(novelty_score=7, rigor_score=7, impact_score=7) if four else {}
    return LLMAnnotation(circle=circle, relevance=rel, model="m", prompt_version=pv,
                         scored_at=datetime(2026, 11, 1), **kw)


def mkp(i, source="arxiv", **kw):
    base = dict(id=f"x:{i}", title=f"t{i}", source=source, abstract="abs", first_seen=TODAY)
    base.update(kw)
    return Paper(**base)


def papers():
    out = [
        mkp(1, llm_status="scored", llm=ann(), final_score=8.4, matched_keywords=["virtual cell", "gene regulatory network"],
            published_venue="Nature Methods", has_code=True, matched_orgs=["Arc Institute"],
            semantic_score=0.5, sources=["arxiv", "openalex"]),
        mkp(2, llm_status="scored", llm=ann("none", 1, "v3", four=False), final_score=0.3,
            matched_keywords=["gene regulatory network"]),
        mkp(3, llm_status="scored", llm=ann("none", 1, "v3", four=False), final_score=0.3,
            matched_keywords=["gene regulatory network"]),
        mkp(4, llm_status="scored", llm=ann("core", 9, "v3", four=False), final_score=9.2,
            matched_keywords=["gene regulatory network", "semantic:rescue"], weights_url="https://huggingface.co/a/b"),
        mkp(5, source="biorxiv", llm_status="error"),
        mkp(6, source="biorxiv", llm_status="unscored", abstract=None),
        mkp(8, llm_status="unscored", matched_keywords=["gene regulatory network"]),
        mkp(7, source="openalex", llm_status="unscored", published_venue="Some Journal", semantic_score=0.1),
    ]
    return out


def test_compute(config_dir):
    cfg = load_config(config_dir)
    d = compute_stats(papers(), None, cfg, TODAY)
    assert d["total"] == 8
    assert d["by_source"] == {"arxiv": 5, "biorxiv": 2, "openalex": 1, "europepmc": 0, "hf_papers": 0}
    assert d["by_sources_any"]["openalex"] == 2
    assert d["yield_by_source"]["arxiv"]["scored"] == 4
    assert d["yield_by_source"]["arxiv"]["yield"] == 0.5
    assert d["llm_status"]["error"] == 1
    assert d["llm_backlog"] == 3  # error + unscored(7) + unscored(8); #6 has no abstract
    assert d["prompt_versions"] == {"v4": 1, "v3": 3}
    assert d["stale_prompt_count"] == 3 or d["stale_prompt_count"] == 4
    assert sum(d["score_hist"].values()) == 4
    assert d["score_hist"]["9-10"] == 1 and d["score_hist"]["0-1"] == 2
    fp = d["keyword_false_positive_top10"]
    assert fp[0]["keyword"] == "gene regulatory network"
    assert (fp[0]["n"], fp[0]["fp"], fp[0]["n_total"]) == (4, 2, 5)
    assert all(r["keyword"] != "semantic:rescue" for r in fp)
    assert all(r["keyword"] != "virtual cell" for r in fp)  # n < 3
    assert d["semantic_rescued"] == 1
    assert d["promoted_any"] == 2 and d["promoted_top_venue"] == 1
    assert d["code_ratio"] == round(1 / 8, 4)
    assert d["weights_ratio"] == round(1 / 8, 4)
    assert d["matched_orgs_top10"] == [{"org": "Arc Institute", "n": 1}]
    assert d["semantic_score_quantiles"]["n"] == 2
    assert d["published_direct"] == 1 and d["published_promoted"] == 1
    assert d["yield_by_source"]["hf_papers"] == {"total": 0, "scored": 0, "relevant": 0, "yield": 0.0}
    sc = d["semantic_calibration"]
    for g in ("core", "peripheral", "none", "unscored"):
        assert {"n", "p10", "p50", "p90"} <= set(sc[g])
    assert sc["core"]["n"] == 2 and sc["none"]["n"] == 2 and sc["unscored"]["n"] == 4
    assert "threshold" in sc and "none_at_or_above_threshold" in sc
    json.dumps(d)
    txt = render_stats(d)
    assert "（已判 4/5）" in txt and "语义校准" in txt and "hf_papers" in txt
    assert "直接抓到的期刊论文 1 篇" in txt
    assert d["fetch_health"] == {}


def test_fetch_health_and_render(config_dir, tmp_path):
    cfg = load_config(config_dir)
    st = FetchState(tmp_path / "s.json")
    now = datetime(2026, 11, 27, 6)
    st.record_success("arxiv", TODAY - timedelta(days=2), 10, now)
    st.record_failure("biorxiv", "boom", now)
    st.add_run({"run_at": "2026-11-27T06:00:00", "fetched": {"arxiv": 10}, "n_new": 3})
    d = compute_stats(papers(), st, cfg, TODAY)
    fh = d["fetch_health"]
    assert fh["sources"]["arxiv"]["days_since_success"] == 2
    assert fh["sources"]["biorxiv"]["consecutive_failures"] == 1
    assert len(fh["recent_runs"]) == 1
    txt = render_stats(d)
    for w in ("总数", "各源", "误报", "水位线"):
        assert w in txt
    json.dumps(d, default=str)


def test_empty_and_none_state(config_dir):
    cfg = load_config(config_dir)
    d = compute_stats([], None, cfg, TODAY)
    assert d["total"] == 0
    txt = render_stats(d)
    assert "总数：0" in txt and "水位线" in txt


def test_enabled_source_ids(config_dir):
    cfg = load_config(config_dir)
    assert enabled_source_ids(cfg) == ["arxiv", "biorxiv", "openalex", "europepmc", "hf_papers"]


def test_empty_calibration(config_dir):
    cfg = load_config(config_dir)
    d = compute_stats([], None, cfg, TODAY)
    assert all(d["semantic_calibration"][g]["n"] == 0 for g in ("core", "peripheral", "none", "unscored"))
