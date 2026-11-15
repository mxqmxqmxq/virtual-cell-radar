"""用本机 Claude Code 命令行（`claude -p`）代替 Anthropic API 打分。

走的是本机 Claude Code 的登录（订阅额度），不需要 ANTHROPIC_API_KEY。
只能在装了并登录了 Claude Code 的机器上用，GitHub Actions 里用不了。

对外暴露和 anthropic.Anthropic 相同形状的 `client.messages.create(...)`，
返回带一个 tool_use 块的响应，LLMScorer 无需区分后端。
"""
from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from types import SimpleNamespace
from typing import Callable

_AUTH_HINTS = ("not logged in", "please run /login", "invalid api key", "authentication")


class ClaudeCLIError(RuntimeError):
    def __init__(self, msg: str, fatal: bool = False):
        super().__init__(msg)
        # LLMScorer._is_fatal 认 status_code 401/403/404：致命错误本次运行停用
        self.status_code = 401 if fatal else None


def cli_available(command: str) -> bool:
    parts = shlex.split(command)
    return bool(parts) and shutil.which(parts[0]) is not None


class ClaudeCLIClient:
    def __init__(self, command: str = "claude", timeout: int = 180,
                 runner: Callable[..., subprocess.CompletedProcess] = subprocess.run):
        self.command = command
        self.timeout = timeout
        self.runner = runner
        self.messages = SimpleNamespace(create=self._create)

    def build_cmd(self, model: str, system: str, schema: dict) -> list[str]:
        return [
            *shlex.split(self.command), "-p",
            "--output-format", "json",
            "--model", model,
            "--tools", "",                 # 纯文本任务，不给任何工具
            "--system-prompt", system,
            "--json-schema", json.dumps(schema, ensure_ascii=False),
            "--no-session-persistence",    # 不在 Claude Code 里留下会话记录
            "--setting-sources", "",       # 不加载用户/项目设置（hooks、插件），更快也更干净
            "--strict-mcp-config",         # 不启动 MCP 服务器
        ]

    def _create(self, *, model: str, system: str, messages: list[dict], tools: list[dict],
                **_ignored) -> SimpleNamespace:
        user = messages[0]["content"]
        cmd = self.build_cmd(model, system, tools[0]["input_schema"])
        try:
            r = self.runner(cmd, input=user, capture_output=True, text=True, timeout=self.timeout)
        except FileNotFoundError as e:
            raise ClaudeCLIError(f"找不到 claude 命令：{e}", fatal=True) from e
        except subprocess.TimeoutExpired as e:
            raise ClaudeCLIError(f"claude 调用超时（{self.timeout}s）") from e
        out = (r.stdout or "").strip()
        if r.returncode != 0:
            tail = ((r.stderr or "") + out)[-300:]
            raise ClaudeCLIError(f"claude 退出码 {r.returncode}: {tail}",
                                 fatal=any(h in tail.lower() for h in _AUTH_HINTS))
        try:
            d = json.loads(out)
        except json.JSONDecodeError as e:
            raise ValueError(f"claude 输出不是 JSON: {out[:120]}") from e
        if d.get("is_error"):
            msg = str(d.get("result"))[:300]
            raise ClaudeCLIError(f"claude 返回错误: {msg}",
                                 fatal=any(h in msg.lower() for h in _AUTH_HINTS))
        so = d.get("structured_output")
        if not isinstance(so, dict):
            raise ValueError("claude 输出缺少 structured_output")
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=so)],
                               cost_usd=d.get("total_cost_usd"))
