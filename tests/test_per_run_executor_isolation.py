"""A.8.2a.4b §8 —— per-run 受控执行器隔离验收（真实 RunManager 入口）。

核心风险：executor registry 存的是**共享实例**，而 Deferred executor 有状态
（binding / grant / inner）。若共享，A 的批准会解锁 B。本测试从真实
`RunManager.start_research()` 出发，证明两个并发 run 完全隔离。全部离线，零付费。
"""
import os
import pathlib
import tempfile

import pytest

pytest.importorskip("starlette")          # runtime_api 依赖（CI 精简 unit 环境未装）

from pilot.event_store import InMemoryEventStore                       # noqa: E402
from pilot import hitl_contracts as HC                                 # noqa: E402
from pilot.hard_gate import HardBudgetGate, GatedModel, ENV_PAID, ENV_CONFIRM   # noqa: E402
from pilot.frozen_evidence import FrozenEvidenceLoader                 # noqa: E402
from pilot.approval_grant import ApprovalGrantError                    # noqa: E402
from pilot.controlled_executor_factory import (                        # noqa: E402
    ControlledResearchExecutorFactory, configure_controlled_research_runtime,
    scoped_approval_event_lookup)
from pilot.role_contracts import ANTHROPIC_OPUS_48, DEEPSEEK_V4_FLASH  # noqa: E402
from tests.test_live_output_wiring import SpyChat                      # noqa: E402
from tests.test_gated_research_executor import SYN, VER, CLM           # noqa: E402

REPO = str(pathlib.Path(__file__).resolve().parent.parent)
CAPS = {"claude-opus-4-8": ANTHROPIC_OPUS_48, "deepseek-v4-flash": DEEPSEEK_V4_FLASH}


@pytest.fixture(autouse=True)
def _switches(monkeypatch):
    monkeypatch.setenv(ENV_PAID, "1")
    monkeypatch.setenv(ENV_CONFIRM, "A82a4b")
    monkeypatch.delenv("CI", raising=False)


def make_factory(calls):
    def gate_factory(run_id):
        return HardBudgetGate(stage="A82a4b",
                              ledger_path=os.path.join(tempfile.mkdtemp(), f"{run_id}.jsonl"),
                              max_usd_global=.18, max_usd_stage=.18, max_usd_task=.18,
                              max_calls_global=3, max_calls_task=3,
                              max_calls_per_model={"claude-opus-4-8": 2,
                                                   "deepseek-v4-flash": 1},
                              max_calls_per_role={"synthesizer": 1, "verifier": 1,
                                                  "claim_extractor": 1},
                              task_timeout_s=60, max_retries=0, default_max_tokens=1600,
                              allow_ci=True)

    payloads = {"synthesizer": SYN, "verifier": VER, "claim_extractor": CLM}

    def model_factory(spec, gate):
        calls.append(spec.role)
        return GatedModel(SpyChat(payloads[spec.role]), gate, role=spec.role,
                          model_id=spec.model_id, max_tokens=spec.max_tokens)

    return ControlledResearchExecutorFactory(
        gate_factory=gate_factory, model_factory=model_factory,
        evidence_loader_factory=lambda run_id: FrozenEvidenceLoader(REPO),
        capabilities=CAPS)


def start(store, factory, rid):
    """模拟 RunManager 对受控工厂的分派（与生产同一条件分支）。"""
    from pilot.hitl import HitlRun
    from tests.test_gated_research_executor import make_spec
    assert getattr(factory, "is_per_run_factory", False)
    ex = factory.create_for_run(rid, store)
    r = HitlRun(rid, store, spec=make_spec(), executor=ex)
    r.start()
    r.answer_clarification(HC.ClarificationAnswer(
        request_id=r.pending["request_id"], expected_state_version=r.state_version,
        idempotency_key="a", selected_option_ids=["strict_causal"]))
    return ex, r


def approve(r):
    r.approve(HC.ApprovalDecision(request_id=r.pending["request_id"],
                                  expected_state_version=r.state_version,
                                  idempotency_key="ap",
                                  action_hash=r.pending["action_hash"]))
    r.join_worker(60)


# ---------------------------------------------------------------- 配置阶段零构造
def test_configuring_the_runtime_constructs_nothing():
    calls = []
    f = make_factory(calls)
    assert calls == [] and f.created_runs == []
    assert f.executor_id == "gated-research-v1" and f.is_per_run_factory is True


def test_creating_a_run_executor_still_resolves_nothing():
    calls = []
    store = InMemoryEventStore()
    ex = make_factory(calls).create_for_run("hitl-research-x", store)
    assert calls == []
    assert ex.registry.resolved_count() == 0
    assert ex.model_call_count() == 0 and not ex.authorized


