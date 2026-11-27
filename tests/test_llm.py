from datetime import date
from types import SimpleNamespace

import pytest

from aivc.config import load_config
from aivc import llm
from aivc.llm import (LLMCache, LLMScorer, ScoreStats, _is_top_venue, build_prompt,
                      build_tool, cache_key, legacy_cache_key, recompute_final_score)
from aivc.models import Paper


@pytest.fixture(autouse=True)
def _nokey(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def cfg(config_dir):
    c = load_config(config_dir)
    c.llm.concurrency = 1
    return c


def mk(i, abstract="An abstract about perturbation.", title=None):
    return Paper(id=f"arxiv:2601.{i:05d}", title=title or f"Paper number {i}", abstract=abstract,
                 source="arxiv", first_seen=date(2026, 1, 1))


def good(circle="core", rel=9, **kw):
    d = {"tasks": ["perturbation_response_prediction"], "paradigms": ["foundation_model_pretraining"],
         "datasets_used": ["Replogle"], "releases_new_dataset": False, "new_dataset_names": [],
         "circle": circle, "relevance": rel, "novelty_zh": "新", "summary_zh": "摘要",
         "novelty_score": rel, "rigor_score": rel, "impact_score": rel}
    d.update(kw)
    return d


class FakeClient:
    def __init__(self, inputs=None, exc=None):
        self.calls = []
        self.inputs = inputs
        self.exc = exc
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        inp = self.inputs(len(self.calls)) if callable(self.inputs) else (self.inputs or good())
        return SimpleNamespace(content=[SimpleNamespace(type="text"), SimpleNamespace(type="tool_use", input=inp)])


def scorer(cfg, tmp_path, client, key=None):
    return LLMScorer(cfg, LLMCache(tmp_path / "c" / "llm.jsonl"), client=client, api_key=key)


def test_no_key_degrades(cfg, tmp_path):
    s = scorer(cfg, tmp_path, None)
    assert not s.available
    out, st = s.score_papers([mk(1), mk(2)])
    assert [p.llm_status for p in out] == ["unscored"] * 2
    assert all(p.llm_skip_reason == "no_api_key" for p in out)
    assert st.skipped_no_key == 2 and st.api_calls == 0


def test_empty_key_is_none(cfg, tmp_path):
    assert not scorer(cfg, tmp_path, None, key="").available


def test_scoring_and_weights_and_call_args(cfg, tmp_path):
    c = FakeClient()
    s = scorer(cfg, tmp_path, c)
    papers = [mk(1)]
    out, st = s.score_papers(papers)
    p = out[0]
    assert papers[0].llm_status == "unscored"  # not mutated
    assert p.llm_status == "scored" and p.final_score == 9.0 and p.must_read
    assert p.llm.circle == "core" and p.llm.relevance == 9 and p.llm.model == "claude-haiku-5-5"
    kw = c.calls[0]
    assert kw["model"] == "claude-haiku-5-5"
    assert kw["tool_choice"] == {"type": "tool", "name": "record_paper_annotation"}
    assert st.api_calls == 1 and st.scored == 1

    c2 = FakeClient(good("peripheral", 9))
    out, _ = scorer(cfg, tmp_path / "x", c2).score_papers([mk(2)])
    assert out[0].final_score == 5.4 and not out[0].must_read  # 四维分均 9 -> base 9


def test_enum_mapping_and_clamp(cfg, tmp_path):
    c = FakeClient(good(tasks=["bogus"], relevance=15))
    out, _ = scorer(cfg, tmp_path, c).score_papers([mk(1)])
    assert out[0].llm.tasks == ["other"] and out[0].llm.relevance == 10


def test_record_level_skip(cfg, tmp_path):
    c = FakeClient()
    s = scorer(cfg, tmp_path, c)
    out, _ = s.score_papers([mk(1)])
    out2, st = s.score_papers(out)
    assert len(c.calls) == 1 and st.skipped_already == 1 and st.api_calls == 0


def test_cache_level_hit(cfg, tmp_path):
    c = FakeClient()
    scorer(cfg, tmp_path, c).score_papers([mk(1)])
    c2 = FakeClient()
    s2 = scorer(cfg, tmp_path, c2)
    out, st = s2.score_papers([mk(1)])
    assert st.cache_hits == 1 and st.api_calls == 0 and not c2.calls
    assert out[0].llm_status == "scored"


def test_cache_hit_without_key(cfg, tmp_path):
    scorer(cfg, tmp_path, FakeClient()).score_papers([mk(1)])
    s = scorer(cfg, tmp_path, None)
    out, st = s.score_papers([mk(1), mk(2)])
    assert out[0].llm_status == "scored" and out[1].llm_skip_reason == "no_api_key"


def test_cache_key_ignores_id(cfg):
    a, b = mk(1, title="Same Title"), mk(2, title="same title!")
    assert cache_key(a, "m", "v1") == cache_key(b, "m", "v1")


def test_no_abstract(cfg, tmp_path):
    c = FakeClient()
    out, st = scorer(cfg, tmp_path, c).score_papers([mk(1, abstract=None)])
    assert out[0].llm_skip_reason == "no_abstract" and not c.calls and st.skipped_no_abstract == 1


def test_budget(cfg, tmp_path):
    c = FakeClient()
    out, st = scorer(cfg, tmp_path, c).score_papers([mk(i) for i in range(5)], max_calls=2)
    assert len(c.calls) == 2 and st.skipped_budget == 3
    assert [p.llm_skip_reason for p in out[2:]] == ["budget"] * 3


def test_invalid_output_retries_then_error(cfg, tmp_path):
    c = FakeClient({"relevance": "x"})
    out, st = scorer(cfg, tmp_path, c).score_papers([mk(1)])
    assert len(c.calls) == 2 and out[0].llm_status == "error" and st.errors == 1


def test_retry_recovers(cfg, tmp_path):
    c = FakeClient(lambda n: {"bad": 1} if n == 1 else good())
    out, _ = scorer(cfg, tmp_path, c).score_papers([mk(1)])
    assert out[0].llm_status == "scored" and len(c.calls) == 2


class AuthErr(Exception):
    status_code = 401


def test_fatal_circuit_breaker(cfg, tmp_path):
    c = FakeClient(exc=AuthErr("bad key"))
    out, st = scorer(cfg, tmp_path, c).score_papers([mk(i) for i in range(4)])
    assert len(c.calls) == 1 and st.disabled_reason
    assert all(p.llm_status == "unscored" and p.llm_skip_reason == "llm_disabled" for p in out)


def test_consecutive_errors_breaker(cfg, tmp_path):
    cfg.llm.api_retries = 0
    c = FakeClient(exc=RuntimeError("net"))
    out, st = scorer(cfg, tmp_path, c).score_papers([mk(i) for i in range(8)])
    assert len(c.calls) == 5 and st.disabled_reason
    assert out[-1].llm_skip_reason == "llm_disabled"


def test_corrupt_cache_lines(tmp_path):
    f = tmp_path / "llm.jsonl"
    f.write_text('not json\n{"key":"a","value":{"x":1}}\n{"nokey":1}\n', encoding="utf-8")
    c = LLMCache(f)
    assert c.get("a") == {"x": 1} and len(c) == 1


def test_prompt_contents(cfg):
    system, user = build_prompt(mk(1), cfg)
    assert "perturbation_response_prediction" in system and "world_model_jepa" in system
    assert "中文" in system and "术语保留英文" in system
    assert "Paper number 1" in user and "An abstract" in user


def test_concurrent_order(cfg, tmp_path):
    cfg.llm.concurrency = 4
    c = FakeClient()
    papers = [mk(i) for i in range(12)]
    out, st = scorer(cfg, tmp_path, c).score_papers(papers)
    assert [p.id for p in out] == [p.id for p in papers]
    assert st.scored == 12 and len(c.calls) == 12


def test_stats_type():
    assert ScoreStats().disabled_reason is None


# ---------------------------------------------------------------- v4
def four(**kw):
    d = good("core", 8, novelty_score=9, rigor_score=7, impact_score=8,
             why_it_matters_zh="值得关注", limitations_zh="摘要未提及")
    d.update(kw)
    return d


def test_api_retries_backoff(cfg, tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr(llm, "_sleep", sleeps.append)
    cfg.llm.api_retries = 2
    c = FakeClient(exc=RuntimeError("net"))
    out, st = scorer(cfg, tmp_path, c).score_papers([mk(1)])
    assert sleeps == [1.0, 2.0] and len(c.calls) == 3
    assert out[0].llm_status == "error" and st.api_calls == 3 and st.errors == 1


def test_api_retry_recovers(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_sleep", lambda s: None)
    c = FakeClient()
    orig = c._create

    def flaky(**kw):
        if len(c.calls) == 0:
            c.calls.append(kw)
            raise RuntimeError("blip")
        return orig(**kw)

    c.messages = SimpleNamespace(create=flaky)
    out, st = scorer(cfg, tmp_path, c).score_papers([mk(1)])
    assert out[0].llm_status == "scored" and len(c.calls) == 2 and st.api_calls == 2


def test_fatal_not_retried(cfg, tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr(llm, "_sleep", sleeps.append)
    c = FakeClient(exc=AuthErr("bad"))
    scorer(cfg, tmp_path, c).score_papers([mk(1)])
    assert len(c.calls) == 1 and sleeps == []


def test_four_dim_composite(cfg, tmp_path):
    p = mk(1).model_copy(update={"has_code": True, "code_url": "https://github.com/a/b",
                                 "weights_url": "https://huggingface.co/a/b", "has_weights": True,
                                 "citation_count": 50})
    out, _ = scorer(cfg, tmp_path, FakeClient(four())).score_papers([p])
    assert out[0].final_score == 8.9  # base 8.1 + capped bonus 0.8
    a = out[0].llm
    assert (a.novelty_score, a.rigor_score, a.impact_score) == (9, 7, 8)
    assert a.why_it_matters_zh == "值得关注"


def test_none_circle_no_bonus_and_peripheral(cfg, tmp_path):
    p = mk(1).model_copy(update={"has_code": True})
    out, _ = scorer(cfg, tmp_path / "a", FakeClient(four(circle="none"))).score_papers([p])
    assert out[0].final_score == round(8.1 * 0.3, 1)
    out, _ = scorer(cfg, tmp_path / "b", FakeClient(four(circle="peripheral"))).score_papers([p])
    assert out[0].final_score == round(8.1 * 0.6 + 0.2, 1)


def test_final_capped_at_10(cfg, tmp_path):
    p = mk(1).model_copy(update={"has_code": True, "hf_upvotes": 50, "has_weights": True})
    out, _ = scorer(cfg, tmp_path, FakeClient(four(relevance=10, novelty_score=10, rigor_score=10,
                                                   impact_score=10))).score_papers([p])
    assert out[0].final_score == 10.0


def test_v3_clamped_and_none(cfg, tmp_path):
    cfg.llm.prompt_version = "v3"
    out, _ = scorer(cfg, tmp_path, FakeClient(four(novelty_score=15, rigor_score=-2))).score_papers([mk(1)])
    assert out[0].llm.novelty_score == 10 and out[0].llm.rigor_score == 0
    d = four()
    for k in ("novelty_score", "rigor_score", "impact_score"):
        del d[k]
    out, _ = scorer(cfg, tmp_path / "x", FakeClient(d)).score_papers([mk(2)])
    assert out[0].llm_status == "scored" and out[0].llm.novelty_score is None


def test_is_top_venue():
    tv = ["Nature", "Cell", "Science"]
    assert _is_top_venue("Nature", tv) and _is_top_venue("nature", tv)
    assert _is_top_venue("Nature (2026)", tv) and _is_top_venue("Cell 2025", tv)
    assert not _is_top_venue("Nature Communications", ["Nature"])
    assert _is_top_venue("Nature Methods", ["Nature", "Nature Methods"])
    assert not _is_top_venue("Cellular Signalling", tv)
    assert not _is_top_venue(None, tv) and not _is_top_venue("bioRxiv", tv)


def test_build_tool_and_prompt_v4(cfg):
    props = build_tool(cfg)["input_schema"]
    for k in ("novelty_score", "rigor_score", "impact_score", "why_it_matters_zh", "limitations_zh"):
        assert k in props["properties"] and k in props["required"]
    system, _ = build_prompt(mk(1), cfg)
    assert "novelty_score" in system and "锚点" in system
    cfg.llm.rubric_lines = ["## 自定义细则"]
    cfg.llm.anchor_lines = ["- 自定义锚点行"]
    system, _ = build_prompt(mk(1), cfg)
    assert "自定义细则" in system and "自定义锚点行" in system and "10 档" not in system


def test_backend_auto_openai(cfg, tmp_path, monkeypatch):
    import json
    import httpx
    from aivc.openai_compat import OpenAICompatClient

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    seen = {}

    def handler(req):
        seen["auth"] = req.headers.get("authorization")
        args = json.dumps(four())
        return httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [
            {"function": {"name": "record_paper_annotation", "arguments": args}}]}}]})

    real = OpenAICompatClient

    def fake(base_url, key, **kw):
        return real(base_url, key, client=httpx.Client(transport=httpx.MockTransport(handler)), **kw)

    monkeypatch.setattr(llm, "OpenAICompatClient", fake)
    s = scorer(cfg, tmp_path, None)
    assert s.backend == "openai_compat" and s.available
    out, st = s.score_papers([mk(1)])
    assert out[0].llm_status == "scored" and out[0].llm.novelty_score == 9
    assert seen["auth"] == "Bearer sk-test"


