"""Commit A.6.2 硬闸门测试：**全部 fake provider，零真实 API**。
核心断言不是"省钱"，而是【越限时底层 provider 根本没被调用】。"""
import asyncio
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pilot import hard_gate as HG
from pilot.hard_gate import (BudgetExceeded, GateConfigError, GatedModel, HardBudgetGate,
                             assert_all_paid_entrypoints_wrapped, wrap_all)

FAKE = "fake-model"


@pytest.fixture(autouse=True)
def _switches(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv(HG.ENV_PAID, "1")
    monkeypatch.setenv(HG.ENV_CONFIRM, "test")


def mkgate(tmp_path, **kw):
    d = dict(stage="test", ledger_path=tmp_path / "ledger.jsonl",
             max_usd_global=100.0, max_usd_stage=100.0, max_usd_task=100.0,
             max_calls_global=999, max_calls_task=12,
             max_calls_per_model={FAKE: 999}, task_timeout_s=600,
             max_retries=2, default_max_tokens=1000)
    d.update(kw)
    return HardBudgetGate(**d)


class FakeProvider:
    """记录自己被调用了几次。断言"根本没被调用"就靠它。"""

    def __init__(self, in_tok=10, out_tok=5, fail=False, usage=True):
        self.calls = 0
        self.in_tok, self.out_tok, self.fail, self.usage = in_tok, out_tok, fail, usage

    def _resp(self):
        um = ({"input_tokens": self.in_tok, "output_tokens": self.out_tok}
              if self.usage else None)
        return SimpleNamespace(content="ok", usage_metadata=um, response_metadata={})

    def invoke(self, *a, **k):
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider boom")
        return self._resp()

    async def ainvoke(self, *a, **k):
        return self.invoke(*a, **k)

    def stream(self, *a, **k):
        return self.invoke(*a, **k)

    async def astream(self, *a, **k):
        return self.invoke(*a, **k)

    def batch(self, *a, **k):
        return self.invoke(*a, **k)


def gated(tmp_path, provider=None, gate=None, role="test", **kw):
    p = provider or FakeProvider()
    g = gate or mkgate(tmp_path, **kw)
    return GatedModel(p, g, role=role, model_id=FAKE, max_tokens=1000), p, g


# 1 / 2 —— 最核心的两条
@pytest.mark.unit
def test_12th_allowed_13th_rejected_before_invoke(tmp_path):
    m, p, g = gated(tmp_path, max_calls_task=12)
    g.start_task("A1")
    for _ in range(12):
        m.invoke("hi")
    assert p.calls == 12
    with pytest.raises(BudgetExceeded, match="max_calls_task"):
        m.invoke("hi")
    assert p.calls == 12, "第13次必须在 .invoke() 之前被拒，provider 不得被调用"


@pytest.mark.unit
def test_rejected_call_does_not_increment_provider(tmp_path):
    m, p, g = gated(tmp_path, max_calls_task=1)
    g.start_task("T")
    m.invoke("a")
    before = p.calls
    for _ in range(5):
        with pytest.raises(BudgetExceeded):
            m.invoke("a")
    assert p.calls == before


# 3 —— callback 吞异常也绕不过硬闸门
@pytest.mark.unit
def test_callback_swallowing_cannot_bypass_hard_gate(tmp_path):
    """模拟 LangChain 吞掉 callback 异常：硬闸门在 callback 之前就已拒绝。"""
    class SwallowingCallback:
        def on_llm_end(self, *a, **k):
            raise RuntimeError("callback raised but framework swallows it")

    m, p, g = gated(tmp_path, max_calls_task=1)
    g.start_task("T")
    m.invoke("a")
    try:
        SwallowingCallback().on_llm_end()
    except RuntimeError:
        pass                                   # 框架吞掉——与闸门无关
    with pytest.raises(BudgetExceeded):
        m.invoke("a")
    assert p.calls == 1


# 4-7 —— 四个角色都被计数
@pytest.mark.unit
@pytest.mark.parametrize("role", ["planner", "executor", "verifier", "claim_extractor"])
def test_each_role_is_counted(tmp_path, role):
    m, p, g = gated(tmp_path, role=role)
    g.start_task("T")
    m.invoke("x")
    assert g.calls_global == 1 and p.calls == 1
    assert any(e.get("role") == role for e in g.ledger.events() if e["event"] == "reserved")


# 8 —— provider 自动重试也受限
@pytest.mark.unit
def test_provider_retry_is_capped(tmp_path):
    g = mkgate(tmp_path, max_retries=2)
    g.start_task("T")
    for _ in range(2):
        g.before_call(model_id=FAKE, role="r", payload="x", is_retry=True)
    with pytest.raises(BudgetExceeded, match="max_retries"):
        g.before_call(model_id=FAKE, role="r", payload="x", is_retry=True)


# 9/10/11 —— invoke / ainvoke / stream 都受限
@pytest.mark.unit
def test_invoke_is_gated(tmp_path):
    m, p, g = gated(tmp_path, max_calls_task=1)
    g.start_task("T"); m.invoke("a")
    with pytest.raises(BudgetExceeded):
        m.invoke("a")
    assert p.calls == 1


@pytest.mark.unit
def test_ainvoke_is_gated(tmp_path):
    m, p, g = gated(tmp_path, max_calls_task=1)
    g.start_task("T")
    asyncio.run(m.ainvoke("a"))
    with pytest.raises(BudgetExceeded):
        asyncio.run(m.ainvoke("a"))
    assert p.calls == 1


@pytest.mark.unit
def test_stream_is_gated(tmp_path):
    m, p, g = gated(tmp_path, max_calls_task=1)
    g.start_task("T"); m.stream("a")
    with pytest.raises(BudgetExceeded):
        m.stream("a")
    assert p.calls == 1, "stream 必须在建立连接前就被拒"


# 12-15 —— 四类上限彼此独立
@pytest.mark.unit
def test_task_cap_is_independent(tmp_path):
    m, p, g = gated(tmp_path, max_calls_task=2, max_calls_global=999)
    g.start_task("A"); m.invoke("x"); m.invoke("x")
    with pytest.raises(BudgetExceeded, match="max_calls_task"):
        m.invoke("x")
    g.end_task(); g.start_task("B")
    m.invoke("x")                                 # 新任务独立计数
    assert p.calls == 3


@pytest.mark.unit
def test_per_model_cap_is_independent(tmp_path):
    g = mkgate(tmp_path, max_calls_per_model={FAKE: 2}, max_calls_task=99)
    m, p, _ = gated(tmp_path, gate=g)
    g.start_task("T"); m.invoke("x"); m.invoke("x")
    with pytest.raises(BudgetExceeded, match="max_calls_per_model"):
        m.invoke("x")
    assert p.calls == 2


PRICED = "priced-test"      # fake-model 单价为 0，测预算必须用有价模型


@pytest.fixture
def priced():
    HG.PRICES[PRICED] = {"input": 10.0, "output": 10.0, "source": "test"}
    yield PRICED
    HG.PRICES.pop(PRICED, None)


@pytest.mark.unit
def test_stage_budget_is_independent(tmp_path, priced):
    g = mkgate(tmp_path, max_usd_stage=0.0000001, max_usd_task=1e6, max_usd_global=1e6,
               max_calls_per_model={priced: 99})
    p = FakeProvider()
    m = GatedModel(p, g, role="r", model_id=priced, max_tokens=1000)
    g.start_task("T")
    with pytest.raises(BudgetExceeded, match="max_usd_stage"):
        m.invoke("x" * 100000)
    assert p.calls == 0


@pytest.mark.unit
def test_global_budget_is_independent(tmp_path, priced):
    g = mkgate(tmp_path, max_usd_global=0.0000001, max_usd_task=1e6, max_usd_stage=1e6,
               max_calls_per_model={priced: 99})
    p = FakeProvider()
    m = GatedModel(p, g, role="r", model_id=priced, max_tokens=1000)
    g.start_task("T")
    with pytest.raises(BudgetExceeded, match="max_usd_global"):
        m.invoke("x" * 100000)
    assert p.calls == 0


# 16 —— 调用前最坏费用越界 → provider 未触发
@pytest.mark.unit
def test_worst_case_cost_rejects_before_provider(tmp_path):
    g = mkgate(tmp_path, max_usd_task=0.000001)
    HG.PRICES["expensive-test"] = {"input": 1000.0, "output": 1000.0, "source": "test"}
    g.lim["max_calls_per_model"]["expensive-test"] = 99
    m = GatedModel(FakeProvider(), g, role="r", model_id="expensive-test", max_tokens=10000)
    g.start_task("T")
    with pytest.raises(BudgetExceeded, match="max_usd_task"):
        m.invoke("x" * 5000)
    assert object.__getattribute__(m, "_inner").calls == 0
    HG.PRICES.pop("expensive-test")


# 17-20 —— reservation / 结算 / 异常 / usage 缺失
@pytest.mark.unit
def test_reservation_written_atomically_before_call(tmp_path):
    g = mkgate(tmp_path)
    g.start_task("T")
    uid, worst = g.before_call(model_id=FAKE, role="r", payload="x")
    ev = [e for e in g.ledger.events() if e["event"] == "reserved"]
    assert len(ev) == 1 and ev[0]["call_uid"] == uid and ev[0]["worst_case_usd"] == round(worst, 6)
    assert g.reserved_usd == worst


@pytest.mark.unit
def test_successful_call_is_reconciled_and_releases_unused(tmp_path):
    HG.PRICES["settle-test"] = {"input": 10.0, "output": 10.0, "source": "test"}
    g = mkgate(tmp_path, max_calls_per_model={"settle-test": 9})
    m = GatedModel(FakeProvider(in_tok=10, out_tok=5), g, role="r",
                   model_id="settle-test", max_tokens=1000)
    g.start_task("T")
    m.invoke("x")
    ev = {e["event"] for e in g.ledger.events()}
    assert "reconciled" in ev
    assert g.reserved_usd == pytest.approx(0.0, abs=1e-9)      # 未用额度已释放
    assert 0 < g.actual_usd < 0.02
    HG.PRICES.pop("settle-test")


@pytest.mark.unit
def test_failed_call_keeps_reservation_and_marks_maybe_billed(tmp_path):
    g = mkgate(tmp_path)
    m = GatedModel(FakeProvider(fail=True), g, role="r", model_id=FAKE, max_tokens=1000)
    g.start_task("T")
    with pytest.raises(RuntimeError, match="provider boom"):
        m.invoke("x")
    e = [x for x in g.ledger.events() if x["event"] == "failed_maybe_billed"]
    assert len(e) == 1 and e[0]["billing_state"] == "provider_may_have_billed"
    assert g.calls_global == 1, "异常不得把调用计数回滚成 0"


@pytest.mark.unit
def test_missing_usage_is_fail_closed_at_worst_case(tmp_path):
    HG.PRICES["nousage-test"] = {"input": 10.0, "output": 10.0, "source": "test"}
    g = mkgate(tmp_path, max_calls_per_model={"nousage-test": 9})
    m = GatedModel(FakeProvider(usage=False), g, role="r",
                   model_id="nousage-test", max_tokens=1000)
    g.start_task("T")
    m.invoke("x")
    e = [x for x in g.ledger.events() if x["event"] == "usage_unknown"]
    assert len(e) == 1 and e[0]["held_usd"] > 0
    assert g.actual_usd == pytest.approx(e[0]["held_usd"])   # 按最坏计，不当作 0
    HG.PRICES.pop("nousage-test")


# 21 —— 进程重启恢复 reservation
@pytest.mark.unit
def test_reservation_survives_process_restart(tmp_path):
    g = mkgate(tmp_path)
    g.start_task("T")
    _, worst = g.before_call(model_id=FAKE, role="r", payload="x" * 3000)
    g2 = mkgate(tmp_path)                                  # 同一账本，新"进程"
    assert g2.reserved_usd == pytest.approx(worst)
    assert g2.committed_usd == pytest.approx(worst)


# 22 —— 并发不能共同越界
@pytest.mark.unit
def test_concurrent_calls_cannot_jointly_exceed(tmp_path):
    m, p, g = gated(tmp_path, max_calls_task=5)
    g.start_task("T")
    errs, lock = [], threading.Lock()

    def worker():
        try:
            m.invoke("x")
        except BudgetExceeded as e:
            with lock:
                errs.append(e)

    ts = [threading.Thread(target=worker) for _ in range(20)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert p.calls == 5 and len(errs) == 15


# 23 —— 重复 run_id 不覆盖账本
@pytest.mark.unit
def test_duplicate_run_id_appends_not_overwrites(tmp_path):
    g1 = mkgate(tmp_path); g1.start_task("SAME")
    g1.before_call(model_id=FAKE, role="r", payload="a")
    n1 = len(g1.ledger.events())
    g2 = mkgate(tmp_path); g2.start_task("SAME")
    g2.before_call(model_id=FAKE, role="r", payload="b")
    ev = g2.ledger.events()
    assert len(ev) == n1 + 1 and ev[0]["event"] == "reserved"   # 旧事件仍在


# 24 / 25 —— 未知模型 / 未知价格拒绝
@pytest.mark.unit
def test_unknown_model_and_price_are_refused(tmp_path):
    g = mkgate(tmp_path)
    g.start_task("T")
    with pytest.raises(GateConfigError, match="未知模型"):
        HG.price_for("gpt-something-unlisted")
    m = GatedModel(FakeProvider(), g, role="r", model_id="gpt-something-unlisted")
    with pytest.raises((GateConfigError, BudgetExceeded)):
        m.invoke("x")
    assert object.__getattribute__(m, "_inner").calls == 0


# 26 —— 缺任一开关拒绝
@pytest.mark.unit
@pytest.mark.parametrize("drop", [HG.ENV_PAID, HG.ENV_CONFIRM])
def test_missing_either_switch_refuses(tmp_path, monkeypatch, drop):
    m, p, g = gated(tmp_path)
    g.start_task("T")
    monkeypatch.delenv(drop, raising=False)
    with pytest.raises(GateConfigError, match="缺少显式开关"):
        m.invoke("x")
    assert p.calls == 0


# 27 —— Pilot 以外的生产路径默认行为不变
@pytest.mark.unit
def test_production_path_untouched_without_wrapping():
    import ssc_pi_agent as P
    for attr in ("judge_llm", "deepseek_llm_pro"):
        assert not getattr(getattr(P, attr), "_reumani_hard_gate_wrapped", False), \
            "生产模块不得在 import 时被包装"


# 28 —— CI 不可能触发真实付费调用
@pytest.mark.unit
def test_ci_cannot_trigger_paid_call(tmp_path, monkeypatch):
    m, p, g = gated(tmp_path)
    g.start_task("T")
    monkeypatch.setenv("CI", "true")
    with pytest.raises(GateConfigError, match="CI 环境禁止"):
        m.invoke("x")
    assert p.calls == 0


# 覆盖证明：无法包装 → 拒绝启动，不降级
@pytest.mark.unit
def test_unwrappable_target_refuses_to_start(tmp_path):
    g = mkgate(tmp_path)
    mod = SimpleNamespace(__name__="fakemod")
    with pytest.raises(GateConfigError, match="拒绝启动"):
        wrap_all(g, [(mod, "does_not_exist", "r", FAKE, 100)])


@pytest.mark.unit
def test_wrap_all_and_dynamic_proof(tmp_path):
    g = mkgate(tmp_path)
    mod = SimpleNamespace(__name__="fakemod", llm_a=FakeProvider(), llm_b=FakeProvider())
    wrap_all(g, [(mod, "llm_a", "planner", FAKE, 100), (mod, "llm_b", "executor", FAKE, 100)])
    assert assert_all_paid_entrypoints_wrapped([(mod, "llm_a"), (mod, "llm_b")]) is True
    mod.llm_c = FakeProvider()
    with pytest.raises(GateConfigError, match="未被包装"):
        assert_all_paid_entrypoints_wrapped([(mod, "llm_c")])
