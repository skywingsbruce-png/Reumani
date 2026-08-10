"""A.8.2a.4c §5 —— provider 生命周期审计事件的**持久化顺序**与失败注入。

顺序结论必须来自 EventStore 里真实的 sequence，不从对象计数推断。全部离线，零付费。
"""
import pathlib

import pytest

from pilot import hitl_contracts as HC
from pilot.approval_grant import ApprovalGrantError
from pilot.event_store import InMemoryEventStore
from tests.test_deferred_approval_lifecycle import build_stack, start_run

pytestmark = pytest.mark.unit
REPO = str(pathlib.Path(__file__).resolve().parent.parent)


@pytest.fixture(autouse=True)
def _switches(monkeypatch):
    """与 test_deferred_approval_lifecycle 相同的离线开关（provider 全为 fake，零付费）。"""
    from pilot.hard_gate import ENV_CONFIRM, ENV_PAID
    monkeypatch.setenv(ENV_PAID, "1")
    monkeypatch.setenv(ENV_CONFIRM, "A82a3")
    monkeypatch.delenv("CI", raising=False)


def _approve(r):
    r.approve(HC.ApprovalDecision(request_id=r.pending["request_id"],
                                  expected_state_version=r.state_version,
                                  idempotency_key="ap",
                                  action_hash=r.pending["action_hash"]))
    r.join_worker(60)


def _seqs(store, rid):
    return [(e.sequence, e.event_type) for e in store.list(rid)]


# ---------------------------------------------------------------- 真实 sequence 顺序
def test_persisted_event_order_across_the_full_lifecycle():
    reg, gate, calls, ex, store0 = build_stack()
    store, r = start_run(ex, store=store0, rid="hitl-audit-order")
    _approve(r)
    assert r.state == "completed"
    evs = _seqs(store, "hitl-audit-order")
    order = {t: s for s, t in evs}
    first_resolved = min(s for s, t in evs if t == "provider_resolved")
    first_call = min(s for s, t in evs if t == "model_call_started")
    assert order["approval_requested"] < order["approval_granted"] < first_resolved < first_call

    # 三个 provider_resolved 全部早于第一个 model_call_started
    resolved = [s for s, t in evs if t == "provider_resolved"]
    assert len(resolved) == 3
    assert max(resolved) < first_call

    # model_call_started 数量 == 真实 role_calls 总数
    starts = [s for s, t in evs if t == "model_call_started"]
    assert len(starts) == sum(ex.role_calls.values()) == 3

    # sequence 严格单调
    all_seq = [s for s, _ in evs]
    assert all_seq == sorted(all_seq) == list(range(len(all_seq)))


def test_audit_payload_contains_no_sensitive_material():
    reg, gate, calls, ex, store0 = build_stack()
    store, r = start_run(ex, store=store0, rid="hitl-audit-safe")
    _approve(r)
    import json
    for e in store.list("hitl-audit-safe"):
        if e.event_type in ("provider_resolved", "model_call_started"):
            blob = json.dumps(e.safe_payload, ensure_ascii=False)
            for bad in ("sk-", "Bearer", "Authorization", "prompt", "RULES (",
                        "supported_statements", "F:\\", "/home/"):
                assert bad not in blob, f"{e.event_type} 泄漏了 {bad}"
            # _emit 会自动附加 control_state / state_version（既有脱敏控制字段）
            assert set(e.safe_payload) <= {
                "role", "provider_id", "provider", "model_id", "provider_mode",
                "policy_id", "call_index", "cost_estimate_hash",
                "control_state", "state_version"}


def test_two_runs_events_do_not_interleave_identities():
    reg1, _, _, ex1, s1 = build_stack()
    reg2, _, _, ex2, s2 = build_stack()
    st1, r1 = start_run(ex1, store=s1, rid="hitl-audit-r1")
    st2, r2 = start_run(ex2, store=s2, rid="hitl-audit-r2")
    _approve(r1)
    _approve(r2)
    for store, rid in ((st1, "hitl-audit-r1"), (st2, "hitl-audit-r2")):
        evs = store.list(rid)
        assert all(e.run_id == rid for e in evs)
        seq = [e.sequence for e in evs]
        assert seq == sorted(seq) == list(range(len(seq)))


# ---------------------------------------------------------------- 失败注入
class _FailingStore(InMemoryEventStore):
    """在第 N 次写入指定事件类型时失败。"""

    def __init__(self, fail_type, nth=1):
        super().__init__()
        self._fail_type, self._nth, self._seen = fail_type, nth, 0

    def append_batch(self, events):
        for e in events:
            if e.event_type == self._fail_type:
                self._seen += 1
                if self._seen == self._nth:
                    raise IOError(f"injected failure on {self._fail_type} #{self._nth}")
        return super().append_batch(events)


def _run_with_failing_store(fail_type, nth, rid):
    store = _FailingStore(fail_type, nth)
    reg, gate, calls, ex, _ = build_stack(store=store)
    st, r = start_run(ex, store=store, rid=rid)
    try:
        _approve(r)
    except Exception:                                   # noqa: BLE001 —— 收敛在 run 状态里
        pass
    return reg, gate, ex, r, store


