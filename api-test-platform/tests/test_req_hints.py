"""_load_req_points 的适用域分流单测：文档规范类 TP 不进 hint 池（死提示实证）。"""
import json
import tempfile
from pathlib import Path

from generator.generator import _is_doc_spec_tp, _load_req_points


def _write_req(tmp_path, tps):
    p = tmp_path / "requirements.json"
    p.write_text(json.dumps({"test_points": tps}, ensure_ascii=False), encoding="utf-8")
    return p


def test_doc_spec_tp_excluded_from_hints():
    tps = [
        {"id": "TP-001", "priority": "P0", "kind": "positive",
         "title": "正向：方案文档标题中应包含产品标识及迭代版本"},
        {"id": "TP-034", "priority": "P1", "kind": "negative",
         "title": "创建日期为空或非法日期时判定失败"},
        {"id": "TP-050", "priority": "P0", "kind": "boundary",
         "title": "边界：取 6 个（超过上限 5）应被拒绝"},
    ]
    with tempfile.TemporaryDirectory() as d:
        hints, index = _load_req_points(_write_req(Path(d), tps))
    ids = [h["id"] for h in hints]
    assert ids == ["TP-050"]            # 文档规范类被拦，API 域 TP 保留
    assert set(index) == {"TP-001", "TP-034", "TP-050"}   # 追溯索引仍全量（不丢数据）


def test_is_doc_spec_tp_marks():
    assert _is_doc_spec_tp("正向：文档标题字段的值为「OPMS 产品方案【迭代11】」时判定通过")
    assert _is_doc_spec_tp("标题缺少产品标识或迭代版本")
    assert not _is_doc_spec_tp("边界：取 6 个（超过上限 5）应被拒绝")
    assert not _is_doc_spec_tp("正向：购物车上限为 5 件，第 6 件应被拒绝")
