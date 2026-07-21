"""A.6.5 接线验证：轨迹与护栏确实挂进了**真实 ssc_a1.run_agent 执行路径**。
全部 fake 模型 / fake 工具，**零真实 API、零 HTTP**。
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pilot import exec_wiring as EW
from pilot import hard_gate as HG
from pilot import paid_transport as PT
from pilot.hard_gate import BudgetExceeded, GatedModel, HardBudgetGate
from pilot.loop_guard import LoopGuardTriggered

CAPS = {"planner": 2, "verifier": 2, "claim_extractor": 1, "executor": 16}
# A1 形状：含可提取 PMID → exact_id 路由 → search_evidence 进入授权工具集
QUESTION = "请检索 PMID 41657283 的标题与年份"

PLAN_JSON = json.dumps({
    "question": "q", "constraints": "", "selected_resources": [],
    "steps": [{"step_id": 1, "objective": "检索", "tool_name": "search_evidence",
               "arguments": {"query": "x"}, "expected_output": "证据",
               "success_criteria": "拿到 PMID", "risk_level": "low",
               "requires_human_approval": False, "on_failure": "stop"}],
    "stop_conditions": ["证据不足"], "maximum_retries": 2}, ensure_ascii=False)
VERIFY_PASS = json.dumps({"passed": True, "reason": "ok", "missing": "无"})


@pytest.fixture(autouse=True)
def _sw(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("REUMANI_PILOT_PAID", "1")
    monkeypatch.setenv("REUMANI_PILOT_CONFIRM", "test")


def mkgate(tmp_path, **kw):
    d = dict(stage="test", ledger_path=tmp_path / "w.jsonl",
             max_usd_global=25, max_usd_stage=3, max_usd_task=1.5,
             max_calls_global=200, max_calls_task=21,
             max_calls_per_model={"fake-model": 999},
             max_calls_per_role=dict(CAPS),
             task_timeout_s=600, max_retries=0, default_max_tokens=3000)
    d.update(kw)
    return HardBudgetGate(**d)


class Scripted:
    def __init__(self, replies):
        self.calls, self._i = 0, 0
        self.replies = list(replies)
        self.max_retries, self.timeout, self.max_tokens = 0, 120.0, 3000
        self.extra_body = dict(PT.THINKING_DISABLED)

    def invoke(self, *a, **k):
        self.calls += 1
        r = self.replies[min(self._i, len(self.replies) - 1)]
        self._i += 1
        return SimpleNamespace(content=r.get("content", ""),
                               tool_calls=r.get("tool_calls", []),
                               invalid_tool_calls=[],
                               usage_metadata={"input_tokens": 100, "output_tokens": 20},
                               response_metadata={"finish_reason": r.get("finish", "stop"),
                                                  "model_name": "fake-model"})

    def bind_tools(self, *a, **k):
        return self


def wire(tmp_path, exec_replies, verify_replies=(VERIFY_PASS,), **guard_kw):
    g = mkgate(tmp_path)
    trace, guard, hooks, attached, reconciler = EW.install(
        run_id="wire-test", trace_path=tmp_path / "trace.jsonl",
        selected_tools=["search_evidence"], **guard_kw)
    inner = {"planner": Scripted([{"content": PLAN_JSON}]),
             "verifier": Scripted([{"content": v} for v in verify_replies]),
             "executor": Scripted(exec_replies)}
    roles = {r: GatedModel(inner[r], g, role=r, model_id="fake-model",
                           max_tokens=PT.MAX_TOKENS[r]) for r in inner}
    object.__setattr__(roles["executor"], "_hooks", hooks)
    return g, roles, inner, trace, guard


def run_real_chain(monkeypatch, g, roles, inner, iterations=1):
    """走真实 ssc_a1.run_agent；execute() 用 fake 的技能 agent 替身，
    但**模型调用仍然经过 GatedModel + hooks**。"""
    import ssc_a1

    def fake_execute(state, executor_model="deepseek"):
        """替身 executor：**只调模型、从不执行工具**。

        【A.6.6.1】这正是 §6 场景 B 的形状 —— requested 有、executed 没有。
        新的生命周期对账会在下一次模型调用前判定 `tool_lifecycle_inconsistent`
        并 fail-closed，因此这里的循环最多再走一轮就会被拦下。"""
        from langchain_core.messages import AIMessage
        msgs = []
        while True:
            resp = roles["executor"].invoke("react step")      # 过闸门 + hooks
            msgs.append(AIMessage(content=resp.content or ""))
            if not (resp.tool_calls or []):
                break
        return (msgs[-1].content or "结论 PMID 12345678"), msgs

    monkeypatch.setattr(ssc_a1, "execute", fake_execute)
    monkeypatch.setattr("shadow.run_shadow", lambda **kw: {
        "shadow_status": "ok", "manifest_schema_version": "runmanifest-v1"})
    monkeypatch.setattr(ssc_a1, "_save_run", lambda *a, **k: "")
    monkeypatch.setattr(ssc_a1, "_has_citations", lambda t: True)
    # 问题必须含可提取的 PMID —— run_agent 用问题文本算 allowed_tools，
    # 语义问题不会授权 search_evidence，计划会被 fail-closed 拒绝（这是正确行为）。
    return ssc_a1.run_agent(QUESTION, max_iterations=iterations, shadow=True,
                            planner_model=roles["planner"],
                            verifier_model=roles["verifier"])


@pytest.mark.unit
def test_trace_is_populated_through_real_run_agent(monkeypatch, tmp_path):
    """接线证明：真实 run_agent 跑完后，轨迹文件里有逐次模型事件。"""
    exec_replies = [
        {"content": "", "tool_calls": [{"name": "search_evidence",
                                        "args": {"q": "a"}, "id": "c1"}],
         "finish": "tool_calls"},
        {"content": "结论 PMID 12345678", "tool_calls": [], "finish": "stop"},
    ]
    g, roles, inner, trace, guard = wire(tmp_path, exec_replies)
    state = run_real_chain(monkeypatch, g, roles, inner)

    recs = [json.loads(l) for l in (tmp_path / "trace.jsonl").read_text(
        encoding="utf-8").splitlines() if l.strip()]
    models = [r for r in recs if r["event"] == "model_response"]
    # 第 1 次模型响应请求了工具；因为替身从不执行工具，
    # 生命周期对账在下一次模型调用前 fail-closed，所以只会有 1 条模型事件。
    assert len(models) >= 1, f"轨迹里模型事件 {len(models)} 条"
    assert models[0]["tool_calls_count"] == 1
    assert models[0]["tool_names"] == ["search_evidence"]
    assert models[0]["next_graph_node"] == "tools"
    assert all(len(r["content_hash"] or "") in (0, 64) for r in models)
    assert state.final_answer                     # 旧 Verifier 仍决定最终答案
    assert "未验证" in state.final_answer or "证据不足" in state.final_answer


@pytest.mark.unit
def test_loop_guard_blocks_before_provider_in_real_chain(monkeypatch, tmp_path):
    """模型每轮都要求调工具 → 护栏在**下一次模型调用之前**硬中止，
    provider 调用数停在轮数上限，且不是靠预算闸门救场。"""
    always_tool = [{"content": "", "tool_calls": [{"name": "search_evidence",
                                                   "args": {"q": "same"}, "id": "c"}],
                    "finish": "tool_calls"}]
    g, roles, inner, trace, guard = wire(tmp_path, always_tool, max_tool_rounds=8)
    state = run_real_chain(monkeypatch, g, roles, inner)

    # 相同 name+args 连续 3 次即阻断 → provider 调用数远小于 16
    # 工具从不执行 → 生命周期对账先于循环护栏拦下，provider 调用数极小
    assert inner["executor"].calls <= 4, f"provider 被调用 {inner['executor'].calls} 次"
    assert g.calls_by_role["executor"] == inner["executor"].calls
    assert state.errors, "应当记录执行失败"
    assert "未验证" in state.final_answer or "证据不足" in state.final_answer


@pytest.mark.unit
def test_tool_round_cap_distinct_args(monkeypatch, tmp_path):
    """每轮参数不同（不触发重复检测）时，工具轮数上限 8 仍然生效。"""
    replies = [{"content": "", "tool_calls": [{"name": "search_evidence",
                                               "args": {"q": f"q{i}"}, "id": f"c{i}"}],
                "finish": "tool_calls"} for i in range(30)]
    g, roles, inner, trace, guard = wire(tmp_path, replies, max_tool_rounds=8)
    run_real_chain(monkeypatch, g, roles, inner)
    assert guard.rounds <= 8
    # 工具从不执行 → 对账 fail-closed，调用数远低于 v2 的 16
    assert inner["executor"].calls <= 9, f"provider {inner['executor'].calls} 次"
    assert inner["executor"].calls < 16


@pytest.mark.unit
def test_failure_manifest_built_from_real_chain_failure(monkeypatch, tmp_path):
    always_tool = [{"content": "", "tool_calls": [{"name": "search_evidence",
                                                   "args": {"q": "same"}, "id": "c"}],
                    "finish": "tool_calls"}]
    g, roles, inner, trace, guard = wire(tmp_path, always_tool, max_tool_rounds=8)
    run_real_chain(monkeypatch, g, roles, inner)

    from pilot.executor_trace import build_failure_manifest
    m = build_failure_manifest(run_id=trace.run_id, failure_stage="executor",
                               failure_reason="loop_guard:repeated_call", trace=trace,
                               budget_summary=g.summary(), guard_summary=guard.summary())
    assert m["status"] == "failed"
    assert m["shadow_status"] == "not_run_due_to_upstream_failure"
    assert m["failure_reason"].startswith("loop_guard")
    assert m["claims"] == [] and m["evidence_cards"] == []
    blob = json.dumps(m, ensure_ascii=False)
    for leak in ("sk-", "Authorization", "Cookie", "react step", str(ROOT)):
        assert leak not in blob


@pytest.mark.unit
def test_hooks_survive_bind_tools_derivation(tmp_path):
    """派生对象必须带着同一个 hooks，否则接线会在 bind_tools 之后失效。"""
    g = mkgate(tmp_path)
    trace, guard, hooks, _, reconciler = EW.install(run_id="r", trace_path=tmp_path / "t.jsonl")
    m = GatedModel(Scripted([{"content": "x"}]), g, role="executor",
                   model_id="fake-model", max_tokens=3000, hooks=hooks)
    b = m.bind_tools([])
    assert object.__getattribute__(b, "_hooks") is hooks
    # with_config 走重包装路径（底层需提供该方法）
    object.__getattribute__(m, "_inner").with_config = lambda *a, **k: SimpleNamespace(
        invoke=lambda *x, **y: None)
    c = m.with_config()
    assert object.__getattribute__(c, "_hooks") is hooks


@pytest.mark.unit
def test_classify_executor_failure():
    assert EW.classify_executor_failure(
        LoopGuardTriggered("max_tool_rounds")) == "loop_guard:max_tool_rounds"
    assert EW.classify_executor_failure(
        BudgetExceeded("max_calls_per_role[executor]: 17 > 16")) == "role_cap"
    assert EW.classify_executor_failure(
        BudgetExceeded("max_usd_task: $2 > $1.5")) == "budget_cap"


@pytest.mark.unit
def test_skill_tools_are_wrapped_in_lifecycle_proxy(tmp_path):
    """【A.6.6】技能工具被换成 ToolLifecycleProxy —— 观察点在底层函数边界上，
    不再依赖已被证明不可靠的"事后赋 callbacks"。"""
    import ssc_skill_agent as SK
    from pilot.tool_proxy import ToolLifecycleProxy, assert_contract_equivalent

    originals = {t.name: t for t in SK.SKILL_AGENT_TOOLS}
    trace, guard, hooks, wrapped, reconciler = EW.install(
        run_id="r", trace_path=tmp_path / "t.jsonl")
    assert len(wrapped) >= 10, f"只包了 {len(wrapped)} 个工具"
    for t in SK.SKILL_AGENT_TOOLS:
        assert isinstance(t, ToolLifecycleProxy), f"{t.name} 未被代理"
        orig = originals.get(t.name)
        if orig is not None and not isinstance(orig, ToolLifecycleProxy):
            assert assert_contract_equivalent(t, orig) is True
