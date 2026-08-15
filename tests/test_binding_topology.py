"""A.8.2b.2b.3c.0 —— 历史绑定拓扑的版本化（不削弱 preflight）。

两个合法拓扑，其余一律 fail-closed：
- legacy-six-bindings-v1：ssc_a1 仍持有 legacy 绑定 → 严格六绑定；
- explicit-injection-four-bindings-v1：ssc_a1 已满足显式注入契约 → 四绑定。

判定基于**结构证据**（模块字典 + 公开函数签名），不依赖可伪造的布尔标记。
全部离线：零网络、零真实 key、零付费调用。
"""
import importlib.util
import pathlib
import tempfile
import types

import pytest

from pilot.hard_gate import GateConfigError
from pilot.paid_transport import (BINDING_TOPOLOGY_INJECTED_FOUR,
                                  BINDING_TOPOLOGY_LEGACY_SIX, detect_binding_topology)

pytestmark = pytest.mark.unit


_TMP = pathlib.Path(tempfile.mkdtemp(prefix="reumani_topology_stub_"))
_SEQ = [0]


def _make_stub(name, source):
    """写出**真实源文件**并加载 —— 拓扑判定要读源码 AST，内存 stub 不足以证明。"""
    _SEQ[0] += 1
    path = _TMP / f"{name}_{_SEQ[0]}.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_LEGACY_SRC = """
from ssc_pi_agent import judge_llm, deepseek_llm_pro


def plan(state, judge_model="claude", failure_feedback=""):
    return judge_llm


def run_agent(q, constraints="", max_iterations=2):
    return deepseek_llm_pro


def _verifier_llm_call(prompt, judge_model="claude"):
    return judge_llm
"""

_INJECTED_SRC = """
def plan(state, judge_model="claude", failure_feedback="", planner_model=None):
    return planner_model


def run_agent(q, constraints="", planner_model=None, verifier_model=None,
              claim_extractor=None):
    return planner_model


def _verifier_llm_call(prompt, judge_model="claude", verifier_model=None):
    return verifier_model