def test_backend_explicit_openai_needs_key(cfg, tmp_path):
    cfg.llm.backend = "openai_compat"
    assert scorer(cfg, tmp_path, None).backend is None
    cfg.llm.openai_base_url = "http://localhost:11434/v1"
    assert scorer(cfg, tmp_path, None).backend == "openai_compat"


# ---------------------------------------------------------------- T04 新增
def _scored_v3(cfg, tmp_path):
    cfg.llm.prompt_version = "v3"
    out, _ = scorer(cfg, tmp_path / "v3", FakeClient(four())).score_papers([mk(1)])
    cfg.llm.prompt_version = "v4"
    assert out[0].llm_status == "scored" and out[0].llm.prompt_version == "v3"
    return out[0]


@pytest.mark.parametrize("bad", [
    lambda d: d.pop("novelty_score"), lambda d: d.update(rigor_score=11),
    lambda d: d.update(impact_score=-1), lambda d: d.update(novelty_score=True),
    lambda d: d.update(novelty_score="8"),
])
def test_v4_strict_scores(cfg, tmp_path, bad):
    d = four()
    bad(d)
    c = FakeClient(d)
    out, st = scorer(cfg, tmp_path, c).score_papers([mk(1)])
    assert out[0].llm_status == "error" and st.errors == 1 and st.scored == 0
    assert out[0].llm_skip_reason.startswith("invalid_output")


