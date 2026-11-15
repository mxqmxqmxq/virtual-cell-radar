from datetime import date
from types import SimpleNamespace

import pytest

from aivc.config import load_config
from aivc.llm import LLMCache, LLMScorer, ScoreStats, build_prompt, cache_key
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
         "circle": circle, "relevance": rel, "novelty_zh": "新", "summary_zh": "摘要"}
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
    assert out[0].final_score == 5.4 and not out[0].must_read


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
