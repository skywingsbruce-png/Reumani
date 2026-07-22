"""A.6.6.2：完整失败 Manifest / 四个 fake A1 场景 / EvidenceCard 来源 / 并发与恢复。
全部 fake 模型 + 真实 create_agent，零真实 API、零 HTTP。
"""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pilot import exec_wiring as EW
from pilot import paid_transport as PT
from pilot.evidence_from_artifact import EvidenceSourceError, cards_from_messages
from pilot.executor_trace import (ExecutorTrace, build_failure_manifest,
                                  build_failure_manifest_safe)
from pilot.hard_gate import GatedModel, HardBudgetGate
from pilot.lifecycle import LifecycleReconciler
from pilot.loop_guard import ExecutorLoopGuard

GOOD_PMID = "41657283"
BAD_DOI = "10.1080/03009742.2024.2302553"


@pytest.fixture(autouse=True)
def _sw(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("REUMANI_PILOT_PAID", "1")
    monkeypatch.setenv("REUMANI_PILOT_CONFIRM", "test")
    # 每个用例结束后把技能工具还原为原始对象，避免代理泄漏到后续用例
    yield
    try:
        import ssc_skill_agent as SK
        if hasattr(SK, "_REUMANI_ORIGINAL_TOOLS"):
            SK.SKILL_AGENT_TOOLS = list(SK._REUMANI_ORIGINAL_TOOLS)
    except Exception:
        pass


def mkgate(tmp_path, task="1"):
    return HardBudgetGate(stage="test", ledger_path=tmp_path / f"g{task}.jsonl",
                          max_usd_global=25, max_usd_stage=3, max_usd_task=1.5,
                          max_calls_global=200, max_calls_task=21,
                          max_calls_per_model={"fake-model": 999},
                          max_calls_per_role={"planner": 2, "verifier": 2,
                                              "claim_extractor": 1, "executor": 16},
                          task_timeout_s=600, max_retries=0, default_max_tokens=3000)


# ============ §1：完整失败 Manifest ============
def _mk_failed(tmp_path, kind):
    """构造一个已经处于给定失败态的 trace + reconciler。"""
    tr = ExecutorTrace(tmp_path / "t.jsonl", f"run-{kind}")
    tr.record_selected(["search_evidence"])
    rec = LifecycleReconciler(trace=tr)
    if kind == "requested_not_executed":
        rec.mark_requested("c1", "search_evidence")
    elif kind == "execution_incomplete":
        rec.mark_requested("c1", "search_evidence")
        rec.mark_executed("c1", "search_evidence")
    elif kind == "tool_result_not_observed":
        rec.mark_requested("c1", "search_evidence")
        rec.mark_executed("c1", "search_evidence")
        rec.mark_returned("c1", "search_evidence", result_hash="h")
    elif kind == "orphan_observation":
        rec._flag("orphan_observation", "cX", "search_evidence")
    elif kind == "lifecycle_conflict":
        rec._flag("lifecycle_conflict", "c1", "search_evidence")
    elif kind == "trace_incomplete":
        rec.trace_incomplete = True
    return tr, rec


@pytest.mark.unit
@pytest.mark.parametrize("kind", [
    "requested_not_executed", "execution_incomplete", "tool_result_not_observed",
    "orphan_observation", "lifecycle_conflict", "trace_incomplete",
    "loop_guard", "no_progress", "role_cap", "budget_cap", "tool_exception"])
def test_failure_manifest_has_all_required_fields(tmp_path, kind):
    tr, rec = _mk_failed(tmp_path, kind if kind in (
        "requested_not_executed", "execution_incomplete", "tool_result_not_observed",
        "orphan_observation", "lifecycle_conflict", "trace_incomplete") else "misc")
    m = build_failure_manifest(
        run_id=f"run-{kind}", failure_stage="executor", failure_reason=kind,
        trace=tr, budget_summary={"calls": 3, "usd": 0.05},
        guard_summary={"tool_rounds": 8}, reconciler=rec)
    for f in ("status", "primary_failure", "failure_stage", "failure_reason",
              "trace_incomplete", "lifecycle_counts", "lifecycle_inconsistencies",
              "shadow_status", "human_review", "manifest_schema_version", "run_id",
              "trace_file_sha256"):
        assert f in m, f"失败 Manifest 缺字段 {f}（kind={kind}）"
    assert m["status"] == "failed"
    assert m["primary_failure"] == kind
    assert m["human_review"] is True
    assert m["shadow_status"] == "not_run_due_to_upstream_failure"
    assert set(m["lifecycle_counts"]) == {"requested", "executed", "tool_returned",
                                          "failed", "observed"}
    assert m["claims"] == [] and m["evidence_cards"] == []
    blob = json.dumps(m, ensure_ascii=False)
    for leak in ("sk-", "Authorization", "Cookie"):
        assert leak not in blob


@pytest.mark.unit
def test_manifest_failure_does_not_overwrite_primary(tmp_path):
    """Manifest 自身构建失败 → 保留 primary_failure，另记 manifest_failure。"""
    class Boom:
        def consistency(self):
            raise RuntimeError("manifest build boom")

        path = Path(tmp_path / "x.jsonl")

    m, mf = build_failure_manifest_safe(
        run_id="r", failure_stage="executor", failure_reason="loop_guard:max_tool_rounds",
        trace=Boom(), budget_summary={}, reconciler=None)
    assert mf is not None and "boom" in mf
    assert m["primary_failure"] == "loop_guard:max_tool_rounds"
    assert m["manifest_failure"] == mf
    assert m["status"] == "failed" and m["human_review"] is True


# ============ §2：成功 vs 错误 ToolMessage ============
@pytest.mark.unit
def test_error_tool_message_is_not_lifecycle_conflict(tmp_path):
    """failed + error-ToolMessage 同时存在不是 lifecycle_conflict；不算进展，不构卡。"""
    from langchain_core.messages import ToolMessage
    tr = ExecutorTrace(tmp_path / "t.jsonl", "r")
    rec = LifecycleReconciler(trace=tr, guard=ExecutorLoopGuard())
    rec.mark_requested("c1", "search_evidence")
    rec.mark_failed("c1", "search_evidence", error_type="RuntimeError")
    err_tm = ToolMessage(content="Error: boom", tool_call_id="c1",
                         name="search_evidence", status="error")
    rec.reconcile_messages([err_tm])
    kinds = {i["kind"] for i in rec.inconsistencies}
    assert "lifecycle_conflict" not in kinds        # error 观察 ≠ conflict
    # 不算进展：guard 的进展信号里没有该结果
    assert len(rec.guard.progress_signals) == 0
    # 错误 ToolMessage 不构 EvidenceCard
    cards, skipped = cards_from_messages([err_tm])
    assert cards == []
    assert any(s["reason"] == "error_tool_message" for s in skipped)


@pytest.mark.unit
def test_success_toolmessage_becomes_conflict_only_if_tool_failed(tmp_path):
    """只有'已标 failed 却产生成功 ToolMessage'才是 lifecycle_conflict。"""
    from langchain_core.messages import ToolMessage
    rec = LifecycleReconciler()
    rec.mark_requested("c1", "t")
    rec.mark_failed("c1", "t", error_type="RuntimeError")
    ok_tm = ToolMessage(content="looks successful", tool_call_id="c1", name="t")  # 无 status
    rec.reconcile_messages([ok_tm])
    assert any(i["kind"] == "lifecycle_conflict" for i in rec.inconsistencies)


# ============ §4：EvidenceCard 只来自 artifact ============
def _tm(content, artifact, tcid="c1", name="search_evidence", status=None):
    from langchain_core.messages import ToolMessage
    kw = {"content": content, "tool_call_id": tcid, "name": name, "artifact": artifact}
    if status:
        kw["status"] = status
    return ToolMessage(**kw)


@pytest.mark.unit
def test_evidence_card_built_from_artifact_provenance():
    art = {"schema_version": "toolresult-v1", "ok": True,
           "data": {"papers": [{"pmid": GOOD_PMID, "doi": None, "title": "T",
                                "content_level": "abstract",
                                "supporting_excerpt": "真实摘要片段", "year": "2026",
                                "journal": "Ann Med"}], "query": "x"}}
    cards, skipped = cards_from_messages([_tm("自然语言里写着 PMID 99999999", art)])
    assert len(cards) == 1
    assert GOOD_PMID in str(cards[0].provenance.source_ids)
    assert "99999999" not in str(cards[0].provenance.source_ids)   # 不从 content 猜 ID


@pytest.mark.unit
def test_no_card_from_natural_language_only():
    """没有 artifact（普通字符串工具）→ 不构卡，即使 content 里有像 PMID 的数字。"""
    cards, skipped = cards_from_messages([_tm("结果里有 PMID 12345678", None)])
    assert cards == []


@pytest.mark.unit
def test_zero_hits_builds_no_fake_card():
    art = {"schema_version": "toolresult-v1", "data":
           {"retrieval_status": "zero_hits", "candidate_count": 0}}
    cards, skipped = cards_from_messages([_tm("零命中", art, tcid="c2")])
    assert cards == []
    assert any("zero_hits" in s["reason"] for s in skipped)


@pytest.mark.unit
def test_incompatible_schema_is_fail_closed():
    art = {"schema_version": "some-other-v9", "data": {"pmid": GOOD_PMID}}
    cards, skipped = cards_from_messages([_tm("x", art, tcid="c3")])
    assert cards == []
    assert any("incompatible_schema" in s["reason"] for s in skipped)


@pytest.mark.unit
def test_invalid_id_in_artifact_fail_closed():
    art = {"schema_version": "toolresult-v1",
           "data": {"papers": [{"pmid": "not-a-pmid", "title": "T"}]}}
    with pytest.raises(EvidenceSourceError):
        cards_from_messages([_tm("x", art, tcid="c4")], strict=True)


# ============ §3：四个 fake A1 场景（真实 create_agent） ============
SE = {"exact": 0, "zero": 0, "args": []}


def a1_tools(result_mode="ab"):
    """真实 search_evidence 替身：PMID → exact_hit artifact，DOI → zero_hits。"""
    from langchain_core.tools import tool

    @tool(response_format="content_and_artifact")
    def search_evidence(query: str) -> tuple:
        """精确证据检索（离线替身）。"""
        SE["args"].append(query)
        if query == GOOD_PMID:
            SE["exact"] += 1
            data = {"papers": [{"pmid": GOOD_PMID, "doi": None, "title": "CAR-T",
                                "content_level": "abstract",
                                "supporting_excerpt": "摘要", "year": "2026",
                                "journal": "Ann Med"}],
                    "retrieval_status": "exact_hit", "query": query}
            return "命中", {"schema_version": "toolresult-v1", "ok": True, "data": data}
        SE["zero"] += 1
        return "零命中", {"schema_version": "toolresult-v1", "ok": True,
                        "data": {"retrieval_status": "zero_hits", "candidate_count": 0,
                                 "query": query}}

    return search_evidence


class A1Chat:
    def __init__(self, script):
        self.calls, self._i, self.script = 0, 0, list(script)
        self.max_retries, self.timeout, self.max_tokens = 0, 120.0, 3000
        self.extra_body = dict(PT.THINKING_DISABLED)

    def invoke(self, *a, **k):
        from langchain_core.messages import AIMessage
        self.calls += 1
        item = self.script[min(self._i, len(self.script) - 1)]
        self._i += 1
        return AIMessage(content=item.get("content", ""),
                         tool_calls=item.get("tool_calls", []),
                         usage_metadata={"input_tokens": 10, "output_tokens": 5,
                                         "total_tokens": 15})

    def bind_tools(self, *a, **k):
        return self


def run_a1_executor(tmp_path, script, tool_obj, task="A"):
    """走真实 create_agent，模型侧过 GatedModel+hooks，工具侧过 proxy+reconciler。"""
    from langchain.agents import create_agent
    SE.update({"exact": 0, "zero": 0, "args": []})
    g = mkgate(tmp_path, task)
    trace = ExecutorTrace(tmp_path / f"trace{task}.jsonl", f"A1-{task}")
    trace.record_selected([tool_obj.name])
    guard = ExecutorLoopGuard()
    rec = LifecycleReconciler(trace=trace, guard=guard)
    from pilot.tool_proxy import wrap_tools
    proxied = wrap_tools([tool_obj], trace=trace, guard=guard, reconciler=rec,
                         allowed=[tool_obj.name])
    inner = A1Chat(script)
    hooks = EW.ExecutorHooks(trace, guard, reconciler=rec)
    gm = GatedModel(inner, g, role="executor", model_id="fake-model",
                    max_tokens=3000, hooks=hooks)
    g.start_task(task)
    agent = create_agent(gm, proxied)
    return agent, g, trace, guard, rec, inner


@pytest.mark.unit
def test_scenario_A_normal_exact_id(tmp_path):
    """场景 A：PMID exact_hit + DOI zero_hits，生命周期完整，Executor <8 轮结束。"""
    script = [
        {"tool_calls": [{"name": "search_evidence", "args": {"query": GOOD_PMID},
                         "id": "c1"}]},
        {"tool_calls": [{"name": "search_evidence", "args": {"query": BAD_DOI},
                         "id": "c2"}]},
        {"content": f"PMID {GOOD_PMID} 命中；DOI {BAD_DOI} 零命中", "tool_calls": []},
    ]
    agent, g, trace, guard, rec, inner = run_a1_executor(
        tmp_path, script, a1_tools(), "A")
    out = agent.invoke({"messages": [("user", "go")]})
    rec.reconcile_messages(out)

    assert SE["exact"] == 1 and SE["zero"] == 1        # 副作用证明真实执行
    life = rec.summary()["counts"]
    assert life["requested"] == 2 and life["executed"] == 2
    assert life["tool_returned"] == 2 and life["observed"] == 2 and life["failed"] == 0
    assert rec.inconsistencies == []
    assert guard.rounds < 8 and inner.calls <= 4
    # EvidenceCard 只从 exact_hit 的 artifact 构建；zero_hits 不构卡
    cards, skipped = cards_from_messages(out)
    assert len(cards) == 1 and GOOD_PMID in str(cards[0].provenance.source_ids)
    assert BAD_DOI not in json.dumps([c.model_dump() for c in cards], default=str)


@pytest.mark.unit
def test_scenario_B_tool_not_executed(tmp_path):
    """场景 B：模型请求工具，但工具在执行前被拒 → requested=1/executed=0，fail-closed。"""
    tool_obj = a1_tools()
    # allowed 里不含该工具 → proxy 在底层函数前 PermissionError
    from langchain.agents import create_agent
    from pilot.tool_proxy import wrap_tools
    SE.update({"exact": 0, "zero": 0, "args": []})
    g = mkgate(tmp_path, "B")
    trace = ExecutorTrace(tmp_path / "traceB.jsonl", "A1-B")
    trace.record_selected([tool_obj.name])
    guard = ExecutorLoopGuard()
    rec = LifecycleReconciler(trace=trace, guard=guard)
    proxied = wrap_tools([tool_obj], trace=trace, guard=guard, reconciler=rec,
                         allowed=["some_other_tool"])
    inner = A1Chat([{"tool_calls": [{"name": "search_evidence",
                                     "args": {"query": GOOD_PMID}, "id": "c1"}]}])
    hooks = EW.ExecutorHooks(trace, guard, reconciler=rec)
    gm = GatedModel(inner, g, role="executor", model_id="fake-model",
                    max_tokens=3000, hooks=hooks)
    g.start_task("B")
    agent = create_agent(gm, proxied)
    try:
        agent.invoke({"messages": [("user", "go")]})
    except Exception:
        pass
    assert SE["exact"] == 0 and SE["zero"] == 0        # 底层从未执行
    life = rec.summary()["counts"]
    assert life["requested"] == 1 and life["executed"] == 0 and life["observed"] == 0
    m = build_failure_manifest(run_id="A1-B", failure_stage="executor",
                               failure_reason="requested_not_executed", trace=trace,
                               budget_summary=g.summary(), reconciler=rec)
    assert m["status"] == "failed" and m["human_review"] is True
    assert m["lifecycle_counts"]["executed"] == 0


@pytest.mark.unit
def test_scenario_C_same_result_no_progress(tmp_path):
    """场景 C：不同请求参数、相同结果 → 只有首次算进展，连续 3 轮后 no_progress。"""
    from langchain_core.tools import tool

    @tool(response_format="content_and_artifact")
    def const_tool(query: str) -> tuple:
        """总是返回相同结果。"""
        SE["exact"] += 1
        return "same-content", {"schema_version": "toolresult-v1",
                                "data": {"const": True}}

    guard = ExecutorLoopGuard(no_progress_rounds=3)
    tr = ExecutorTrace(tmp_path / "tC.jsonl", "A1-C")
    rec = LifecycleReconciler(trace=tr, guard=guard)
    from langchain_core.messages import ToolMessage
    # 手动模拟：每轮不同 tool_call_id/args，但结果 hash 相同
    for i in range(5):
        cid = f"c{i}"
        rec.mark_requested(cid, "const_tool")
        rec.mark_executed(cid, "const_tool")
        rec.mark_returned(cid, "const_tool", result_hash="SAME_HASH")
        tm = ToolMessage(content="same-content", tool_call_id=cid, name="const_tool")
        try:
            rec.reconcile_messages([tm])
        except Exception:
            break
    # 第一次 SAME_HASH 算进展，之后不算 → 连续 3 轮无进展触发
    assert guard.rounds_without_progress >= 3 or any(
        e["event"] == "loop_guard_triggered" and e["reason"] == "no_progress"
        for e in guard.events)


@pytest.mark.unit
def test_scenario_D_new_result_makes_progress(tmp_path):
    """场景 D：每轮新 result_hash → no_progress 计数重置，不误触发。"""
    from langchain_core.messages import ToolMessage
    guard = ExecutorLoopGuard(no_progress_rounds=3)
    tr = ExecutorTrace(tmp_path / "tD.jsonl", "A1-D")
    rec = LifecycleReconciler(trace=tr, guard=guard)
    for i in range(6):
        cid = f"c{i}"
        rec.mark_requested(cid, "search_evidence")
        rec.mark_executed(cid, "search_evidence")
        rec.mark_returned(cid, "search_evidence", result_hash=f"HASH_{i}")
        tm = ToolMessage(content=f"r{i}", tool_call_id=cid, name="search_evidence")
        rec.reconcile_messages([tm])
    assert not any(e["event"] == "loop_guard_triggered" and e["reason"] == "no_progress"
                   for e in guard.events)
    assert guard.rounds_without_progress == 0


# ============ §5：并发隔离 + restore ============
@pytest.mark.unit
def test_two_runs_do_not_share_state(tmp_path):
    g1 = LifecycleReconciler(guard=ExecutorLoopGuard())
    g2 = LifecycleReconciler(guard=ExecutorLoopGuard())
    g1.mark_requested("c1", "t")
    g1.mark_executed("c1", "t")
    assert g2.summary()["counts"]["requested"] == 0    # 不串线
    assert g1.guard is not g2.guard                     # guard 不共享
    g1.guard.record_progress(["x"])
    assert len(g2.guard.progress_signals) == 0          # 结果 hash 不共享


@pytest.mark.unit
def test_install_restore_returns_original_tools(tmp_path):
    import ssc_skill_agent as SK
    before = [t.name for t in SK.SKILL_AGENT_TOOLS]
    before_types = [type(t).__name__ for t in SK.SKILL_AGENT_TOOLS]
    trace, guard, hooks, wrapped, rec, handle = EW.install(
        run_id="r1", trace_path=tmp_path / "t.jsonl", selected_tools=["search_evidence"])
    from pilot.tool_proxy import ToolLifecycleProxy
    assert all(isinstance(t, ToolLifecycleProxy) for t in SK.SKILL_AGENT_TOOLS)
    handle.restore()
    assert [t.name for t in SK.SKILL_AGENT_TOOLS] == before
    assert [type(t).__name__ for t in SK.SKILL_AGENT_TOOLS] == before_types
    handle.restore()                                    # 幂等


@pytest.mark.unit
def test_restore_runs_even_after_exception(tmp_path):
    import ssc_skill_agent as SK
    before_types = [type(t).__name__ for t in SK.SKILL_AGENT_TOOLS]
    trace, guard, hooks, wrapped, rec, handle = EW.install(
        run_id="r2", trace_path=tmp_path / "t.jsonl", selected_tools=["search_evidence"])
    try:
        with handle:
            raise RuntimeError("agent boom")
    except RuntimeError:
        pass
    assert [type(t).__name__ for t in SK.SKILL_AGENT_TOOLS] == before_types