def test_openai_effective_model_consistent(cfg, tmp_path, monkeypatch):
    import json
    import httpx
    from aivc.openai_compat import OpenAICompatClient

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [
            {"function": {"name": "record_paper_annotation", "arguments": json.dumps(four())}}]}}]})

    real = OpenAICompatClient
    monkeypatch.setattr(llm, "OpenAICompatClient", lambda b, k, **kw: real(
        b, k, client=httpx.Client(transport=httpx.MockTransport(handler)), **kw))
    s = scorer(cfg, tmp_path, None)
    p = mk(1)
    out, _ = s.score_papers([p])
    assert s.effective_model == "gpt-4o-mini" and seen["body"]["model"] == "gpt-4o-mini"
    assert out[0].llm.model == "gpt-4o-mini"
    import json as _j
    rows = [_j.loads(x) for x in (tmp_path / "c" / "llm.jsonl").read_text().splitlines()]
    assert rows[0]["model"] == "gpt-4o-mini"
    assert rows[0]["key"] == cache_key(p, "gpt-4o-mini", cfg.llm.prompt_version, "openai_compat")
    assert rows[0]["key"] != cache_key(p, cfg.llm.model, cfg.llm.prompt_version, "api")


def test_openai_without_model_and_claude_name_disabled(cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg.llm.openai_model = None
    s = scorer(cfg, tmp_path, None)
    assert not s.available and s.backend is None
    out, st = s.score_papers([mk(1)])
    assert out[0].llm_skip_reason == "no_api_key" and "Claude" in st.disabled_reason
    cfg.llm.model = "deepseek-chat"
    assert scorer(cfg, tmp_path, None).effective_model == "deepseek-chat"


def test_legacy_cache_key_readable(cfg, tmp_path):
    p = mk(1)
    cache = LLMCache(tmp_path / "c" / "llm.jsonl")
    cache.put(legacy_cache_key(p, cfg.llm.model, cfg.llm.prompt_version), four())
    c = FakeClient()
    out, st = LLMScorer(cfg, cache, client=c).score_papers([p])
    assert st.cache_hits == 1 and not c.calls and out[0].llm_status == "scored"
    assert cache_key(p, "m", "v1") == cache_key(p, "m", "v1", "api")
    assert cache_key(p, "m", "v1") != legacy_cache_key(p, "m", "v1")
    assert cache_key(p, "m", "v1", "openai_compat") != cache_key(p, "m", "v1", "api")


def test_upgrade_budget_zero_keeps_old_scored(cfg, tmp_path):
    old = _scored_v3(cfg, tmp_path)
    c = FakeClient(four())
    out, st = scorer(cfg, tmp_path / "n", c).score_papers([old], max_calls=0)
    q = out[0]
    assert q.llm_status == "scored" and q.llm == old.llm and q.final_score == old.final_score
    assert q.llm_skip_reason.startswith("upgrade_skipped:") and not c.calls and st.skipped_budget == 1


def test_upgrade_fatal_and_breaker_keep_old(cfg, tmp_path):
    old = _scored_v3(cfg, tmp_path)
    out, _ = scorer(cfg, tmp_path / "a", FakeClient(exc=AuthErr("x"))).score_papers([old])
    assert out[0].llm_status == "scored" and out[0].llm == old.llm
    assert out[0].llm_skip_reason.startswith("upgrade_skipped:")
    cfg.llm.api_retries = 0
    out, _ = scorer(cfg, tmp_path / "b", FakeClient({"bad": 1})).score_papers([old])
    assert out[0].llm_status == "scored" and out[0].llm == old.llm
    assert out[0].llm_skip_reason.startswith("upgrade_skipped:invalid_output")
    out, _ = scorer(cfg, tmp_path / "c2", None).score_papers([old])
    assert out[0].llm_status == "scored" and out[0].llm_skip_reason == "upgrade_skipped:no_api_key"


def test_recompute_final_score(cfg, tmp_path):
    base = mk(1).model_copy(update={"code_url": "https://github.com/a/b"})
    out, _ = scorer(cfg, tmp_path, FakeClient(four())).score_papers([base])
    p = out[0]
    assert p.final_score == 8.1  # code_url 无发布证据不加分
    q = recompute_final_score(p.model_copy(update={"published_venue": "Nature"}), cfg)
    assert q.final_score == 8.4
    q2 = recompute_final_score(p.model_copy(update={"citation_count": 50, "has_code": True}), cfg)
    assert q2.final_score == 8.6
    assert recompute_final_score(q2, cfg) == q2 and recompute_final_score(q2, cfg) is q2
    un = mk(3)
    assert recompute_final_score(un, cfg) is un
    hi = p.model_copy(update={"has_code": True, "has_weights": True, "citation_count": 99})
    cfg.llm.must_read_threshold = 8.5
    assert recompute_final_score(hi, cfg).must_read


def test_retries_count_as_requests(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_sleep", lambda s: None)
    cfg.llm.api_retries = 2
    c = FakeClient()
    orig = c._create

    def flaky(**kw):
        if len(c.calls) < 2:
            c.calls.append(kw)
            raise RuntimeError("net")
        return orig(**kw)

    c.messages = SimpleNamespace(create=flaky)
    out, st = scorer(cfg, tmp_path, c).score_papers([mk(1)])
    assert out[0].llm_status == "scored" and st.api_calls == 3 and st.papers_attempted == 1


def test_retries_hit_max_calls(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_sleep", lambda s: None)
    cfg.llm.api_retries = 5
    c = FakeClient(exc=RuntimeError("net"))
    out, st = scorer(cfg, tmp_path, c).score_papers([mk(1), mk(2)], max_calls=2)
    assert len(c.calls) == 2 and st.api_calls == 2
    assert out[0].llm_status == "error" and st.skipped_budget == 1
    assert out[1].llm_skip_reason == "budget"


class BadReq(Exception):
    status_code = 400


def test_400_not_retried(cfg, tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr(llm, "_sleep", sleeps.append)
    c = FakeClient(exc=BadReq("nope"))
    out, st = scorer(cfg, tmp_path, c).score_papers([mk(1)])
    assert len(c.calls) == 1 and st.api_calls == 1 and sleeps == [] and out[0].llm_status == "error"


def test_429_and_5xx_retried(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_sleep", lambda s: None)
    for code in (429, 503):
        E = type("E", (Exception,), {"status_code": code})
        c = FakeClient(exc=E("x"))
        scorer(cfg, tmp_path / str(code), c).score_papers([mk(1)])
        assert len(c.calls) == 3


def test_openai_400_downgrade_end_to_end(cfg, tmp_path, monkeypatch):
    import json
    import httpx
    from aivc.openai_compat import OpenAICompatClient

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    bodies = []

    def handler(req):
        body = json.loads(req.content)
        bodies.append(body)
        if "tools" in body:
            return httpx.Response(400, json={"error": "tools unsupported"})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(four())}}]})

    real = OpenAICompatClient
    monkeypatch.setattr(llm, "OpenAICompatClient", lambda b, k, **kw: real(
        b, k, client=httpx.Client(transport=httpx.MockTransport(handler)), **kw))
    out, st = scorer(cfg, tmp_path, None).score_papers([mk(1)])
    assert out[0].llm_status == "scored" and st.api_calls == 2 and st.papers_attempted == 1
    assert "response_format" in bodies[1] and "tools" not in bodies[1]


def test_anthropic_sdk_internal_retries_disabled(cfg, tmp_path, monkeypatch):
    """SDK 自带重试会绕过 max_calls 预算：必须 max_retries=0，且 429 连续失败时 api_calls==真实请求数并在上限停止。"""
    import sys

    seen = {}
    E429 = type("E429", (Exception,), {"status_code": 429})
    reqs = []

    class FakeAnthropic:
        def __init__(self, **kw):
            seen.update(kw)
            self.messages = SimpleNamespace(create=self._create)

        def _create(self, **kw):
            reqs.append(kw)
            raise E429("rate")

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=FakeAnthropic))
    monkeypatch.setattr(llm, "_sleep", lambda s: None)
    cfg.llm.api_retries = 5
    s = LLMScorer(cfg, LLMCache(tmp_path / "c" / "llm.jsonl"), api_key="k")
    out, st = s.score_papers([mk(1), mk(2)], max_calls=3)
    assert seen["max_retries"] == 0
    assert len(reqs) == 3 and st.api_calls == 3
