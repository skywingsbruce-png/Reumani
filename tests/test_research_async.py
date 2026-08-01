"""A.7.5.3.1 —— Research Run 异步执行 + 阶段失败 fail-closed（§11）。

全部 fake：零付费模型、零外部网络、零代码执行、零设备、零 Planner/ReAct。
用 Event/Barrier 控制 worker，不用真实 sleep 验证时序。
"""
import os
import threading
import warnings

import pytest

from pilot.event_store import InMemoryEventStore, JsonlEventStore
from pilot.hitl import HitlRun, RecoveryError
from pilot import hitl_contracts as HC
from pilot.research_contracts import ResearchFailureManifest, ResearchContractError
from pilot.fake_research_executor import FakeResearchExecutor, build_default_spec, STAGES

pytestmark = pytest.mark.unit

ROUNDS = 20


# ------------------------------ helpers ------------------------------
class _Boom:
    """把某个阶段替换成会抛指定异常的实现（保留 role 以维持角色额度语义）。"""

    def __init__(self, exc, role=None):
        self.exc, self.calls = exc, 0
        if role is not None:
            self.role = role

    def __call__(self, ctx, state):
        self.calls += 1
        raise self.exc


def _mk(rid="hitl-async-t", *, gates=None, store=None, fail=None, exc=None):
    store = store or InMemoryEventStore()
    ex = FakeResearchExecutor(stage_gates=gates)
    if fail:
        orig = ex._impl[fail]
        ex._impl[fail] = _Boom(exc or RuntimeError("injected"), getattr(orig, "role", None))
    r = HitlRun(rid, store, spec=build_default_spec(), executor=ex)
    r.start()
    r.answer_clarification(HC.ClarificationAnswer(
        request_id=r.pending["request_id"], expected_state_version=r.state_version,
        idempotency_key="a", selected_option_ids=["strict_causal"]))
    return store, ex, r


def _approve(r, key="ap"):
    return r.approve(HC.ApprovalDecision(
        request_id=r.pending["request_id"], expected_state_version=r.state_version,
        idempotency_key=key, action_hash=r.pending["action_hash"]))


def _settle(r, t=20):
    r.join_worker(t)
    return r


def _wait(pred, timeout=20.0):
    ev = threading.Event()
    for _ in range(int(timeout / 0.005)):
        if pred():
            return True
        ev.wait(0.005)
    return False


def _types(store, rid):
    return [e.event_type for e in store.list(rid)]


def _live_workers(run=None):
    """默认只看**本 run** 的 worker（避免同进程内其它测试的线程互相干扰）。"""
    if run is not None:
        w = run._worker
        return [w.name] if (w is not None and w.is_alive()) else []
    return [t.name for t in threading.enumerate() if t.name.startswith("hitl-research-")]


# ============================ §11.1 默认异步 ============================
def test_approve_returns_running_immediately_without_gate_or_delay():
    """无 gate / 无 delay 的默认路径也必须异步：approve 返回时不得已经 completed。"""
    store, ex, r = _mk("hitl-async-a1")
    snap = _approve(r)
    assert snap["control_state"] == "running"           # 立即返回 running
    assert snap["control_state"] != "completed"
    assert snap["research"]["worker_generation"] >= 1
    _settle(r)
    assert r.state == "completed" and len(r._stages_done) == len(STAGES)


def test_approve_does_not_wait_for_stages_barrier_proof():
    """用 Event 证明 approve 不等待阶段：第一个阶段被阻塞时 approve 仍已返回。"""
    gate = threading.Event()
    store, ex, r = _mk("hitl-async-a2", gates={"validate_evidence": gate})
    snap = _approve(r)                                   # 阶段仍被 gate 卡住
    assert snap["control_state"] == "running"
    assert len(r._stages_done) == 0                      # 一个阶段都没完成就已返回
    assert _wait(lambda: r._open_reservations == 1)      # worker 确实在后台跑
    gate.set(); _settle(r)
    assert r.state == "completed" and len(r._stages_done) == len(STAGES)


def test_background_worker_runs_all_stages_once_in_order():
    store, ex, r = _mk("hitl-async-a3")
    _approve(r); _settle(r)
    assert ex.stage_counts() == {s: 1 for s in STAGES}
    done = [e.safe_payload["stage"] for e in store.list("hitl-async-a3")
            if e.event_type == "research_stage_completed"]
    assert done == list(STAGES)
    t = _types(store, "hitl-async-a3")
    assert t.count("research_stage_started") == len(STAGES)      # started 先于 completed 落库
    assert t.count("run_completed") == 1 and t.count("artifact_created") == 1


