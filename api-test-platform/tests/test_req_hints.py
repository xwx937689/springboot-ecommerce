"""_load_req_points 的适用域分流单测：非 api 域 TP 不进 hint 池（死提示实证）。"""
import json
import tempfile
from pathlib import Path

from generator.generator import _fallback_domain, _load_req_points
from requirements.testpoints import _classify_domain


def _write_req(tmp_path, tps):
    p = tmp_path / "requirements.json"
    p.write_text(json.dumps({"test_points": tps}, ensure_ascii=False), encoding="utf-8")
    return p


def test_non_api_domain_excluded_from_hints():
    tps = [
        {"id": "TP-001", "priority": "P0", "kind": "positive",
         "title": "正向：方案文档标题中应包含产品标识及迭代版本"},
        {"id": "TP-034", "priority": "P1", "kind": "negative",
         "title": "创建日期为空或非法日期时判定失败"},
        {"id": "TP-060", "priority": "P0", "kind": "positive",
         "title": "正向：售后订单的支付行数据中存在「B2B 让利金额」字段"},
        {"id": "TP-050", "priority": "P0", "kind": "boundary",
         "title": "边界：取 6 个（超过上限 5）应被拒绝"},
    ]
    with tempfile.TemporaryDirectory() as d:
        hints, index = _load_req_points(_write_req(Path(d), tps))
    ids = [h["id"] for h in hints]
    assert ids == ["TP-050"]            # doc_spec/data_layer 都被拦，只留 api 域
    assert set(index) == {"TP-001", "TP-034", "TP-060", "TP-050"}  # 追溯索引仍全量


def test_domain_field_takes_priority_over_title_keywords():
    # 产生时打标优先：title 含关键词但显式 domain=api 的 TP 不误拦
    tps = [{"id": "TP-070", "priority": "P0", "kind": "positive", "domain": "api",
            "title": "正向：文档中心接口返回标题字段列表"}]
    with tempfile.TemporaryDirectory() as d:
        hints, _ = _load_req_points(_write_req(Path(d), tps))
    assert [h["id"] for h in hints] == ["TP-070"]


def test_fallback_domain_marks():
    assert _fallback_domain("正向：文档标题字段的值为「OPMS 产品方案【迭代11】」时判定通过") == "doc_spec"
    assert _fallback_domain("正向：售后订单的支付行数据中存在「B2B 让利金额」字段") == "data_layer"
    # 回归护栏："流转"曾是误拦源（TP-011 活提示被误判 data_layer），必须保持 api
    assert _fallback_domain("正向：渠道 300 的 CPC人工收银订单在 OMS 中不再以渠道号 201 流转") == "api"


def test_classify_domain_at_source():
    # 与 generator 回退词表同口径（testpoints._classify_domain 产生时盖章）
    assert _classify_domain("标题缺少产品标识或迭代版本") == "doc_spec"
    assert _classify_domain("正向：售后订单的支付行数据中存在「B2B 让利金额」字段") == "data_layer"
    assert _classify_domain("正向：渠道 300 的 CPC人工收银订单在 OMS 中不再以渠道号 201 流转") == "api"
    assert _classify_domain("正向：购物车上限为 5 件，第 6 件应被拒绝") == "api"
