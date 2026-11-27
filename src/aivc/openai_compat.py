"""OpenAI 兼容 chat/completions 后端（OpenAI / DeepSeek / OpenRouter / Ollama / vLLM）。

对外暴露与 anthropic.Anthropic 相同形状的 `client.messages.create(...)`，
返回带一个 tool_use 块的响应，LLMScorer 无需区分后端。
密钥由调用方传入，模块导入时不读环境变量。
"""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

import httpx

_FATAL_STATUS = (401, 403, 404)


class OpenAICompatError(RuntimeError):
    def __init__(self, msg: str, status_code: int | None = None, n_requests: int = 1):
        super().__init__(msg)
        # llm._is_fatal 认 401/403/404；429/5xx/网络错误为非致命，交给退避重试
        self.status_code = status_code
        self.n_requests = n_requests  # 本次调用实际发出的 HTTP 请求数（含降级重发）


_DOWNGRADE = {"tools": "json_object", "json_object": "plain"}
_SCHEMA_HINT = "\n\n只输出一个 JSON 对象，不要任何其他文字；字段必须符合以下 JSON Schema："


def _parse_json_text(text: str) -> dict:
    s = (text or "").strip()
    s = re.sub(r"^```[a-zA-Z0-9]*\s*", "", s)
    s = re.sub(r"\s*```$", "", s).strip()
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("openai_compat 输出不是可解析的 JSON")
    try:
        d = json.loads(s[i:j + 1])
    except json.JSONDecodeError as e:
        raise ValueError("openai_compat 输出不是可解析的 JSON") from e
    if not isinstance(d, dict):
        raise ValueError("openai_compat 输出不是可解析的 JSON")
    return d


class OpenAICompatClient:
    def __init__(self, base_url: str, api_key: str | None, *, timeout: float = 120,
                 model_override: str | None = None, client: httpx.Client | None = None,
                 user_agent: str = "virtual-cell-radar/0.1"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or None
        self.model_override = model_override
        self.user_agent = user_agent
        self._http = client or httpx.Client(timeout=timeout)
        self.mode = "tools"  # tools / json_object / plain；降级后粘在实例上
        self.requests_made = 0
        self.messages = SimpleNamespace(create=self._create)

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "User-Agent": self.user_agent}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _create(self, *, model: str, max_tokens: int | None = None, system: str,
                messages: list[dict], tools: list[dict], tool_choice=None,
                **_ignored) -> SimpleNamespace:
        n = 0
        while True:
            body = self._body(model, max_tokens, system, messages, tools)
            n += 1
            self.requests_made += 1
            try:
                r = self._http.post(f"{self.base_url}/chat/completions",
                                    headers=self._headers(), json=body)
            except httpx.TransportError as e:
                raise OpenAICompatError(f"openai_compat 网络错误: {e}", None, n) from e
            if r.status_code in (400, 422) and self.mode in _DOWNGRADE:
                # 工具/response_format 能力不被支持：立即降级重发，不 sleep、不重发同一模式
                self.mode = _DOWNGRADE[self.mode]
                continue
            break
        if r.status_code >= 400:
            raise OpenAICompatError(f"openai_compat HTTP {r.status_code}: {r.text[:200]}",
                                    r.status_code, n)
        try:
            msg = r.json()["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise ValueError("openai_compat 输出不是可解析的 JSON") from e
        calls = msg.get("tool_calls") or []
        if calls:
            args = (calls[0].get("function") or {}).get("arguments")
            if isinstance(args, dict):
                return self._wrap(args, n)
            if isinstance(args, str):
                return self._wrap(_parse_json_text(args), n)
        return self._wrap(_parse_json_text(msg.get("content") or ""), n)

    def _body(self, model, max_tokens, system, messages, tools) -> dict:
        if self.mode == "tools":
            sys_text = system
        else:
            sys_text = system + _SCHEMA_HINT + json.dumps(tools[0]["input_schema"],
                                                          ensure_ascii=False)
        body = {
            "model": self.model_override or model,
            "messages": [
                {"role": "system", "content": sys_text},
                {"role": "user", "content": messages[0]["content"]},
            ],
            "max_tokens": max_tokens or 1024,
            "temperature": 0,
        }
        if self.mode == "tools":
            body["tools"] = [{"type": "function",
                              "function": {"name": t["name"],
                                           "description": t.get("description", ""),
                                           "parameters": t["input_schema"]}} for t in tools]
            body["tool_choice"] = {"type": "function", "function": {"name": tools[0]["name"]}}
        elif self.mode == "json_object":
            body["response_format"] = {"type": "json_object"}
        return body

    @staticmethod
    def _wrap(d: dict, n_requests: int = 1) -> SimpleNamespace:
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=d)],
                               n_requests=n_requests)