def test_stage_started_persisted_before_body_runs():
    """stage_started 必须在阶段本体执行前落库（供恢复期识别不确定在途阶段）。"""
    gate = threading.Event()
    store, ex, r = _mk("hitl-async-a4", gates={"synthesizer": gate})
    _approve(r)
    idx = STAGES.index("synthesizer")
    assert _wait(lambda: r._exec_cursor == idx and r._open_reservations == 1)
    started = [e.safe_payload["stage"] for e in store.list("hitl-async-a4")
               if e.event_type == "research_stage_started"]
    assert "synthesizer" in started                       # 已 started
    completed = [e.safe_payload["stage"] for e in store.list("hitl-async-a4")
                 if e.event_type == "research_stage_completed"]
    assert "synthesizer" not in completed                 # 但尚未 completed
    gate.set(); _settle(r)


def test_demo_run_stays_synchronous_and_unchanged():
    store = InMemoryEventStore()
    d = HitlRun("hitl-demo-async", store); d.start()
    d.answer_clarification(HC.ClarificationAnswer(request_id=d.pending["request_id"],
        expected_state_version=d.state_version, idempotency_key="a", selected_option_ids=["skin"]))
    snap = d.approve(HC.ApprovalDecision(request_id=d.pending["request_id"],
        expected_state_version=d.state_version, idempotency_key="p",
        action_hash=d.pending["action_hash"]))
    assert snap["control_state"] == "completed"           # demo 行为不变（同步）
    assert "research_stage_started" not in _types(store, "hitl-demo-async")


# ============================ §11.2 每个阶段失败 ============================
@pytest.mark.parametrize("stage", list(STAGES))
@pytest.mark.parametrize("exc,label", [
    (RuntimeError("plain failure"), "plain"),
    (TimeoutError("stage timed out"), "timeout"),
    (ResearchContractError("structured validation failure"), "validation"),
])
def test_every_stage_failure_is_fail_closed(stage, exc, label):
    rid = f"hitl-async-f-{stage}-{label}"
    store, ex, r = _mk(rid, fail=stage, exc=exc)
    _approve(r); _settle(r)
    t = _types(store, rid)
    assert r.state == "failed" and r.needs_human_review is True
    assert t.count("research_stage_failed") == 1
    assert t.count("run_failed") == 1
    assert t.count("run_completed") == 0
    assert t.count("artifact_created") == 0               # 无成功产物
    assert len(r.artifacts) == 0
    # 失败阶段之后的阶段一次也没跑
    idx = STAGES.index(stage)
    for later in STAGES[idx + 1:]:
        assert ex.stage_counts()[later] == 0
    # primary failure 保留且脱敏
    assert r.primary_failure["failed_stage"] == stage
    assert r.primary_failure["error_type"] == type(exc).__name__
    assert "Traceback" not in (r.primary_failure["error_summary"] or "")
    # 不伪造 claims
    assert r.failure_manifest["claims"] == []
    assert r.failure_manifest["research_artifact_created"] is False
    assert r._open_reservations == 0
    assert _wait(lambda: not _live_workers(r), timeout=10)  # worker 已退出


def test_failure_events_carry_no_sensitive_data():
    store, ex, r = _mk("hitl-async-sec", fail="verifier",
                       exc=RuntimeError(r"boom at C:\Users\alice\.env with api_key=sk-abc123"))
    _approve(r); _settle(r)
    blob = "\n".join(e.model_dump_json() for e in store.list("hitl-async-sec"))
    for bad in ("Traceback", "sk-abc123", "C:\\\\Users", "alice", ".env"):
        assert bad not in blob
    from pilot.runtime_events import SAFE_PAYLOAD_KEYS
    for e in store.list("hitl-async-sec"):
        for k in e.safe_payload:
            assert k in SAFE_PAYLOAD_KEYS


def test_failure_manifest_contract_rejects_fake_success():
    with pytest.raises(Exception):
        ResearchFailureManifest(run_id="r", failed_stage="s", error_type="E",
                                claims=[{"claim_id": "C1"}])
    with pytest.raises(Exception):
        ResearchFailureManifest(run_id="r", failed_stage="s", error_type="E",
                                research_artifact_created=True)


