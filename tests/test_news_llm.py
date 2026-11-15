from datetime import date
from types import SimpleNamespace

import pytest

from aivc.config import load_config
from aivc.llm import LLMCache, LLMScorer
from aivc.models import NewsItem, make_news_id
from aivc.news_llm import (NEWS_TOOL_NAME, NewsExtractor, NewsLLMStats, build_news_prompt,
                           build_news_tool, news_cache_key)

SNIP = "SECRET-SNIPPET-TEXT-9f3a unique sentence from the feed."


@pytest.fixture(autouse=True)
def _nokey(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def cfg(config_dir):
    c = load_config(config_dir)
    c.llm.concurrency = 1
    return c


def mk(i, title=None, **kw):
    t = title or f"Acme Bio raises funding round number {i}"
    return NewsItem(id=make_news_id(f"https://ex.com/{i}"), title=t, url=f"https://ex.com/{i}",
                    source="Ex", published=date(2026, 11, 1), first_seen=date(2026, 11, 2), **kw)


def good(**kw):
    d = {"event_type": "funding", "relevance": 8, "companies": ["Acme Bio"], "investors": ["VC One"],
         "amount_usd": 120000000, "round": "Series B", "summary_zh": "某公司完成融资。",
         "significance_zh": "资金规模较大。"}
    d.update(kw)
    return d


class FakeClient:
    def __init__(self, inputs=None):
        self.calls = []
        self.inputs = inputs
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        inp = self.inputs(len(self.calls)) if callable(self.inputs) else (self.inputs or good())
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=inp)])


def ext(cfg, tmp_path, client):
    cache = LLMCache(tmp_path / "c" / "news.jsonl")
    return NewsExtractor(cfg, cache, client=client), cache


def test_extract_ok(cfg, tmp_path):
    cl = FakeClient()
    e, _ = ext(cfg, tmp_path, cl)
    out, st = e.extract([(mk(1), SNIP)])
    it = out[0]
    assert isinstance(st, NewsLLMStats)
    assert (st.scored, st.api_calls, st.errors) == (1, 1, 0)
    assert it.llm_status == "scored" and it.event_type == "funding" and it.relevance == 8
    assert it.amount_usd == 120000000 and it.round == "Series B" and it.companies == ["Acme Bio"]
    assert it.llm.prompt_version == cfg.pipeline.news.prompt_version
    assert it.llm.model == (cfg.pipeline.news.model or cfg.llm.model)
    assert cl.calls[0]["tool_choice"]["name"] == NEWS_TOOL_NAME
    assert cl.calls[0]["model"] == it.llm.model


def test_cache_hit_no_call_and_skip_already(cfg, tmp_path):
    cl = FakeClient()
    e, cache = ext(cfg, tmp_path, cl)
    e.extract([(mk(1), SNIP)])
    e2 = NewsExtractor(cfg, LLMCache(tmp_path / "c" / "news.jsonl"), client=cl)
    out, st = e2.extract([(mk(1), SNIP)])
    assert len(cl.calls) == 1 and st.cache_hits == 1 and out[0].llm_status == "scored"
    again, st2 = e2.extract([(out[0], SNIP)])
    assert len(cl.calls) == 1 and st2.cache_hits == 0 and again[0] is out[0]


def test_prompt_version_change_rescores(cfg, tmp_path):
    cl = FakeClient()
    e, _ = ext(cfg, tmp_path, cl)
    first, _ = e.extract([(mk(1), SNIP)])
    cfg.pipeline.news.prompt_version = "news-vNEXT"
    e2 = NewsExtractor(cfg, LLMCache(tmp_path / "c" / "news.jsonl"), client=cl)
    out, st = e2.extract([(first[0], SNIP)])
    assert len(cl.calls) == 2 and st.api_calls == 1 and out[0].llm.prompt_version == "news-vNEXT"


def test_no_backend(cfg, tmp_path):
    e, _ = ext(cfg, tmp_path, None)
    assert not e.available
    out, st = e.extract([(mk(1), SNIP), (mk(2), "")])
    assert [o.llm_status for o in out] == ["unscored"] * 2
    assert all(o.llm_skip_reason == "no_backend" for o in out)
    assert st.skipped_no_backend == 2


