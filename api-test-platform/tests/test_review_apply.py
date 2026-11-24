"""review.py apply 的正式测试网（此前是手工五路径验证，未进 pytest）。

关键纪律：要么全对、要么不动——任何错误路径产物文件哈希必须不变。
"""
import hashlib
import json

import pytest

from requirements.review import _cmd_apply


class Args:
    def __init__(self, product, decisions):
        self.product = product
        self.decisions = decisions


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


@pytest.fixture
def env(tmp_path):
    product = {
        "source_sha256": "a" * 64,
        "modules": [],
        "rules": [{"id": "BR-001", "module_id": "M1", "text": "t",
                   "review_status": "pending"},
                  {"id": "BR-002", "module_id": "M1", "text": "t",
                   "review_status": "approved"}],
        "criteria": [{"id": "AC-001", "module_id": "M1", "text": "t",
                      "review_status": "pending"}],
    }
    p = tmp_path / "product.json"
    p.write_text(json.dumps(product, ensure_ascii=False), encoding="utf-8")
    return p


def _decisions(tmp_path, decisions, sha=None):
    d = {"decisions": decisions}
    if sha is not None:
        d["product_sha256"] = sha
    p = tmp_path / "decisions.json"
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return p


def test_happy_path_applies_and_reports(env, tmp_path):
    before = _sha(env)
    rc = _cmd_apply(Args(str(env), str(_decisions(tmp_path, {"BR-001": "rejected"}))))
    assert rc == 0
    data = json.loads(env.read_text(encoding="utf-8"))
    assert data["rules"][0]["review_status"] == "rejected"
    assert _sha(env) != before


def test_multiple_errors_all_reported_once(env, tmp_path, capsys):
    # 错误收集式：三类错误一轮报清，而不是 fail-fast 一个一个修
    dec = _decisions(tmp_path, {"BR-999": "approved",       # 不存在
                                "AC-001": "pending",        # 非法值
                                "BR-002": "rejected"})      # 已裁决
    rc = _cmd_apply(Args(str(env), str(dec)))
    assert rc == 1
    err = capsys.readouterr().out
    assert "不存在" in err and "非法裁决值" in err and "已裁决" in err
    assert "共 3 处问题" in err


def test_any_error_leaves_file_untouched(env, tmp_path, capsys):
    before = _sha(env)
    dec = _decisions(tmp_path, {"BR-999": "approved", "BR-001": "rejected"})
    rc = _cmd_apply(Args(str(env), str(dec)))
    assert rc == 1
    assert _sha(env) == before                       # 要么全对、要么不动
    err = capsys.readouterr().out
    assert "BR-999" in err and "共 1 处问题" in err   # 只报真错的；合法项静默不落盘


def test_sha_mismatch_rejected(env, tmp_path):
    dec = _decisions(tmp_path, {"BR-001": "approved"}, sha="b" * 64)
    before = _sha(env)
    assert _cmd_apply(Args(str(env), str(dec))) == 1
    assert _sha(env) == before


def test_illegal_status_rejected(env, tmp_path):
    dec = _decisions(tmp_path, {"BR-001": "yes"})
    assert _cmd_apply(Args(str(env), str(dec))) == 1


def test_already_decided_rejected(env, tmp_path):
    dec = _decisions(tmp_path, {"BR-002": "rejected"})
    assert _cmd_apply(Args(str(env), str(dec))) == 1