def test_no_run_completed_or_stage_completed_after_run_failed():
    store, ex, r = _mk("hitl-async-order", fail="claim_extractor")
    _approve(r); _settle(r)
    t = _types(store, "hitl-async-order")
    i = t.index("run_failed")
    assert "research_stage_completed" not in t[i:]
    assert "artifact_created" not in t[i:] and "run_completed" not in t[i:]


# ============================ §11.3 持久化失败 ============================
class _FailAtType(InMemoryEventStore):
    """在写入指定 event_type 时抛错，用于持久化失败注入。"""

    def __init__(self, bad_type):
        super().__init__(); self.bad = bad_type

    def append_batch(self, batch):
        if any(e.event_type == self.bad for e in batch):
            raise OSError(f"injected sink failure on {self.bad}")
        super().append_batch(batch)


@pytest.mark.parametrize("bad", ["research_stage_started", "research_stage_completed"])
def test_persistence_failure_does_not_fake_success(bad):
    store = _FailAtType(bad)
    ex = FakeResearchExecutor()
    r = HitlRun(f"hitl-async-p-{bad}", store, spec=build_default_spec(), executor=ex)
    r.start()
    r.answer_clarification(HC.ClarificationAnswer(request_id=r.pending["request_id"],
        expected_state_version=r.state_version, idempotency_key="a",
        selected_option_ids=["strict_causal"]))
    _approve(r); _settle(r)
    t = _types(store, f"hitl-async-p-{bad}")
    assert "run_completed" not in t and "artifact_created" not in t   # 绝不假成功
    assert len(r.artifacts) == 0
    assert r.state != "completed"
    assert _wait(lambda: not _live_workers(r), timeout=10)


def test_failure_persistence_failure_is_secondary_only():
    """写失败事件本身失败 → primary failure 不被覆盖，且标记人工审查。"""
    store = _FailAtType("research_stage_failed")
    ex = FakeResearchExecutor()
    ex._impl["verifier"] = _Boom(RuntimeError("primary boom"), role="verifier")
    r = HitlRun("hitl-async-sec2", store, spec=build_default_spec(), executor=ex)
    r.start()
    r.answer_clarification(HC.ClarificationAnswer(request_id=r.pending["request_id"],
        expected_state_version=r.state_version, idempotency_key="a",
        selected_option_ids=["strict_causal"]))
    _approve(r); _settle(r)
    assert r.primary_failure["error_summary"].startswith("primary boom")   # primary 保留
    assert r.secondary_failure is not None                                 # 次级失败被记录
    assert r.needs_human_review is True
    assert len(r.artifacts) == 0
    assert "run_completed" not in _types(store, "hitl-async-sec2")


# ============================ §11.4 并发与竞争（各 20 轮） ============================
def test_concurrent_approve_starts_exactly_one_worker():
    for i in range(ROUNDS):
        store, ex, r = _mk(f"hitl-async-c{i}")
        v, ah, rid = r.state_version, r.pending["action_hash"], r.pending["request_id"]
        res, barrier = [None, None], threading.Barrier(2)

        def go(n, key):
            barrier.wait()
            try:
                res[n] = ("ok", r.approve(HC.ApprovalDecision(
                    request_id=rid, expected_state_version=v, idempotency_key=key, action_hash=ah)))
            except Exception as e:      # noqa: BLE001
                res[n] = ("err", e)
        ts = [threading.Thread(target=go, args=(0, "A")), threading.Thread(target=go, args=(1, "B"))]
        for t in ts:
            t.start()
        for t in ts:
            t.join(15)
        _settle(r)
        assert len([x for x in res if x[0] == "ok"]) == 1
        assert r._worker_generation == 1                 # 只启动过一个 worker
        assert ex.artifacts_built == 1 and ex.stage_counts() == {s: 1 for s in STAGES}


def test_idempotent_replay_starts_no_new_worker():
    for i in range(ROUNDS):
        store, ex, r = _mk(f"hitl-async-i{i}")
        ah = r.pending["action_hash"]
        _approve(r, key="k"); _settle(r)
        gen = r._worker_generation
        r.approve(HC.ApprovalDecision(request_id=f"apr-hitl-async-i{i}",
                                      expected_state_version=r.state_version,
                                      idempotency_key="k", action_hash=ah))
        assert r._worker_generation == gen == 1          # 幂等重放不启动新 worker
        assert ex.artifacts_built == 1


