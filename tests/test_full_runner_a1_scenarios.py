"""A.6.6.3 §4/§5：完整 fake A1 场景，走真实 ssc_a1.run_agent + middleware 注入的
真实 create_agent。不手工 reconcile、不手工构 EvidenceCard。零真实 API、零 HTTP。

场景 A 断言链路真正到达 Verifier / Claim extractor / Shadow / 科研 Manifest。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pilot import exec_wiring as EW
from pilot import paid_transport as PT
from pilot.hard_gate import GatedModel, HardBudgetGate

GOOD_PMID = "41657283"
BAD_DOI = "10.1080/03009742.2024.2302553"
SE = {"exact": 0, "zero": 0, "const": 0, "uniq": 0}


@pytest.fixture(autouse=True)
def _sw(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("REUMANI_PILOT_PAID", "1")
    monkeypatch.setenv("REUMANI_PILOT_CONFIRM", "test")
    SE.update({"exact": 0, "zero": 0, "const": 0, "uniq": 0})
    yield
    try:
        import ssc_skill_agent as SK
        if hasattr(SK, "_REUMANI_ORIGINAL_TOOLS"):
            SK.SKILL_AGENT_TOOLS = list(SK._REUMANI_ORIGINAL_TOOLS)
    except Exception:
        pass


def mkgate(tmp_path):
    return HardBudgetGate(stage="test", ledger_path=tmp_path / "g.jsonl",
                          max_usd_global=25, max_usd_stage=3, max_usd_task=1.5,
                          max_calls_global=200, max_calls_task=21,
                          max_calls_per_model={"fake-model": 999},
                          max_calls_per_role={"planner": 2, "verifier": 2,
                                              "claim_extractor": 1, "executor": 16},
                          task_timeout_s=600, max_retries=0, default_max_tokens=3000)


class Chat:
    def __init__(self, script):
        self.n, self._i, self.script = 0, 0, list(script)
        self.max_retries, self.timeout, self.max_tokens = 0, 120.0, 3000
        self.extra_body = dict(PT.THINKING_DISABLED)

    def invoke(self, *a, **k):
        from langchain_core.messages import AIMessage
        self.n += 1
        item = self.script[min(self._i, len(self.script) - 1)]
        self._i += 1
        return AIMessage(content=item.get("content", ""),
                         tool_calls=item.get("tool_calls", []),
                         usage_metadata={"input_tokens": 10, "output_tokens": 5,
                                         "total_tokens": 15})

    def bind_tools(self, *a, **k):
        return self


def search_evidence_tool():
    from langchain_core.tools import tool

    @tool(response_format="content_and_artifact")
    def search_evidence(query: str) -> tuple:
        """精确证据检索（离线替身）。"""
        if query == GOOD_PMID:
            SE["exact"] += 1
            data = {"papers": [{"pmid": GOOD_PMID, "doi": None, "title": "CAR-T",
                                "content_level": "abstract", "supporting_excerpt": "摘要",
                                "year": "2026", "journal": "Ann Med"}],
                    "retrieval_status": "exact_hit", "query": query}
            prov = {"content_level": "abstract"}
            return "命中", {"schema_version": "toolresult-v1", "ok": True,
                          "provenance": prov, "data": data}
        SE["zero"] += 1
        return "零命中", {"schema_version": "toolresult-v1", "ok": True,
                        "provenance": {"content_level": "abstract"},
                        "data": {"retrieval_status": "zero_hits", "candidate_count": 0,
                                 "query": query, "papers": []}}

    return search_evidence


def run_via_runner_path(monkeypatch, tmp_path, exec_script, tool_obj, question,
                        verify_pass=True):
    """走真实 ssc_a1.run_agent；execute() 用真实 create_agent + 注入的 middleware。"""
    import ssc_a1
    import ssc_skill_agent as SK

    g = mkgate(tmp_path)
    trace, guard, hooks, reconciler, mw, handle = EW.install_middleware_mode(
        run_id="A1-full", trace_path=tmp_path / "trace.jsonl", selected_tools=[tool_obj.name])
    # 让技能 agent 只用这个工具
    if not hasattr(SK, "_REUMANI_ORIGINAL_TOOLS"):
        SK._REUMANI_ORIGINAL_TOOLS = list(SK.SKILL_AGENT_TOOLS)
    SK.SKILL_AGENT_TOOLS = [tool_obj]

    exec_inner = Chat(exec_script)
    exec_gm = GatedModel(exec_inner, g, role="executor", model_id="fake-model",
                         max_tokens=3000, hooks=hooks)
    plan_json = json.dumps({
        "question": "q", "constraints": "", "selected_resources": [],
        "steps": [{"step_id": 1, "objective": "检索", "tool_name": "search_evidence",
                   "arguments": {"query": GOOD_PMID}, "expected_output": "证据",
                   "success_criteria": "拿到 PMID", "risk_level": "low",
                   "requires_human_approval": False, "on_failure": "stop"}],
        "stop_conditions": ["证据不足"], "maximum_retries": 2}, ensure_ascii=False)
    vresult = json.dumps({"passed": verify_pass, "reason": "ok" if verify_pass else "no",
                          "missing": "无"})
    planner = GatedModel(Chat([{"content": plan_json}]), g, role="planner",
                         model_id="fake-model", max_tokens=2000)
    verifier = GatedModel(Chat([{"content": vresult}]), g, role="verifier",
                          model_id="fake-model", max_tokens=2000)
    claim = GatedModel(Chat([{"content": "[]"}]), g, role="claim_extractor",
                       model_id="fake-model", max_tokens=2000)

    calls = {"execute": 0, "shadow": False, "claim": 0}

    def real_execute(state, executor_model="deepseek"):
        calls["execute"] += 1
        # 走 install_middleware_mode 打过补丁的入口 —— 与未来真实 A1 相同的路径，
        # middleware 在此被注入（不是直接调 langchain.create_agent 绕开补丁）。
        agent = SK.create_react_agent(exec_gm, SK.SKILL_AGENT_TOOLS)
        try:
            out = agent.invoke({"messages": [("user", state.user_query)]},
                               {"recursion_limit": 30})
        except Exception as e:
            state.errors.append(f"执行出错：{e}")
            raise
        msgs = out["messages"]
        final = msgs[-1].content if hasattr(msgs[-1], "content") else str(msgs[-1])
        state._exec_messages = msgs        # 供 shadow 用
        return final, msgs

    def fake_shadow(**kw):
        calls["shadow"] = True
        claim.invoke("claims")             # claim extractor 真实计量
        calls["claim"] += 1
        # 从真实 ToolMessage.artifact 构 EvidenceCard（真实运行调用方）
        from pilot.evidence_from_artifact import cards_from_messages
        cards, _ = cards_from_messages(kw.get("messages") or [])
        import manifest_safety as MS
        return MS.sanitize_manifest({
            "run_id": "A1-full", "shadow_status": "ok",
            "manifest_schema_version": "runmanifest-v1",
            "evidence_cards": [c.model_dump() for c in cards],
            "comparison": {"agree": True}})

    monkeypatch.setattr(ssc_a1, "execute", real_execute)
    monkeypatch.setattr("shadow.run_shadow", fake_shadow)
    monkeypatch.setattr(ssc_a1, "_save_run", lambda *a, **k: "")
    monkeypatch.setattr(ssc_a1, "_has_citations", lambda t: True)
    try:
        state = ssc_a1.run_agent(question, max_iterations=2, shadow=True,
                                 planner_model=planner, verifier_model=verifier)
    finally:
        handle.restore()
    return state, g, trace, guard, reconciler, calls, exec_inner


@pytest.mark.unit
def test_scenario_A_full_runner_reaches_shadow(monkeypatch, tmp_path):
    q = f"请检索 PMID {GOOD_PMID} 与 DOI {BAD_DOI}"
    exec_script = [
        {"tool_calls": [{"name": "search_evidence", "args": {"query": GOOD_PMID},
                         "id": "c1"}]},
        {"tool_calls": [{"name": "search_evidence", "args": {"query": BAD_DOI},
                         "id": "c2"}]},
        {"content": f"PMID {GOOD_PMID} 命中；DOI {BAD_DOI} 零命中", "tool_calls": []},
    ]
    state, g, trace, guard, rec, calls, inner = run_via_runner_path(
        monkeypatch, tmp_path, exec_script, search_evidence_tool(), q, verify_pass=True)

    assert SE["exact"] == 1 and SE["zero"] == 1               # 工具真实执行
    life = rec.summary()["counts"]
    assert life["requested"] == 2 and life["executed"] == 2   # 逐 id 关联
    assert life["tool_returned"] == 2 and life["observed"] == 2 and life["failed"] == 0
    assert rec.inconsistencies == []
    assert g.calls_by_role["planner"] >= 1                    # Planner
    assert g.calls_by_role["verifier"] >= 1                   # Verifier
    assert calls["shadow"] is True and calls["claim"] >= 1    # Shadow + Claim
    # 旧 Verifier 决定最终答案（passed=True → 采纳执行结果）
    assert state.final_answer and "未验证" not in state.final_answer
    assert state.verification_results and state.verification_results[-1]["passed"] is True
    # EvidenceCard 只含有效 PMID，不含不存在的 DOI
    from pilot.evidence_from_artifact import cards_from_messages
    cards, _ = cards_from_messages(getattr(state, "_exec_messages", []))
    assert len(cards) == 1 and GOOD_PMID in str(cards[0].provenance.source_ids)
    assert BAD_DOI not in json.dumps([c.model_dump() for c in cards], default=str)


@pytest.mark.unit
def test_scenario_C_same_result_no_progress_full_runner(monkeypatch, tmp_path):
    """真实 Agent：不同参数、相同结果 → no_progress 从真实 observed 触发。"""
    from langchain_core.tools import tool

    @tool(response_format="content_and_artifact")
    def const_tool(query: str) -> tuple:
        """总返回相同结果。"""
        SE["const"] += 1
        return "same", {"schema_version": "toolresult-v1",
                        "provenance": {"content_level": "abstract"},
                        "data": {"const": 1}}      # 相同 data → 相同 result hash

    q = f"请检索 PMID {GOOD_PMID}"
    # 每轮不同参数、不同 id，但结果相同
    exec_script = [{"tool_calls": [{"name": "const_tool", "args": {"query": f"q{i}"},
                                    "id": f"c{i}"}]} for i in range(6)]
    state, g, trace, guard, rec, calls, inner = run_via_runner_path(
        monkeypatch, tmp_path, exec_script, const_tool, q, verify_pass=False)
    assert SE["const"] >= 3
    assert any(e["event"] == "loop_guard_triggered" and e["reason"] == "no_progress"
               for e in guard.events)
    assert state.errors                                       # fail-closed
    assert "未验证" in state.final_answer or "证据不足" in state.final_answer


@pytest.mark.unit
def test_scenario_D_new_result_makes_progress_full_runner(monkeypatch, tmp_path):
    """真实 Agent：每轮新结果 → no_progress 不误触发，正常收尾。"""
    from langchain_core.tools import tool

    @tool(response_format="content_and_artifact")
    def uniq_tool(query: str) -> tuple:
        """每次返回不同结果。"""
        SE["uniq"] += 1
        return f"r{SE['uniq']}", {"schema_version": "toolresult-v1",
                                  "provenance": {"content_level": "abstract"},
                                  "data": {"n": SE["uniq"]}}

    q = f"请检索 PMID {GOOD_PMID}"
    exec_script = [{"tool_calls": [{"name": "uniq_tool", "args": {"query": f"q{i}"},
                                    "id": f"c{i}"}]} for i in range(4)] + \
                  [{"content": "done", "tool_calls": []}]
    state, g, trace, guard, rec, calls, inner = run_via_runner_path(
        monkeypatch, tmp_path, exec_script, uniq_tool, q, verify_pass=True)
    assert not any(e["event"] == "loop_guard_triggered" and e["reason"] == "no_progress"
                   for e in guard.events)
    assert calls["shadow"] is True                           # 走到 Shadow


@pytest.mark.unit
def test_middleware_restore_in_finally(monkeypatch, tmp_path):
    """create_agent 的 monkeypatch 在 finally 一定被撤销。"""
    import ssc_skill_agent as SK
    original = SK.create_react_agent
    trace, guard, hooks, rec, mw, handle = EW.install_middleware_mode(
        run_id="r", trace_path=tmp_path / "t.jsonl", selected_tools=["search_evidence"])
    assert SK.create_react_agent is not original             # 已注入
    handle.restore()
    assert SK.create_react_agent is original                 # 已还原
    handle.restore()                                          # 幂等