def test_first_provider_resolved_failure_blocks_all_invokes():
    reg, gate, ex, r, store = _run_with_failing_store(
        "provider_resolved", 1, "hitl-audit-f1")
    assert sum(ex.role_calls.values()) == 0             # 没有任何 provider invoke
    assert gate.calls_by_role == {}
    assert r.state == "failed" and r.needs_human_review
    assert not [a for a in r.artifacts if a.get("kind") == "json"]


def test_mid_sequence_provider_resolved_failure_keeps_earlier_events():
    reg, gate, ex, r, store = _run_with_failing_store(
        "provider_resolved", 2, "hitl-audit-f2")
    assert sum(ex.role_calls.values()) == 0
    persisted = [e for e in store.list("hitl-audit-f2") if e.event_type == "provider_resolved"]
    assert len(persisted) == 1                          # 先落盘的保留，不回滚
    assert r.state == "failed"


def test_model_call_started_failure_blocks_that_role_and_the_rest():
    reg, gate, ex, r, store = _run_with_failing_store(
        "model_call_started", 1, "hitl-audit-f3")
    assert sum(ex.role_calls.values()) == 0             # 该角色 invoke=0
    assert gate.calls_by_role == {}                     # 未消耗额度
    assert gate.retries == 0                            # 不自动重试
    assert r.state == "failed"
    types = [e.event_type for e in store.list("hitl-audit-f3")]
    assert "artifact_created" not in types


def test_audit_failure_permanently_disables_the_executor():
    """审计写入失败后不得靠重试绕过。"""
    reg, gate, ex, r, store = _run_with_failing_store(
        "provider_resolved", 1, "hitl-audit-f4")
    from pilot.research_contracts import ResearchRunContext
    ctx = ResearchRunContext(run_id="hitl-audit-f4", question="q", question_hash="h",
                             clarification_answer="strict_causal", answer_hash="a")
    with pytest.raises(ApprovalGrantError):
        ex.run_stage(stage="validate_evidence", ctx=ctx, state={})
    assert sum(ex.role_calls.values()) == 0


# ---------------------------------------------------------------- 静态守卫
def test_stage_body_no_longer_passes_emit_none():
    src = (pathlib.Path(REPO) / "pilot" / "hitl.py").read_text(encoding="utf-8")
    assert "emit=self._stage_audit_emitter(generation)" in src
    assert "state=snapshot_state, emit=None" not in src


def test_audit_emit_precedes_provider_invoke_in_source():
    src = (pathlib.Path(REPO) / "pilot" / "gated_research_executor.py").read_text(encoding="utf-8")
    i_emit = src.index('emit("model_call_started"')
    i_invoke = src.index("out = bound.invoke(prompt)")
    assert i_emit < i_invoke, "审计事件必须写在 provider invoke 之前"


def test_from_registry_is_never_reached_on_the_startup_or_pre_approval_path():
    """生产启动模块不得 resolve provider；runtime_api 里唯一的 eager 分支必须被显式标注。"""
    root = pathlib.Path(REPO) / "pilot"
    startup = (root / "controlled_executor_factory.py").read_text(encoding="utf-8")
    assert "from_registry(" not in startup

    api = (root / "runtime_api.py").read_text(encoding="utf-8")
    calls = [i for i in range(len(api)) if api.startswith("from_registry(", i)]
    assert len(calls) == 1, "runtime_api 里 eager resolve 的入口数量变化了，请重新审计"
    marker = api.index("NOT-A-PRODUCTION-STARTUP-PATH")
    assert marker < calls[0], "eager 分支必须带非生产路径标注"

    deferred = (root / "deferred_research_executor.py").read_text(encoding="utf-8")
    body = deferred[deferred.index("def _ensure_resolved"):deferred.index("def run_stage")]
    assert "from_registry(" in body                       # 唯一的生产 resolve 点
    assert "self._grant" in body                          # 且必须绑定已核验的 ApprovalGrant


def test_deferred_executor_holds_no_lock_during_provider_invoke():
    """审计回调与 provider 调用都不得在持有 HitlRun 锁时进行（§禁止项）。"""
    lines = (pathlib.Path(REPO) / "pilot" / "hitl.py").read_text(encoding="utf-8").splitlines()
    target = next(n for n, ln in enumerate(lines) if "_executor.run_stage(" in ln)
    indent = len(lines[target]) - len(lines[target].lstrip())
    # 向上找最近的、缩进更浅的 `with self._lock:`；若存在且其块尚未退出，则说明持锁调用。
    for n in range(target - 1, -1, -1):
        ln = lines[n]
        if not ln.strip():
            continue
        cur = len(ln) - len(ln.lstrip())
        if cur < indent:                                  # 找到了包裹当前语句的外层块
            assert "with self._lock" not in ln, "run_stage 不得在 self._lock 作用域内调用"
            indent = cur
        if cur == 0 and ln.startswith("    ") is False and ln.strip().startswith("def "):
            break


def test_audit_emitter_rejects_arbitrary_event_types():
    reg, gate, calls, ex, store0 = build_stack()
    store, r = start_run(ex, store=store0, rid="hitl-audit-type")
    emitter = r._stage_audit_emitter(r._worker_generation)
    with pytest.raises(HC.ContractViolation):
        emitter("run_completed", summary="x", safe_payload={})
    with pytest.raises(HC.ContractViolation):
        emitter("artifact_created", summary="x", safe_payload={})
