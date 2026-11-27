from datetime import date

import pytest

from aivc.config import load_config
from aivc.models import Paper
from aivc.prefilter import apply_keywords, filter_relevant


@pytest.fixture
def cfg(config_dir):
    return load_config(config_dir)


def mk(title, abstract=None, keywords=()):
    return Paper(id="x:" + title[:5], title=title, abstract=abstract, source="other",
                 keywords=list(keywords), first_seen=date(2026, 1, 1))


def test_core_with_title_weight(cfg):
    p = apply_keywords(mk("Perturbation prediction with flows"), cfg)
    assert p.circle == "core"
    assert p.keyword_score == 4.5  # 3.0 * 1.5
    assert "perturbation prediction" in p.matched_keywords
    q = apply_keywords(mk("Flows", "We study perturbation prediction."), cfg)
    assert q.keyword_score == 3.0


def test_peripheral_and_none(cfg):
    assert apply_keywords(mk("Spatial transcriptomics atlas"), cfg).circle == "none"
    assert apply_keywords(mk("Deep learning for spatial transcriptomics atlas"), cfg).circle == "peripheral"
    n = apply_keywords(mk("Bird migration in wetlands", "An ecological survey of birds."), cfg)
    assert n.circle == "none" and n.keyword_score == 0


def test_no_mutation(cfg):
    p = mk("Virtual cell models")
    apply_keywords(p, cfg)
    assert p.circle == "none"


def test_exclude_terms(cfg):
    p = apply_keywords(mk("Deep learning spatial transcriptomics for stock price prediction"), cfg)
    assert p.circle == "none" and p.keyword_score == 0
    q = apply_keywords(mk("Virtual cell and quantum computing"), cfg)
    assert q.circle == "core"


def test_word_boundaries_and_punctuation(cfg):
    assert apply_keywords(mk("UCEs are cool", "nothing"), cfg).circle == "none"
    assert apply_keywords(mk("x", "We use the AIDO.Cell model"), cfg).circle == "core"
    assert apply_keywords(mk("Perturb-seq screens"), cfg).circle == "core"
    assert apply_keywords(mk("Perturb seq screens"), cfg).circle == "core"
    assert apply_keywords(mk("A single cell foundation model"), cfg).circle == "core"


def test_real_config_examples(cfg):
    p = apply_keywords(mk(
        "RELAY: Relational In-Context Learning for Cellular Perturbation Prediction",
        "We predict single-cell transcriptional responses to genetic perturbation in Perturb-seq."), cfg)
    assert p.circle == "core"
    e = apply_keywords(mk("Forest beetle diversity", "Species richness across tropical forests."), cfg)
    assert e.circle == "none"


def test_filter_relevant_sorted_and_threshold(cfg):
    ps = [mk("Deep learning for spatial transcriptomics"), mk("Birds"), mk("Virtual cell perturbation prediction")]
    out = filter_relevant(ps, cfg)
    assert [p.title for p in out] == ["Virtual cell perturbation prediction", "Deep learning for spatial transcriptomics"]
    cfg2 = cfg.model_copy(deep=True)
    cfg2.keywords.min_keyword_score = 100
    cfg2.keywords.semantic_rescue_threshold = 1.0  # 隔离语义捞回，只测关键词门槛
    assert filter_relevant(ps, cfg2) == []


@pytest.mark.parametrize("term_text", [
    "We study virtual cells at scale",
    "single-cell foundation models for biology",
    "Inferring gene regulatory networks",
    "Pooled CRISPR screens with single-cell readout",
    "Predicting perturbation effects",
])
def test_plural_forms_match(cfg, term_text):
    assert apply_keywords(mk(term_text), cfg).circle == "core"


def test_acronym_no_plural_and_wildcard(cfg):
    assert apply_keywords(mk("UCEs are cool", "nothing"), cfg).circle == "none"


@pytest.mark.parametrize("title,abstract", [
    ("CellFlow: Flow matching for single-cell perturbation responses", ""),
    ("A JEPA world model of cellular dynamics", ""),
    ("scDiffusion: generating single-cell RNA-seq data with diffusion models", ""),
    ("Optimal transport maps for predicting cell responses to treatment",
     "We model transcriptomic responses."),
    ("BEYOND STATIC BENCHMARKS: LIVING EVALUATION FOR SINGLE-CELL FOUNDATION MODELS", ""),
    ("Rethinking One Gene, One Token: Single-Cell Foundation Models", ""),
    ("Balancing Invariance and Information Preservation in Single-Cell JEPA", ""),
    ("PerturbSB: Schrödinger Bridge for single-cell perturbation", ""),
    ("scMeanFlow for scRNA-seq Synthesis", ""),
    ("GEMOT: Towards Mechanistic World Models For Biology", "cell perturbation"),
])
def test_combo_rules_and_iclr_regressions(cfg, title, abstract):
    p = apply_keywords(mk(title, abstract), cfg)
    assert p.circle == "core", (title, p.matched_keywords)


def test_combo_needs_both_sides(cfg):
    assert apply_keywords(mk("Flow matching for images", "photos only"), cfg).circle == "none"
    assert apply_keywords(mk("Single-cell atlas of liver", "cells"), cfg).circle == "none"


@pytest.mark.parametrize("title,abstract", [
    ("Perturbation theory for protein folding kinetics", "We study a gene circuit."),
    ("Robust stability of bacterial growth under environmental perturbation", "gene and cell growth"),
    ("Adversarial perturbations of image classifiers", "each cell of the grid"),
    ("Reaction-diffusion model of morphogen gradients", "cellular patterning and gene expression"),
    ("Diffusion MRI of white matter", "cellular microstructure"),
    ("Mitochondrial calcium diffusion in neurons", "cellular calcium"),
    ("Generative models of protein sequences", "cellular function"),
    ("Latent dynamics of neural population activity", "cell recordings"),
    ("World models for robot manipulation", "inspired by biology"),
])
def test_combo_false_positive_regressions(cfg, title, abstract):
    p = apply_keywords(mk(title, abstract), cfg)
    assert p.circle == "none", (title, p.matched_keywords, p.keyword_score)


