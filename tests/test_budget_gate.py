"""Round 2 费用闸门单测：全部用假 usage，零付费调用。
验证的是"到阈值必须抛出"，不是"能省钱"。"""
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pilot.budget_gate import BudgetExceeded, BudgetGate, price_for


def _gate(**kw):
    d = dict(max_usd=1.0, max_calls_global=10, max_calls_per_task=4, task_timeout_s=600)
    d.update(kw)
    return BudgetGate(**d)


@pytest.mark.unit
def test_cost_math_matches_official_opus_price():
    g = _gate()
    # 1M 输入 + 1M 输出 = $5 + $25 = $30 —— 会超 $1 上限，所以先放大上限验证算术
    g2 = _gate(max_usd=1000)
    cost = g2.record("claude-opus-4-8", 1_000_000, 1_000_000)
    assert round(cost, 6) == 30.0
    assert round(g2.usd, 6) == 30.0
    assert price_for("claude-opus-4-8")["input"] == 5.00
    assert g.usd == 0.0


@pytest.mark.unit
def test_unknown_model_does_not_price_at_zero():
    """未知模型不得按 0 计价（否则超支不可见）——回退到最贵档。"""
    g = _gate(max_usd=1000)
    g.record("some-unlisted-model", 1_000_000, 0)
    assert g.usd == 5.00


@pytest.mark.unit
def test_budget_hard_stop_raises():
    g = _gate(max_usd=0.10)
    with pytest.raises(BudgetExceeded, match="budget_usd"):
        for _ in range(20):
            g.record("claude-opus-4-8", 100_000, 0)      # 每次 $0.50
    assert g.stopped_reason.startswith("budget_usd")


@pytest.mark.unit
def test_global_call_cap_raises():
    g = _gate(max_usd=1e9, max_calls_global=3, max_calls_per_task=99)
    with pytest.raises(BudgetExceeded, match="max_calls_global"):
        for _ in range(5):
            g.record("deepseek-chat", 10, 10)
    assert g.calls_global == 4                            # 第 4 次越界并抛出


@pytest.mark.unit
def test_per_task_call_cap_raises_and_is_scoped():
    g = _gate(max_usd=1e9, max_calls_global=999, max_calls_per_task=2)
    g.start_task("A1")
    g.record("deepseek-chat", 10, 10)
    g.record("deepseek-chat", 10, 10)
    with pytest.raises(BudgetExceeded, match=r"max_calls_per_task\[A1\]"):
        g.record("deepseek-chat", 10, 10)
    g.end_task()
    g2 = _gate(max_usd=1e9, max_calls_global=999, max_calls_per_task=2)
    g2.start_task("A1"); g2.record("deepseek-chat", 1, 1); g2.end_task()
    g2.start_task("B1"); g2.record("deepseek-chat", 1, 1)   # 新任务计数归零，不应抛
    assert g2.calls_task == 1


@pytest.mark.unit
def test_task_timeout_raises():
    g = _gate(task_timeout_s=0.01)
    g.start_task("D1")
    time.sleep(0.05)
    with pytest.raises(BudgetExceeded, match="task_timeout"):
        g.check_timeout()
    assert g.per_task["D1"]["timed_out"] is True


@pytest.mark.unit
def test_no_auto_raise_of_budget():
    """闸门不得提供任何"自动提高预算"的路径。"""
    g = _gate(max_usd=0.01)
    with pytest.raises(BudgetExceeded):
        g.record("claude-opus-4-8", 1_000_000, 0)
    assert g.max_usd == 0.01                              # 上限未被自我修改
    with pytest.raises(BudgetExceeded):                   # 再次调用仍然抛，不会"放行"
        g.record("deepseek-chat", 1, 1)


@pytest.mark.unit
def test_on_llm_end_reads_langchain_shapes():
    g = _gate(max_usd=1e9)
    # 形态一：llm_output.token_usage（ChatOpenAI/DeepSeek 风格）
    r1 = SimpleNamespace(llm_output={"model_name": "deepseek-chat",
                                     "token_usage": {"prompt_tokens": 100, "completion_tokens": 50}},
                         generations=[[]])
    g.on_llm_end(r1)
    assert g.in_tok == 100 and g.out_tok == 50
    # 形态二：generations[0][0].message.usage_metadata（ChatAnthropic 风格）
    msg = SimpleNamespace(usage_metadata={"input_tokens": 7, "output_tokens": 3},
                          response_metadata={"model": "claude-opus-4-8"})
    r2 = SimpleNamespace(llm_output={}, generations=[[SimpleNamespace(message=msg)]])
    g.on_llm_end(r2)
    assert g.in_tok == 107 and g.out_tok == 53
    assert g.calls_global == 2


@pytest.mark.unit
def test_unknown_callbacks_are_noop_not_crash():
    g = _gate()
    g.on_chain_start({}, {})          # 任意未实现回调都不得炸掉运行
    g.on_tool_end("x")
    assert g.calls_global == 0


@pytest.mark.unit
def test_summary_shape_for_report():
    g = _gate(max_usd=1e9)
    g.start_task("C1"); g.record("claude-opus-4-8", 1000, 100); g.end_task()
    s = g.summary()
    assert s["calls"] == 1 and s["input_tokens"] == 1000
    assert s["per_task"]["C1"]["calls"] == 1 and s["per_task"]["C1"]["seconds"] >= 0
    assert s["limits"]["max_usd"] == 1e9
    assert "估计" in s["price_note"]
