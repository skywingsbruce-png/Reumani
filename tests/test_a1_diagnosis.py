"""A.6.4：A1 Executor 空转的**离线**根因诊断。零真实 API、零 HTTP。

只做观察与取证，**不修复**任何行为。
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pilot import paid_transport as PT
from pilot.hard_gate import BudgetExceeded, GatedModel, HardBudgetGate

WRAPPED = "_reumani_hard_gate_wrapped"
STAGE = "diag"


@pytest.fixture(autouse=True)
def _sw(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("REUMANI_PILOT_PAID", "1")
    monkeypatch.setenv("REUMANI_PILOT_CONFIRM", STAGE)


def mkgate(tmp_path, **kw):
    d = dict(stage=STAGE, ledger_path=tmp_path / "diag.jsonl",
             max_usd_global=10, max_usd_stage=10, max_usd_task=10,
             max_calls_global=999, max_calls_task=999,
             max_calls_per_model={"fake-model": 999},
             max_calls_per_role={"executor": 999},
             task_timeout_s=600, max_retries=0, default_max_tokens=3000)
    d.update(kw)
    return HardBudgetGate(**d)


class RecordingChat:
    """记录收到的 tools / kwargs；可脚本化返回 tool_calls 或纯文本。"""

    def __init__(self, script=None):
        self.calls, self.bound_tools, self.bound_kwargs, self._i = 0, None, None, 0
        self.script = list(script or [])
        self.max_retries, self.timeout, self.max_tokens = 0, 120.0, 3000
        self.extra_body = dict(PT.THINKING_DISABLED)

    def invoke(self, *a, **k):
        from langchain_core.messages import AIMessage
        self.calls += 1
        item = self.script[min(self._i, len(self.script) - 1)] if self.script else {}
        self._i += 1
        return AIMessage(content=item.get("content", ""),
                         tool_calls=item.get("tool_calls", []),
                         usage_metadata={"input_tokens": 10, "output_tokens": 5,
                                         "total_tokens": 15})

    async def ainvoke(self, *a, **k):
        return self.invoke(*a, **k)

    def bind_tools(self, tools, **kw):
        self.bound_tools, self.bound_kwargs = tools, kw
        return self                                  # 简化：仍返回自身以便计数

    def with_config(self, *a, **k):
        return SimpleNamespace(_raw_escape=True, invoke=self.invoke)

    def with_retry(self, *a, **k):
        return SimpleNamespace(_raw_escape=True, invoke=self.invoke)


# ---------- §3：GatedModel 与 bind_tools ----------
@pytest.mark.unit
def test_bind_tools_preserves_gate_role_and_ledger(tmp_path):
    g = mkgate(tmp_path)
    inner = RecordingChat()
    gm = GatedModel(inner, g, role="executor", model_id="fake-model", max_tokens=3000)
    bound = gm.bind_tools([{"name": "t1"}])
    assert getattr(bound, WRAPPED, False) is True            # 仍是 GatedModel
    assert object.__getattribute__(bound, "_role") == "executor"   # role 不变
    assert object.__getattribute__(bound, "_gate") is g            # 同一账本
    g.start_task("T")
    bound.invoke("x")
    assert g.calls_global == 1 and inner.calls == 1          # 绑定后仍过闸门
    ev = [e for e in g.ledger.events() if e["event"] == "reserved"]
    assert ev[-1]["role"] == "executor"


@pytest.mark.unit
def test_tool_schema_actually_reaches_the_model(tmp_path):
    g = mkgate(tmp_path)
    inner = RecordingChat()
    gm = GatedModel(inner, g, role="executor", model_id="fake-model", max_tokens=3000)
    gm.bind_tools([{"name": "search_evidence"}, {"name": "query_data_lake"}])
    assert inner.bound_tools is not None
    assert [t["name"] for t in inner.bound_tools] == ["search_evidence", "query_data_lake"]


@pytest.mark.unit
def test_runnable_config_methods_escape_the_gate(tmp_path):
    """**已知缺陷取证**：__getattr__ 把 with_config / with_retry 透传给底层，
    返回的是**未包装**对象 → 存在绕过闸门的路径。当前 LangGraph 路径未使用它们，
    但这是真实的逃逸口。本测试锁定现状，不做修复。"""
    g = mkgate(tmp_path)
    inner = RecordingChat()
    gm = GatedModel(inner, g, role="executor", model_id="fake-model", max_tokens=3000)
    escapes = []
    for meth in ("with_config", "with_retry"):
        obj = getattr(gm, meth)()
        if not getattr(obj, WRAPPED, False):
            escapes.append(meth)
    assert escapes == ["with_config", "with_retry"], \
        f"逃逸口集合发生变化，需要重新评估：{escapes}"


# ---------- §4：ReAct 终止逻辑 ----------
def _react(model, tools=None):
    """必须使用**与产品完全相同**的入口：ssc_skill_agent 用的是
    `from langchain.agents import create_agent as create_react_agent`（LangChain 1.x），
    不是已弃用的 langgraph.prebuilt.create_react_agent —— 两者行为不同。"""
    from langchain.agents import create_agent
    from langchain_core.tools import tool

    @tool
    def probe_tool(query: str) -> str:
        """离线探针工具。"""
        return "probe result"

    return create_agent(model, tools or [probe_tool])


@pytest.mark.unit
def test_react_ends_after_one_call_when_no_tool_calls(tmp_path):
    """无 tool_calls 时 ReAct 应当**一次模型响应就结束**。"""
    g = mkgate(tmp_path)
    inner = RecordingChat([{"content": "最终答案", "tool_calls": []}])
    gm = GatedModel(inner, g, role="executor", model_id="fake-model", max_tokens=3000)
    g.start_task("T")
    agent = _react(gm)
    agent.invoke({"messages": [("user", "hi")]})
    assert inner.calls == 1, f"无工具调用却循环了 {inner.calls} 次"


@pytest.mark.unit
def test_react_loops_once_per_tool_call_round(tmp_path):
    """每个 tool_call 轮次 = 1 次模型调用；工具执行后回到模型。"""
    g = mkgate(tmp_path)
    script = [{"content": "", "tool_calls": [{"name": "probe_tool",
                                              "args": {"query": "q"}, "id": f"c{i}"}]}
              for i in range(3)] + [{"content": "done", "tool_calls": []}]
    inner = RecordingChat(script)
    gm = GatedModel(inner, g, role="executor", model_id="fake-model", max_tokens=3000)
    g.start_task("T")
    agent = _react(gm)
    out = agent.invoke({"messages": [("user", "hi")]})
    tool_msgs = [m for m in out["messages"] if type(m).__name__ == "ToolMessage"]
    assert inner.calls == 4 and len(tool_msgs) == 3
    assert g.calls_by_role["executor"] == 4


@pytest.mark.unit
def test_empty_content_without_tool_calls_still_terminates(tmp_path):
    """空 content 且无 tool_calls：ReAct 仍应结束，而不是空转。"""
    g = mkgate(tmp_path)
    inner = RecordingChat([{"content": "", "tool_calls": []}])
    gm = GatedModel(inner, g, role="executor", model_id="fake-model", max_tokens=3000)
    g.start_task("T")
    agent = _react(gm)
    agent.invoke({"messages": [("user", "hi")]})
    assert inner.calls == 1, "空 content 触发了额外循环"


@pytest.mark.unit
def test_repeated_tool_calls_burn_one_model_call_each(tmp_path):
    """模型每轮都要求调工具 → 每轮烧 1 次模型调用；
    这正是 A1 观测到的形态（16 次调用、输出都很短、输入单调增长）。"""
    g = mkgate(tmp_path, max_calls_per_role={"executor": 16})
    script = [{"content": "", "tool_calls": [{"name": "probe_tool",
                                              "args": {"query": "q"}, "id": f"c{i}"}]}
              for i in range(40)]
    inner = RecordingChat(script)
    gm = GatedModel(inner, g, role="executor", model_id="fake-model", max_tokens=3000)
    g.start_task("T")
    agent = _react(gm)
    with pytest.raises(Exception) as ei:            # 闸门或递归上限先到
        agent.invoke({"messages": [("user", "hi")]},
                     {"recursion_limit": 100})
    assert inner.calls == 16, f"provider 实际被调用 {inner.calls} 次"
    assert "max_calls_per_role[executor]" in str(ei.value)


@pytest.mark.unit
def test_budget_gate_is_the_only_effective_loop_guard(tmp_path):
    """**安全发现**：把角色上限放到 999，模型持续要求调工具时，
    `langchain.agents.create_agent` 在默认配置下**没有**先于闸门触发的递归护栏 ——
    一直跑到闸门在第 1000 次调用前拒绝为止。
    也就是说：预算闸门是这条链路上**唯一**有效的循环护栏。
    （刻画现状，不修复。）"""
    g = mkgate(tmp_path, max_calls_per_role={"executor": 999}, max_calls_task=9999,
               max_calls_global=9999)
    script = [{"content": "", "tool_calls": [{"name": "probe_tool",
                                              "args": {"query": "q"}, "id": "c"}]}]
    inner = RecordingChat(script)          # 最后一项会被无限重复
    gm = GatedModel(inner, g, role="executor", model_id="fake-model", max_tokens=3000)
    g.start_task("T")
    agent = _react(gm)
    with pytest.raises(Exception) as ei:
        agent.invoke({"messages": [("user", "hi")]})
    assert "max_calls_per_role[executor]" in str(ei.value), \
        f"先触发的不是闸门，而是：{type(ei.value).__name__}: {str(ei.value)[:200]}"
    assert inner.calls == 999, f"闸门拒绝前 provider 被调用 {inner.calls} 次"


@pytest.mark.unit
def test_invalid_tool_calls_are_visible_not_swallowed(tmp_path):
    """模型把工具调用写成**文本**而不是结构化 tool_calls 时，
    ReAct 会认为"没有工具要调"→ 直接结束（不会静默重试）。"""
    g = mkgate(tmp_path)
    inner = RecordingChat([{"content": '我要调用 search_evidence {"query": "x"}',
                            "tool_calls": []}])
    gm = GatedModel(inner, g, role="executor", model_id="fake-model", max_tokens=3000)
    g.start_task("T")
    agent = _react(gm)
    out = agent.invoke({"messages": [("user", "hi")]})
    assert inner.calls == 1
    assert not [m for m in out["messages"] if type(m).__name__ == "ToolMessage"]


# ---------- §5：工具路由 ----------
@pytest.mark.unit
def test_id_extraction_asymmetry_pmid_yes_bare_doi_no():
    """**根因取证**：A1 的 PMID 能被提取，**裸 DOI 不能**。

    `ids.extract_dois()` 只匹配 `_DOI_URL`（doi.org/… 形式），
    文本里的 `DOI 10.1080/…` 提取不到；而 `ids.valid_doi()` 对同一个裸 DOI 返回 True
    —— 提取器与校验器口径不一致。这是精确 ID 路由失效的直接前置原因。
    （刻画现状，本轮不修复。）"""
    import ids
    from pilot.round2_tasks import TASKS
    q = TASKS["A1"]["question"]
    bare_doi = "10.1080/03009742.2024.2302553"
    assert ids.extract_pmids(q) == ["41657283"]          # PMID 可提取
    assert ids.extract_dois(q) == []                      # 裸 DOI 提取不到
    assert ids.valid_doi(bare_doi) is True                # 但校验器认为它合法
    assert ids.extract_dois(f"https://doi.org/{bare_doi}") == [bare_doi]  # URL 形式才行


@pytest.mark.unit
def test_a1_tool_selection_snapshot():
    """取证：确定性选择器对 A1 实际选出哪些工具（记录现状，不修改路由）。"""
    from pilot.round2_tasks import TASKS
    from tool_registry import select_tool_names
    sel = select_tool_names(TASKS["A1"]["question"])
    assert isinstance(sel, list) and sel
    # A1 运行时实际选中的四个工具（现场记录）
    assert "search_evidence" not in sel, \
        f"选择器行为已改变，需重新评估根因：{sel}"
    assert "search_literature" in sel


@pytest.mark.unit
def test_search_evidence_is_registered_and_structured():
    """search_evidence 确实存在且是结构化工具 —— 说明问题在路由，不在工具缺失。"""
    import ssc_skill_agent as SK
    names = [t.name for t in SK.SKILL_AGENT_TOOLS]
    assert "search_evidence" in names
    t = [x for x in SK.SKILL_AGENT_TOOLS if x.name == "search_evidence"][0]
    assert t.response_format == "content_and_artifact"


# ---------- 现场完整性 ----------
@pytest.mark.unit
def test_a1_scene_files_unmodified():
    """诊断不得改动 A1 现场。用固定下来的 SHA-256 复核**本机存在的**现场文件。

    注意：A1 的原始 run.json 与账本按设计**不入库**（.gitignore），
    所以在 CI 的干净检出里它们本就不存在 —— 那是正确状态，不是"现场丢失"。
    因此只校验本机实际存在的文件；一个都不存在时跳过。
    """
    import hashlib
    import json
    p = ROOT / "pilot" / "round2_results" / "A1_scene_hashes.json"
    if not p.exists():
        pytest.skip("现场 hash 清单不存在")
    scene = json.loads(p.read_text(encoding="utf-8"))["files"]
    checked = 0
    for rel, meta in scene.items():
        if not meta.get("exists"):
            continue
        f = ROOT / rel
        if not f.exists():
            continue                      # 非本机环境（如 CI 干净检出）：跳过该文件
        # 用 **LF 规范化** 后的 hash 比对：被跟踪的文本文件在 Windows 检出时会变成 CRLF，
        # 原始字节 hash 会假报"被改动"（与 CI #25 同一类问题）。
        raw = f.read_bytes()
        assert hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest() == meta["sha256_lf"], \
            f"现场文件被改动：{rel}"
        checked += 1
    if checked == 0:
        pytest.skip("本机没有 A1 现场文件（干净检出），无可校验对象")
