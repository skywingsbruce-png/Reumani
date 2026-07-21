"""A.6.6 §3/§4：Tool proxy 的**真实执行**证明与契约等价。零真实 API。

不只断言 trace 里有 executed，还断言**底层真实工具的副作用**。
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pilot import exec_wiring as EW
from pilot import paid_transport as PT
from pilot.executor_trace import ExecutorTrace
from pilot.hard_gate import GatedModel, HardBudgetGate
from pilot.loop_guard import ExecutorLoopGuard, LoopGuardTriggered
from pilot.tool_proxy import ToolLifecycleProxy, assert_contract_equivalent, wrap_tools

SE = {"plain": 0, "struct": 0, "boom": 0, "aio": 0, "args": []}


@pytest.fixture(autouse=True)
def _sw(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("REUMANI_PILOT_PAID", "1")
    monkeypatch.setenv("REUMANI_PILOT_CONFIRM", "test")
    for k in SE:
        SE[k] = [] if k == "args" else 0


def tools():
    from langchain_core.tools import tool

    @tool
    def t_plain(query: str) -> str:
        """普通工具。"""
        SE["plain"] += 1
        SE["args"].append(query)
        return f"plain::{query}"

    @tool(response_format="content_and_artifact")
    def t_struct(query: str) -> tuple:
        """结构化工具。"""
        SE["struct"] += 1
        SE["args"].append(query)
        return f"struct::{query}", {"schema_version": "toolresult-v1", "ok": True,
                                    "data": {"q": query, "source_ids": [f"pmid:{query}"],
                                             "content_level": "abstract",
                                             "retrieval_status": "exact_hit"}}

    @tool
    def t_boom(query: str) -> str:
        """抛异常工具。"""
        SE["boom"] += 1
        raise RuntimeError("tool boom")

    @tool
    async def t_aio(query: str) -> str:
        """异步工具。"""
        SE["aio"] += 1
        SE["args"].append(query)
        return f"aio::{query}"

    return t_plain, t_struct, t_boom, t_aio


def mkgate(tmp_path):
    return HardBudgetGate(stage="test", ledger_path=tmp_path / "g.jsonl",
                          max_usd_global=10, max_usd_stage=10, max_usd_task=10,
                          max_calls_global=99, max_calls_task=99,
                          max_calls_per_model={"fake-model": 99},
                          max_calls_per_role={"executor": 99},
                          task_timeout_s=600, max_retries=0, default_max_tokens=3000)


class FakeChat:
    def __init__(self, tool_name, n_rounds=1):
        self.calls, self.tool_name, self.n = 0, tool_name, n_rounds
        self.max_retries, self.timeout, self.max_tokens = 0, 120.0, 3000
        self.extra_body = dict(PT.THINKING_DISABLED)

    def invoke(self, *a, **k):
        from langchain_core.messages import AIMessage
        self.calls += 1
        um = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        if self.calls <= self.n:
            return AIMessage(content="", tool_calls=[
                {"name": self.tool_name, "args": {"query": f"Q{self.calls}"},
                 "id": f"call_{self.calls}"}], usage_metadata=um)
        return AIMessage(content="done", tool_calls=[], usage_metadata=um)

    async def ainvoke(self, *a, **k):
        return self.invoke(*a, **k)

    def bind_tools(self, tools, **kw):
        return self


def real_agent(tmp_path, tool_obj, n_rounds=1, guard=None, allowed=None):
    from langchain.agents import create_agent
    g = mkgate(tmp_path)
    trace = ExecutorTrace(tmp_path / "t.jsonl", "proxy-test")
    trace.record_selected([tool_obj.name])
    guard = guard or ExecutorLoopGuard()
    proxied = wrap_tools([tool_obj], trace=trace, guard=guard,
                         allowed=allowed if allowed is not None else [tool_obj.name])
    inner = FakeChat(tool_obj.name, n_rounds)
    hooks = EW.ExecutorHooks(trace, guard)      # requested 来自模型侧
    gm = GatedModel(inner, g, role="executor", model_id="fake-model", max_tokens=3000,
                    hooks=hooks)
    g.start_task("T")
    return create_agent(gm, proxied), g, trace, guard, inner, proxied[0]


# ---------- §4-1/3：普通工具成功 ----------
@pytest.mark.unit
def test_plain_tool_real_execution_and_full_lifecycle(tmp_path):
    t_plain, _, _, _ = tools()
    agent, g, trace, guard, inner, proxy = real_agent(tmp_path, t_plain)
    out = agent.invoke({"messages": [("user", "go")]})
    tm = [m for m in out["messages"] if type(m).__name__ == "ToolMessage"]

    assert SE["plain"] == 1                      # 底层真的跑了，且只跑一次
    assert SE["args"] == ["Q1"]                  # 参数正确
    assert len(tm) == 1 and tm[0].tool_call_id == "call_1"
    assert "plain::Q1" in tm[0].content          # 返回值一致
    c = trace.consistency()
    assert c["requested"] == ["t_plain"]
    assert c["executed"] == ["t_plain"]          # ← 旧接线这里是空
    assert c["observed"] == ["t_plain"]
    assert c["requested_not_executed"] == []


# ---------- §4-2：结构化工具 artifact 不丢 ----------
@pytest.mark.unit
def test_structured_tool_artifact_preserved_through_proxy(tmp_path):
    _, t_struct, _, _ = tools()
    agent, g, trace, guard, inner, proxy = real_agent(tmp_path, t_struct)
    out = agent.invoke({"messages": [("user", "go")]})
    tm = [m for m in out["messages"] if type(m).__name__ == "ToolMessage"][0]
    assert SE["struct"] == 1
    art = getattr(tm, "artifact", None)
    assert art is not None and art["schema_version"] == "toolresult-v1"
    assert art["data"]["q"] == "Q1"
    assert "struct::Q1" in tm.content
    assert trace.consistency()["observed"] == ["t_struct"]


# ---------- §4-4：工具抛异常 ----------
@pytest.mark.unit
def test_tool_exception_recorded_as_failed_not_success(tmp_path):
    _, _, t_boom, _ = tools()
    agent, g, trace, guard, inner, proxy = real_agent(tmp_path, t_boom)
    try:
        agent.invoke({"messages": [("user", "go")]})
    except Exception:
        pass
    assert SE["boom"] == 1
    recs = [json.loads(l) for l in (tmp_path / "t.jsonl").read_text(
        encoding="utf-8").splitlines() if l.strip()]
    ends = [r for r in recs if r["event"] == "tool_end"]
    assert ends and ends[0]["status"] == "error"
    assert ends[0]["error_type"] == "RuntimeError"
    assert ends[0]["returned_tool_message"] is False
    assert trace.consistency()["observed"] == []      # 失败不算 observed


# ---------- §4-6：异步路径 ----------
@pytest.mark.unit
def test_async_tool_lifecycle(tmp_path):
    _, _, _, t_aio = tools()
    agent, g, trace, guard, inner, proxy = real_agent(tmp_path, t_aio)
    out = asyncio.run(agent.ainvoke({"messages": [("user", "go")]}))
    tm = [m for m in out["messages"] if type(m).__name__ == "ToolMessage"]
    assert SE["aio"] == 1 and len(tm) == 1
    c = trace.consistency()
    assert c["executed"] == ["t_aio"] and c["observed"] == ["t_aio"]


# ---------- §4-7：未授权工具在底层函数前阻断 ----------
@pytest.mark.unit
def test_unauthorized_tool_blocked_before_underlying_function(tmp_path):
    t_plain, _, _, _ = tools()
    agent, g, trace, guard, inner, proxy = real_agent(tmp_path, t_plain,
                                                      allowed=["some_other_tool"])
    try:
        agent.invoke({"messages": [("user", "go")]})
    except Exception:
        pass
    assert SE["plain"] == 0, "未授权工具的底层函数被执行了"


# ---------- §4-8：重复 Guard 在底层函数前阻断 ----------
@pytest.mark.unit
def test_repeat_guard_blocks_before_underlying_function(tmp_path):
    from langchain_core.tools import tool

    @tool
    def t_same(query: str) -> str:
        """总是同参数。"""
        SE["plain"] += 1
        return "same"

    class SameArgs(FakeChat):
        def invoke(self, *a, **k):
            from langchain_core.messages import AIMessage
            self.calls += 1
            return AIMessage(content="", tool_calls=[
                {"name": "t_same", "args": {"query": "SAME"}, "id": f"c{self.calls}"}],
                usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})

    from langchain.agents import create_agent
    g = mkgate(tmp_path)
    trace = ExecutorTrace(tmp_path / "t.jsonl", "r")
    guard = ExecutorLoopGuard()
    proxied = wrap_tools([t_same], trace=trace, guard=guard, allowed=["t_same"])
    gm = GatedModel(SameArgs("t_same"), g, role="executor", model_id="fake-model",
                    max_tokens=3000)
    g.start_task("T")
    agent = create_agent(gm, proxied)
    try:
        agent.invoke({"messages": [("user", "go")]}, {"recursion_limit": 50})
    except Exception:
        pass
    assert SE["plain"] == 2, f"重复保护未在底层函数前生效，实际执行 {SE['plain']} 次"
    assert any(e["event"] == "loop_guard_triggered" for e in guard.events)


# ---------- §3：契约等价 ----------
@pytest.mark.unit
def test_proxy_contract_equivalence():
    t_plain, t_struct, _, _ = tools()
    for orig in (t_plain, t_struct):
        p = ToolLifecycleProxy(orig)
        assert assert_contract_equivalent(p, orig) is True
        assert p.name == orig.name and p.description == orig.description
        assert p.args_schema is orig.args_schema
        assert p.response_format == orig.response_format
        assert p.args == orig.args


@pytest.mark.unit
def test_proxy_recording_failure_does_not_change_tool_semantics(tmp_path):
    """代理自身记录出错，不得把成功改写成失败。"""
    t_plain, _, _, _ = tools()

    class BrokenTrace:
        def record_tool_start(self, **kw):
            raise RuntimeError("trace 写入失败")

        def record_tool_end(self, **kw):
            raise RuntimeError("trace 写入失败")

    p = ToolLifecycleProxy(t_plain, trace=BrokenTrace(), guard=None,
                           allowed=["t_plain"])
    out = p.invoke({"query": "X"})
    assert SE["plain"] == 1 and out == "plain::X"