def test_budget(cfg, tmp_path):
    cl = FakeClient()
    e, _ = ext(cfg, tmp_path, cl)
    out, st = e.extract([(mk(i), SNIP + str(i)) for i in range(4)], max_calls=2)
    assert len(cl.calls) == 2 and st.skipped_budget == 2 and st.scored == 2
    assert [o.llm_skip_reason for o in out[2:]] == ["budget", "budget"]
    assert len(out) == 4 and out[0].id == mk(0).id


@pytest.mark.parametrize("bad", [
    {"event_type": "nonsense", "relevance": 5, "companies": [], "investors": [],
     "summary_zh": "x", "significance_zh": "y"},
    {"event_type": "funding", "companies": [], "investors": [], "summary_zh": "x", "significance_zh": "y"},
    {"event_type": "funding", "relevance": 5, "companies": "notalist", "investors": [],
     "summary_zh": "x", "significance_zh": "y"},
])
def test_invalid_retry_then_error(cfg, tmp_path, bad):
    cl = FakeClient(inputs=bad)
    e, _ = ext(cfg, tmp_path, cl)
    out, st = e.extract([(mk(1), SNIP)])
    assert len(cl.calls) == 2 and st.api_calls == 1 and st.errors == 1
    assert out[0].llm_status == "error" and out[0].llm_skip_reason.startswith("invalid_output")


def test_retry_succeeds(cfg, tmp_path):
    cl = FakeClient(inputs=lambda n: {"event_type": "bad"} if n == 1 else good())
    e, _ = ext(cfg, tmp_path, cl)
    out, st = e.extract([(mk(1), SNIP)])
    assert out[0].llm_status == "scored" and st.errors == 0 and len(cl.calls) == 2


def test_circuit_breaker(cfg, tmp_path):
    cl = FakeClient(inputs={"bad": 1})
    e, _ = ext(cfg, tmp_path, cl)
    out, st = e.extract([(mk(i), SNIP + str(i)) for i in range(8)])
    assert st.errors == 5 and st.disabled_reason
    assert [o.llm_skip_reason for o in out[5:]] == ["llm_disabled"] * 3
    assert len(cl.calls) == 10


def test_relevance_clamp_and_amounts(cfg, tmp_path):
    cases = [(15, None, None), (-3, 0, None), (7, -5, None), (7, 0, None)]
    for n, (rel, amt, _) in enumerate(cases):
        d = good(relevance=rel, amount_usd=amt)
        e, _ = ext(cfg, tmp_path / str(n), FakeClient(inputs=d))
        out, _ = e.extract([(mk(1), SNIP)])
        assert out[0].relevance == max(0, min(10, rel)) and out[0].amount_usd is None


def test_amount_missing_null_and_trunc(cfg, tmp_path):
    d = good(summary_zh="长" * 500, significance_zh="短" * 500)
    del d["amount_usd"], d["round"]
    e, _ = ext(cfg, tmp_path / "a", FakeClient(inputs=d))
    o = e.extract([(mk(1), SNIP)])[0][0]
    assert o.amount_usd is None and o.round is None
    assert len(o.summary_zh) == 200 and len(o.significance_zh) == 120
    e, _ = ext(cfg, tmp_path / "b", FakeClient(inputs=good(amount_usd=None, round=None)))
    o = e.extract([(mk(1), SNIP)])[0][0]
    assert o.amount_usd is None and o.round is None


def test_cache_file_has_no_snippet(cfg, tmp_path):
    e, cache = ext(cfg, tmp_path, FakeClient())
    e.extract([(mk(1), SNIP)])
    text = cache.path.read_text(encoding="utf-8")
    assert text.strip()
    assert SNIP not in text and "SECRET-SNIPPET" not in text


def test_cache_key(cfg):
    a = news_cache_key(mk(1), "s1", "m", "v1")
    assert a == news_cache_key(mk(1), "s1", "m", "v1")
    assert a != news_cache_key(mk(1), "s2", "m", "v1")
    assert a != news_cache_key(mk(1), "s1", "m", "v2")
    assert "s1" not in a


def test_prompt_and_tool(cfg):
    item = mk(1, companies=["Acme Bio"])
    system, user = build_news_prompt(item, "x" * 5000, cfg)
    assert cfg.pipeline.news.prompt_version in system
    assert "不得臆测" in system and "自己的话" in system and "不得照抄" in system
    assert "不得使用引号引用原文" in system and "120000000" in system
    for t in ("funding", "m_and_a", "model_or_data_release"):
        assert t in system
    assert item.title in user and "Acme Bio" in user and "Ex" in user and "2026-11-01" in user
    assert cfg.companies.companies[0].name in user
    assert len(user) < 5000
    tool = build_news_tool()
    sch = tool["input_schema"]
    assert tool["name"] == NEWS_TOOL_NAME
    assert "amount_usd" not in sch["required"] and "round" not in sch["required"]
    assert all(not isinstance(p.get("type"), list) for p in sch["properties"].values())


