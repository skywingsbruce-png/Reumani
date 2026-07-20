"""A.6.3.3 §4：走**真实 ssc_a1.run_agent 编排**，但用 fake 模型与 fake 工具。
零真实 API、零 HTTP。目的：证明 Planner / Verifier 在真实调用链里确实分别计量。
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pilot import hard_gate as HG
from pilot import paid_transport as PT
from pilot.hard_gate import BudgetExceeded, GatedModel, HardBudgetGate

FAKE = "fake-model"
CAPS = {"planner": 2, "verifier": 2, "claim_extractor": 1, "executor": 16}


@pytest.fixture(autouse=True)
def _sw(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv(HG.ENV_PAID, "1")
    monkeypatch.setenv(HG.ENV_CONFIRM, "test")


def mkgate(tmp_path):
    return HardBudgetGate(stage="test", ledger_path=tmp_path / "orch.jsonl",
                          max_usd_global=25.0, max_usd_stage=3.0, max_usd_task=1.5,
                          max_calls_global=200, max_calls_task=21,
                          max_calls_per_model={FAKE: 999},
                          max_calls_per_role=dict(CAPS),
                          task_timeout_s=600, max_retries=0, default_max_tokens=2000)


# 必须符合冻结的 schemas.ResearchPlan / PlanStep（不放宽 schema，只把测试数据写对）
PLAN_JSON = json.dumps({
    "question": "测试问题", "constraints": "", "selected_resources": [],
    "steps": [{"step_id": 1, "objective": "检索证据", "tool_name": "search_literature",
               "arguments": {"query": "x"}, "expected_output": "候选文献",
               "success_criteria": "拿到 PMID", "risk_level": "low",
               "requires_human_approval": False, "on_failure": "stop"}],
    "stop_conditions": ["证据不足"], "maximum_retries": 2}, ensure_ascii=False)
VERIFY_PASS = json.dumps({"passed": True, "reason": "证据充分", "missing": "无"})
VERIFY_FAIL = json.dumps({"passed": False, "reason": "证据不足", "missing": "全文"})


class FakeChat:
    def __init__(self, tag, replies):
        self.tag, self.calls, self._i = tag, 0, 0
        self.replies = list(replies)
        self.max_retries, self.timeout, self.max_tokens = 0, 120.0, 2000
        self.extra_body = dict(PT.THINKING_DISABLED)

    def invoke(self, *a, **k):
        self.calls += 1
        r = self.replies[min(self._i, len(self.replies) - 1)]
        self._i += 1
        return SimpleNamespace(content=r, tool_calls=[],
                               usage_metadata={"input_tokens": 100, "output_tokens": 20},
                               response_metadata={})

    def bind_tools(self, *a, **k):
        return self


def wire(tmp_path, verify_replies):
    """构造 gate + 四个角色 wrapper（planner / verifier 独立）。"""
    g = mkgate(tmp_path)
    inner = {"planner": FakeChat("planner", [PLAN_JSON]),
             "verifier": FakeChat("verifier", verify_replies),
             "executor": FakeChat("executor", ["执行完成，见 PMID 12345678"]),
             "claim_extractor": FakeChat("claim", ['[]'])}
    roles = {r: GatedModel(inner[r], g, role=r, model_id=FAKE,
                           max_tokens=PT.MAX_TOKENS[r]) for r in CAPS}
    return g, roles, inner


def run_chain(monkeypatch, tmp_path, verify_replies, iterations=2):
    """驱动真实 run_agent，但把 executor（技能 agent）与 shadow 换成 fake。"""
    import ssc_a1
    g, roles, inner = wire(tmp_path, verify_replies)

    def fake_execute(state, executor_model="deepseek"):
        roles["executor"].invoke("react")           # 真实计量一次 executor 调用
        from langchain_core.messages import AIMessage
        return "执行完成，见 PMID 12345678", [AIMessage(content="done")]

    def fake_shadow(**kw):
        roles["claim_extractor"].invoke("claims")   # 真实计量一次 claim 调用
        return {"shadow_status": "ok", "manifest_schema_version": "runmanifest-v1"}

    monkeypatch.setattr(ssc_a1, "execute", fake_execute)
    monkeypatch.setattr("shadow.run_shadow", fake_shadow)
    monkeypatch.setattr(ssc_a1, "_save_run", lambda *a, **k: "")
    monkeypatch.setattr(ssc_a1, "_has_citations", lambda t: True)

    state = ssc_a1.run_agent("测试问题", max_iterations=iterations, shadow=True,
                             planner_model=roles["planner"],
                             verifier_model=roles["verifier"])
    return state, g, roles, inner


# 1 —— 一轮路径四类事件齐全
@pytest.mark.unit
def test_one_iteration_produces_all_four_role_events(monkeypatch, tmp_path):
    state, g, roles, inner = run_chain(monkeypatch, tmp_path, [VERIFY_PASS], iterations=1)
    ev = [e for e in g.ledger.events() if e["event"] == "reserved"]
    seen = [e["role"] for e in ev]
    assert "planner" in seen and "executor" in seen and "verifier" in seen
    assert "claim_extractor" in seen
    assert g.calls_by_role["planner"] == 1 and g.calls_by_role["verifier"] == 1
    assert state.final_answer                       # 旧 Verifier 判过 → 有最终答案


# 2 —— 两轮路径：planner ≤2、verifier ≤2、角色不同、总数正确
@pytest.mark.unit
def test_two_iterations_respect_per_role_caps(monkeypatch, tmp_path):
    state, g, roles, inner = run_chain(monkeypatch, tmp_path,
                                       [VERIFY_FAIL, VERIFY_PASS], iterations=2)
    assert g.calls_by_role["planner"] == 2
    assert g.calls_by_role["verifier"] == 2
    assert g.calls_by_role["executor"] == 2
    ev = [e["role"] for e in g.ledger.events() if e["event"] == "reserved"]
    assert ev.count("planner") == 2 and ev.count("verifier") == 2
    assert len(set(ev)) == 4                        # 四个不同角色标签
    assert g.calls_task == sum(g.calls_by_role.values())
    print("\n调用图:", json.dumps(ev, ensure_ascii=False))


# 3 —— planner 用尽后被拒，verifier 不受影响
@pytest.mark.unit
def test_planner_exhausted_then_verifier_unaffected(monkeypatch, tmp_path):
    state, g, roles, inner = run_chain(monkeypatch, tmp_path,
                                       [VERIFY_FAIL, VERIFY_PASS], iterations=2)
    with pytest.raises(BudgetExceeded, match=r"max_calls_per_role\[planner\]"):
        roles["planner"].invoke("third")
    assert inner["planner"].calls == 2              # provider 未被第 3 次触达
    # verifier 也已用满自己的 2 次，但那是它自己的额度，不是被 planner 借走
    assert g.calls_by_role["verifier"] == 2


# 4 —— verifier 用尽后被拒，planner 不受影响
@pytest.mark.unit
def test_verifier_exhausted_then_planner_unaffected(monkeypatch, tmp_path):
    g, roles, inner = wire(tmp_path, [VERIFY_PASS])
    g.start_task("T")
    roles["verifier"].invoke("1"); roles["verifier"].invoke("2")
    with pytest.raises(BudgetExceeded, match=r"max_calls_per_role\[verifier\]"):
        roles["verifier"].invoke("3")
    assert inner["verifier"].calls == 2
    roles["planner"].invoke("1")                    # planner 自己的额度还在
    assert inner["planner"].calls == 1


# 5 —— 默认未注入路径行为与修改前一致
@pytest.mark.unit
def test_default_uninjected_path_uses_global_objects(monkeypatch, tmp_path):
    import ssc_a1
    used = {"planner": 0, "verifier": 0}

    class Probe:
        def invoke(self, *a, **k):
            used["verifier"] += 1
            return SimpleNamespace(content=VERIFY_PASS)

    monkeypatch.setattr(ssc_a1, "judge_llm", Probe())
    monkeypatch.setattr(ssc_a1, "_has_citations", lambda t: True)
    state = ssc_a1.AgentState(user_query="q", max_iterations=1)
    state.plan = "p"
    ssc_a1.verify(state, "结论 PMID 1")             # 不传 verifier_model
    assert used["verifier"] == 1, "未注入时必须回落到原全局 judge_llm"


# 6 —— Pilot 注入不影响"旧 Verifier 决定最终答案"
@pytest.mark.unit
def test_injection_does_not_change_who_decides_final_answer(monkeypatch, tmp_path):
    # 旧 Verifier 判不通过 → 最终答案必须带"未验证/证据不足"，而不是直接采纳执行结果
    state, g, _, _ = run_chain(monkeypatch, tmp_path, [VERIFY_FAIL, VERIFY_FAIL],
                               iterations=2)
    assert "未验证" in state.final_answer or "证据不足" in state.final_answer
    assert state.verification_results[-1]["passed"] is False
