"""A.6.5 提交 1+3：事件轨迹 / 四类来源 / 逃逸口 / 循环护栏 / 失败 Manifest。
全部 fake，**零真实 API**。"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pilot import hard_gate as HG
from pilot import paid_transport as PT
from pilot.executor_trace import ExecutorTrace, build_failure_manifest
from pilot.hard_gate import BudgetExceeded, GateConfigError, GatedModel, HardBudgetGate
from pilot.loop_guard import (ExecutorLoopGuard, LoopGuardTriggered, MAX_TOOL_ROUNDS,
                              call_signature)

WRAPPED = "_reumani_hard_gate_wrapped"


@pytest.fixture(autouse=True)
def _sw(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("REUMANI_PILOT_PAID", "1")
    monkeypatch.setenv("REUMANI_PILOT_CONFIRM", "test")


def mkgate(tmp_path, **kw):
    d = dict(stage="test", ledger_path=tmp_path / "g.jsonl",
             max_usd_global=10, max_usd_stage=10, max_usd_task=10,
             max_calls_global=999, max_calls_task=999,
             max_calls_per_model={"fake-model": 999},
             max_calls_per_role={"executor": 999},
             task_timeout_s=600, max_retries=0, default_max_tokens=3000)
    d.update(kw)
    return HardBudgetGate(**d)


class Fake:
    def __init__(self):
        self.calls = 0
        self.max_retries, self.timeout, self.max_tokens = 0, 120.0, 3000
        self.extra_body = dict(PT.THINKING_DISABLED)

    def invoke(self, *a, **k):
        self.calls += 1
        return SimpleNamespace(content="ok", tool_calls=[],
                               usage_metadata={"input_tokens": 1, "output_tokens": 1},
                               response_metadata={})

    def bind_tools(self, *a, **k):
        return self

    def bind(self, *a, **k):
        return self

    def with_config(self, *a, **k):
        return SimpleNamespace(invoke=self.invoke)         # 裸对象

    def with_types(self, *a, **k):
        return SimpleNamespace(invoke=self.invoke)

    def with_retry(self, *a, **k):
        return SimpleNamespace(invoke=self.invoke)

    def with_fallbacks(self, *a, **k):
        return SimpleNamespace(invoke=self.invoke)


def gm(tmp_path, gate=None):
    g = gate or mkgate(tmp_path)
    inner = Fake()
    return GatedModel(inner, g, role="executor", model_id="fake-model",
                      max_tokens=3000), inner, g


# ---------- §4：Runnable 派生逃逸口 ----------
@pytest.mark.unit
@pytest.mark.parametrize("meth", ["bind", "bind_tools", "with_config", "with_types"])
def test_derivations_stay_wrapped(tmp_path, meth):
    m, inner, g = gm(tmp_path)
    derived = getattr(m, meth)([]) if meth in ("bind_tools",) else getattr(m, meth)()
    assert getattr(derived, WRAPPED, False), f"{meth}() 返回了未包装对象"
    assert object.__getattribute__(derived, "_role") == "executor"
    assert object.__getattribute__(derived, "_gate") is g
    g.start_task("T")
    derived.invoke("x")
    assert g.calls_global == 1 and inner.calls == 1


@pytest.mark.unit
@pytest.mark.parametrize("meth,pat", [("with_retry", "禁止任何自动重试"),
                                      ("with_fallbacks", "未经审计")])
def test_forbidden_derivations_are_refused(tmp_path, meth, pat):
    m, inner, g = gm(tmp_path)
    with pytest.raises(GateConfigError, match=pat):
        getattr(m, meth)()
    assert inner.calls == 0


@pytest.mark.unit
def test_unknown_with_star_derivation_is_fail_closed(tmp_path):
    m, inner, g = gm(tmp_path)
    object.__getattribute__(m, "_inner").with_something_new = lambda *a, **k: object()
    with pytest.raises(GateConfigError, match="未审计的 Runnable 派生方法"):
        m.with_something_new()
    assert inner.calls == 0


@pytest.mark.unit
def test_no_derivation_returns_a_bare_paid_client(tmp_path):
    """遍历当前可用的派生方法，确认没有一个能拿到可直接联网的裸客户端。"""
    m, inner, g = gm(tmp_path)
    leaked = []
    for name in ("bind", "bind_tools", "with_config", "with_types"):
        try:
            d = getattr(m, name)([]) if name == "bind_tools" else getattr(m, name)()
        except GateConfigError:
            continue
        if not getattr(d, WRAPPED, False):
            leaked.append(name)
    assert leaked == [], f"这些派生方法泄露了裸客户端：{leaked}"


# ---------- §7：循环护栏 ----------
@pytest.mark.unit
def test_max_tool_rounds_blocks_before_next_call():
    lg = ExecutorLoopGuard(max_tool_rounds=8)
    for i in range(8):
        lg.before_tool_round("t", {"i": i})
    with pytest.raises(LoopGuardTriggered) as ei:
        lg.before_tool_round("t", {"i": 99})
    assert ei.value.reason == "max_tool_rounds"
    assert any(e["event"] == "loop_guard_triggered" for e in lg.events)


@pytest.mark.unit
def test_identical_call_three_times_is_blocked():
    lg = ExecutorLoopGuard()
    lg.before_tool_round("search", {"q": "x"})
    lg.before_tool_round("search", {"q": "x"})              # warning
    assert any(e["event"] == "repeat_warning" for e in lg.events)
    with pytest.raises(LoopGuardTriggered) as ei:
        lg.before_tool_round("search", {"q": "x"})
    assert ei.value.reason == "repeated_call"


@pytest.mark.unit
def test_same_tool_different_args_is_not_flagged():
    """不能仅凭工具名判重复。"""
    lg = ExecutorLoopGuard()
    for i in range(5):
        lg.before_tool_round("search", {"q": f"query-{i}"})
    assert not any(e["event"] == "loop_guard_triggered" for e in lg.events)
    assert call_signature("search", {"q": "a"}) != call_signature("search", {"q": "b"})


@pytest.mark.unit
def test_ab_ab_cycle_is_detected_and_blocked():
    lg = ExecutorLoopGuard()
    seq = [("A", {"i": 1}), ("B", {"i": 2})] * 3
    with pytest.raises(LoopGuardTriggered) as ei:
        for name, args in seq:
            lg.before_tool_round(name, args)
    assert ei.value.reason == "cycle"


@pytest.mark.unit
def test_no_progress_triggers_after_streak():
    lg = ExecutorLoopGuard(no_progress_rounds=3)
    lg.record_progress(["evid:1"])                 # 有进展
    lg.record_progress([])                          # 1
    lg.record_progress([])                          # 2
    with pytest.raises(LoopGuardTriggered) as ei:
        lg.record_progress([])                      # 3 → 停
    assert ei.value.reason == "no_progress"


@pytest.mark.unit
def test_new_evidence_id_resets_no_progress_counter():
    lg = ExecutorLoopGuard(no_progress_rounds=3)
    lg.record_progress([]); lg.record_progress([])
    assert lg.rounds_without_progress == 2
    assert lg.record_progress(["evid:new"]) is True
    assert lg.rounds_without_progress == 0
    lg.record_progress([]); lg.record_progress([])   # 重新计数，不应触发
    assert lg.rounds_without_progress == 2


# ---------- §2/§3：事件轨迹与四类来源 ----------
def _resp(content="", tool_calls=None, finish="stop"):
    return SimpleNamespace(content=content, tool_calls=tool_calls or [],
                           invalid_tool_calls=[],
                           response_metadata={"finish_reason": finish,
                                              "model_name": "fake-model"})


@pytest.mark.unit
def test_trace_records_model_response_without_prompt_or_body(tmp_path):
    tr = ExecutorTrace(tmp_path / "t.jsonl", "run1")
    tr.record_model_response(outer_iteration=1, executor_call_index=1,
                             provider="deepseek", model="deepseek-v4-flash",
                             role="executor",
                             response=_resp("敏感的完整正文 sk-abcdefghijk", []),
                             input_tokens=100, output_tokens=20,
                             next_graph_node="END", termination_reason="no_tool_calls")
    rec = json.loads((tmp_path / "t.jsonl").read_text(encoding="utf-8").strip())
    assert rec["content_present"] is True and rec["content_length"] > 0
    assert len(rec["content_hash"]) == 64 and rec["hash_algorithm"] == "sha256"
    assert "敏感的完整正文" not in json.dumps(rec, ensure_ascii=False)   # 不存正文
    assert "sk-" not in json.dumps(rec, ensure_ascii=False)              # 不存密钥
    assert rec["finish_reason"] == "stop" and rec["termination_reason"] == "no_tool_calls"
    assert set(rec["response_metadata"]) <= {"finish_reason", "model_name"}


@pytest.mark.unit
def test_single_tool_call_produces_all_four_source_records(tmp_path):
    tr = ExecutorTrace(tmp_path / "t.jsonl", "run1")
    tr.record_selected(["search_evidence", "query_data_lake"])
    tr.record_model_response(outer_iteration=1, executor_call_index=1,
                             provider="deepseek", model="m", role="executor",
                             response=_resp("", [{"name": "search_evidence",
                                                  "args": {"query": "x"}, "id": "c1"}]))
    tr.record_tool_start(tool_call_id="c1", tool_name="search_evidence",
                         arguments={"query": "x"})
    tr.record_tool_end(tool_call_id="c1", tool_name="search_evidence", status="ok",
                       result="result text", structured=True,
                       returned_tool_message=True)
    c = tr.consistency()
    assert c["selected"] == ["query_data_lake", "search_evidence"]
    assert c["requested"] == ["search_evidence"]
    assert c["executed"] == ["search_evidence"]
    assert c["observed"] == ["search_evidence"]
    assert c["requested_not_executed"] == [] and c["executed_without_tool_message"] == []


@pytest.mark.unit
def test_selected_is_never_used_to_infer_executed(tmp_path):
    tr = ExecutorTrace(tmp_path / "t.jsonl", "run1")
    tr.record_selected(["a", "b", "c"])
    c = tr.consistency()
    assert c["selected"] == ["a", "b", "c"] and c["executed"] == []   # 选中 ≠ 执行


@pytest.mark.unit
def test_requested_but_tool_failed(tmp_path):
    tr = ExecutorTrace(tmp_path / "t.jsonl", "r")
    tr.record_selected(["t1"])
    tr.record_model_response(outer_iteration=1, executor_call_index=1, provider="p",
                             model="m", role="executor",
                             response=_resp("", [{"name": "t1", "args": {}, "id": "c1"}]))
    tr.record_tool_start(tool_call_id="c1", tool_name="t1", arguments={})
    tr.record_tool_end(tool_call_id="c1", tool_name="t1", status="error",
                       error_type="ToolError", returned_tool_message=False)
    c = tr.consistency()
    assert c["executed_without_tool_message"] == ["t1"]


@pytest.mark.unit
def test_observed_without_request_is_flagged(tmp_path):
    tr = ExecutorTrace(tmp_path / "t.jsonl", "r")
    tr.record_selected(["t1"])
    tr.record_tool_start(tool_call_id="c9", tool_name="t1", arguments={})
    tr.record_tool_end(tool_call_id="c9", tool_name="t1", status="ok",
                       result="x", returned_tool_message=True)
    assert tr.consistency()["observed_without_request"] == ["t1"]


@pytest.mark.unit
def test_unauthorized_execution_is_flagged(tmp_path):
    tr = ExecutorTrace(tmp_path / "t.jsonl", "r")
    tr.record_selected(["allowed_tool"])
    tr.record_model_response(outer_iteration=1, executor_call_index=1, provider="p",
                             model="m", role="executor",
                             response=_resp("", [{"name": "forbidden_tool",
                                                  "args": {}, "id": "c1"}]))
    tr.record_tool_start(tool_call_id="c1", tool_name="forbidden_tool", arguments={})
    c = tr.consistency()
    assert c["requested_outside_selected"] == ["forbidden_tool"]
    assert c["unauthorized_executed"] == ["forbidden_tool"]


@pytest.mark.unit
def test_trace_survives_exception_append_only(tmp_path):
    """即使后续抛异常，已发生的事件也必须留在盘上。"""
    p = tmp_path / "t.jsonl"
    tr = ExecutorTrace(p, "r")
    tr.record_selected(["t1"])
    tr.record_tool_start(tool_call_id="c1", tool_name="t1", arguments={"a": 1})
    try:
        raise RuntimeError("agent.invoke 炸了")
    except RuntimeError:
        pass
    lines = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert [x["event"] for x in lines] == ["selected", "tool_start"]
    assert [x["event_index"] for x in lines] == [1, 2]


# ---------- §8：失败诊断 Manifest ----------
@pytest.mark.unit
@pytest.mark.parametrize("stage,reason", [("executor", "loop_guard:max_tool_rounds"),
                                          ("executor", "role_cap:executor 17>16"),
                                          ("executor", "no_progress")])
def test_failure_manifest_is_generated_and_sanitized(tmp_path, stage, reason):
    tr = ExecutorTrace(tmp_path / "t.jsonl", "run-x")
    tr.record_selected(["search_evidence"])
    tr.record_tool_start(tool_call_id="c1", tool_name="search_evidence",
                         arguments={"query": "q"})
    m = build_failure_manifest(run_id="run-x", failure_stage=stage,
                               failure_reason=reason, trace=tr,
                               budget_summary={"calls": 16, "usd": 0.08},
                               guard_summary={"tool_rounds": 8})
    assert m["manifest_schema_version"] == "runmanifest-v1"
    assert m["shadow_status"] == "not_run_due_to_upstream_failure"
    blob = json.dumps(m, ensure_ascii=False)
    for leak in ("sk-", "Authorization", "Cookie", "ANTHROPIC_API_KEY"):
        assert leak not in blob
    assert "run-x" in blob and reason in blob


@pytest.mark.unit
def test_failure_manifest_is_not_a_research_manifest(tmp_path):
    tr = ExecutorTrace(tmp_path / "t.jsonl", "r")
    m = build_failure_manifest(run_id="r", failure_stage="executor",
                               failure_reason="loop_guard", trace=tr,
                               budget_summary={})
    assert m.get("claims") == [] and m.get("evidence_cards") == []
    assert m["shadow_status"] == "not_run_due_to_upstream_failure"