def test_stop_beats_late_completion():
    for i in range(ROUNDS):
        gate = threading.Event()
        store, ex, r = _mk(f"hitl-async-s{i}", gates={"synthesizer": gate})
        _approve(r)
        idx = STAGES.index("synthesizer")
        assert _wait(lambda: r._exec_cursor == idx and r._open_reservations == 1)
        r.stop("s", r.state_version)
        gate.set(); _settle(r)
        assert r.state == "stopped"                      # 终态不可逆
        t = _types(store, f"hitl-async-s{i}")
        assert "artifact_created" not in t and "run_completed" not in t
        assert ex.stage_counts()["verifier"] == 0        # 下一阶段未启动
        assert not _live_workers(r)


def test_stop_beats_late_failure():
    for i in range(ROUNDS):
        gate = threading.Event()
        store, ex, r = _mk(f"hitl-async-sf{i}", gates={"synthesizer": gate}, fail="synthesizer",
                           exc=RuntimeError("late boom"))
        _approve(r)
        assert _wait(lambda: r._open_reservations == 1)
        r.stop("s", r.state_version)
        gate.set(); _settle(r)
        assert r.state == "stopped"                      # 先提交的终态不被迟到异常改成 failed
        t = _types(store, f"hitl-async-sf{i}")
        assert t.count("run_failed") == 0 and t.count("run_stopped") == 1
        assert not _live_workers(r)


def test_failure_during_pausing_ends_failed_not_paused():
    for i in range(ROUNDS):
        gate = threading.Event()
        store, ex, r = _mk(f"hitl-async-pf{i}", gates={"synthesizer": gate}, fail="synthesizer",
                           exc=RuntimeError("boom while pausing"))
        _approve(r)
        assert _wait(lambda: r._open_reservations == 1)
        r.pause("p", r.state_version)
        assert r.state == "pausing"
        gate.set(); _settle(r)
        assert r.state == "failed"                       # 失败优先于 paused
        assert r.needs_human_review is True
        assert len(r.artifacts) == 0
        assert not _live_workers(r)


def test_pause_after_failure_is_terminal_conflict():
    store, ex, r = _mk("hitl-async-pt", fail="verifier")
    _approve(r); _settle(r)
    assert r.state == "failed"
    with pytest.raises((HC.IllegalTransition, HC.StaleState)):
        r.pause("p", r.state_version)
    with pytest.raises((HC.IllegalTransition, HC.StaleState)):
        r.resume("z", r.state_version)                   # 失败后不可自动/手动恢复


def test_two_runs_progress_independently():
    for i in range(ROUNDS):
        s1, e1, r1 = _mk(f"hitl-async-x{i}")
        s2, e2, r2 = _mk(f"hitl-async-y{i}", fail="verifier")
        _approve(r1); _approve(r2)
        _settle(r1); _settle(r2)
        assert r1.state == "completed" and r2.state == "failed"


# ============================ §11.5 恢复 ============================
def _fresh(tmp_path):
    return JsonlEventStore(str(tmp_path))


def test_recover_failed_run_stays_failed_and_not_resumable(tmp_path):
    store = _fresh(tmp_path)
    ex = FakeResearchExecutor()
    ex._impl["verifier"] = _Boom(RuntimeError("recover boom"), role="verifier")
    spec = build_default_spec()
    r = HitlRun("hitl-async-rf", store, spec=spec, executor=ex)
    r.start()
    r.answer_clarification(HC.ClarificationAnswer(request_id=r.pending["request_id"],
        expected_state_version=r.state_version, idempotency_key="a",
        selected_option_ids=["strict_causal"]))
    _approve(r); _settle(r)
    assert r.state == "failed"
    r2 = HitlRun.recover("hitl-async-rf", _fresh(tmp_path), spec=spec, executor=FakeResearchExecutor())
    assert r2.state == "failed"
    assert r2.primary_failure["failed_stage"] == "verifier"
    assert r2.needs_human_review is True
    with pytest.raises(HC.IllegalTransition):
        r2.resume("z", r2.state_version)
    assert r2.pending is None                       # 失败终态不再有待审批项
    with pytest.raises((HC.IllegalTransition, HC.ContractViolation, HC.StaleState)):
        r2.approve(HC.ApprovalDecision(request_id="apr-hitl-async-rf",
                                       expected_state_version=r2.state_version,
                                       idempotency_key="late", action_hash="x"))
    assert len(r2.artifacts) == 0


