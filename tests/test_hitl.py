"""A.7.5 / A.7.5.1 —— 人机协作控制（Clarification/Approval/Pause/Resume）状态机 + 契约测试。

Deterministic fake：零付费模型、零网络、零设备、零任意代码。验证 §1/§3/§4/§5/§6/§10，
以及 A.7.5.1 加固：真正并发原子性、持久化重建、执行中协作式 Pause。
"""
import json
import os
import threading
import time

import pytest

from pilot.event_store import InMemoryEventStore, JsonlEventStore
from pilot.hitl import HitlRun, rebuild_state_from_events, RecoveryError, idem_hash
from pilot import hitl_contracts as HC

pytestmark = pytest.mark.unit


def _wait(pred, timeout=5.0):
    """轮询等待条件（用于观察后台 worker 到达在途边界）。"""
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.005)
    return False


def _drive_concurrent(fns):
    """用 barrier 让 N 个调用真正同时释放，返回 [("ok",结果)|("err",异常)]。"""
    n = len(fns)
    barrier = threading.Barrier(n)
    results = [None] * n

    def wrap(i, fn):
        barrier.wait()                       # 同时释放（不接受顺序调用冒充并发）
        try:
            results[i] = ("ok", fn())
        except Exception as e:               # noqa: BLE001
            results[i] = ("err", e)

    threads = [threading.Thread(target=wrap, args=(i, fn)) for i, fn in enumerate(fns)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    return results


def _run(rid="r"):
    store = InMemoryEventStore()
    r = HitlRun(rid, store)
    r.start()
    return store, r


def _answer(r, sel="skin", key="a1"):
    return r.answer_clarification(HC.ClarificationAnswer(
        request_id=r.pending["request_id"], expected_state_version=r.state_version,
        idempotency_key=key, selected_option_ids=[sel]))


def _decision(r, key, action_hash=None):
    return HC.ApprovalDecision(request_id=r.pending["request_id"], expected_state_version=r.state_version,
                              idempotency_key=key, action_hash=action_hash or r.pending["action_hash"])


# ------------------------------ state machine (§1) ------------------------------
def test_legal_and_illegal_transitions():
    assert HC.can_transition("running", "awaiting_clarification")
    assert HC.can_transition("awaiting_approval", "running")
    assert HC.can_transition("paused", "awaiting_approval")
    assert not HC.can_transition("completed", "running")     # terminal never resumes
    assert not HC.can_transition("stopped", "running")
    assert not HC.can_transition("running", "paused")        # must go via pausing
    with pytest.raises(HC.IllegalTransition):
        HC.assert_transition("completed", "running")


def test_six_control_states_and_terminals():
    assert HC.is_terminal("completed") and HC.is_terminal("failed") and HC.is_terminal("stopped")
    assert not HC.is_terminal("paused") and not HC.is_terminal("awaiting_clarification")


# ------------------------------ clarification (§3) ------------------------------
def test_clarification_waits_with_zero_tool_calls():
    store, r = _run()
    assert r.state == "awaiting_clarification" and r.tool_calls == 0
    assert r.pending["type"] == "clarification"
    ids = {o["id"] for o in r.pending["allowed_options"]}
    assert ids == {"skin", "lung", "both"} and r.pending["allow_other"] is True
    types = [e.event_type for e in store.list("r")]
    assert "clarification_requested" in types


def test_clarification_answer_resumes_same_step():
    store, r = _run()
    _answer(r, "skin")
    assert r.state == "awaiting_approval" and r.tool_calls == 0
    t = [e.event_type for e in store.list("r")]
    assert "clarification_answered" in t and t.index("clarification_answered") > t.index("clarification_requested")


def test_free_text_is_bounded_and_desensitized():
    long = "rm -rf / ; " + "x" * 500 + "\n\tpath/to/file"
    ans = HC.ClarificationAnswer(request_id="q", expected_state_version=0, idempotency_key="k",
                                 other_text=long)
    assert len(ans.other_text) <= HC.FREE_TEXT_MAX and "\n" not in ans.other_text and "\t" not in ans.other_text


# ------------------------------ approval (§4) ------------------------------
def test_approval_binds_action_hash_and_is_simulation():
    store, r = _run(); _answer(r)
    assert r.pending["type"] == "approval" and r.pending["is_simulation"] is True
    assert r.pending["risk_level"] == "high" and "不连接真实设备" in r.pending["expected_side_effect"]
    assert r.tool_calls == 0                                   # awaiting_approval → 0 tool calls


def test_approval_mismatched_action_hash_rejected():
    store, r = _run(); _answer(r)
    with pytest.raises(HC.ContractViolation):
        r.approve(_decision(r, "k", action_hash="WRONG"))
    assert r.tool_calls == 0                                   # rejected → no execution


def test_approve_executes_fake_action_once_and_completes():
    store, r = _run(); _answer(r)
    r.approve(_decision(r, "ap1"))
    assert r.state == "completed" and r.tool_calls == 1 and len(r.artifacts) == 1
    t = [e.event_type for e in store.list("r")]
    assert t.count("tool_started") == 1 and "approval_granted" in t and "artifact_created" in t
    assert r.lifecycle == {"requested": 1, "executed": 1, "tool_returned": 1, "observed": 1}


def test_deny_no_execution_no_artifact_not_success():
    store, r = _run(); _answer(r, "both")
    r.deny(_decision(r, "d1"))
    assert r.state == "stopped" and r.tool_calls == 0 and len(r.artifacts) == 0
    t = [e.event_type for e in store.list("r")]
    assert "approval_denied" in t and "artifact_created" not in t and "run_completed" not in t


# ------------------------------ pause / resume (§5) ------------------------------
def test_pause_resume_restores_prior_state_zero_calls():
    store, r = _run(); _answer(r)
    r.pause("p", r.state_version)
    assert r.state == "paused" and r.tool_calls == 0
    r.resume("rz", r.state_version)
    assert r.state == "awaiting_approval" and r.pending["type"] == "approval"  # restored, not lost
    r.approve(_decision(r, "a"))
    assert r.state == "completed" and r.tool_calls == 1        # no double execution across pause


def test_stop_is_terminal_and_not_resumable():
    store, r = _run()
    r.stop("s", r.state_version)
    assert r.state == "stopped"
    with pytest.raises(HC.IllegalTransition):
        r.resume("z", r.state_version)


def test_completed_is_immutable():
    store, r = _run(); _answer(r); r.approve(_decision(r, "a"))
    with pytest.raises(HC.IllegalTransition):
        r.stop("s", r.state_version)


# ------------------------------ idempotency / version (§6) ------------------------------
def test_stale_state_version_rejected():
    store, r = _run()
    with pytest.raises(HC.StaleState):
        r.answer_clarification(HC.ClarificationAnswer(request_id=r.pending["request_id"],
            expected_state_version=999, idempotency_key="k", selected_option_ids=["skin"]))


def test_idempotent_answer_no_double_transition():
    store, r = _run()
    v = r.state_version
    _answer(r, "lung", key="dup")
    st, ver, n = r.state, r.state_version, len(store.list("r"))
    # replay same idempotency_key → cached, no new events / no state change
    r.answer_clarification(HC.ClarificationAnswer(request_id="clr-r", expected_state_version=v,
        idempotency_key="dup", selected_option_ids=["lung"]))
    assert r.state == st and r.state_version == ver and len(store.list("r")) == n


def test_idempotent_approve_executes_once():
    store, r = _run(); _answer(r)
    ah = r.pending["action_hash"]                             # 幂等重试用相同 payload（action_hash）
    r.approve(_decision(r, "apX"))
    calls, arts = r.tool_calls, len(r.artifacts)
    # second approve with same idempotency_key + same payload → cached (no second execution)
    r.approve(HC.ApprovalDecision(request_id="apr-r", expected_state_version=r.state_version,
        idempotency_key="apX", action_hash=ah))
    assert r.tool_calls == calls == 1 and len(r.artifacts) == arts == 1


def test_two_browser_approve_second_conflicts():
    store, r = _run(); _answer(r)
    v = r.state_version
    r.approve(_decision(r, "browserA"))                       # first wins
    with pytest.raises((HC.StaleState, HC.IllegalTransition, HC.ContractViolation)):
        r.approve(HC.ApprovalDecision(request_id="apr-r", expected_state_version=v,
            idempotency_key="browserB", action_hash="x"))     # stale version → conflict


# ------------------------------ event replay / safety (§10) ------------------------------
def test_rebuild_state_from_append_only_events():
    store, r = _run()
    assert rebuild_state_from_events(store.list("r"))["control_state"] == "awaiting_clarification"
    _answer(r)
    mid = rebuild_state_from_events(store.list("r"))
    assert mid["control_state"] == "awaiting_approval" and mid["pending"]["type"] == "approval"
    r.approve(_decision(r, "a"))
    done = rebuild_state_from_events(store.list("r"))
    assert done["control_state"] == "completed" and done["pending"] is None


def test_no_sensitive_info_in_events():
    store, r = _run(); _answer(r)
    r.approve(_decision(r, "a"))
    blob = "\n".join(e.model_dump_json() for e in store.list("r")).lower()
    for bad in ("prompt", "api_key", "authorization", "cookie", "bearer", "patient"):
        assert bad not in blob
    for e in store.list("r"):
        for k in e.safe_payload:
            assert k in __import__("pilot.runtime_events", fromlist=["x"]).SAFE_PAYLOAD_KEYS


def test_zero_model_or_tool_calls_while_waiting_or_paused():
    store, r = _run()
    assert r.tool_calls == 0                                   # awaiting_clarification
    _answer(r)
    assert r.tool_calls == 0                                   # awaiting_approval
    r.pause("p", r.state_version)
    assert r.tool_calls == 0                                   # paused
    r.resume("rz", r.state_version)
    assert r.tool_calls == 0                                   # awaiting_approval again


# ============================ A.7.5.1 §1 真正并发原子性 ============================
def _approve_fn(r, key, version=None, ah=None):
    return lambda: r.approve(HC.ApprovalDecision(
        request_id="apr-r", expected_state_version=r.state_version if version is None else version,
        idempotency_key=key, action_hash=ah or r.pending["action_hash"]))


def test_concurrent_approve_different_keys_only_one_wins():
    store, r = _run(); _answer(r)
    v, ah = r.state_version, r.pending["action_hash"]
    res = _drive_concurrent([_approve_fn(r, "A", v, ah), _approve_fn(r, "B", v, ah)])
    oks = [x for x in res if x[0] == "ok"]
    errs = [x for x in res if x[0] == "err"]
    assert len(oks) == 1 and len(errs) == 1                    # 只允许一个通过
    assert isinstance(errs[0][1], (HC.StaleState, HC.IllegalTransition, HC.ContractViolation))
    assert r.state == "completed" and r.tool_calls == 1 and len(r.artifacts) == 1
    t = [e.event_type for e in store.list("r")]
    assert t.count("tool_started") == 1 and t.count("run_completed") == 1
    seqs = [e.sequence for e in store.list("r")]
    assert seqs == list(range(len(seqs)))                      # sequence 连续且唯一


def test_concurrent_same_idem_key_executes_once():
    store, r = _run(); _answer(r)
    v, ah = r.state_version, r.pending["action_hash"]
    res = _drive_concurrent([_approve_fn(r, "same", v, ah), _approve_fn(r, "same", v, ah)])
    assert all(x[0] == "ok" for x in res)                      # 同 key → 都返回（一次执行，一次缓存）
    assert r.tool_calls == 1 and len(r.artifacts) == 1
    t = [e.event_type for e in store.list("r")]
    assert t.count("tool_started") == 1 and t.count("run_completed") == 1


def test_concurrent_pause_and_approve_one_wins():
    store, r = _run(); _answer(r)
    v, ah = r.state_version, r.pending["action_hash"]
    res = _drive_concurrent([_approve_fn(r, "ap", v, ah), lambda: r.pause("pz", v)])
    assert len([x for x in res if x[0] == "ok"]) == 1          # pause 与 approve 不能同时成功
    assert r.state in ("completed", "paused")


def test_concurrent_answer_and_stop_one_wins():
    store, r = _run()
    v = r.state_version
    answer = lambda: r.answer_clarification(HC.ClarificationAnswer(
        request_id="clr-r", expected_state_version=v, idempotency_key="an", selected_option_ids=["skin"]))
    res = _drive_concurrent([answer, lambda: r.stop("st", v)])
    assert len([x for x in res if x[0] == "ok"]) == 1          # answer 与 stop 不能同时改状态
    seqs = [e.sequence for e in store.list("r")]
    assert seqs == list(range(len(seqs)))                      # 事件 sequence 连续且唯一


class _FailingBatchStore(InMemoryEventStore):
    """可控注入 append_batch 失败，验证"写失败回滚，内存不领先于日志"。"""
    fail = False

    def append_batch(self, batch):
        if self.fail:
            raise OSError("simulated disk failure")
        super().append_batch(batch)


def test_event_write_failure_rolls_back_state():
    store = _FailingBatchStore()
    r = HitlRun("r", store); r.start()
    v, st, n = r.state_version, r.state, len(store.list("r"))
    store.fail = True
    with pytest.raises(OSError):
        _answer(r, key="a1")                                  # 落盘失败
    assert r.state == st and r.state_version == v and len(store.list("r")) == n   # 完整回滚
    store.fail = False
    _answer(r, key="a2")                                      # 恢复后仍可作答
    assert r.state == "awaiting_approval"


# ============================ A.7.5.1 §2 持久化重建（新进程/新对象） ============================
def _fresh(tmp_path):
    return JsonlEventStore(str(tmp_path))                     # "重启"= 新 store 实例读同一目录


def test_recover_awaiting_clarification_still_answerable(tmp_path):
    r = HitlRun("hitl-x", _fresh(tmp_path)); r.start()
    r2 = HitlRun.recover("hitl-x", _fresh(tmp_path))          # 新对象 + 新 store 实例
    assert r2.state == "awaiting_clarification" and r2.pending["type"] == "clarification"
    r2.answer_clarification(HC.ClarificationAnswer(request_id=r2.pending["request_id"],
        expected_state_version=r2.state_version, idempotency_key="a", selected_option_ids=["skin"]))
    assert r2.state == "awaiting_approval"


def test_recover_awaiting_approval_then_approve(tmp_path):
    r = HitlRun("hitl-y", _fresh(tmp_path)); r.start(); _answer(r)
    r2 = HitlRun.recover("hitl-y", _fresh(tmp_path))
    assert r2.state == "awaiting_approval" and r2.pending["type"] == "approval"
    r2.approve(HC.ApprovalDecision(request_id=r2.pending["request_id"],
        expected_state_version=r2.state_version, idempotency_key="ap", action_hash=r2.pending["action_hash"]))
    assert r2.state == "completed" and r2.tool_calls == 1 and len(r2.artifacts) == 1
    assert r2.lifecycle == {"requested": 1, "executed": 1, "tool_returned": 1, "observed": 1}


def test_recover_paused_from_awaiting_approval_resumes_to_awaiting_approval(tmp_path):
    r = HitlRun("hitl-z", _fresh(tmp_path)); r.start(); _answer(r); r.pause("p", r.state_version)
    r2 = HitlRun.recover("hitl-z", _fresh(tmp_path))
    assert r2.state == "paused"
    r2.resume("rz", r2.state_version)
    assert r2.state == "awaiting_approval" and r2.pending["type"] == "approval"


def test_recover_completed_is_immutable(tmp_path):
    r = HitlRun("hitl-c", _fresh(tmp_path)); r.start(); _answer(r); r.approve(_decision(r, "a"))
    r2 = HitlRun.recover("hitl-c", _fresh(tmp_path))
    assert r2.state == "completed"
    with pytest.raises(HC.IllegalTransition):
        r2.stop("s", r2.state_version)


def test_recover_stopped_is_immutable(tmp_path):
    r = HitlRun("hitl-s", _fresh(tmp_path)); r.start(); r.stop("s", r.state_version)
    r2 = HitlRun.recover("hitl-s", _fresh(tmp_path))
    assert r2.state == "stopped"
    with pytest.raises(HC.IllegalTransition):
        r2.resume("z", r2.state_version)


def test_recover_replay_old_idem_no_duplicate(tmp_path):
    r = HitlRun("hitl-d", _fresh(tmp_path)); r.start(); _answer(r, key="ans1")
    ah = r.pending["action_hash"]; r.approve(_decision(r, "apr1"))
    store2 = _fresh(tmp_path)
    n, calls, arts = len(store2.list("hitl-d")), 1, 1
    r2 = HitlRun.recover("hitl-d", store2)
    assert r2.tool_calls == calls and len(r2.artifacts) == arts
    # 重启后重放旧 idempotency_key + 同 payload → 识别为重复，不产生新事件/工具/artifact
    r2.approve(HC.ApprovalDecision(request_id="apr-hitl-d", expected_state_version=r2.state_version,
        idempotency_key="apr1", action_hash=ah))
    assert len(store2.list("hitl-d")) == n and r2.tool_calls == calls and len(r2.artifacts) == arts


def test_events_store_idem_hash_not_raw_key(tmp_path):
    store = _fresh(tmp_path)
    r = HitlRun("hitl-h", store); r.start()
    _answer(r, key="SECRET-KEY-123")
    blob = "\n".join(e.model_dump_json() for e in store.list("hitl-h"))
    assert "SECRET-KEY-123" not in blob                       # 不存原始 key
    assert idem_hash("SECRET-KEY-123") in blob                # 只存稳定 hash


def test_recover_fail_closed_on_broken_log(tmp_path):
    r = HitlRun("hitl-b", _fresh(tmp_path)); r.start(); _answer(r)
    p = os.path.join(str(tmp_path), "hitl-b.jsonl")
    lines = open(p, encoding="utf-8").read().splitlines()
    del lines[2]                                              # 删中间一条 → sequence 断裂
    open(p, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
    with pytest.raises(RecoveryError):
        HitlRun.recover("hitl-b", _fresh(tmp_path))           # fail-closed


def test_recovered_state_matches_live(tmp_path):
    r = HitlRun("hitl-m", _fresh(tmp_path)); r.start(); _answer(r)
    r2 = HitlRun.recover("hitl-m", _fresh(tmp_path))
    assert (r2.state, r2.state_version, r2.pending["action_hash"], r2._answer) == \
           (r.state, r.state_version, r.pending["action_hash"], r._answer)


# ============================ A.7.5.1 §4 执行中协作式 Pause ============================
def _approve_async(r, key="ap"):
    return r.approve(HC.ApprovalDecision(request_id="apr-r", expected_state_version=r.state_version,
        idempotency_key=key, action_hash=r.pending["action_hash"]))


def test_inflight_pause_completes_current_call_then_pauses():
    gate = threading.Event()
    store = InMemoryEventStore()
    r = HitlRun("r", store, exec_gate=gate); r.start(); _answer(r)
    _approve_async(r)                                         # 异步：worker 在工具阶段阻塞于 gate
    assert _wait(lambda: r._open_reservations == 1)          # 已开始的在途调用
    assert r.state == "running"
    r.pause("pz", r.state_version)                            # pause 到达 → 先 pausing（不杀调用）
    assert r.state == "pausing"
    gate.set()                                               # 在途调用返回 → 边界后进入 paused
    r.join_worker()
    assert r.state == "paused"
    assert r.tool_calls == 1                                 # 在途调用完成一次
    assert len(r.artifacts) == 0                             # 下一阶段未执行
    assert r._open_reservations == 0                         # 无悬挂预留
    # resume → 从下一未完成阶段继续，不重复已完成阶段
    r.resume("rz", r.state_version)
    r.join_worker()
    assert r.state == "completed" and r.tool_calls == 1 and len(r.artifacts) == 1
    t = [e.event_type for e in store.list("r")]
    assert t.count("tool_started") == 1 and t.count("artifact_created") == 1 and t.count("run_completed") == 1
    seqs = [e.sequence for e in store.list("r")]
    assert seqs == list(range(len(seqs)))                    # 事件不重复、连续唯一


def test_stop_during_pausing_stays_stopped():
    gate = threading.Event()
    store = InMemoryEventStore()
    r = HitlRun("r", store, exec_gate=gate); r.start(); _answer(r)
    _approve_async(r)
    assert _wait(lambda: r._open_reservations == 1)
    r.pause("pz", r.state_version)
    assert r.state == "pausing"
    r.stop("st", r.state_version)                            # Stop 在 pausing 到达
    assert r.state == "stopped"
    gate.set()                                               # 随后完成的调用不得把状态改回
    r.join_worker()
    assert r.state == "stopped"                              # 终态稳定
    assert r.tool_calls == 0 and len(r.artifacts) == 0       # 干净丢弃
    assert r._open_reservations == 0


def test_inflight_pause_no_duplicate_charge_across_pause():
    gate = threading.Event()
    store = InMemoryEventStore()
    r = HitlRun("r", store, exec_gate=gate); r.start(); _answer(r)
    _approve_async(r)
    assert _wait(lambda: r._open_reservations == 1)
    r.pause("pz", r.state_version); gate.set(); r.join_worker()
    lc_paused = dict(r.lifecycle)
    r.resume("rz", r.state_version); r.join_worker()
    # 恢复后完成：每个 lifecycle 计数恰好 1（无重复扣费/重复调用）
    assert r.lifecycle == {"requested": 1, "executed": 1, "tool_returned": 1, "observed": 1}
    assert lc_paused["executed"] == 1                        # 暂停时工具已执行一次（不再重复）
