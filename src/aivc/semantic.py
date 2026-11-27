"""纯标准库 TF-IDF + 余弦的语义预筛：给 Paper 写 semantic_score（0~1）。"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from .config import AppConfig
from .models import Paper

_TOKEN_RE = re.compile(r"[a-z][a-z0-9\-]{1,}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]+")

STOPWORDS = frozenset(
    """a an the and or but if of in on at to for from by with without as is are was were be been being
    this that these those it its we our us they their them which who whom what when where while than then
    also can could may might will would should has have had do does did not no into over under between
    via per such both each other more most some any all use used paper propose proposed method methods
    results approach show shows using based""".split()
)

MAX_EXEMPLARS = 200
BIGRAM_WEIGHT = 0.35  # 含下划线的 token（英文 bigram）降权；CJK 2-gram 权重 1.0
TOP_K = 3
KW_DOC_WEIGHT = 0.7  # 关键词组文档很短，少数共有词就能高相似；降权避免压过 exemplar / scope
GENERIC_TOKEN_WEIGHT = 0.3  # 全由通用 ML 词构成的 token（deep learning / foundation model ...）降权
GENERIC_WORDS = frozenset(
    """deep learning machine neural network networks foundation model models generative diffusion
    transformer transformers graph benchmark benchmarks predict prediction predictions predicting
    computational large language representation framework architecture training pretrained
    pre-trained fine-tuning attention learned data dataset datasets novel performance
    generation generate generates generating generated synthesis
    state-of-the-art outperforms effective efficient""".split()
)


def tokenize(text: str) -> list[str]:
    """小写、去停用词；英文词 + 相邻 bigram（w1_w2）+ CJK 连续字符 2-gram。"""
    low = (text or "").lower()
    words = [w for w in _TOKEN_RE.findall(low) if w not in STOPWORDS]
    out = words + [f"{a}_{b}" for a, b in zip(words, words[1:])]
    for run in _CJK_RE.findall(low):
        if len(run) == 1:
            out.append(run)
        else:
            out.extend(run[i : i + 2] for i in range(len(run) - 1))
    return out


def select_exemplars(papers: list[Paper]) -> list[Paper]:
    out = [
        p
        for p in papers
        if p.final_score is not None
        and p.final_score >= 8.0
        and p.llm is not None
        and p.llm.circle == "core"
        and (p.abstract or "").strip()
    ]
    out.sort(key=lambda p: (-(p.final_score or 0.0), p.id))
    return out


# 通用 ML / 泛修饰词：在 all_of 里只是「限定词」，不带领域含义。拼进原型会让 kw: 文档
# 退化成「通用 ML 词袋」，使图像扩散 / MRI 分割 / 流行病 GNN 等无关论文高分，故剔除。
GENERIC_MODIFIERS = frozenset(
    t.strip().lower()
    for t in """deep learning|machine learning|neural network|foundation model|generative|generative model|
    diffusion|diffusion model|diffusion-based|denoising diffusion|latent diffusion|conditional diffusion|
    with diffusion models|using diffusion models|flow matching|optimal transport|transformer|graph neural|
    representation learning|predict|predict*|prediction|computational|in silico|benchmark|inference|infer*|
    generat*|design|fine-tun*|zero-shot|causal|world model|jepa|joint embedding predictive""".replace(
        "\n", ""
    ).split("|")
    if t.strip()
)


def _domain_terms(g) -> list[str]:
    """关键词组的领域词：terms + all_of 各子组中剔除通用 ML 修饰词后的剩余词。"""
    parts = list(g.terms)
    for sub in g.all_of:
        parts.extend(t for t in sub if t.strip().lower() not in GENERIC_MODIFIERS)
    return parts


def prototype_docs(
    cfg: AppConfig, exemplars: list[Paper] | None = None
) -> list[tuple[str, str]]:
    """原型文档 (doc_id, text)：scope 圈 + 关键词组 + 冻结 exemplars。"""
    docs: list[tuple[str, str]] = []
    for name in sorted(cfg.scope.circles):
        c = cfg.scope.circles[name]
        docs.append((f"scope:{name}", " ".join([c.description, *c.topics])))
    for g in cfg.keywords.groups:
        parts = _domain_terms(g)
        docs.append((f"kw:{g.name}", " ".join(t.replace("*", "") for t in parts)))
    ex = sorted(
        exemplars or [], key=lambda p: (-(p.final_score or 0.0), p.id)
    )[:MAX_EXEMPLARS]
    for p in ex:
        docs.append((p.id, f"{p.title} {p.abstract}"))
    return [(i, d) for i, d in docs if d.strip()]


@dataclass
class Prototype:
    idf: dict[str, float] = field(default_factory=dict)
    docs: list[tuple[str, dict[str, float]]] = field(default_factory=list)
    vector: dict[str, float] = field(default_factory=dict)  # 均值归一化（调试/兼容）


def _normalize(vec: dict[str, float]) -> dict[str, float]:
    norm = math.sqrt(sum(v * v for v in vec.values()))
    if norm == 0:
        return {}
    return {k: v / norm for k, v in vec.items()}


def _token_weight(t: str) -> float:
    w = BIGRAM_WEIGHT if "_" in t else 1.0
    if all(part in GENERIC_WORDS for part in t.split("_")):
        w *= GENERIC_TOKEN_WEIGHT
    return w


def _tfidf(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    tf = Counter(tokens)
    return _normalize(
        {t: (1.0 + math.log(c)) * idf[t] * _token_weight(t) for t, c in tf.items() if t in idf}
    )


def build_prototype(cfg: AppConfig, exemplars: list[Paper] | None = None) -> Prototype:
    """冻结原型：IDF 仅来自原型文档（scope + keywords + exemplars），不含当前批次。"""
    docs = prototype_docs(cfg, exemplars)
    toks = [(i, tokenize(d)) for i, d in docs]
    n = len(toks)
    df: Counter[str] = Counter()
    for _, t in toks:
        df.update(set(t))
    idf = {t: math.log((1 + n) / (1 + d)) + 1.0 for t, d in sorted(df.items())}
    vecs = [(i, _tfidf(t, idf)) for i, t in toks]
    vecs = [(i, v) for i, v in vecs if v]
    acc: dict[str, float] = {}
    for _, v in vecs:
        for t, x in v.items():
            acc[t] = acc.get(t, 0.0) + x
    mean = {t: x / max(len(vecs), 1) for t, x in sorted(acc.items())}
    return Prototype(idf=idf, docs=vecs, vector=_normalize(mean))


def score_text(
    text: str, proto: Prototype, exclude_ids: frozenset[str] = frozenset()
) -> float:
    """与未排除原型文档余弦相似度的 Top-K 均值，[0,1]。"""
    toks = tokenize(text)
    if not toks or not proto.docs:
        return 0.0
    vec = _tfidf(toks, proto.idf)
    if not vec:
        return 0.0
    sims: list[float] = []
    for did, dv in proto.docs:
        if did in exclude_ids:
            continue
        small, big = (vec, dv) if len(vec) <= len(dv) else (dv, vec)
        sim = sum(v * big.get(t, 0.0) for t, v in small.items())
        sims.append(sim * KW_DOC_WEIGHT if did.startswith("kw:") else sim)
    if not sims:
        return 0.0
    sims.sort(reverse=True)
    top = sims[:TOP_K]
    return round(min(1.0, max(0.0, sum(top) / len(top))), 4)


def _paper_text(p: Paper) -> str:
    return " ".join([p.title or "", p.abstract or "", " ".join(p.keywords)])


def score_semantic(
    papers: list[Paper],
    cfg: AppConfig,
    exemplars: list[Paper] | None = None,
    proto: Prototype | None = None,
    exclude_self: bool = False,
) -> list[Paper]:
    if proto is None:
        proto = build_prototype(cfg, exemplars)
    out: list[Paper] = []
    for p in papers:
        ex = frozenset({p.id}) if exclude_self else frozenset()
        out.append(p.model_copy(update={"semantic_score": score_text(_paper_text(p), proto, ex)}))
    return out