def test_recover_indeterminate_inflight_stage_needs_human_review(tmp_path):
    """stage_started 后没有 completed/failed → 不确定执行：不自动重放、需人工审查。"""
    gate = threading.Event()
    store = _fresh(tmp_path)
    spec = build_default_spec()
    ex = FakeResearchExecutor(stage_gates={"synthesizer": gate})
    r = HitlRun("hitl-async-ind", store, spec=spec, executor=ex)
    r.start()
    r.answer_clarification(HC.ClarificationAnswer(request_id=r.pending["request_id"],
        expected_state_version=r.state_version, idempotency_key="a",
        selected_option_ids=["strict_causal"]))
    _approve(r)
    # 必须等 synthesizer 的 stage_started **已落库**（游标就位不代表事件已写）
    assert _wait(lambda: any(e.event_type == "research_stage_started"
                             and e.safe_payload.get("stage") == "synthesizer"
                             for e in store.list("hitl-async-ind")))
    # 模拟进程消失：从事件恢复（synthesizer 已 started 未收敛）
    ex2 = FakeResearchExecutor()
    r2 = HitlRun.recover("hitl-async-ind", _fresh(tmp_path), spec=spec, executor=ex2)
    assert r2.needs_human_review is True
    assert r2.interrupted_stage == "synthesizer"
    assert sum(ex2.stage_counts().values()) == 0        # 不自动重放
    assert len(r2.artifacts) == 0 and not r2._exec_active
    r.stop("s", r.state_version); gate.set(); _settle(r)  # 收尾，避免写入已销毁 tmp_path


def test_recover_completed_stages_not_repeated(tmp_path):
    store = _fresh(tmp_path)
    spec = build_default_spec()
    ex = FakeResearchExecutor()
    r = HitlRun("hitl-async-rc", store, spec=spec, executor=ex)
    r.start()
    r.answer_clarification(HC.ClarificationAnswer(request_id=r.pending["request_id"],
        expected_state_version=r.state_version, idempotency_key="a",
        selected_option_ids=["strict_causal"]))
    _approve(r); _settle(r)
    n = len(store.list("hitl-async-rc"))
    ex2 = FakeResearchExecutor()
    r2 = HitlRun.recover("hitl-async-rc", _fresh(tmp_path), spec=spec, executor=ex2)
    assert r2.state == "completed" and len(r2._stages_done) == len(STAGES)
    assert sum(ex2.stage_counts().values()) == 0        # 恢复不重跑
    assert len(_fresh(tmp_path).list("hitl-async-rc")) == n


def test_stopped_run_does_not_restart_worker(tmp_path):
    store = _fresh(tmp_path)
    spec = build_default_spec()
    r = HitlRun("hitl-async-st", store, spec=spec, executor=FakeResearchExecutor())
    r.start()
    r.stop("s", r.state_version)
    r2 = HitlRun.recover("hitl-async-st", _fresh(tmp_path), spec=spec, executor=FakeResearchExecutor())
    assert r2.state == "stopped" and not r2._exec_active
    assert not _live_workers(r2)


# ============================ §11.6 线程清理 ============================
@pytest.mark.parametrize("mode", ["success", "failure", "stop"])
def test_no_worker_threads_survive(mode):
    for i in range(ROUNDS):
        if mode == "success":
            store, ex, r = _mk(f"hitl-async-t{mode}{i}")
            _approve(r); _settle(r)
        elif mode == "failure":
            store, ex, r = _mk(f"hitl-async-t{mode}{i}", fail="shadow")
            _approve(r); _settle(r)
        else:
            gate = threading.Event()
            store, ex, r = _mk(f"hitl-async-t{mode}{i}", gates={"synthesizer": gate})
            _approve(r)
            assert _wait(lambda: r._open_reservations == 1)
            try:
                r.stop("s", r.state_version)
            finally:
                gate.set()                               # finally 释放，测试失败也不遗留线程
            _settle(r)
        assert not _live_workers(r), f"round {i}: leaked {_live_workers(r)}"
        assert r._exec_active is False


def test_no_unhandled_thread_exception_warning():
    """阶段抛异常不得逃逸成未处理线程异常（旧实现会产生 PytestUnhandledThreadExceptionWarning）。"""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        store, ex, r = _mk("hitl-async-warn", fail="synthesizer")
        _approve(r); _settle(r)
    names = [type(w.message).__name__ for w in caught]
    assert not any("UnhandledThreadException" in n for n in names), names
    assert r.state == "failed"
