import statistics
import time
from datetime import date, datetime
from pathlib import Path

import pytest

from aivc.config import load_config
from aivc.models import LLMAnnotation, Paper
from aivc.semantic import (
    build_prototype,
    prototype_docs,
    score_semantic,
    score_text,
    select_exemplars,
    tokenize,
)

REL_ABS = (
    "We present a virtual cell model for perturbation prediction on Perturb-seq "
    "single-cell data, predicting transcriptional response to unseen genetic perturbations."
)
IRR_ABS = "We study lattice gauge theory and Monte Carlo simulations of quark confinement."


@pytest.fixture
def cfg(config_dir):
    return load_config(config_dir)


def _ann():
    return LLMAnnotation(relevance=9, circle="core", model="m", prompt_version="v4",
                         scored_at=datetime(2026, 1, 1))


def mk(title, abstract=None, pid=None, **kw):
    return Paper(id=pid or "x:" + title[:8], title=title, abstract=abstract,
                 source="other", first_seen=date(2026, 1, 1), **kw)


def test_tokenize_cjk_and_bigram():
    t = tokenize("虚拟细胞 single cell")
    assert {"虚拟", "拟细", "细胞"} <= set(t)
    assert "single_cell" in t


def test_tokenize_stopwords_and_bigrams():
    t = tokenize("We propose a method using the Perturb-seq approach")
    assert "perturb-seq" in t
    for w in ("we", "the", "propose", "method", "using", "approach", "a"):
        assert w not in t
    assert "perturb-seq" in t and tokenize("single cell") == ["single", "cell", "single_cell"]


def test_relevant_beats_irrelevant(cfg):
    rel = mk("Virtual cell perturbation prediction", REL_ABS, pid="r")
    irr = mk("Lattice gauge theory", IRR_ABS, pid="i")
    out = score_semantic([rel, irr], cfg)
    assert out[0].semantic_score - out[1].semantic_score >= 0.1
    for p in out:
        assert 0.0 <= p.semantic_score <= 1.0
        assert round(p.semantic_score, 4) == p.semantic_score


def test_no_mutation_and_empty_inputs(cfg):
    p = mk("Virtual cell perturbation prediction", REL_ABS)
    out = score_semantic([p], cfg)
    assert p.semantic_score == 0.0 and out[0] is not p
    out = score_semantic([mk("Virtual cell", None, pid="a"), mk("", "", pid="b"),
                          mk("", None, pid="c")], cfg)
    assert out[0].semantic_score > 0
    assert out[1].semantic_score == 0.0 and out[2].semantic_score == 0.0
    assert score_semantic([], cfg) == []


def test_exemplar_does_not_lower_score(cfg):
    target = mk("Perturbation response prediction with flow matching",
                "Predicting perturbation response of single cells with flow matching.", pid="t")
    ex = mk("Flow matching for perturbation response", "Flow matching predicts single-cell "
            "perturbation response across cell lines.", pid="e",
            final_score=9.0, llm=_ann())
    assert select_exemplars([ex, target]) == [ex]
    base = score_semantic([target], cfg)[0].semantic_score
    with_ex = score_semantic([target], cfg, exemplars=[ex])[0].semantic_score
    assert with_ex >= base
    assert len(prototype_docs(cfg, [ex])) == len(prototype_docs(cfg)) + 1
    assert prototype_docs(cfg, [ex])[-1][0] == "e"


def test_deterministic_and_fast(cfg):
    papers = [mk(f"Paper {i} single-cell perturbation", REL_ABS + f" variant {i}", pid=f"p{i}")
              for i in range(300)]
    exs = [mk(f"ex {i}", REL_ABS, pid=f"e{i}", final_score=9.0,
              llm=_ann()) for i in range(20)]
    t0 = time.time()
    a = score_semantic(papers, cfg, exs)
    assert time.time() - t0 < 1.0
    assert [p.semantic_score for p in a] == [p.semantic_score for p in score_semantic(papers, cfg, exs)]


def test_score_text_empty(cfg):
    proto = build_prototype(cfg)
    assert score_text("", proto) == 0.0
    assert score_text("the of and", proto) == 0.0


def test_frozen_idf_cross_batch_invariant(cfg):
    target = mk("Virtual cell perturbation prediction", REL_ABS, pid="t")
    others_a = [mk(f"Quantum finance {i}", IRR_ABS + f" {i}", pid=f"a{i}") for i in range(5)]
    others_b = [mk(f"Single cell atlas {i}", REL_ABS + f" atlas {i}", pid=f"b{i}") for i in range(9)]
    s1 = score_semantic([target, *others_a], cfg)[0].semantic_score
    s2 = score_semantic([*others_b, target], cfg)[-1].semantic_score
    assert s1 == s2 > 0


def test_unrelated_low(cfg):
    thr = cfg.keywords.semantic_rescue_threshold
    quantum = mk("Quantum error correction", "Surface codes for fault tolerant quantum computing "
                 "and stock price forecasting with options pricing models.", pid="q")
    rel = mk("Virtual cell perturbation prediction", REL_ABS, pid="r")
    a, b = score_semantic([quantum, rel], cfg)
    assert a.semantic_score < thr and a.semantic_score < b.semantic_score


