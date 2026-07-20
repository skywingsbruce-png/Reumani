"""【已退役】A.6.2 之前的软闸门。**不要使用。**

历史结论保留在文档里（不删、不改写）：
- `SHADOW_PILOT_ROUND2_REPORT.md`：它只能软中止，第 13/14 次调用照样发生且已计费；
- `SHADOW_PILOT_ROUND2_CALL_TRACE.md`：它把 14 次调用全按 Opus 计价（静默回退缺陷）；
- `pilot/round2_results/stage1_metrics.json`：它产出的原始账本，原样保留。
完整实现仍可在 git 历史中查阅（commit `6ea604f` / public `680e6b8`）。

A.6.3.1 退役它的两个理由：
1. 它内嵌了**第三份**单价表（含未核实的 deepseek-chat 估价），违反"价格单一来源"；
2. 它是已知失效的软闸门，留着可被误用。

替代：`pilot.hard_gate.HardBudgetGate` + `pilot.prices`（唯一价格权威）。
"""

DEPRECATED_IN = "A.6.3.1"
SUPERSEDED_BY = "pilot.hard_gate.HardBudgetGate"
HISTORICAL_COMMITS = {"dev": "6ea604f", "public": "680e6b8"}


class BudgetExceeded(RuntimeError):
    """保留符号以免旧引用 ImportError；不再承担任何闸门职责。"""


class BudgetGate:
    def __init__(self, *args, **kwargs):
        raise RuntimeError(
            f"BudgetGate 已于 {DEPRECATED_IN} 退役（只能软中止，且内嵌重复/未核实价格）。"
            f"请改用 {SUPERSEDED_BY}；历史实现见 git {HISTORICAL_COMMITS}。")
