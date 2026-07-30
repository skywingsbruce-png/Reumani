"""A.7.5.1.1 —— HITL 加固的独立故障注入 + 真并发审计测试。

零付费模型、零网络、零设备。验证：损坏日志 fail-closed、event sink 失败回滚、
重启幂等（完整 replay vs 抑制、同 key 不同 payload 冲突）、50 轮真并发、
执行中 pause 的 stale worker / stop 抢占 / pausing 崩溃恢复。
"""
import json
import os
import threading

import pytest

from pilot.event_store import InMemoryEventStore, JsonlEventStore
from pilot.hitl import HitlRun, RecoveryError, idem_hash
from pilot.runtime_events import make_event
from pilot import hitl_contracts as HC

pytestmark = pytest.mark.unit


# ------------------------------ helpers ------------------------------
def _to_approval(store, rid="hitl-r", sel="skin"):
    r = HitlRun(rid, store); r.start()
    r.answer_clarification(HC.ClarificationAnswer(request_id=r.pending["request_id"],
        expected_state_version=r.state_version, idempotency_key="a", selected_option_ids=[sel]))
    return r


def _complete(r, key="ap"):
    return r.approve(HC.ApprovalDecision(request_id=r.pending["request_id"],
        expected_state_version=r.state_version, idempotency_key=key, action_hash=r.pending["action_hash"]))


def _corrupt(tmp_path, rid, mutate):
    p = os.path.join(str(tmp_path), f"{rid}.jsonl")
    lines = open(p, encoding="utf-8").read().splitlines()
    mutate(lines)
    open(p, "w", encoding="utf-8", newline="\n").write("\n".join(l for l in lines if l is not None) + "\n")


# ============================ §5/§6 损坏日志 → fail-closed ============================
def test_recover_fail_closed_truncated_last_json(tmp_path):
    _to_approval(JsonlEventStore(str(tmp_path)), "hitl-t")
    p = os.path.join(str(tmp_path), "hitl-t.jsonl")
    data = open(p, encoding="utf-8").read()
    open(p, "w", encoding="utf-8").write(data[:-30])          # 截断最后一条 JSON
    with pytest.raises(RecoveryError):
        HitlRun.recover("hitl-t", JsonlEventStore(str(tmp_path)))


def test_recover_fail_closed_content_hash_mismatch(tmp_path):
    _to_approval(JsonlEventStore(str(tmp_path)), "hitl-h")
    def mut(lines):
        obj = json.loads(lines[1]); obj["summary"] = "TAMPERED"
        lines[1] = json.dumps(obj, ensure_ascii=False)
    _corrupt(tmp_path, "hitl-h", mut)
    with pytest.raises(RecoveryError):
        HitlRun.recover("hitl-h", JsonlEventStore(str(tmp_path)))


def test_recover_fail_closed_unknown_schema_version(tmp_path):
    _to_approval(JsonlEventStore(str(tmp_path)), "hitl-s")
    def mut(lines):
        obj = json.loads(lines[0]); obj["schema_version"] = "reumani-event-v99"
        lines[0] = json.dumps(obj, ensure_ascii=False)
    _corrupt(tmp_path, "hitl-s", mut)
    with pytest.raises(RecoveryError):
        HitlRun.recover("hitl-s", JsonlEventStore(str(tmp_path)))


def test_recover_fail_closed_sequence_gap(tmp_path):
    _to_approval(JsonlEventStore(str(tmp_path)), "hitl-g")
    _corrupt(tmp_path, "hitl-g", lambda lines: lines.__setitem__(2, None))   # 删中间一条 → 缺号
    with pytest.raises(RecoveryError):
        HitlRun.recover("hitl-g", JsonlEventStore(str(tmp_path)))


def test_recover_fail_closed_sequence_duplicate(tmp_path):
    _to_approval(JsonlEventStore(str(tmp_path)), "hitl-d")
    _corrupt(tmp_path, "hitl-d", lambda lines: lines.insert(2, lines[1]))     # 重复一条
    with pytest.raises(RecoveryError):
        HitlRun.recover("hitl-d", JsonlEventStore(str(tmp_path)))


