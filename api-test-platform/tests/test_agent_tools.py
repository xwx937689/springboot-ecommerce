"""ToolBox 单测：垃圾入参收敛、检索正确性、submit 回显——垃圾进不出异常。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.tools import ToolBox


def _endpoints() -> dict:
    return {"services": [
        {"service": "cart-service", "endpoints": [
            {"service": "cart-service", "tag": "cart-controller", "method": "POST",
             "path": "/api/cart/items", "auth": "jwt", "summary": None,
             "request_body": {"schema_ref": "#/components/schemas/AddItemRequest"}},
            {"service": "cart-service", "tag": "cart-controller", "method": "GET",
             "path": "/api/cart", "auth": "jwt", "summary": None, "request_body": None},
        ]},
        {"service": "order-service", "endpoints": [
            {"service": "order-service", "tag": "order-controller", "method": "POST",
             "path": "/api/orders", "auth": "jwt", "summary": "下单",
             "request_body": None},
        ]},
    ]}


def _examples() -> list[dict]:
    return [{"title": "边界：购物车超 99 件", "gherkin": "Scenario: 购物车上限\n    Given 已登录\n    Then 拒绝"}]


def _box() -> ToolBox:
    return ToolBox({"rules": []}, _endpoints(), _examples())


# ---------- lookup_interfaces ----------

def test_lookup_hits_by_path_and_service():
    text = _box().dispatch("lookup_interfaces", json.dumps({"keyword": "cart"}))
    assert "/api/cart/items" in text and "AddItemRequest" in text

def test_lookup_hits_by_summary_chinese():
    text = _box().dispatch("lookup_interfaces", json.dumps({"keyword": "下单"}))
    assert "/api/orders" in text

def test_lookup_no_hit_is_message_not_error():
    text = _box().dispatch("lookup_interfaces", json.dumps({"keyword": "不存在的业务"}))
    assert "没有匹配" in text and "[ERROR]" not in text

def test_lookup_empty_keyword_contained():
    assert _box().dispatch("lookup_interfaces", json.dumps({"keyword": ""})).startswith("[ERROR]")

# ---------- search_examples ----------

def test_search_examples_hit():
    text = _box().dispatch("search_examples", json.dumps({"keyword": "购物车"}))
    assert "参照[1]" in text and "购物车上限" in text

def test_search_examples_miss_is_message():
    text = _box().dispatch("search_examples", json.dumps({"keyword": "无关词"}))
    assert "没有检索到" in text

# ---------- submit_case 与垃圾入参 ----------

def test_submit_case_echoes_and_stores_payload():
    box = _box()
    payload = {"preconditions": "已登录", "steps": ["POST /api/cart/items"],
               "expected_results": "返回 200"}
    text = box.dispatch("submit_case", json.dumps(payload, ensure_ascii=False))
    assert json.loads(text) == payload
    assert box._submit_payload == payload

def test_invalid_json_args_contained():
    assert _box().dispatch("lookup_interfaces", "{不是json").startswith("[ERROR]")

def test_unknown_tool_contained():
    text = _box().dispatch("delete_database", "{}")
    assert "[ERROR] 未知工具" in text

def test_non_object_args_contained():
    assert _box().dispatch("lookup_interfaces", "[1,2]").startswith("[ERROR]")
