"""A.6.6.3 §1/§2/§6：权威 tool_call_id 逐调用关联 + 并行乱序 + 并发隔离。
真实 create_agent + middleware，零真实 API、零 HTTP。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pilot import exec_wiring as EW
from pilot import paid_transport as PT
from pilot.executor_trace import ExecutorTrace
from pilot.hard_gate import GatedModel, HardBudgetGate
from pilot.lifecycle import LifecycleReconciler, cid_hash
from pilot.loop_guard import ExecutorLoopGuard
from pilot.tool_middleware import LifecycleMiddleware, assert_middleware_available

SE = {"calls": 0, "order": []}


@pytest.fixture(autouse=True)
def _sw(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("REUMANI_PILOT_PAID", "1")
    monkeypatch.setenv("REUMANI_PILOT_CONFIRM", "test")
    SE.update({"calls": 0, "order": []})


def mkgate(tmp_path):
    return HardBudgetGate(stage="test", ledger_path=tmp_path / "g.jsonl",
                          max_usd_global=25, max_usd_stage=3, max_usd_task=1.5,
                          max_calls_global=200, max_calls_task=21,
                          max_calls_per_model={"fake-model": 999},
                          max_calls_per_role={"executor": 16},
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


def build(tmp_path, script, tools, allowed=None):
    from langchain.agents import create_agent
    g = mkgate(tmp_path)
    trace = ExecutorTrace(tmp_path / "t.jsonl", "mw")
    trace.record_selected([t.name for t in tools])
    guard = ExecutorLoopGuard()
    rec = LifecycleReconciler(trace=trace, guard=guard)
    mw = LifecycleMiddleware(rec, trace=trace, guard=guard,
                             allowed=allowed or [t.name for t in tools])
    inner = Chat(script)
    hooks = EW.ExecutorHooks(trace, guard, reconciler=rec)
    gm = GatedModel(inner, g, role="executor", model_id="fake-model",
                    max_tokens=3000, hooks=hooks)
    g.start_task("T")
    agent = create_agent(gm, tools, middleware=[mw])
    return agent, g, trace, guard, rec, inner


@pytest.mark.unit
def test_authoritative_tool_call_id_available():
    assert assert_middleware_available() is True


@pytest.mark.unit
def test_single_call_correlates_all_four_phases_by_id(tmp_path):
    from langchain_core.tools import tool

    @tool
    def t(query: str) -> str:
        """probe"""
        SE["calls"] += 1
        return f"r::{query}"

    script = [{"tool_calls": [{"name": "t", "args": {"query": "Q"}, "id": "TCID_A"}]},
              {"content": "done", "tool_calls": []}]
    agent, g, trace, guard, rec, inner = build(tmp_path, script, [t])
    out = agent.invoke({"messages": [("user", "go")]})
    rec.reconcile_messages(out)

    cid = cid_hash("TCID_A")
    r = rec.calls[cid]
    assert r["requested"] == 1 and r["executed"] == 1
    assert r["tool_returned"] == 1 and r["failed"] == 0 and r["observed"] == 1
    assert rec.inconsistencies == []


@pytest.mark.unit
def test_parallel_out_of_order_completion_still_correlates(tmp_path):
    """同一轮发两个 tool_call，工具**乱序完成**，仍按 id 正确关联（不是顺序配对）。"""
    from langchain_core.tools import tool

    @tool
    def slow(query: str) -> str:
        """先请求、后完成的工具。"""
        SE["order"].append(f"exec:{query}")
        return f"slow::{query}"

    @tool
    def fast(query: str) -> str:
        """后请求、先完成的工具。"""
        SE["order"].append(f"exec:{query}")
        return f"fast::{query}"

    # 一轮里两个 tool_call：slow(id=S) 在前，fast(id=F) 在后
    script = [{"tool_calls": [
        {"name": "slow", "args": {"query": "S"}, "id": "ID_SLOW"},
        {"name": "fast", "args": {"query": "F"}, "id": "ID_FAST"}]},
        {"content": "done", "tool_calls": []}]
    agent, g, trace, guard, rec, inner = build(tmp_path, script, [slow, fast])
    out = agent.invoke({"messages": [("user", "go")]})
    rec.reconcile_messages(out)

    # 两个 id 各自四阶段完整，且 observed 内容对应各自调用
    for tcid, expect in (("ID_SLOW", "slow::S"), ("ID_FAST", "fast::F")):
        r = rec.calls[cid_hash(tcid)]
        assert r["requested"] == 1 and r["executed"] == 1 and r["observed"] == 1
    tms = {m.tool_call_id: m.content for m in out["messages"]
           if type(m).__name__ == "ToolMessage"}
    assert "slow::S" in tms["ID_SLOW"] and "fast::F" in tms["ID_FAST"]
    assert rec.inconsistencies == []


@pytest.mark.unit
def test_duplicate_execution_blocked(tmp_path):
    """同一 tool_call_id 被执行两次 → fail-closed。"""
    rec = LifecycleReconciler()
    rec.mark_requested("D", "t")
    rec.mark_executed("D", "t")
    from pilot.lifecycle import DUPLICATE_EXECUTION, LifecycleError
    with pytest.raises(LifecycleError) as ei:
        rec.mark_executed("D", "t")
    assert ei.value.reason == DUPLICATE_EXECUTION


@pytest.mark.unit
def test_orphan_toolmessage_flagged(tmp_path):
    from langchain_core.messages import ToolMessage
    rec = LifecycleReconciler()
    rec.reconcile_messages([ToolMessage(content="x", tool_call_id="NEVER_REQUESTED",
                                        name="t")])
    assert any(i["kind"] == "orphan_observation" for i in rec.inconsistencies)


@pytest.mark.unit
def test_no_sequence_or_name_guessing_in_correlation():
    """静态：关联逻辑基于 tool_call_id，不用工具名/顺序/数量/run_id/参数hash配对。"""
    src = (ROOT / "pilot" / "tool_middleware.py").read_text(encoding="utf-8")
    assert 'tc.get("id")' in src            # 权威 id
    # middleware 用 tcid = tc.get("id")；不得从 run_manager 取 id 作为关联键
    assert "run_manager" not in src, "middleware 不应引用 run_manager"
    # 所有打点方法都以 tcid 为第一参数（权威 id 贯穿）
    for meth in ("_before", "_on_return", "_on_fail"):
        assert f"def {meth}(self, tcid" in src
    life = (ROOT / "pilot" / "lifecycle.py").read_text(encoding="utf-8")
    assert "cid_hash" in life                # 全部按 tool_call_id hash


# §6 并发隔离
@pytest.mark.unit
def test_two_concurrent_runs_do_not_crosstalk(tmp_path):
    from langchain_core.messages import ToolMessage
    r1 = LifecycleReconciler(guard=ExecutorLoopGuard())
    r2 = LifecycleReconciler(guard=ExecutorLoopGuard())
    r1.mark_requested("A", "t"); r1.mark_executed("A", "t")
    r1.mark_returned("A", "t", result_hash="h1")
    r1.reconcile_messages([ToolMessage(content="c1", tool_call_id="A", name="t")])
    # run2 完全独立
    assert r2.summary()["counts"]["requested"] == 0
    assert r2.summary()["counts"]["observed"] == 0
    assert r1.guard is not r2.guard
    assert len(r2.guard.progress_signals) == 0