def test_plain_diffusion_model_single_cell_is_core(cfg):
    p = apply_keywords(mk("Diffusion models for single-cell data generation", ""), cfg)
    assert p.circle == "core", p.matched_keywords


def test_hyphen_prefixed_term_does_not_match(cfg):
    p = apply_keywords(mk("A reaction-diffusion-based model of gene expression patterning", ""), cfg)
    assert p.circle == "none", p.matched_keywords


@pytest.mark.parametrize("title", [
    "Gene expression changes after osmotic perturbation in yeast",
    "CRISPR knockout of TP53 under DNA damage perturbation",
    "Multimodal protein representations across human brain cell types with CRISPR screens",
    "The Multi-scale Copula Index: a measure of association applied to drug response",
])
def test_broad_terms_alone_not_core(cfg, title):
    assert apply_keywords(mk(title, ""), cfg).circle != "core", title


@pytest.mark.parametrize("title", [
    "Predicting drug response from single-cell transcriptomes",
    "Deep learning on pooled CRISPR screens with single-cell readout",
])
def test_broad_terms_with_context_are_core(cfg, title):
    assert apply_keywords(mk(title, ""), cfg).circle == "core", title


def test_prefilter_uses_termmatch():
    from aivc import prefilter, termmatch
    assert prefilter._compile_term is termmatch.compile_term


def test_all_of_spatial_transcriptomics_context(cfg):
    assert apply_keywords(mk("Visium slides of tissue"), cfg).circle == "none"
    p = apply_keywords(mk("Visium slides", "We use a transformer to predict expression."), cfg)
    assert p.circle == "peripheral"


def test_soft_exclude_penalizes_peripheral_not_core(cfg):
    base = apply_keywords(mk("Deep learning for spatial transcriptomics"), cfg)
    soft = apply_keywords(mk("Deep learning for spatial transcriptomics", "in plant tissue"), cfg)
    assert soft.keyword_score == round(base.keyword_score - cfg.keywords.soft_exclude_penalty, 2)
    assert "soft_exclude:plant" in soft.matched_keywords
    core = apply_keywords(mk("Virtual cell perturbation prediction", "in plant tissue"), cfg)
    assert core.circle == "core" and core.keyword_score == apply_keywords(mk("Virtual cell perturbation prediction"), cfg).keyword_score
    assert not any(m.startswith("soft_exclude") for m in core.matched_keywords)


def test_soft_exclude_to_zero_is_none(cfg):
    cfg2 = cfg.model_copy(deep=True)
    cfg2.keywords.soft_exclude_penalty = 100.0
    p = apply_keywords(mk("Deep learning for spatial transcriptomics", "plant"), cfg2)
    assert p.circle == "none" and p.keyword_score == 0


def test_semantic_rescue(cfg):
    ps = [mk("Birds of the wetlands", "an ecological survey")]
    cfg2 = cfg.model_copy(deep=True)
    cfg2.keywords.semantic_rescue_threshold = 0.0
    out = filter_relevant(ps, cfg2)
    assert len(out) == 1 and out[0].circle == "peripheral"
    assert "semantic:rescue" in out[0].matched_keywords
    cfg3 = cfg.model_copy(deep=True)
    cfg3.keywords.semantic_rescue_threshold = 1.1
    assert filter_relevant(ps, cfg3) == []


def test_hard_exclude_not_rescued(cfg):
    cfg2 = cfg.model_copy(deep=True)
    cfg2.keywords.semantic_rescue_threshold = 0.0
    assert filter_relevant([mk("Cryptocurrency markets")], cfg2) == []


def test_semantic_score_written_and_rank_score(cfg):
    from aivc.prefilter import rank_score
    a = mk("Virtual cell perturbation prediction").model_copy(update={"keyword_score": 3.0})
    b = a.model_copy(update={"hf_upvotes": 5, "published_venue": "Nature"})
    assert rank_score(b) == rank_score(a) + 1.0
    out = filter_relevant([mk("Virtual cell perturbation prediction")], cfg)
    assert out[0].semantic_score > 0


def test_classify_keyword_pass_returns_same(cfg):
    from aivc.prefilter import classify

    p = apply_keywords(mk("Perturbation prediction with flows"), cfg)
    assert classify(p, cfg) is p


def test_classify_semantic_rescue_and_below_threshold(cfg):
    from aivc.prefilter import classify

    p = apply_keywords(mk("Zorblax manifold forecasting", "Zorblax latent trajectories."), cfg)
    assert p.circle == "none"
    hi = classify(p.model_copy(update={"semantic_score": 0.9}), cfg)
    assert hi is not None and hi.circle == "peripheral"
    assert hi.matched_keywords.count("semantic:rescue") == 1
    again = classify(hi.model_copy(update={"semantic_score": 0.9}), cfg)
    assert again is not None and again.matched_keywords.count("semantic:rescue") == 1
    assert classify(p.model_copy(update={"semantic_score": 0.0}), cfg) is None


def test_classify_exclude_terms_block_rescue(cfg):
    from aivc.prefilter import classify

    term = cfg.keywords.exclude_terms[0].replace("*", "")
    p = apply_keywords(mk(f"Zorblax {term} study", "Zorblax."), cfg)
    assert p.circle == "none"
    assert classify(p.model_copy(update={"semantic_score": 0.9}), cfg) is None