def test_threaded(cfg, tmp_path):
    cfg.llm.concurrency = 4
    cl = FakeClient()
    e, _ = ext(cfg, tmp_path, cl)
    out, st = e.extract([(mk(i), SNIP + str(i)) for i in range(10)])
    assert st.scored == 10 and all(o.llm_status == "scored" for o in out)


def test_v3_prompt_rules(cfg):
    assert cfg.pipeline.news.prompt_version == "news-v5"
    system, _ = build_news_prompt(mk(1), "", cfg)
    for kw in ("泛科普", "观点/评论", "访谈", "年度盘点", "框架协议", "备忘录", "区域市场拓展",
               "研究机构", "期刊新闻栏目", "大型科技公司博客", "不因公司或机构知名而加分", "只有标题",
               "品牌活动", "汇总类文章", "分析稿", "传闻或洽谈中的融资", "VC 或基金自身的募资"):
        assert kw in system, kw


def test_v5_prompt_rules(cfg):
    system, _ = build_news_prompt(mk(1), "", cfg)
    assert "news-v5" in system
    for kw in ("虚拟细胞", "细胞基础模型", "扰动", "分子生成", "临床进展", "实验室自动化", "细胞治疗",
               "无论金额多大", "封顶规则", "单细胞", "表型"):
        assert kw in system, kw
    assert "AI 药物发现领域 ≥1 亿美元融资" not in system


def test_max_requests_limits_and_counts_retries(cfg, tmp_path):
    # 每次都无输出 -> 每条 2 次请求；上限 3：第 1 条用 2 次，第 2 条首次用第 3 次、重试被拦
    cl = FakeClient(inputs=lambda n: {"bad": 1})
    e, _ = ext(cfg, tmp_path, cl)
    out, st = e.extract([(mk(i), SNIP + str(i)) for i in range(1, 4)], max_requests=3)
    assert len(cl.calls) == 3 and st.requests == 3
    assert [o.llm_status for o in out] == ["error", "error", "unscored"]
    assert out[2].llm_skip_reason == "budget" and st.skipped_budget == 1


def test_max_requests_counts_success_and_zero(cfg, tmp_path):
    cl = FakeClient()
    e, _ = ext(cfg, tmp_path, cl)
    out, st = e.extract([(mk(i), SNIP + str(i)) for i in range(1, 4)], max_requests=2)
    assert len(cl.calls) == 2 and st.requests == 2 and st.scored == 2 and st.skipped_budget == 1
    cl2 = FakeClient()
    e2, _ = ext(cfg, tmp_path / "z", cl2)
    out, st = e2.extract([(mk(9), SNIP)], max_requests=0)
    assert cl2.calls == [] and st.requests == 0 and st.skipped_budget == 1


def test_max_requests_none_unchanged(cfg, tmp_path):
    cl = FakeClient(inputs=lambda n: {"bad": 1} if n == 1 else good())
    e, _ = ext(cfg, tmp_path, cl)
    out, st = e.extract([(mk(i), SNIP + str(i)) for i in range(1, 4)])
    assert st.requests == len(cl.calls) == 4 and st.scored == 3 and st.api_calls == 3


def test_version_in_cache_key_misses_old_cache(cfg, tmp_path):
    cl = FakeClient()
    e, cache = ext(cfg, tmp_path, cl)
    cfg.pipeline.news.prompt_version = "news-v2"
    e_old = NewsExtractor(cfg, cache, client=cl)
    e_old.extract([(mk(1), SNIP)])
    assert len(cl.calls) == 1
    cfg.pipeline.news.prompt_version = "news-v5"
    e_new = NewsExtractor(cfg, LLMCache(tmp_path / "c" / "news.jsonl"), client=cl)
    out, st = e_new.extract([(mk(1), SNIP)])
    assert st.cache_hits == 0 and st.api_calls == 1 and len(cl.calls) == 2
    assert out[0].llm.prompt_version == "news-v5"
