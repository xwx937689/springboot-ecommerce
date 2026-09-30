"""Phase 9 - 失败归因：规则预分类 + LLM 语义归因。

分类枚举（固定五类，LLM 必须从中选择）：
  EXPECTATION_ISSUE  预期口径错——用例期望不符合系统实际契约/惯例
  DATA_STATE         业务数据/状态导致的正常拒绝（409 幂等、已支付再操作等）
  AUTH_MODEL         鉴权/角色模型判断错——该接口实际不需要或需要不同凭证
  REAL_DEFECT        真缺陷候选——系统行为违反契约或 5xx
  ENVIRONMENT        环境/基础设施问题（连接失败、503 熔断等）

流程：规则预分类给出 hint 与置信度 -> 高置信直接定类；
      低置信/UNKNOWN 的进 LLM（按服务分批）-> 合并产出 failure_report.json。
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, Field

from generator.config import LLMSettings
from generator.llm import LLMError, extract_json_array, make_client

CLASSES = {"EXPECTATION_ISSUE", "DATA_STATE", "AUTH_MODEL", "REAL_DEFECT", "ENVIRONMENT"}

SYSTEM_PROMPT = (
    "你是资深 API 测试平台工程师，负责对自动化用例失败做根因分类。"
    "被测系统：Spring Boot 微服务电商，统一响应包装 ApiResponse{success,data,error,"
    "timestamp}，校验失败 400、未认证 401、禁止 403、不存在 404、冲突 409。"
    "你必须只输出一个 JSON 数组，不要任何其他文本。"
)

HIGH_CONFIDENCE = 0.9


class Verdict(BaseModel):
    case_id: str
    classification: str
    confidence: float = 0.5
    reason: str = ""
    suggestion: str = ""


def rule_hint(r: dict) -> tuple[str | None, float, str]:
    """确定性预分类：返回 (分类, 置信度, 理由)。返回 None 表示交给 LLM。"""
    status = r.get("status", 0)
    cat = r.get("category", "")
    err = (r.get("error") or "").lower()

    if status == 0 or "http error" in err:
        return "ENVIRONMENT", HIGH_CONFIDENCE, "连接失败/网络层错误"
    if status == 503:
        return "ENVIRONMENT", HIGH_CONFIDENCE, "503 熔断/下游不可用（系统容错设计触发）"
    if status >= 500:
        return "REAL_DEFECT", 0.8, f"服务端 {status}，违反契约的未处理异常"
    if status == 409:
        return "DATA_STATE", 0.85, "409 冲突：业务状态拒绝（幂等/重复），符合设计"
    if cat == "not_found" and status in (200, 201):
        return "EXPECTATION_ISSUE", 0.7, "期望 404 但系统用 200 包装业务错误（统一 ApiResponse 惯例）"
    if cat == "auth":
        return "AUTH_MODEL", 0.5, "鉴权类用例，需结合服务实际安全配置判断"
    if status in (200, 201, 204):
        return "EXPECTATION_ISSUE", 0.6, "实际成功但用例期望非 2xx（口径或包装差异）"
    return None, 0.0, ""


def _llm_item(r: dict) -> dict:
    return {
        "id": r["case_id"],
        "service": r["service"],
        "category": r["category"],
        "method": r["method"],
        "url": r["url"].replace("http://localhost", ""),
        "expected": r.get("expected", {}),
        "actual_status": r["status"],
        "response_keys": r.get("json_keys"),
        "error": r.get("error"),
        "rule_hint": r.get("_hint"),
    }


def analyze(results_path: Path, out_path: Path, settings: LLMSettings | None = None,
            batch_size: int = 15) -> dict:
    settings = settings or LLMSettings.from_env()
    data = json.loads(results_path.read_text(encoding="utf-8-sig"))
    fails = [r for r in data["results"] if not r["ok"]]

    verdicts: list[Verdict] = []
    llm_queue: list[dict] = []

    # 1) 规则预分类
    for r in fails:
        cls, conf, why = rule_hint(r)
        r["_hint"] = f"{cls or 'UNKNOWN'}: {why}" if cls else "UNKNOWN"
        if cls and conf >= HIGH_CONFIDENCE:
            verdicts.append(Verdict(case_id=r["case_id"], classification=cls,
                                    confidence=conf, reason=why))
        else:
            llm_queue.append(r)

    # 2) LLM 归因（按服务分批）
    stats = {"total": len(fails), "rule": len(verdicts), "llm_ok": 0, "llm_failed": 0}
    client = make_client(settings) if settings.provider != "mock" else None
    if client is not None and llm_queue:
        by_service: dict[str, list[dict]] = {}
        for r in llm_queue:
            by_service.setdefault(r["service"], []).append(r)
        for svc, items in by_service.items():
            for i in range(0, len(items), batch_size):
                chunk = items[i:i + batch_size]
                try:
                    reply = client.complete(SYSTEM_PROMPT, _build_prompt(svc, chunk))
                    for raw in extract_json_array(reply.text):
                        if raw.get("classification") not in CLASSES:
                            continue
                        verdicts.append(Verdict(
                            case_id=str(raw.get("id", "")),
                            classification=raw["classification"],
                            confidence=float(raw.get("confidence", 0.5)),
                            reason=str(raw.get("reason", ""))[:300],
                            suggestion=str(raw.get("suggestion", ""))[:300],
                        ))
                    stats["llm_ok"] += 1
                except (LLMError, json.JSONDecodeError) as exc:
                    stats["llm_failed"] += 1
                    print(f"[WARN] LLM 归因失败 {svc}#{i}: {exc}")

    # 3) 兜底：没拿到结论的标 UNKNOWN
    covered = {v.case_id for v in verdicts}
    for r in llm_queue:
        if r["case_id"] not in covered:
            verdicts.append(Verdict(case_id=r["case_id"], classification="ENVIRONMENT",
                                    confidence=0.1, reason="LLM 未覆盖，默认环境类待人工复核"))

    report = {
        "summary": {
            "failures": stats["total"],
            "by_class": dict(Counter(v.classification for v in verdicts)),
            "llm_batches_ok": stats["llm_ok"],
            "llm_batches_failed": stats["llm_failed"],
            "provider": settings.provider,
            "model": settings.model or "-",
        },
        "verdicts": [v.model_dump() for v in verdicts],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _build_prompt(service: str, items: list[dict]) -> str:
    return (
        f"以下是 {service} 的自动化用例失败记录。对每条给出根因分类。\n"
        f"分类只能取：{sorted(CLASSES)}\n"
        "判别要点：\n"
        "- 409/重复数据/状态机拒绝 -> DATA_STATE；\n"
        "- 5xx 或行为违反接口契约 -> REAL_DEFECT；\n"
        "- 期望码与统一 ApiResponse 惯例不符（如期望 404 实际 200 包装）-> EXPECTATION_ISSUE；\n"
        "- 该接口实际无需鉴权或需要别的角色 -> AUTH_MODEL；\n"
        "- 连接失败/熔断 503 -> ENVIRONMENT。\n"
        "输出 JSON 数组，元素结构：\n"
        '{"id":"用例id","classification":"...","confidence":0.0~1.0,'
        '"reason":"一句话根因","suggestion":"一句话修复建议"}\n\n'
        f"失败记录：\n{json.dumps(items, ensure_ascii=False)}"
    )
