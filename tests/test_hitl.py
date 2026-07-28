"""A.7.5 —— 人机协作控制（Clarification/Approval/Pause/Resume）状态机 + 契约测试。

Deterministic fake：零付费模型、零网络、零设备、零任意代码。验证 §1/§3/§4/§5/§6/§10。
"""
import json

import pytest

from pilot.event_store import InMemoryEventStore
from pilot.hitl import HitlRun, rebuild_state_from_events
from pilot import hitl_contracts as HC

pytestmark = pytest.mark.unit


def _run(rid="r"):
    store = InMemoryEventStore()
    r = HitlRun(rid, store.append)
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
    r.approve(_decision(r, "apX"))
    calls, arts = r.tool_calls, len(r.artifacts)
    # second approve with same idempotency_key → cached (no second execution)
    r.approve(HC.ApprovalDecision(request_id="apr-r", expected_state_version=r.state_version,
        idempotency_key="apX", action_hash=r._pending_obj.action_hash if r._pending_obj else "x"))
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
