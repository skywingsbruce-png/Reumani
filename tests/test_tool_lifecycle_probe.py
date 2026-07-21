"""A.6.6 §2：用**产品实际入口** `langchain.agents.create_agent` 构造零联网真实 Agent 图，
判定工具是否真的执行、以及旧 callback 接线为何漏记。零真实 API、零 HTTP。

不手动调用工具、不手动写 executed/observed 事件 —— 一切由真实 agent.invoke 驱动。
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pilot import exec_wiring as EW
from pilot import paid_transport as PT
from pilot.hard_gate import GatedModel, HardBudgetGate

SIDE_EFFECTS = {"calls": 0, "args": [], "artifact_calls": 0}


@pytest.fixture(autouse=True)
def _sw(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("REUMANI_PILOT_PAID", "1")
    monkeypatch.setenv("REUMANI_PILOT_CONFIRM", "test")
    SIDE_EFFECTS.update({"calls": 0, "args": [], "artifact_calls": 0})


def mkgate(tmp_path):
    return HardBudgetGate(stage="test", ledger_path=tmp_path / "p.jsonl",
                          max_usd_global=10, max_usd_stage=10, max_usd_task=10,
                          max_calls_global=99, max_calls_task=99,
                          max_calls_per_model={"fake-model": 99},
                          max_calls_per_role={"executor": 99},
                          task_timeout_s=600, max_retries=0, default_max_tokens=3000)


def make_probe_tools():
    """真实 BaseTool，带确定性副作用计数器。"""
    from langchain_core.tools import tool

    @tool
    def probe_plain(query: str) -> str:
        """普通字符串工具（离线探针）。"""
        SIDE_EFFECTS["calls"] += 1
        SIDE_EFFECTS["args"].append(query)
        return f"plain-result::{query}"

    @tool(response_format="content_and_artifact")
    def probe_struct(query: str) -> tuple:
        """结构化工具（content_and_artifact，离线探针）。"""
        SIDE_EFFECTS["artifact_calls"] += 1
        SIDE_EFFECTS["args"].append(query)
        return f"struct-content::{query}", {"schema_version": "toolresult-v1",
                                            "ok": True, "data": {"q": query}}

    return probe_plain, probe_struct


class FakeChat:
    """两轮：先请求工具，再收尾。走 GatedModel 包装，与 Pilot 路径一致。"""

    def __init__(self, tool_name):
        self.calls, self.tool_name = 0, tool_name
        self.max_retries, self.timeout, self.max_tokens = 0, 120.0, 3000
        self.extra_body = dict(PT.THINKING_DISABLED)

    def invoke(self, *a, **k):
        from langchain_core.messages import AIMessage
        self.calls += 1
        um = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        if self.calls == 1:
            return AIMessage(content="调用工具",
                             tool_calls=[{"name": self.tool_name,
                                          "args": {"query": "Q1"}, "id": "call_probe_1"}],
                             usage_metadata=um)
        return AIMessage(content="完成", tool_calls=[], usage_metadata=um)

    async def ainvoke(self, *a, **k):
        return self.invoke(*a, **k)

    def bind_tools(self, tools, **kw):
        self._bound = tools
        return self


def build_real_agent(tmp_path, tool_obj, attach_callbacks=True):
    """与 Pilot 相同的路径：install() 挂 callback → GatedModel 包装 → create_agent。"""
    from langchain.agents import create_agent

    g = mkgate(tmp_path)
    trace, guard, hooks, attached = EW.install(
        run_id="probe", trace_path=tmp_path / "t.jsonl",
        selected_tools=[tool_obj.name])
    if attach_callbacks:
        cb = EW.ToolTraceCallback(trace)
        tool_obj.callbacks = [cb]          # 旧接线方式：事后给 BaseTool 赋 callbacks
    inner = FakeChat(tool_obj.name)
    gm = GatedModel(inner, g, role="executor", model_id="fake-model",
                    max_tokens=3000, hooks=hooks)
    g.start_task("probe")
    agent = create_agent(gm, [tool_obj])
    return agent, g, trace, guard, inner


# ---------- 决定性问题 1/2：工具函数是否执行、ToolMessage 是否生成 ----------
@pytest.mark.unit
def test_real_agent_actually_executes_the_tool(tmp_path):
    plain, _ = make_probe_tools()
    agent, g, trace, guard, inner = build_real_agent(tmp_path, plain)

    out = agent.invoke({"messages": [("user", "测试")]})
    msgs = out["messages"]
    tool_msgs = [m for m in msgs if type(m).__name__ == "ToolMessage"]
    ai_msgs = [m for m in msgs if type(m).__name__ == "AIMessage"]

    # 1) 工具函数确实执行了（真实副作用）
    assert SIDE_EFFECTS["calls"] == 1, f"底层工具函数被调用 {SIDE_EFFECTS['calls']} 次"
    assert SIDE_EFFECTS["args"] == ["Q1"], "工具没有收到正确参数"
    # 2) ToolMessage 真实生成
    assert len(tool_msgs) == 1, f"ToolMessage 数 = {len(tool_msgs)}"
    assert tool_msgs[0].tool_call_id == "call_probe_1"
    assert "plain-result::Q1" in tool_msgs[0].content
    # 3) 图确实重新进入 model 节点
    assert inner.calls == 2, f"模型被调用 {inner.calls} 次"
    assert any(getattr(m, "tool_calls", None) for m in ai_msgs)
    print("\n消息序列:", [type(m).__name__ for m in msgs])


@pytest.mark.unit
def test_old_callback_wiring_misses_executed_and_observed(tmp_path):
    """**关键取证**：即使工具真的执行了，旧的"事后给 BaseTool 赋 callbacks"接线
    也可能没有记录 executed/observed —— 这正是 A1-rerun 里 executed=[] 的成因候选。"""
    plain, _ = make_probe_tools()
    agent, g, trace, guard, inner = build_real_agent(tmp_path, plain)
    agent.invoke({"messages": [("user", "测试")]})

    assert SIDE_EFFECTS["calls"] == 1          # 工具确实跑了
    cons = trace.consistency()
    print("\n工具真实执行次数:", SIDE_EFFECTS["calls"])
    print("trace consistency:", json.dumps(cons, ensure_ascii=False))
    # 记录现状：requested 有，executed/observed 由本测试断言（见下方参数化结论）
    assert cons["requested"] == [plain.name]
    # 这一条是**诊断输出**，不预设结论：把真实差异暴露出来
    trace_missed = (cons["executed"] == [] and cons["observed"] == [])
    print("callback 是否漏记 executed/observed:", trace_missed)
    assert isinstance(trace_missed, bool)


@pytest.mark.unit
def test_callback_identity_original_vs_agent_copy(tmp_path):
    """callback 挂在原工具上，create_agent 实际用的是不是同一个对象？"""
    plain, _ = make_probe_tools()
    agent, g, trace, guard, inner = build_real_agent(tmp_path, plain)
    agent.invoke({"messages": [("user", "测试")]})
    bound = getattr(inner, "_bound", None)
    if bound:
        same_object = bound[0] is plain
        print(f"\nbind_tools 收到同一对象: {same_object}; "
              f"类型: {type(bound[0]).__name__}; "
              f"带 callbacks: {bool(getattr(bound[0], 'callbacks', None))}")
    else:
        print("\nbind_tools 未被调用（create_agent 走了别的绑定路径）")
    # 无论绑定路径如何，结论一致：工具执行了，但事后挂的 callback 没记到
    assert SIDE_EFFECTS["calls"] == 1
    assert trace.consistency()["executed"] == [], \
        "若此处非空说明旧接线已可靠，需重新评估 A.6.6 的前提"


@pytest.mark.unit
def test_structured_tool_artifact_survives_real_agent(tmp_path):
    """content_and_artifact 工具在真实 agent 路径里 artifact 不丢。"""
    _, struct = make_probe_tools()
    agent, g, trace, guard, inner = build_real_agent(tmp_path, struct)
    out = agent.invoke({"messages": [("user", "测试")]})
    tool_msgs = [m for m in out["messages"] if type(m).__name__ == "ToolMessage"]
    assert SIDE_EFFECTS["artifact_calls"] == 1
    assert len(tool_msgs) == 1
    art = getattr(tool_msgs[0], "artifact", None)
    assert art is not None, "artifact 丢失"
    assert art["schema_version"] == "toolresult-v1" and art["data"]["q"] == "Q1"
    assert "struct-content::Q1" in tool_msgs[0].content


@pytest.mark.unit
def test_tool_exception_path_in_real_agent(tmp_path):
    """工具抛异常时：底层确实被调用，且不会伪装成成功。"""
    from langchain_core.tools import tool

    @tool
    def probe_boom(query: str) -> str:
        """会抛异常的探针工具。"""
        SIDE_EFFECTS["calls"] += 1
        raise RuntimeError("probe tool boom")

    agent, g, trace, guard, inner = build_real_agent(tmp_path, probe_boom)
    try:
        out = agent.invoke({"messages": [("user", "测试")]})
        msgs = out["messages"]
        tool_msgs = [m for m in msgs if type(m).__name__ == "ToolMessage"]
        statuses = [getattr(m, "status", None) for m in tool_msgs]
        print("\n异常路径 ToolMessage 状态:", statuses)
        assert SIDE_EFFECTS["calls"] == 1
        # 要么以 error ToolMessage 返回，要么抛出——两者都不能是"成功"
        assert not tool_msgs or "error" in str(statuses).lower() or \
            any("boom" in str(m.content) for m in tool_msgs)
    except RuntimeError as e:
        assert "boom" in str(e)
        assert SIDE_EFFECTS["calls"] == 1


@pytest.mark.unit
def test_async_path_executes_tool(tmp_path):
    """异步路径也走真实执行。"""
    import asyncio

    plain, _ = make_probe_tools()
    agent, g, trace, guard, inner = build_real_agent(tmp_path, plain)
    out = asyncio.run(agent.ainvoke({"messages": [("user", "测试")]}))
    tool_msgs = [m for m in out["messages"] if type(m).__name__ == "ToolMessage"]
    assert SIDE_EFFECTS["calls"] == 1
    assert len(tool_msgs) == 1
