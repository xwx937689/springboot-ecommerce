"""AgentHarness 离线单测：FakeClient 脚本化 tool-loop，零 token 验证循环机制。"""
import json

import pytest

from generator.agent import AgentHarness
from generator.llm import LLMError


def _msg(content="", tool_calls=None, usage=(10, 5)):
    m = {"role": "assistant", "content": content,
         "_model": "fake", "_usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]}}
    if tool_calls:
        m["tool_calls"] = tool_calls
    return m


def _tc(name, args, id="t1"):
    return {"id": id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


class FakeClient:
    """按脚本逐轮返回 chat 响应；记录每轮看到的消息角色序列。"""

    def __init__(self, script):
        self.script = list(script)
        self.role_history = []

    def chat(self, messages, tools=None):
        self.role_history.append([m["role"] for m in messages])
        return self.script.pop(0)


SCHEMAS = {"CreateOrderRequest": {"properties": {"skuId": {"type": "string"},
                                                 "quantity": {"type": "integer"}}}}
TPS = {"TP-003": {"id": "TP-003", "title": "边界：取 6 个（超过上限 5）应被拒绝",
                  "expected_results": "请求被系统拒绝"}}
CASES = [{"name": "超上限拒绝", "category": "boundary",
          "expected": {"status_codes": [400]}}]


def test_tool_loop_submit_finishes_immediately():
    # 轮1查schema → 轮2查需求 → 轮3 submit_cases：提交后立即收尾，不等第4轮
    client = FakeClient([
        _msg(tool_calls=[_tc("get_schema", {"name": "CreateOrderRequest"}, "a1")]),
        _msg(tool_calls=[_tc("get_requirement", {"tp_id": "TP-003"}, "a2")]),
        _msg(tool_calls=[_tc("submit_cases", {"cases": CASES}, "a3")]),
    ])
    h = AgentHarness(client, SCHEMAS, TPS)
    raw, usage = h.run("任务")
    assert raw == CASES
    assert usage["turns"] == 3 and usage["tool_calls"] == 3
    assert usage["prompt_tokens"] == 30 and usage["completion_tokens"] == 15
    # 工具结果以 role=tool 进队列，且审计字段不进
    assert client.role_history[1][-1] == "tool"
    assert all("_usage" not in m for round_ in client.role_history for m in round_)


def test_tool_results_carry_real_data():
    captured = {}

    class CaptureClient(FakeClient):
        def chat(self, messages, tools=None):
            for m in messages:
                if m.get("role") == "tool":
                    captured.setdefault("tool_results", []).append(json.loads(m["content"]))
            return super().chat(messages, tools)

    client = CaptureClient([
        _msg(tool_calls=[_tc("get_schema", {"name": "CreateOrderRequest"}, "a1"),
                         _tc("get_requirement", {"tp_id": "TP-003"}, "a2")]),
        _msg(tool_calls=[_tc("submit_cases", {"cases": CASES}, "a3")]),
    ])
    AgentHarness(client, SCHEMAS, TPS).run("任务")
    r = captured["tool_results"]
    assert r[0]["found"] is True and "skuId" in r[0]["schema"]["properties"]
    assert r[1]["found"] is True and r[1]["requirement"]["title"].startswith("边界")


def test_bad_arguments_returns_error_not_crash():
    # arguments 非法 JSON：给模型回错误信息继续循环，不炸 harness
    client = FakeClient([
        _msg(tool_calls=[{"id": "a1", "type": "function",
                          "function": {"name": "get_schema", "arguments": "{bad json"}}]),
        _msg(tool_calls=[_tc("submit_cases", {"cases": CASES}, "a2")]),
    ])
    raw, usage = AgentHarness(client, SCHEMAS, TPS).run("任务")
    assert raw == CASES and usage["turns"] == 2


def test_unknown_tool_keeps_loop_alive():
    client = FakeClient([
        _msg(tool_calls=[_tc("no_such_tool", {}, "a1")]),
        _msg(tool_calls=[_tc("submit_cases", {"cases": CASES}, "a2")]),
    ])
    raw, _ = AgentHarness(client, SCHEMAS, TPS).run("任务")
    assert raw == CASES


def test_content_fallback_parses_json_array():
    # 模型不走 submit_cases，直接文本输出 JSON 数组 → 兼容路径
    client = FakeClient([_msg(content=json.dumps(CASES, ensure_ascii=False))])
    raw, usage = AgentHarness(client, SCHEMAS, TPS).run("任务")
    assert raw == CASES and usage["turns"] == 1


def test_max_turns_guard():
    # 每轮都查工具但永不提交 → 超过上限必须抛错（防失控烧 token）
    script = [_msg(tool_calls=[_tc("get_schema", {"name": "x"}, f"a{i}")])
              for i in range(10)]
    client = FakeClient(script)
    with pytest.raises(LLMError):
        AgentHarness(client, SCHEMAS, TPS, max_turns=3).run("任务")


def test_submit_with_non_list_cases_rejected():
    # cases 不是数组：不收尾，把错误回给模型
    client = FakeClient([
        _msg(tool_calls=[_tc("submit_cases", {"cases": "oops"}, "a1")]),
        _msg(tool_calls=[_tc("submit_cases", {"cases": CASES}, "a2")]),
    ])
    raw, usage = AgentHarness(client, SCHEMAS, TPS).run("任务")
    assert raw == CASES and usage["turns"] == 2