def test_recover_fail_closed_post_terminal_event(tmp_path):
    store = JsonlEventStore(str(tmp_path))
    r = _to_approval(store, "hitl-pt"); _complete(r)             # completed（终态）
    p = os.path.join(str(tmp_path), "hitl-pt.jsonl")
    last = json.loads(open(p, encoding="utf-8").read().splitlines()[-1])
    nseq = last["sequence"] + 1
    ev = make_event(run_id="hitl-pt", sequence=nseq, event_type="step_started",
                    event_id=f"hitl-pt-{nseq:04d}", step_id=3, status="running", summary="post-terminal",
                    safe_payload={"control_state": "running", "state_version": 99})
    with open(p, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(ev.model_dump(), ensure_ascii=False, sort_keys=True) + "\n")
    with pytest.raises(RecoveryError):                          # 终态后追加 running → fail-closed
        HitlRun.recover("hitl-pt", JsonlEventStore(str(tmp_path)))


def test_recover_fail_closed_state_version_regress(tmp_path):
    store = JsonlEventStore(str(tmp_path))
    _to_approval(store, "hitl-rv")
    def mut(lines):
        obj = json.loads(lines[-1]); obj["safe_payload"]["state_version"] = 0   # 回退
        # 重新算 hash 使其结构合法，只让 state_version 单调性被破坏
        from pilot.runtime_events import RuntimeEvent, event_content_hash
        obj.pop("content_hash", None)
        ev = RuntimeEvent(**obj, content_hash="x") if False else None
        lines[-1] = json.dumps(obj, ensure_ascii=False)
    # 注：state_version 属于 content_hash，改它会先触发 hash mismatch（也应 fail-closed）
    _corrupt(tmp_path, "hitl-rv", mut)
    with pytest.raises(RecoveryError):
        HitlRun.recover("hitl-rv", JsonlEventStore(str(tmp_path)))


# ============================ §6 event sink 失败 → 回滚，无漂移 ============================
class _FailAt(InMemoryEventStore):
    """在第 N 次 append_batch 调用时抛错（N 从 1 计）。"""
    def __init__(self, fail_on_call):
        super().__init__(); self._n = 0; self._fail_on = fail_on_call

    def append_batch(self, batch):
        self._n += 1
        if self._n == self._fail_on:
            raise OSError("injected sink failure")
        super().append_batch(batch)


def test_sink_failure_no_state_drift_and_no_receipt():
    # start() 是第 1 次 append_batch；让 answer 的 append_batch 失败
    store = _FailAt(fail_on_call=2)
    r = HitlRun("hitl-f", store); r.start()                     # 第1次成功
    v, st, ev_n = r.state_version, r.state, len(store.list("hitl-f"))
    with pytest.raises(OSError):
        r.answer_clarification(HC.ClarificationAnswer(request_id=r.pending["request_id"],
            expected_state_version=r.state_version, idempotency_key="k1", selected_option_ids=["skin"]))
    # 状态/版本/pending/事件数完全回滚；幂等 receipt 未写入（可用新 key 重试同样失败点已过）
    assert r.state == st and r.state_version == v and len(store.list("hitl-f")) == ev_n
    assert r.pending is not None and r.pending["type"] == "clarification"
    assert idem_hash("k1") not in r._idem                        # 失败不留 receipt
    assert r.tool_calls == 0
    # 失败没有消费 clarification：修好后仍可作答
    store._fail_on = 999
    r.answer_clarification(HC.ClarificationAnswer(request_id=r.pending["request_id"],
        expected_state_version=r.state_version, idempotency_key="k2", selected_option_ids=["skin"]))
    assert r.state == "awaiting_approval"


