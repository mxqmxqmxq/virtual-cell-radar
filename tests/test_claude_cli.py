import json
import subprocess
from types import SimpleNamespace

import pytest

from aivc import llm as llm_mod
from aivc.claude_cli import ClaudeCLIClient, ClaudeCLIError
from aivc.config import load_config
from aivc.llm import LLMCache, LLMScorer, _is_fatal
from aivc.models import Paper
from datetime import date

ANN = {"tasks": ["perturbation_response_prediction"], "paradigms": ["flow_matching_diffusion_ot"], "datasets_used": ["Norman"],
       "releases_new_dataset": False, "new_dataset_names": [], "circle": "core", "relevance": 9,
       "novelty_zh": "新", "summary_zh": "摘要"}


@pytest.fixture(autouse=True)
def _nokey(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def cfg(config_dir):
    c = load_config(config_dir)
    c.llm.concurrency = 1
    return c


def paper(i=1):
    return Paper(id=f"arxiv:2611.{i:05d}", title=f"Virtual cell flow matching {i}", source="arxiv",
                 abstract="We predict perturbation responses.", first_seen=date(2026, 11, 14))


def runner_returning(payload, rc=0, stderr="", calls=None):
    def run(cmd, **kw):
        if calls is not None:
            calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, rc, stdout=json.dumps(payload) if payload is not None else "",
                                           stderr=stderr)
    return run


def test_build_cmd_is_isolated():
    cmd = ClaudeCLIClient().build_cmd("claude-haiku-5-5", "sys", {"type": "object"})
    assert cmd[:2] == ["claude", "-p"]
    for flag in ("--json-schema", "--no-session-persistence", "--strict-mcp-config", "--tools"):
        assert flag in cmd
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--model") + 1] == "claude-haiku-5-5"


def test_create_returns_tool_use_block():
    calls = []
    c = ClaudeCLIClient(runner=runner_returning({"is_error": False, "structured_output": ANN}, calls=calls))
    resp = c.messages.create(model="m", max_tokens=1, system="s",
                             messages=[{"role": "user", "content": "标题：x"}],
                             tools=[{"input_schema": {"type": "object"}}])
    assert resp.content[0].type == "tool_use" and resp.content[0].input == ANN
    assert calls[0][1]["input"] == "标题：x"  # 正文走 stdin，不放在命令行参数里


def test_missing_structured_output_is_retryable_value_error():
    c = ClaudeCLIClient(runner=runner_returning({"is_error": False, "result": "hi"}))
    with pytest.raises(ValueError):
        c.messages.create(model="m", system="s", messages=[{"content": "x"}],
                          tools=[{"input_schema": {}}])


def test_auth_failure_is_fatal():
    c = ClaudeCLIClient(runner=runner_returning(None, rc=1, stderr="Not logged in · Please run /login"))
    with pytest.raises(ClaudeCLIError) as ei:
        c.messages.create(model="m", system="s", messages=[{"content": "x"}],
                          tools=[{"input_schema": {}}])
    assert _is_fatal(ei.value)


def test_timeout_is_not_fatal():
    def run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)
    c = ClaudeCLIClient(runner=run)
    with pytest.raises(ClaudeCLIError) as ei:
        c.messages.create(model="m", system="s", messages=[{"content": "x"}],
                          tools=[{"input_schema": {}}])
    assert not _is_fatal(ei.value)


def test_auto_backend_uses_cli_without_key(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(llm_mod, "cli_available", lambda command: True)
    s = LLMScorer(cfg, LLMCache(tmp_path / "c.jsonl"))
    assert s.backend == "claude_cli" and s.available
    s._client.runner = runner_returning({"is_error": False, "structured_output": ANN})
    out, stats = s.score_papers([paper()])
    assert out[0].llm_status == "scored" and out[0].llm.relevance == 9 and stats.api_calls == 1


def test_auto_backend_prefers_api_key(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(llm_mod, "cli_available", lambda command: True)
    s = LLMScorer(cfg, LLMCache(tmp_path / "c.jsonl"), api_key="sk-test")
    assert s.backend == "api"


def test_backend_api_without_key_is_unavailable(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(llm_mod, "cli_available", lambda command: True)
    cfg.llm.backend = "api"
    s = LLMScorer(cfg, LLMCache(tmp_path / "c.jsonl"))
    assert s.backend is None and not s.available


def test_no_backend_at_all(cfg, tmp_path):
    s = LLMScorer(cfg, LLMCache(tmp_path / "c.jsonl"))
    assert not s.available