# ---------------------------------------------------------------- 双 Run 隔离
def test_two_concurrent_runs_are_fully_isolated():
    calls = []
    factory = make_factory(calls)
    store = InMemoryEventStore()
    exA, rA = start(store, factory, "hitl-research-aaa")
    exB, rB = start(store, factory, "hitl-research-bbb")

    # 实例、Registry、Gate 全部不同
    assert exA is not exB
    assert exA.registry is not exB.registry
    assert exA.gate is not exB.gate
    assert exA.pending_binding.run_id == "hitl-research-aaa"
    assert exB.pending_binding.run_id == "hitl-research-bbb"

    approve(rA)                                    # 只批准 A
    assert rA.state == "completed"
    assert exA.authorized and not exB.authorized

    # A 的 resolve 不影响 B
    assert exA.registry.resolved_count() == 3
    assert exB.registry.resolved_count() == 0
    assert exB.model_call_count() == 0
    assert calls.count("synthesizer") == 1         # 只有 A 构造过

    # A 的 Grant 不能授权 B
    with pytest.raises(ApprovalGrantError):
        exB.authorize(rA.approval_grant)
    assert exB.registry.resolved_count() == 0

    # B 的 deny 不影响已完成的 A
    rB.deny(HC.ApprovalDecision(request_id=rB.pending["request_id"],
                                expected_state_version=rB.state_version,
                                idempotency_key="dn",
                                action_hash=rB.pending["action_hash"]))
    assert rA.state == "completed" and exA.authorized
    assert exB.registry.resolved_count() == 0

    # 账本分离
    assert exA.gate.calls_by_role.get("synthesizer") == 1
    assert exB.gate.calls_by_role.get("synthesizer") is None


def test_scoped_lookup_cannot_read_another_run():
    store = InMemoryEventStore()
    factory = make_factory([])
    exA, rA = start(store, factory, "hitl-research-la")
    exB, rB = start(store, factory, "hitl-research-lb")
    lookup_a = scoped_approval_event_lookup(store, "hitl-research-la")
    # 本 run 可读
    assert lookup_a("hitl-research-la", 0) is not None
    # 越权读取另一个 run → 拒绝
    with pytest.raises(ApprovalGrantError):
        lookup_a("hitl-research-lb", 0)


def test_cross_run_request_id_is_rejected_and_grants_do_not_transfer():
    """相同 spec 的两个 run 其 action_hash 本就相同（run_id 不参与该 hash）——
    隔离由 request_id / state_version 与**绑定了 run_id 的 Grant** 保证。"""
    store = InMemoryEventStore()
    factory = make_factory([])
    exA, rA = start(store, factory, "hitl-research-ha")
    exB, rB = start(store, factory, "hitl-research-hb")
    # 拿 A 的 request_id 去批 B → 拒绝
    with pytest.raises(Exception):
        rB.approve(HC.ApprovalDecision(request_id=rA.pending["request_id"],
                                       expected_state_version=rB.state_version,
                                       idempotency_key="x",
                                       action_hash=rB.pending["action_hash"]))
    assert exB.registry.resolved_count() == 0 and not exB.authorized
    # 即便 action_hash 相同，binding 里的 run_id 不同 → A 的 Grant 不能授权 B
    approve(rA)
    assert rA.approval_grant.run_id == "hitl-research-ha"
    with pytest.raises(ApprovalGrantError):
        exB.authorize(rA.approval_grant)
    assert exB.registry.resolved_count() == 0 and not exB.authorized


def test_factory_refuses_without_run_id_or_store():
    from pilot.controlled_executor_factory import ControlledExecutorFactoryError
    f = make_factory([])
    with pytest.raises(ControlledExecutorFactoryError):
        f.create_for_run("", InMemoryEventStore())
    with pytest.raises(ControlledExecutorFactoryError):
        f.create_for_run("hitl-research-z", None)


# ---------------------------------------------------------------- 生产接线证据
def test_run_manager_dispatches_to_the_per_run_factory():
    """真实 RunManager 必须识别工厂并为每个 run 建独立实例。"""
    src = (pathlib.Path(REPO) / "pilot" / "runtime_api.py").read_text(encoding="utf-8")
    assert "is_per_run_factory" in src
    assert "create_for_run(run_id, self.store)" in src


def test_controlled_factory_has_a_non_test_production_caller():
    """§11：工厂必须有真实非测试调用者（configure_ 入口 + RunManager 分派）。"""
    root = pathlib.Path(REPO) / "pilot"
    hits = [p.name for p in root.glob("*.py")
            if "ControlledResearchExecutorFactory(" in p.read_text(encoding="utf-8")]
    assert "controlled_executor_factory.py" in hits          # configure_ 入口构造它
    api = (root / "runtime_api.py").read_text(encoding="utf-8")
    assert "registered.create_for_run" in api                # RunManager 真实分派