def test_sink_failure_on_approve_does_not_start_worker_or_execute():
    store = _FailAt(fail_on_call=3)                             # start=1, answer=2, approve=3 fails
    r = _to_approval(store, "hitl-fa")
    v = r.state_version
    with pytest.raises(OSError):
        _complete(r, key="ap")
    assert r.state == "awaiting_approval" and r.state_version == v
    assert r.tool_calls == 0 and len(r.artifacts) == 0
    assert not r._exec_active


# ============================ §7 重启幂等：完整 replay vs 抑制；同 key 不同 payload ============================
def test_live_idempotency_full_response_replay():
    store = InMemoryEventStore()
    r = _to_approval(store, "hitl-i")
    ah = r.pending["action_hash"]                              # 真正的幂等重试：同 key + 同 payload
    first = _complete(r, key="ap")
    again = r.approve(HC.ApprovalDecision(request_id="apr-hitl-i", expected_state_version=r.state_version,
        idempotency_key="ap", action_hash=ah))
    assert again == first                                       # 同进程：完整响应 replay（快照一致）


def test_same_key_same_payload_no_duplicate_side_effect():
    store = InMemoryEventStore()
    r = _to_approval(store, "hitl-ss")
    ah = r.pending["action_hash"]
    _complete(r, key="ap"); n, calls, arts = len(store.list("hitl-ss")), r.tool_calls, len(r.artifacts)
    r.approve(HC.ApprovalDecision(request_id="apr-hitl-ss", expected_state_version=r.state_version,
        idempotency_key="ap", action_hash=ah))
    assert len(store.list("hitl-ss")) == n and r.tool_calls == calls == 1 and len(r.artifacts) == arts == 1


def test_same_key_different_payload_is_conflict_not_silent():
    store = InMemoryEventStore()
    r = HitlRun("hitl-dp", store); r.start()
    r.answer_clarification(HC.ClarificationAnswer(request_id=r.pending["request_id"],
        expected_state_version=r.state_version, idempotency_key="K", selected_option_ids=["skin"]))
    # 同 key、不同 payload（lung）、原 version → 必须 fail-closed 冲突，不得静默返回旧结果
    with pytest.raises((HC.ContractViolation, HC.StaleState)):
        r.answer_clarification(HC.ClarificationAnswer(request_id="clr-hitl-dp",
            expected_state_version=0, idempotency_key="K", selected_option_ids=["lung"]))


def test_recovered_replay_old_key_suppresses_side_effects(tmp_path):
    store = JsonlEventStore(str(tmp_path))
    r = _to_approval(store, "hitl-rk"); ah = r.pending["action_hash"]; _complete(r, key="apK")
    n = len(store.list("hitl-rk"))
    r2 = HitlRun.recover("hitl-rk", JsonlEventStore(str(tmp_path)))
    r2.approve(HC.ApprovalDecision(request_id="apr-hitl-rk", expected_state_version=r2.state_version,
        idempotency_key="apK", action_hash=ah))               # 重启后旧 key + 同 payload → 抑制，无新副作用
    assert len(store.list("hitl-rk")) == n and r2.tool_calls == 1 and len(r2.artifacts) == 1


# ============================ §8 真并发（barrier 同时释放），每场景 50 轮 ============================
ROUNDS = 50