"""


def _legacy_like():
    """迁移前的 ssc_a1：持有两个 legacy 绑定，公开函数不接受注入。"""
    m = _make_stub("legacy", _LEGACY_SRC)
    m.judge_llm = object()          # import 失败时也保证属性存在（离线）
    m.deepseek_llm_pro = object()
    return m


def _injected_like():
    """迁移后的 ssc_a1：无 legacy 绑定、源码无回退、公开函数接受注入。"""
    return _make_stub("injected", _INJECTED_SRC)


# ---------------------------------------------------------------- 两个合法拓扑
def test_legacy_module_is_detected_as_six_binding_topology():
    assert detect_binding_topology(_legacy_like()) == BINDING_TOPOLOGY_LEGACY_SIX


def test_injected_module_is_detected_as_four_binding_topology():
    assert detect_binding_topology(_injected_like()) == BINDING_TOPOLOGY_INJECTED_FOUR


def test_the_real_ssc_a1_matches_one_of_the_two_topologies():
    """无论当前仓库处于迁移前还是迁移后，都必须落在**明确**的合法拓扑上。"""
    import ssc_a1
    assert detect_binding_topology(ssc_a1) in (BINDING_TOPOLOGY_LEGACY_SIX,
                                               BINDING_TOPOLOGY_INJECTED_FOUR)


# ---------------------------------------------------------------- fail-closed
def test_single_sided_binding_is_refused():
    """半迁移：只剩一个 legacy 绑定 —— 无法确定模型来源。"""
    for keep in ("judge_llm", "deepseek_llm_pro"):
        m = _legacy_like()
        delattr(m, "judge_llm" if keep == "deepseek_llm_pro" else "deepseek_llm_pro")
        with pytest.raises(GateConfigError, match="单边绑定"):
            detect_binding_topology(m)


def test_missing_bindings_without_injection_contract_is_refused():
    """绑定没了，但公开函数并不接受注入 → 未知拓扑，不得放行。"""
    m = types.ModuleType("half_stub")
    m.plan = lambda state: None
    m.run_agent = lambda q: None
    m._verifier_llm_call = lambda prompt: None
    with pytest.raises(GateConfigError, match="未满足显式注入契约"):
        detect_binding_topology(m)


def test_partially_satisfied_injection_contract_is_refused():
    """只补了一半签名（例如有 planner_model 但 run_agent 缺 claim_extractor）→ 拒绝。"""
    m = types.ModuleType("partial_stub")
    m.plan = lambda state, planner_model=None: None
    m.run_agent = lambda q, planner_model=None, verifier_model=None: None    # 缺 claim_extractor
    m._verifier_llm_call = lambda prompt, verifier_model=None: None
    with pytest.raises(GateConfigError, match="claim_extractor"):
        detect_binding_topology(m)


def test_legacy_bindings_plus_injection_contract_is_refused():
    """两者兼具 = 半迁移：模型可能来自全局也可能来自注入 → 拒绝。"""
    m = _injected_like()
    m.judge_llm = object()
    m.deepseek_llm_pro = object()
    with pytest.raises(GateConfigError, match="半迁移"):
        detect_binding_topology(m)


def test_a_forged_boolean_flag_cannot_grant_the_injected_topology():
    """拓扑不看任何自称标记 —— 只看结构证据。"""
    m = types.ModuleType("forged_stub")
    m.EXPLICIT_MODEL_INJECTION = True          # 伪造的自称
    m.MIGRATED = True
    m.plan = lambda state: None
    m.run_agent = lambda q: None
    m._verifier_llm_call = lambda prompt: None
    with pytest.raises(GateConfigError):
        detect_binding_topology(m)


def test_missing_public_function_is_refused():
    m = _injected_like()
    delattr(m, "_verifier_llm_call")
    with pytest.raises(GateConfigError, match="缺函数"):
        detect_binding_topology(m)


def test_unimported_module_is_refused():
    import sys
    saved = sys.modules.pop("ssc_a1", None)
    try:
        with pytest.raises(GateConfigError, match="尚未导入"):
            detect_binding_topology(None)
    finally:
        if saved is not None:
            sys.modules["ssc_a1"] = saved


# ---------------------------------------------------------------- 强度未被削弱
def test_four_binding_topology_still_asserts_the_other_four():
    """切到四绑定拓扑**不等于**放宽：ssc_pi_agent 与 ssc_skill_agent 仍逐项断言。"""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "pilot" / "paid_transport.py").read_text(encoding="utf-8")
    body = src[src.index("def assert_bindings_after_import"):]
    for name in ("ssc_pi_agent.judge_llm", "ssc_pi_agent.deepseek_llm_pro",
                 "ssc_skill_agent.judge_llm", "ssc_skill_agent.deepseek_llm_pro"):
        assert f'"{name}"' in body, f"四绑定拓扑丢掉了 {name}"
    # 六绑定分支必须仍然补上 ssc_a1 的两项
    assert 'checks["ssc_a1.judge_llm"]' in body
    assert 'checks["ssc_a1.deepseek_llm_pro"]' in body
    # 身份断言仍是 `is not`，未被降级成真值/名称比较
    assert "if actual is not expect:" in body


def test_topology_detection_does_not_trigger_module_getattr():
    """判定用 vars(module)，不得唤醒模块级 __getattr__（与不触发式扫描一致）。"""
    calls = []
    m = _injected_like()

    def boom(name):
        calls.append(name)
        raise AssertionError("拓扑判定触发了 __getattr__")

    m.__getattr__ = boom
    assert detect_binding_topology(m) == BINDING_TOPOLOGY_INJECTED_FOUR
    assert calls == []


def test_frozen_artifacts_and_budget_untouched():
    """本轮不得改冻结评测题、协议、历史结果或预算。"""
    import pathlib
    repo = pathlib.Path(__file__).resolve().parent.parent
    for rel in ("ssc_eval_questions.json", "pilot/budget_policy.py", "pilot/prices.py"):
        p = repo / rel
        if p.exists():
            assert "A.8.2b.2b.3c.0" not in p.read_text(encoding="utf-8", errors="replace")