def test_new_terminology_rescued_with_exemplars(cfg):
    from aivc.prefilter import apply_keywords

    text = ("forecasting transcriptional outcomes of gene knockouts with pretrained cellular models")
    abstract = (text + ". We forecast expression outcomes of gene knockouts in unseen cells "
                "using a pretrained cellular foundation model.")
    target = mk("Forecasting transcriptional outcomes of gene knockouts", abstract, pid="t")
    assert apply_keywords(target, cfg).circle == "none"
    exs = [
        mk(f"Pretrained cellular model forecasts knockout transcriptional outcomes {i}",
           "Pretrained cellular foundation models forecast transcriptional outcomes of gene "
           f"knockouts in unseen cells, expression response variant {i}.",
           pid=f"e{i}", final_score=9.0, llm=_ann())
        for i in range(4)
    ]
    base = score_semantic([target], cfg)[0].semantic_score
    boosted = score_semantic([target], cfg, exs)[0].semantic_score
    assert boosted > base
    assert boosted >= cfg.keywords.semantic_rescue_threshold


def test_exclude_ids_lowers_score(cfg):
    ex = mk("Flow matching for perturbation response", "Flow matching predicts single-cell "
            "perturbation response across cell lines.", pid="e", final_score=9.0, llm=_ann())
    proto = build_prototype(cfg, [ex])
    txt = f"{ex.title} {ex.abstract}"
    full = score_text(txt, proto)
    excl = score_text(txt, proto, frozenset({"e"}))
    assert excl < full
    out = score_semantic([ex], cfg, proto=proto, exclude_self=True)[0].semantic_score
    assert out == excl


def test_real_data_distribution_readonly(cfg):
    """真实库离线评估（阈值校准依据见 config/keywords.yaml 注释）。

    库内论文均已过关键词预筛，core 与 none 分布重合（median 约 0.125/0.128），
    因此只断言：none 组超阈值比例宽松上限、core 中位数不显著低于 none。
    """
    data_dir = Path(__file__).parent.parent / "data" / "papers"
    if not data_dir.is_dir():
        pytest.skip("no data/papers")
    from aivc.storage import PaperStore

    papers = PaperStore(data_dir.parent).load_all()
    if len(papers) < 100:
        pytest.skip("fewer than 100 papers")
    scored = score_semantic(papers, cfg, select_exemplars(papers), exclude_self=True)
    pos = [p.semantic_score for p in scored if p.llm and p.llm.circle == "core"]
    neg = [p.semantic_score for p in scored if p.llm and p.llm.circle == "none"]
    if not pos or not neg:
        pytest.skip("not enough labelled papers")
    thr = cfg.keywords.semantic_rescue_threshold
    msg = f"core median={statistics.median(pos)} none median={statistics.median(neg)}"
    assert sum(x >= thr for x in neg) / len(neg) <= 0.30, msg
    assert statistics.median(pos) >= statistics.median(neg) - 0.02, msg


def test_kw_docs_exclude_generic_ml_modifiers(cfg):
    from aivc.semantic import prototype_docs

    docs = dict(prototype_docs(cfg))
    for name in ("kw:spatial_transcriptomics", "kw:grn"):
        words = set(docs[name].lower().replace(",", " ").split())
        assert not words & {"transformer", "benchmark", "diffusion", "generative", "neural", "foundation"}
    assert "visium" in docs["kw:spatial_transcriptomics"].lower()


GENERIC_NEG = {
    "image diffusion": "Diffusion transformer for high-fidelity image generation. A transformer-based "
                       "denoising diffusion model with deep learning for text-to-image synthesis, "
                       "benchmarked against generative baselines.",
    "mri segmentation": "Deep learning for brain tumor segmentation in MRI. A neural network transformer "
                        "predicts tumor boundaries on MRI scans, benchmarked on BraTS.",
    "epidemic gnn": "Graph neural network for infectious disease transmission forecasting. We predict "
                    "epidemic spread using graph neural networks and machine learning, benchmarked on COVID-19.",
    "llm code": "Large language model for code generation. A transformer foundation model with deep "
                "learning predicts programs, benchmark on HumanEval.",
    "clinical trial": "A randomized clinical trial of statin therapy in elderly patients with primary "
                      "endpoint of cardiovascular events.",
}


def test_generic_ml_and_clinical_below_threshold_and_below_relevant(cfg):
    thr = cfg.keywords.semantic_rescue_threshold
    exs = [
        mk(f"Pretrained cellular model forecasts knockout transcriptional outcomes {i}",
           "Pretrained cellular foundation models forecast transcriptional outcomes of gene "
           f"knockouts in unseen cells, expression response variant {i}.",
           pid=f"e{i}", final_score=9.0, llm=_ann())
        for i in range(4)
    ]
    proto = build_prototype(cfg, exs)
    rel = mk("Forecasting transcriptional outcomes of gene knockouts",
             "We forecast expression outcomes of gene knockouts in unseen cells using a pretrained "
             "cellular foundation model.", pid="t")
    rel_s = score_semantic([rel], cfg, proto=proto)[0].semantic_score
    assert rel_s >= thr
    for name, text in GENERIC_NEG.items():
        s = score_text(text, proto)
        assert s < thr, (name, s)
        assert s < rel_s, (name, s, rel_s)