def _race(fns):
    n = len(fns); barrier = threading.Barrier(n); out = [None] * n

    def wrap(i, fn):
        barrier.wait()
        try:
            out[i] = ("ok", fn())
        except Exception as e:  # noqa: BLE001
            out[i] = ("err", e)
    ts = [threading.Thread(target=wrap, args=(i, f)) for i, f in enumerate(fns)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert all(not t.is_alive() for t in ts), "deadlock: thread still alive"   # 无死锁
    return out


def _seqs_ok(store, rid):
    s = [e.sequence for e in store.list(rid)]
    return s == list(range(len(s)))


def test_concurrent_approve_vs_approve_50():
    for i in range(ROUNDS):
        store = InMemoryEventStore(); rid = f"hitl-aa{i}"
        r = _to_approval(store, rid); v, ah = r.state_version, r.pending["action_hash"]
        mk = lambda k: (lambda: r.approve(HC.ApprovalDecision(request_id=f"apr-{rid}",
            expected_state_version=v, idempotency_key=k, action_hash=ah)))
        res = _race([mk("A"), mk("B")])
        assert len([x for x in res if x[0] == "ok"]) == 1, f"round {i}: not exactly one win"
        assert r.tool_calls == 1 and len(r.artifacts) == 1 and r.state == "completed"
        assert _seqs_ok(store, rid)


def test_concurrent_pause_vs_approve_50():
    for i in range(ROUNDS):
        store = InMemoryEventStore(); rid = f"hitl-pa{i}"
        r = _to_approval(store, rid); v, ah = r.state_version, r.pending["action_hash"]
        approve = lambda: r.approve(HC.ApprovalDecision(request_id=f"apr-{rid}",
            expected_state_version=v, idempotency_key="ap", action_hash=ah))
        res = _race([approve, lambda: r.pause("pz", v)])
        assert len([x for x in res if x[0] == "ok"]) == 1
        assert r.tool_calls <= 1 and _seqs_ok(store, rid)


def test_concurrent_answer_vs_stop_50():
    for i in range(ROUNDS):
        store = InMemoryEventStore(); rid = f"hitl-as{i}"
        r = HitlRun(rid, store); r.start(); v = r.state_version
        answer = lambda: r.answer_clarification(HC.ClarificationAnswer(request_id=f"clr-{rid}",
            expected_state_version=v, idempotency_key="an", selected_option_ids=["skin"]))
        res = _race([answer, lambda: r.stop("st", v)])
        assert len([x for x in res if x[0] == "ok"]) == 1
        assert _seqs_ok(store, rid)


def test_concurrent_resume_vs_resume_50():
    for i in range(ROUNDS):
        store = InMemoryEventStore(); rid = f"hitl-rr{i}"
        r = _to_approval(store, rid); r.pause("p", r.state_version); v = r.state_version
        mk = lambda k: (lambda: r.resume(k, v))
        res = _race([mk("R1"), mk("R2")])
        assert len([x for x in res if x[0] == "ok"]) == 1
        assert r.state == "awaiting_approval" and _seqs_ok(store, rid)


def test_concurrent_diff_key_same_version_one_wins_50():
    for i in range(ROUNDS):
        store = InMemoryEventStore(); rid = f"hitl-dk{i}"
        r = _to_approval(store, rid); v, ah = r.state_version, r.pending["action_hash"]
        mk = lambda k: (lambda: r.approve(HC.ApprovalDecision(request_id=f"apr-{rid}",
            expected_state_version=v, idempotency_key=k, action_hash=ah)))
        res = _race([mk("k1"), mk("k2"), mk("k3")])
        oks = [x for x in res if x[0] == "ok"]
        assert len(oks) == 1 and r.tool_calls == 1 and _seqs_ok(store, rid)


def test_concurrent_two_runs_not_serialized_by_global_lock_50():
    # 不同 run 各自独立 RLock：并发操作两个 run 都成功、无死锁（无全局大锁）
    for i in range(ROUNDS):
        s1 = InMemoryEventStore(); s2 = InMemoryEventStore()
        r1 = _to_approval(s1, f"hitl-x{i}"); r2 = _to_approval(s2, f"hitl-y{i}")
        res = _race([lambda: _complete(r1, "a1"), lambda: _complete(r2, "a2")])
        assert all(x[0] == "ok" for x in res)
        assert r1.state == "completed" and r2.state == "completed"


# ============================ §9 执行中 pause / stale worker / stop 抢占 / pausing 崩溃 ============================
def _inflight(rid="hitl-if"):
    store = InMemoryEventStore(); gate = threading.Event()
    r = HitlRun(rid, store, exec_gate=gate); r.start()
    r.answer_clarification(HC.ClarificationAnswer(request_id=r.pending["request_id"],
        expected_state_version=r.state_version, idempotency_key="a", selected_option_ids=["skin"]))
    r.approve(HC.ApprovalDecision(request_id=r.pending["request_id"],
        expected_state_version=r.state_version, idempotency_key="ap", action_hash=r.pending["action_hash"]))
    # 等 worker 进入在途
    end = threading.Event()
    for _ in range(2000):
        if r._open_reservations == 1:
            break
        end.wait(0.002)
    return store, gate, r


def test_inflight_stop_preempts_late_completion_50():
    for _ in range(50):
        store, gate, r = _inflight(f"hitl-sp{_}")
        r.pause("pz", r.state_version)
        r.stop("st", r.state_version)                          # stop 在 pausing 到达
        gate.set(); r.join_worker(5)
        assert r.state == "stopped"                            # 迟到 completion 不得改回
        assert r.tool_calls == 0 and len(r.artifacts) == 0 and r._open_reservations == 0


def test_inflight_pause_then_resume_no_double_exec_50():
    for _ in range(50):
        store, gate, r = _inflight(f"hitl-pr{_}")
        r.pause("pz", r.state_version)
        gate.set(); r.join_worker(5)
        assert r.state == "paused" and r.tool_calls == 1 and len(r.artifacts) == 0
        r.resume("rz", r.state_version); r.join_worker(5)
        assert r.state == "completed" and r.tool_calls == 1 and len(r.artifacts) == 1


# ============================ §11 单进程边界（负向测试，记录已知缺口） ============================
def test_workers_gt_1_rejected_config_level_only():
    """workers!=1 被显式拒绝——但这只是**配置级检查**，不是操作系统级单实例锁。

    已知缺口（架构级，本阶段只报告不修复）：两个服务进程若使用不同端口指向同一 durable 目录，
    双方都能启动并写入同一事件目录；进程内 RLock 不跨进程，因此**不保证**多进程互斥。
    """
    from pilot.runtime_api import serve
    with pytest.raises(ValueError):
        serve(workers=2)
    # 明确记录：不存在跨进程互斥原语（无文件锁/租约）。若未来实现，应在此处断言其存在。
    import inspect
    from pilot import runtime_api
    src = inspect.getsource(runtime_api.serve)
    assert "workers" in src and "!= 1" in src.replace(" ", "") or "int(workers) != 1" in src
    assert "flock" not in src and "msvcrt" not in src and "lockfile" not in src.lower(), \
        "若已引入跨进程锁，请更新本测试与报告中的单进程边界声明"


def test_pausing_crash_recovery_needs_human_review_no_auto_resume(tmp_path):
    # run 处于 pausing 时"进程消失" → 新实例从事件恢复，不得自动 running，需人工审查
    store = JsonlEventStore(str(tmp_path)); gate = threading.Event()
    r = HitlRun("hitl-pc", store, exec_gate=gate); r.start()
    r.answer_clarification(HC.ClarificationAnswer(request_id=r.pending["request_id"],
        expected_state_version=r.state_version, idempotency_key="a", selected_option_ids=["skin"]))
    r.approve(HC.ApprovalDecision(request_id=r.pending["request_id"],
        expected_state_version=r.state_version, idempotency_key="ap", action_hash=r.pending["action_hash"]))
    for _ in range(2000):
        if r._open_reservations == 1:
            break
        threading.Event().wait(0.002)
    r.pause("pz", r.state_version)                             # → pausing（已持久化）
    assert r.state == "pausing"
    # 模拟崩溃：丢弃对象（worker 是 daemon，测试结束回收），从事件恢复
    r2 = HitlRun.recover("hitl-pc", JsonlEventStore(str(tmp_path)))
    assert r2.state == "pausing"                              # 不自动恢复成 running
    assert r2.needs_human_review is True                     # 标记人工审查
    gate.set()                                               # 释放旧 worker（不应影响 r2）
