"""Agent harness 核心：受限工具循环（tool calling loop）。

循环协议（OpenAI tool_calls）：
  system(职责+不编造纪律) -> user(TP 上下文) -> [assistant(tool_calls) -> tool(结果)]*
  -> submit_case(合法) -> 结束
异常路径全部收敛，不炸循环：
  - 纯文本回复（没调工具）-> nudge 一次，引导走 submit_case；
  - submit 内容不合法 -> [REJECTED] 打回，模型可重写；
  - 轮次耗尽 -> status=failed（宁可失败，不可编造）。
每轮的调用与结果摘要进 trace——审计留痕是企业标准，也是面试叙事的证据链。
"""
from __future__ import annotations

import json

from .tools import TOOL_SCHEMAS

SYSTEM_PROMPT = (
    "你是电商系统的测试用例生成 Agent。任务：为给定的测试点（TP）写出业务级测试步骤"
    "（Given/When/Then 的素材，不含 Given/Then，只填 preconditions/steps/expected_results）。\n"
    "纪律：\n"
    "1. 写步骤前必须先 lookup_interfaces 查真实接口契约，steps 里引用的接口必须来自查询结果，禁止编造路径；\n"
    "2. 需要对齐粒度/风格时可 search_examples 查参照用例；\n"
    "3. 完成后用 submit_case 提交，它是唯一的结束方式；\n"
    "4. expected_results 必须是单一可断言的结果；steps 1~10 条，每条是一个具体操作。"
)

MAX_TURNS_DEFAULT = 6
_RESULT_CUT = 2000       # 喂回模型的工具结果上限


def _validate_submit(payload: dict) -> str | None:
    """校验 submit_case 载荷，返回错误原因或 None。"""
    pre = payload.get("preconditions")
    steps = payload.get("steps")
    exp = payload.get("expected_results")
    if not isinstance(pre, str) or not pre.strip():
        return "preconditions 不能为空"
    if not isinstance(steps, list) or not (1 <= len(steps) <= 10):
        return "steps 必须是 1~10 条"
    if not all(isinstance(s, str) and s.strip() for s in steps):
        return "steps 每条必须是非空字符串"
    if not isinstance(exp, str) or not exp.strip():
        return "expected_results 不能为空"
    return None


def build_user_context(tp: dict, ac_text: str, rule_texts: list[str]) -> str:
    """TP 上下文一次性给足（这些是确定数据，不需要工具往返）。"""
    rules = "\n".join(f"  - {r}" for r in rule_texts) or "  - （无）"
    return (
        f"测试点 {tp.get('id', '?')}（{tp.get('kind', '?')}/{tp.get('priority', '?')}）："
        f"{tp.get('title', '')}\n"
        f"验收标准：{ac_text or '（无，依据关联规则）'}\n"
        f"关联规则：\n{rules}\n"
        f"预期结果（TP 层）：{tp.get('expected_results', '')}\n"
        f"请生成该测试点的业务级测试步骤。"
    )


def run_case_agent(tp: dict, ac_text: str, rule_texts: list[str],
                   toolbox, client, max_turns: int = MAX_TURNS_DEFAULT) -> dict:
    """跑一个 TP 的生成循环。返回审计记录 dict（含 status/trace/usage）。"""
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_context(tp, ac_text, rule_texts)},
    ]
    trace: list[dict] = []
    usage = {"prompt_tokens": 0, "completion_tokens": 0}

    for turn in range(1, max_turns + 1):
        msg = client.chat(messages, TOOL_SCHEMAS)
        for k in usage:
            usage[k] += (msg.get("_usage") or {}).get(k, 0)

        # assistant 消息进队列（摘掉 _model/_usage 审计字段）
        messages.append({
            "role": "assistant",
            "content": msg.get("content", ""),
            "tool_calls": msg.get("tool_calls") or [],
        })

        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            # 没调工具：nudge 一次，不把纯文本当结论（防幻觉定稿）
            trace.append({"tool": "(no tool)", "args": "", "result": (msg.get("content") or "")[:200]})
            messages.append({"role": "user",
                             "content": "请改用 submit_case 工具提交，它是唯一的结束方式。"})
            continue

        for tc in tool_calls:
            name = tc.get("function", {}).get("name", "")
            raw_args = tc.get("function", {}).get("arguments", "{}")
            result_text = toolbox.dispatch(name, raw_args)

            if name != "submit_case":
                trace.append({"tool": name, "args": raw_args[:200], "result": result_text[:200]})
                messages.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                                 "content": result_text[:_RESULT_CUT]})
                continue

            try:
                payload = json.loads(result_text)
            except json.JSONDecodeError:
                payload = None
            if not isinstance(payload, dict) or (err := _validate_submit(payload)):
                reason = err or "submit 载荷不是合法 JSON 对象"
                trace.append({"tool": name, "args": raw_args[:200], "result": f"[REJECTED] {reason}"})
                messages.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                                 "content": f"[REJECTED] {reason}（请修正后重新提交）"})
                continue

            trace.append({"tool": name, "args": raw_args[:200], "result": "accepted"})
            return {
                "status": "filled",
                "turns": turn,
                "preconditions": payload["preconditions"].strip(),
                "steps": [s.strip() for s in payload["steps"]],
                "expected_results": payload["expected_results"].strip(),
                "notes": str(payload.get("notes", "")).strip(),
                "trace": trace,
                "usage": usage,
            }

    return {
        "status": "failed",
        "reason": f"max_turns({max_turns}) 耗尽，未收到合法 submit_case",
        "turns": max_turns,
        "trace": trace,
        "usage": usage,
    }
