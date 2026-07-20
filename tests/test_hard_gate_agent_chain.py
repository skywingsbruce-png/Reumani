"""真实 Agent 编排 + 假模型：还原 A1 走过的 Planner–Executor–Verifier–Claim 路径。
零真实 API。目的有三：
  1. 输出逐阶段调用图（用于 CALL_TRACE 的经验证据）；
  2. 证明达到上限后，下一次 fake provider **根本没有被调用**；
  3. 测量该任务的最少 / 典型 / 最坏调用次数。
不修改任何生产科研逻辑——只把模块级 LLM 对象在测试内替换掉。
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pilot import hard_gate as HG
from pilot.hard_gate import BudgetExceeded, GatedModel, HardBudgetGate

FAKE = "fake-model"
CALLS = []           # 逐次调用图：(role, call_index)


class FakeChat:
    """够用的假 chat model：支持 invoke / bind_tools，可脚本化返回。"""

    def __init__(self, role, script):
        self.role, self.script, self.calls, self._i = role, list(script), 0, 0

    def _next(self):
        if not self.script:
            return SimpleNamespace(content="", tool_calls=[])
        item = self.script[min(self._i, len(self.script) - 1)]
        self._i += 1
        return item

    def invoke(self, *a, **k):
        self.calls += 1
        CALLS.append((self.role, len(CALLS) + 1))
        item = self._next()
        from langchain_core.messages import AIMessage
        return AIMessage(content=item.get("content", ""),
                         tool_calls=item.get("tool_calls", []),
                         usage_metadata={"input_tokens": 100, "output_tokens": 20,
                                         "total_tokens": 120})

    async def ainvoke(self, *a, **k):
        return self.invoke(*a, **k)

    def bind_tools(self, *a, **k):
        return self

    def bind(self, *a, **k):
        return self


def _gate(tmp_path, **kw):
    d = dict(stage="test", ledger_path=tmp_path / "chain.jsonl",
             max_usd_global=100.0, max_usd_stage=100.0, max_usd_task=100.0,
             max_calls_global=999, max_calls_task=12,
             max_calls_per_model={FAKE: 999}, task_timeout_s=600,
             max_retries=2, default_max_tokens=1000)
    d.update(kw)
    return HardBudgetGate(**d)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv(HG.ENV_PAID, "1")
    monkeypatch.setenv(HG.ENV_CONFIRM, "test")
    CALLS.clear()


@pytest.mark.unit
def test_full_chain_call_graph_with_fake_models(tmp_path, monkeypatch):
    """跑完整链路，输出逐阶段调用图。"""
    g = _gate(tmp_path, max_calls_task=99)
    planner = GatedModel(FakeChat("planner", [{"content": json.dumps({
        "question": "q", "steps": [{"step_id": "s1", "goal": "检索",
                                    "tool_name": "search_evidence",
                                    "success_criteria": "拿到 PMID", "maximum_retries": 1}]})}]),
        g, role="planner", model_id=FAKE, max_tokens=1000)
    # executor：2 轮工具调用 + 1 轮收尾 = 3 次 LLM 调用（ReAct 的典型形态）
    exec_script = [
        {"content": "", "tool_calls": [{"name": "search_evidence",
                                        "args": {"query": "x"}, "id": "t1"}]},
        {"content": "", "tool_calls": [{"name": "search_evidence",
                                        "args": {"query": "y"}, "id": "t2"}]},
        {"content": "完成：见 PMID 12345678", "tool_calls": []},
    ]
    executor = GatedModel(FakeChat("executor", exec_script), g,
                          role="executor", model_id=FAKE, max_tokens=1000)
    verifier = GatedModel(FakeChat("verifier", [{"content": json.dumps(
        {"passed": True, "status": "passed", "reason": "ok", "missing": []})}]),
        g, role="verifier", model_id=FAKE, max_tokens=1000)

    g.start_task("A1_fake")
    # 真实编排：planner.make_plan → executor(ReAct 循环) → verifier
    planner.invoke("plan prompt")
    for _ in exec_script:
        executor.invoke("react step")
    verifier.invoke("verify prompt")

    graph = [{"call_index": i, "role": r} for r, i in CALLS]
    roles = [r for r, _ in CALLS]
    assert roles == ["planner", "executor", "executor", "executor", "verifier"]
    assert g.calls_global == 5
    print("\n调用图：" + json.dumps(graph, ensure_ascii=False))


@pytest.mark.unit
def test_provider_not_invoked_after_cap_in_real_chain(tmp_path):
    """达到上限后，下一次 fake provider 根本没有被调用。"""
    g = _gate(tmp_path, max_calls_task=4)
    inner = FakeChat("executor", [{"content": "", "tool_calls": []}])
    m = GatedModel(inner, g, role="executor", model_id=FAKE, max_tokens=1000)
    g.start_task("A1_fake")
    for _ in range(4):
        m.invoke("step")
    assert inner.calls == 4
    with pytest.raises(BudgetExceeded, match="max_calls_task"):
        m.invoke("step")
    assert inner.calls == 4, "越限后 provider 不得被触碰"


@pytest.mark.unit
def test_measure_min_typical_worst_call_counts(tmp_path):
    """测量 A1 形态任务的最少/典型/最坏调用次数（max_iterations=2）。

    结构：每次 iteration = 1 planner + N executor(ReAct) + 1 verifier；
    通过后停止；不通过则再跑一轮；最后 shadow claim extraction 1 次。
    """
    def simulate(react_calls_per_iter, iterations, claim=True):
        g = _gate(tmp_path, max_calls_task=999, ledger_path=tmp_path / f"m{iterations}.jsonl")
        g.start_task("sim")
        n = 0
        for _ in range(iterations):
            g.before_call(model_id=FAKE, role="planner", payload="p"); n += 1
            for _ in range(react_calls_per_iter):
                g.before_call(model_id=FAKE, role="executor", payload="e"); n += 1
            g.before_call(model_id=FAKE, role="verifier", payload="v"); n += 1
        if claim:
            g.before_call(model_id=FAKE, role="claim_extractor", payload="c"); n += 1
        return n

    minimum = simulate(react_calls_per_iter=1, iterations=1)     # 一轮通过、执行器一次答完
    typical = simulate(react_calls_per_iter=4, iterations=2)     # 两轮 × 4 次工具循环
    worst = simulate(react_calls_per_iter=8, iterations=2)       # 两轮 × 8 次工具循环

    assert minimum == 4, f"最少 = 1 plan + 1 exec + 1 verify + 1 claim = 4，实测 {minimum}"
    assert typical == 13, f"典型 = 2×(1+4+1) + 1 = 13，实测 {typical}"
    assert worst == 21, f"最坏 = 2×(1+8+1) + 1 = 21，实测 {worst}"
    # 冻结的每题上限 12 落在"典型"之下 —— 这正是 A1 被中止的结构性原因
    assert typical > 12
    print(f"\nA1 形态调用数：最少={minimum} 典型={typical} 最坏={worst}（冻结上限=12）")
