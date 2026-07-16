"""Verifier fail-closed 测试：任何核查异常/证据不足都必须【未通过】，绝不默认放行。
verifier_call 被注入，不触发真实 LLM。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ssc_a1 import AgentState, verify

# 带可核实引用的执行结果（让证据门通过，好单独测其它失败路径）
CITED = "SSc 皮肤成纤维细胞高表达 SFRP2（PMID: 12345678），提示其致病作用。"


def _state():
    s = AgentState(user_query="SSc 的致病成纤维亚群？")
    s.plan = "1. 查文献 2. 汇总"
    return s


def _fake(ret):
    def call(prompt, judge_model="claude"):
        return ret
    return call


def _assert_failclosed(v, status=None):
    assert v["passed"] is False, f"应 fail-closed，实际 passed={v['passed']}"
    if status:
        assert v["status"] == status, f"status 期望 {status}，实际 {v['status']}"


# 1) 非法 JSON
def test_illegal_json():
    v = verify(_state(), CITED, verifier_call=_fake("这不是JSON {passed maybe"))
    _assert_failclosed(v, "verification_error")


# 2) 空字符串
def test_empty_output():
    _assert_failclosed(verify(_state(), CITED, verifier_call=_fake("")), "verification_error")
    _assert_failclosed(verify(_state(), CITED, verifier_call=_fake("   ")), "verification_error")


# 3) 超时
def test_timeout():
    def slow(prompt, judge_model="claude"):
        time.sleep(0.3)
        return '{"passed": true}'
    v = verify(_state(), CITED, verifier_call=slow, timeout=0.05)
    _assert_failclosed(v, "verifier_timeout")


# 4) 缺 passed 字段
def test_missing_passed_field():
    v = verify(_state(), CITED, verifier_call=_fake('{"reason": "看起来不错", "missing": "无"}'))
    _assert_failclosed(v, "verification_error")


# 5) passed 是字符串 "true" 而非布尔
def test_passed_is_string_true():
    v = verify(_state(), CITED, verifier_call=_fake('{"passed": "true", "reason": "ok"}'))
    _assert_failclosed(v, "verification_error")
    # 数字 1 同样不放行
    _assert_failclosed(verify(_state(), CITED, verifier_call=_fake('{"passed": 1}')), "verification_error")


# 6) 工具执行失败但 LLM 生成了看似正常的答案
def test_tool_failure_overrides_llm_pass():
    v = verify(_state(), CITED, tool_failed=True, verifier_call=_fake('{"passed": true, "reason": "答案完整"}'))
    _assert_failclosed(v, "tool_execution_failed")


# 7) 没有证据卡且结论无任何引用（强结论）→ 未验证/证据不足
def test_no_evidence_strong_conclusion():
    strong = "CENP-B 一定导致系统性硬化症，机制已完全明确，可作为治疗靶点。"  # 无 PMID/DOI
    v = verify(_state(), strong, evidence_cards=[], require_evidence=True,
               verifier_call=_fake('{"passed": true, "reason": "结论清晰"}'))
    _assert_failclosed(v, "insufficient_evidence")


# 附加：Verifier 调用异常也必须 fail-closed
def test_verifier_raises():
    def boom(prompt, judge_model="claude"):
        raise RuntimeError("connection refused")
    _assert_failclosed(verify(_state(), CITED, verifier_call=boom), "verifier_unavailable")


# 正向对照：真正通过时才 passed=True（防止改成"永远不通过"）
def test_genuine_pass():
    v = verify(_state(), CITED, verifier_call=_fake('{"passed": true, "reason": "证据充分且回答到位"}'))
    assert v["passed"] is True and v["status"] == "passed"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                print(f"FAIL {name}: {e}")
