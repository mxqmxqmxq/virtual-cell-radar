import json

import httpx
import pytest

from aivc.openai_compat import OpenAICompatClient, OpenAICompatError

TOOLS = [{"name": "record", "description": "d",
          "input_schema": {"type": "object", "properties": {"a": {"type": "integer"}}}}]


def make(handler, **kw):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatClient("http://llm.test/v1/", kw.pop("api_key", "sk-x"), client=http, **kw)


def call(c):
    return c.messages.create(model="m1", max_tokens=50, system="sys",
                             messages=[{"role": "user", "content": "hi"}],
                             tools=TOOLS, tool_choice={"type": "tool", "name": "record"})


def resp(message):
    return httpx.Response(200, json={"choices": [{"message": message}]})


def test_tool_calls_path():
    seen = {}

    def h(req):
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = json.loads(req.content)
        return resp({"tool_calls": [{"function": {"name": "record", "arguments": '{"a": 3}'}}]})

    r = call(make(h))
    assert r.content[0].type == "tool_use" and r.content[0].input == {"a": 3}
    assert seen["url"] == "http://llm.test/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-x"
    b = seen["body"]
    assert b["model"] == "m1" and b["max_tokens"] == 50 and b["temperature"] == 0
    assert b["messages"][0] == {"role": "system", "content": "sys"}
    assert b["messages"][1] == {"role": "user", "content": "hi"}
    assert b["tools"] == [{"type": "function", "function": {
        "name": "record", "description": "d", "parameters": TOOLS[0]["input_schema"]}}]
    assert b["tool_choice"] == {"type": "function", "function": {"name": "record"}}


def test_arguments_dict():
    r = call(make(lambda req: resp({"tool_calls": [{"function": {"arguments": {"a": 1}}}]})))
    assert r.content[0].input == {"a": 1}


def test_content_fallback_with_fence():
    r = call(make(lambda req: resp({"content": "```json\n{\"a\": 7}\n```"})))
    assert r.content[0].input == {"a": 7}


def test_content_not_json():
    with pytest.raises(ValueError, match="不是可解析的 JSON"):
        call(make(lambda req: resp({"content": "sorry, no"})))


def test_http_401():
    with pytest.raises(OpenAICompatError) as e:
        call(make(lambda req: httpx.Response(401, text="bad key")))
    assert e.value.status_code == 401


def test_http_503():
    with pytest.raises(OpenAICompatError) as e:
        call(make(lambda req: httpx.Response(503, text="down")))
    assert e.value.status_code == 503


def test_transport_error():
    def h(req):
        raise httpx.ConnectError("boom")
    with pytest.raises(OpenAICompatError) as e:
        call(make(h))
    assert e.value.status_code is None


def test_no_key_no_auth_header():
    seen = {}

    def h(req):
        seen["has"] = "authorization" in req.headers
        return resp({"content": "{\"a\": 1}"})

    call(make(h, api_key=None))
    assert seen["has"] is False


def test_model_override():
    seen = {}

    def h(req):
        seen["model"] = json.loads(req.content)["model"]
        return resp({"content": "{\"a\": 1}"})

    call(make(h, model_override="local-llama"))
    assert seen["model"] == "local-llama"


def _seq(statuses, bodies):
    def h(req):
        bodies.append(json.loads(req.content))
        st = statuses[len(bodies) - 1]
        if st == 200:
            return resp({"content": "{\"a\": 5}"})
        return httpx.Response(st, text="unsupported")
    return h


def test_400_downgrades_to_json_object():
    bodies = []
    r = call(make(_seq([400, 200], bodies)))
    assert r.n_requests == 2 and r.content[0].input == {"a": 5}
    b = bodies[1]
    assert b["response_format"] == {"type": "json_object"}
    assert "tools" not in b and "tool_choice" not in b
    assert json.dumps(TOOLS[0]["input_schema"], ensure_ascii=False) in b["messages"][0]["content"]


def test_two_400_then_plain():
    bodies = []
    r = call(make(_seq([400, 422, 200], bodies)))
    assert r.n_requests == 3 and r.content[0].input == {"a": 5}
    assert "response_format" not in bodies[2] and "tools" not in bodies[2]


def test_three_400_raises():
    bodies = []
    with pytest.raises(OpenAICompatError) as e:
        call(make(_seq([400, 400, 400], bodies)))
    assert e.value.status_code == 400 and e.value.n_requests == 3


def test_downgrade_sticks_on_instance():
    bodies = []
    c = make(_seq([400, 200, 200], bodies))
    call(c)
    r = call(c)
    assert r.n_requests == 1
    assert "tools" not in bodies[2] and bodies[2]["response_format"] == {"type": "json_object"}
    assert c.requests_made == 3


def test_401_no_downgrade():
    bodies = []
    with pytest.raises(OpenAICompatError) as e:
        call(make(_seq([401], bodies)))
    assert e.value.status_code == 401 and len(bodies) == 1 and e.value.n_requests == 1
