"""旧软闸门已于 A.6.3.1 退役。这里只断言它确实被退役且不再持有价格常量。

原有的 10 项行为测试连同实现一起留在 git 历史（dev `6ea604f` / public `680e6b8`），
对应的失败结论保留在 SHADOW_PILOT_ROUND2_REPORT.md 与 CALL_TRACE.md，均未改写。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pilot import budget_gate as BG


@pytest.mark.unit
def test_old_soft_gate_is_deprecated_and_refuses_construction():
    assert BG.DEPRECATED_IN == "A.6.3.1"
    assert BG.SUPERSEDED_BY == "pilot.hard_gate.HardBudgetGate"
    with pytest.raises(RuntimeError, match="已于 A.6.3.1 退役"):
        BG.BudgetGate(max_usd=1)


@pytest.mark.unit
def test_old_soft_gate_holds_no_price_constants():
    s = (ROOT / "pilot" / "budget_gate.py").read_text(encoding="utf-8")
    assert "PRICES_PER_MTOK" not in s
    for lit in ("0.27", "1.10", "5.00", "25.00"):
        assert lit not in s, f"退役模块仍残留价格数字 {lit}"
