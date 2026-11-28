"""Agent harness 循环单测：用脚本化 FakeClient 走完所有分支——不花一分钱 token。

覆盖：正常闭环 / 纯文本 nudge / submit 被打回重写 / 轮次耗尽 failed / usage 审计。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.runner import run_case_agent, build_user_context
from agent.tools import ToolBox


def _tc(call_id: str, name: str, **args) -> dict:
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def _reply(content="", tool_calls=None, usage=(10, 5)) -> dict:
    return {"role": "assistant", "content": content, "tool_calls": tool_calls or [],
            "_usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]}}


class FakeClient:
    """脚本化客户端：按序弹出预设回复，记录收到的消息队列供断言。"""

    def __init__(self, replies: list[dict]):
        self.replies = list(replies)
        self.calls = 0
        self.seen: list[list[dict]] = []

    def chat(self, messages: list[dict], tools=None) -> dict:
        self.calls += 1
        self.seen.append([dict(m) for m in messages])
        return self.replies.pop(0)


_TP = {"id": "TP-232", "kind": "exception", "priority": "P1",
       "title": "异常：重复提交订单", "expected_results": "只生成一笔订单"}

_VALID = {"preconditions": "购物车中已有商品且已登录",
          "steps": ["调用 POST /api/cart/items 加入商品", "连续两次调用 POST /api/orders 提交"],
          "expected_results": "仅生成一笔订单", "notes": "防重提交校验"}


def _box() -> ToolBox:
    eps = {"services": [{"service": "order-service", "endpoints": [
        {"service": "order-service", "tag": "order-controller", "method": "POST",
         "path": "/api/orders", "auth": "jwt", "summary": "下单", "request_body": None}]}]}
    return ToolBox({}, eps, [])


# ---------- 正常闭环 ----------

def test_happy_path_lookup_then_submit():
    client = FakeClient([
        _reply(tool_calls=[_tc("c1", "lookup_interfaces", keyword="订单")]),
        _reply(tool_calls=[_tc("c2", "submit_case", **_VALID)]),
    ])
    result = run_case_agent(_TP, "重复提交应只生成一笔", ["订单不能重复提交"], _box(), client)
    assert result["status"] == "filled"
    assert result["steps"] == _VALID["steps"]
    assert result["turns"] == 2
    assert [t["tool"] for t in result["trace"]] == ["lookup_interfaces", "submit_case"]
    # usage 审计：两轮 10+10 / 5+5
    assert result["usage"] == {"prompt_tokens": 20, "completion_tokens": 10}
    # tool 结果确实以 role=tool 喂回过模型
    assert any(m.get("role") == "tool" for batch in client.seen for m in batch)

# ---------- 纯文本回复 -> nudge ----------

def test_plain_text_gets_nudged_then_submit():
    client = FakeClient([
        _reply(content="我觉得可以提交了"),
        _reply(tool_calls=[_tc("c2", "submit_case", **_VALID)]),
    ])
    result = run_case_agent(_TP, "", [], _box(), client)
    assert result["status"] == "filled"
    # nudge 消息确实发出了
    assert any(m.get("role") == "user" and "唯一的结束方式" in m.get("content", "")
               for batch in client.seen for m in batch)

# ---------- submit 不合法 -> 打回重写 ----------

def test_invalid_submit_rejected_then_retry():
    invalid = dict(_VALID, steps=[])
    client = FakeClient([
        _reply(tool_calls=[_tc("c1", "submit_case", **invalid)]),
        _reply(tool_calls=[_tc("c2", "submit_case", **_VALID)]),
    ])
    result = run_case_agent(_TP, "", [], _box(), client)
    assert result["status"] == "filled"
    # [REJECTED] 打回消息进了模型的消息队列
    assert any(m.get("role") == "tool" and "[REJECTED]" in m.get("content", "")
               for batch in client.seen for m in batch)
    assert any("steps 必须是 1~10 条" in t["result"] for t in result["trace"])

# ---------- 轮次耗尽 -> failed（宁可失败，不可编造） ----------

def test_max_turns_exhausted_fails():
    stalling = _reply(tool_calls=[_tc("c1", "lookup_interfaces", keyword="订单")])
    client = FakeClient([stalling] * 3)
    result = run_case_agent(_TP, "", [], _box(), client, max_turns=3)
    assert result["status"] == "failed"
    assert "max_turns" in result["reason"]
    assert client.calls == 3

# ---------- 上下文组装 ----------

def test_build_user_context_contains_tp_and_rules():
    text = build_user_context(_TP, "重复提交应只生成一笔", ["订单不能重复提交"])
    assert "TP-232" in text and "重复提交应只生成一笔" in text and "订单不能重复提交" in text

def test_build_user_context_empty_ac_fallback():
    text = build_user_context(_TP, "", [])
    assert "（无，依据关联规则）" in text
