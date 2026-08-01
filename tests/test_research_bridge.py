"""A.7.5.3 —— 参数化 Research Run 桥接测试（§12）。

全部使用 fake executor：零付费模型、零外部网络、零代码执行、零设备、零 Planner/ReAct。
覆盖 §12.1 桥接 / 12.2 三角色链 / 12.3 冻结与篡改 / 12.4 幂等并发 / 12.5 重启恢复 / 12.6 pause·stop。
"""
import json
import os
import threading

import pytest

from pilot.event_store import InMemoryEventStore, JsonlEventStore
from pilot.hitl import HitlRun, RecoveryError
from pilot import hitl_contracts as HC
from pilot import research_contracts as RC
from pilot.fake_research_executor import (FakeResearchExecutor, EXECUTOR_ID, STAGES,
                                          build_default_spec, fixture_evidence_refs)

pytestmark = pytest.mark.unit


# ------------------------------ helpers ------------------------------
def _mk(rid="hitl-research-t", *, gates=None, store=None, question=None):
    store = store or InMemoryEventStore()
    ex = FakeResearchExecutor(stage_gates=gates)
    spec = build_default_spec(question=question)
    r = HitlRun(rid, store, spec=spec, executor=ex)
    r.start()
    return store, ex, spec, r


def _answer(r, opt="strict_causal", key="a"):
    return r.answer_clarification(HC.ClarificationAnswer(
        request_id=r.pending["request_id"], expected_state_version=r.state_version,
        idempotency_key=key, selected_option_ids=[opt]))


def _approve(r, key="ap", action_hash=None):
    """A.7.5.3.1：approve 立即返回 running；阶段由后台 worker 推进。"""
    return r.approve(HC.ApprovalDecision(
        request_id=r.pending["request_id"], expected_state_version=r.state_version,
        idempotency_key=key, action_hash=action_hash or r.pending["action_hash"]))


def _settle(r, timeout=20):
    """等待后台 research worker 结束（异步语义下断言终态前必须调用）。"""
    r.join_worker(timeout)
    return r


def _wait(pred, timeout=5.0):
    end = threading.Event()
    for _ in range(int(timeout / 0.005)):
        if pred():
            return True
        end.wait(0.005)
    return False


def _types(store, rid):
    return [e.event_type for e in store.list(rid)]


def _seq_ok(store, rid):
    s = [e.sequence for e in store.list(rid)]
    return s == list(range(len(s)))


# ============================ §12.1 桥接 ============================
def test_research_question_and_clarification_come_from_spec_not_hardcoded():
    store, ex, spec, r = _mk(question="自定义研究问题：证据是否支持直接因果？")
    assert r.run_type == "research"
    assert r.pending["type"] == "clarification"
    assert r.pending["prompt"].startswith("你希望结论采用哪种证据标准")
    assert {o["id"] for o in r.pending["allowed_options"]} == {
        "strict_causal", "mechanistic_hypothesis", "association_only"}
    assert r.pending["recommended"] == "strict_causal"
    # 不再是写死的 IL-6 / 组织来源
    blob = json.dumps(r.pending, ensure_ascii=False)
    assert "组织来源" not in blob and "皮肤成纤维细胞" not in blob


def test_zero_executor_calls_before_clarification_answered():
    store, ex, spec, r = _mk()
    assert sum(ex.stage_counts().values()) == 0 and ex.model_call_count() == 0
    assert r.tool_calls == 0


def test_zero_executor_calls_before_approval():
    store, ex, spec, r = _mk()
    _answer(r)
    assert r.state == "awaiting_approval"
    assert sum(ex.stage_counts().values()) == 0 and ex.model_call_count() == 0


def test_approve_runs_executor_once_and_completes():
    store, ex, spec, r = _mk()
    _answer(r); _approve(r); _settle(r)
    assert r.state == "completed"
    assert ex.stage_counts() == {s: 1 for s in STAGES}
    assert ex.artifacts_built == 1 and len(r.artifacts) == 1


def test_deny_runs_no_executor_and_no_artifact():
    store, ex, spec, r = _mk()
    _answer(r)
    r.deny(HC.ApprovalDecision(request_id=r.pending["request_id"],
                               expected_state_version=r.state_version,
                               idempotency_key="d", action_hash=r.pending["action_hash"]))
    assert r.state == "stopped"
    assert sum(ex.stage_counts().values()) == 0 and len(r.artifacts) == 0
    assert "artifact_created" not in _types(store, "hitl-research-t")


def test_unregistered_executor_fail_closed():
    with pytest.raises(RC.ExecutorNotRegistered):
        RC.get_executor("no-such-executor")


def test_research_requires_both_spec_and_executor_no_fallback():
    store = InMemoryEventStore()
    with pytest.raises(HC.ContractViolation):
        HitlRun("hitl-research-x", store, spec=build_default_spec(), executor=None)
    with pytest.raises(HC.ContractViolation):
        HitlRun("hitl-research-y", store, spec=None, executor=FakeResearchExecutor())


def test_executor_id_must_match_spec():
    store = InMemoryEventStore()
    spec = build_default_spec(executor_id="some-other-id")
    with pytest.raises(HC.ContractViolation):
        HitlRun("hitl-research-z", store, spec=spec, executor=FakeResearchExecutor())


def test_demo_run_behaviour_unchanged():
    """demo run 完全不受影响：仍是写死的组织来源澄清 + fake wet-lab action。"""
    store = InMemoryEventStore()
    d = HitlRun("hitl-demo", store); d.start()
    assert d.run_type == "demo"
    assert {o["id"] for o in d.pending["allowed_options"]} == {"skin", "lung", "both"}
    d.answer_clarification(HC.ClarificationAnswer(request_id=d.pending["request_id"],
        expected_state_version=d.state_version, idempotency_key="a", selected_option_ids=["skin"]))
    assert d.pending["tool_name"] == "simulate_wetlab_package"
    d.approve(HC.ApprovalDecision(request_id=d.pending["request_id"],
        expected_state_version=d.state_version, idempotency_key="p",
        action_hash=d.pending["action_hash"]))
    assert d.state == "completed" and d.tool_calls == 1
    assert "research_stage_completed" not in _types(store, "hitl-demo")


# ============================ §12.2 三角色链 ============================
def test_three_roles_called_once_each_in_order():
    store, ex, spec, r = _mk()
    _answer(r); _approve(r); _settle(r)
    assert ex.role_counts() == {"synthesizer": 1, "verifier": 1, "claim_extractor": 1}
    done = [e.safe_payload["stage"] for e in store.list("hitl-research-t")
            if e.event_type == "research_stage_completed"]
    assert done == list(STAGES)                      # 顺序正确、每阶段一次


def test_no_planner_react_network_code_device():
    store, ex, spec, r = _mk()
    _answer(r); _approve(r); _settle(r)
    assert ex.forbidden_counts() == {"planner": 0, "react_executor": 0, "network": 0,
                                     "code_execution": 0, "device": 0}


def test_claims_only_cite_existing_evidence_ids():
    store, ex, spec, r = _mk()
    _answer(r)
    art_ids = {e.evidence_id for e in spec.evidence_refs}
    _approve(r); _settle(r)
    art = ex.build_artifact.__self__  # noqa: F841 - executor kept for clarity
    claims = r._research_state["claims"]
    for c in claims:
        cited = set(c.supporting_evidence_ids) | set(c.contradicting_evidence_ids) | set(c.unresolved_evidence_ids)
        assert cited <= art_ids


def test_shadow_creates_no_evidence_and_does_not_override_verifier():
    store, ex, spec, r = _mk()
    _answer(r); _approve(r); _settle(r)
    st = r._research_state
    assert st["shadow_created_evidence"] == 0
    assert st["shadow_overrode_verifier"] is False
    assert st["verifier_is_final"] is True
    # Verifier 裁决保留；无干预/纵向证据 → 因果封顶
    assert st["verifier_verdict"] == "insufficient_evidence"
    assert st["causal_tier"] == "insufficient_for_direct_causality"


def test_policy_forbids_privileged_flags():
    p = RC.ResearchExecutionPolicy(allow_network=True)
    with pytest.raises(RC.ResearchContractError):
        p.assert_zero_paid_stage()


def test_role_limits_cannot_exceed_one():
    with pytest.raises(Exception):
        RC.ResearchExecutionPolicy(role_limits={"synthesizer": 2})


# ============================ §12.3 冻结与篡改 ============================
def test_post_approval_question_change_rejected():
    gate = threading.Event()
    store, ex, spec, r = _mk(gates={"synthesizer": gate})
    _answer(r); _approve(r)
    idx = STAGES.index("synthesizer")
    assert _wait(lambda: r._exec_cursor == idx and r._open_reservations == 1, timeout=20)
    object.__setattr__(r._spec, "question", "被篡改的问题")     # 批准后改问题
    gate.set()
    r.join_worker(10)
    assert r.state != "completed"                                # 拒绝执行，不得完成
    assert len(r.artifacts) == 0


def test_post_approval_evidence_change_rejected():
    store, ex, spec, r = _mk()
    _answer(r)
    r._frozen_plan = r._freeze_plan()                            # 模拟已冻结
    spec.evidence_refs.pop()                                     # 批准后改证据
    with pytest.raises(HC.ContractViolation):
        r._assert_plan_unchanged()


def test_post_approval_executor_change_rejected():
    store, ex, spec, r = _mk()
    _answer(r)
    r._frozen_plan = r._freeze_plan()
    r._executor = FakeResearchExecutor()
    r._executor.executor_id = "swapped"
    with pytest.raises(HC.ContractViolation):
        r._assert_plan_unchanged()


def test_post_approval_policy_change_rejected():
    store, ex, spec, r = _mk()
    _answer(r)
    r._frozen_plan = r._freeze_plan()
    spec.execution_policy.max_model_calls = 0                    # 批准后改策略
    with pytest.raises(HC.ContractViolation):
        r._assert_plan_unchanged()


def test_action_hash_mismatch_rejected():
    store, ex, spec, r = _mk()
    _answer(r)
    with pytest.raises(HC.ContractViolation):
        _approve(r, action_hash="WRONG")
    assert sum(ex.stage_counts().values()) == 0


def test_unknown_schema_and_fields_rejected():
    with pytest.raises(Exception):
        RC.ResearchRunSpec.model_validate({"schema_version": "research-run-v99", "run_type": "research",
                                           "question": "q", "executor_id": EXECUTOR_ID,
                                           "clarification": {"question": "c"}, "approval":
                                           {"action_summary": "a", "expected_side_effect": "b"}})
    base = build_default_spec().model_dump(mode="json")
    base["evil_injected_model"] = {"client": "anthropic"}         # 客户端注入内部对象
    with pytest.raises(Exception):
        RC.ResearchRunSpec.model_validate(base)


def test_oversized_question_rejected():
    with pytest.raises(Exception):
        build_default_spec(question="x" * (RC.QUESTION_MAX + 1))


def test_evidence_tamper_detected_by_executor():
    """A.7.5.3.1：篡改证据 → validate_evidence 阶段失败 → fail-closed 终态 failed（异步收敛）。"""
    store, ex, spec, r = _mk()
    spec.evidence_refs[0].content_hash = "tampered"
    _answer(r); _approve(r); _settle(r)
    assert r.state == "failed" and r.needs_human_review is True
    assert len(r.artifacts) == 0
    t = _types(store, "hitl-research-t")
    assert t.count("research_stage_failed") == 1 and t.count("run_failed") == 1
    assert "artifact_created" not in t and "run_completed" not in t
    assert r.primary_failure["failed_stage"] == "validate_evidence"


# ============================ §12.4 幂等与并发 ============================
def test_concurrent_approve_only_one_succeeds():
    for i in range(20):
        store, ex, spec, r = _mk(rid=f"hitl-research-c{i}")
        _answer(r)
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
            t.join(10)
        _settle(r)
        assert len([x for x in res if x[0] == "ok"]) == 1
        assert ex.artifacts_built == 1 and len(r.artifacts) == 1
        assert ex.role_counts() == {"synthesizer": 1, "verifier": 1, "claim_extractor": 1}
        assert _seq_ok(store, f"hitl-research-c{i}")


def test_same_idem_key_same_payload_no_double_execution():
    store, ex, spec, r = _mk()
    _answer(r)
    ah = r.pending["action_hash"]
    _approve(r, key="k", action_hash=ah); _settle(r)
    n = len(store.list("hitl-research-t"))
    r.approve(HC.ApprovalDecision(request_id=f"apr-hitl-research-t", expected_state_version=r.state_version,
                                  idempotency_key="k", action_hash=ah))
    assert len(store.list("hitl-research-t")) == n
    assert ex.artifacts_built == 1 and ex.role_counts()["synthesizer"] == 1


def test_same_key_different_payload_conflicts():
    store, ex, spec, r = _mk()
    _answer(r)
    v, ah = r.state_version, r.pending["action_hash"]
    _approve(r, key="k", action_hash=ah)
    with pytest.raises((HC.ContractViolation, HC.StaleState)):
        r.approve(HC.ApprovalDecision(request_id="apr-hitl-research-t", expected_state_version=v,
                                      idempotency_key="k", action_hash="different"))


def test_artifact_and_version_increment_once():
    store, ex, spec, r = _mk()
    _answer(r)
    v = r.state_version
    _approve(r); _settle(r)
    assert len([t for t in _types(store, "hitl-research-t") if t == "artifact_created"]) == 1
    assert r.state_version > v and _seq_ok(store, "hitl-research-t")


def test_two_research_runs_not_serialized_by_global_lock():
    s1, e1, sp1, r1 = _mk("hitl-research-p1")
    s2, e2, sp2, r2 = _mk("hitl-research-p2")
    _answer(r1); _answer(r2)
    out, barrier = [None, None], threading.Barrier(2)

    def go(n, run, key):
        barrier.wait()
        try:
            out[n] = ("ok", _approve(run, key=key))
        except Exception as e:      # noqa: BLE001
            out[n] = ("err", e)
    ts = [threading.Thread(target=go, args=(0, r1, "x")), threading.Thread(target=go, args=(1, r2, "y"))]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    _settle(r1); _settle(r2)
    assert all(o[0] == "ok" for o in out)
    assert r1.state == "completed" and r2.state == "completed"


# ============================ §12.5 重启恢复 ============================
def _fresh(tmp_path):
    return JsonlEventStore(str(tmp_path))


def test_recover_awaiting_clarification(tmp_path):
    store, ex, spec, r = _mk("hitl-research-r1", store=_fresh(tmp_path))
    r2 = HitlRun.recover("hitl-research-r1", _fresh(tmp_path), spec=spec, executor=FakeResearchExecutor())
    assert r2.run_type == "research" and r2.state == "awaiting_clarification"
    assert r2.pending["type"] == "clarification"


def test_recover_awaiting_approval_and_can_approve(tmp_path):
    store, ex, spec, r = _mk("hitl-research-r2", store=_fresh(tmp_path))
    _answer(r)
    ex2 = FakeResearchExecutor()
    r2 = HitlRun.recover("hitl-research-r2", _fresh(tmp_path), spec=spec, executor=ex2)
    assert r2.state == "awaiting_approval" and r2.pending["type"] == "approval"
    snap = r2.snapshot()["research"]
    assert snap["executor_id"] == EXECUTOR_ID
    _approve(r2); _settle(r2)
    assert r2.state == "completed" and ex2.artifacts_built == 1


def test_recover_completed_is_immutable(tmp_path):
    store, ex, spec, r = _mk("hitl-research-r3", store=_fresh(tmp_path))
    _answer(r); _approve(r); _settle(r)
    r2 = HitlRun.recover("hitl-research-r3", _fresh(tmp_path), spec=spec, executor=FakeResearchExecutor())
    assert r2.state == "completed"
    snap = r2.snapshot()["research"]
    assert snap["stages_done"] == list(STAGES) and snap["verifier_verdict"] == "insufficient_evidence"
    with pytest.raises(HC.IllegalTransition):
        r2.stop("s", r2.state_version)


def test_recover_replay_old_idem_no_duplicate(tmp_path):
    store, ex, spec, r = _mk("hitl-research-r4", store=_fresh(tmp_path))
    _answer(r)
    ah = r.pending["action_hash"]
    _approve(r, key="apK", action_hash=ah); _settle(r)
    st2 = _fresh(tmp_path)
    n = len(st2.list("hitl-research-r4"))
    ex2 = FakeResearchExecutor()
    r2 = HitlRun.recover("hitl-research-r4", st2, spec=spec, executor=ex2)
    r2.approve(HC.ApprovalDecision(request_id="apr-hitl-research-r4",
                                   expected_state_version=r2.state_version,
                                   idempotency_key="apK", action_hash=ah))
    assert len(st2.list("hitl-research-r4")) == n
    assert ex2.artifacts_built == 0 and len(r2.artifacts) == 1     # 无重复执行/重复产物


def test_recovered_research_without_spec_cannot_execute(tmp_path):
    store, ex, spec, r = _mk("hitl-research-r5", store=_fresh(tmp_path))
    _answer(r)
    r2 = HitlRun.recover("hitl-research-r5", _fresh(tmp_path))      # 无 spec/executor
    assert r2.run_type == "research" and r2.state == "awaiting_approval"
    with pytest.raises(HC.ContractViolation):                      # fail-closed，不回退 demo
        _approve(r2)


def test_recover_corrupt_log_fail_closed(tmp_path):
    store, ex, spec, r = _mk("hitl-research-r6", store=_fresh(tmp_path))
    _answer(r)
    p = os.path.join(str(tmp_path), "hitl-research-r6.jsonl")
    data = open(p, encoding="utf-8").read()
    open(p, "w", encoding="utf-8").write(data[:-25])               # 截断
    with pytest.raises(RecoveryError):
        HitlRun.recover("hitl-research-r6", _fresh(tmp_path), spec=spec, executor=FakeResearchExecutor())


def test_legacy_events_without_run_type_stay_demo(tmp_path):
    store = _fresh(tmp_path)
    d = HitlRun("hitl-legacy", store); d.start()                   # demo（事件无 run_type）
    r2 = HitlRun.recover("hitl-legacy", _fresh(tmp_path))
    assert r2.run_type == "demo"                                   # 不擅自升级为 research


# ============================ §12.6 Pause / Resume / Stop ============================
def _gated(stage, rid="hitl-research-g"):
    """跑到**指定阶段**正在在途阻塞时返回（不是任意阶段），避免慢机器上的竞态。"""
    gate = threading.Event()
    idx = STAGES.index(stage)
    store, ex, spec, r = _mk(rid, gates={stage: gate})
    _answer(r); _approve(r)
    # 门控阶段会一直阻塞 → 游标停在 idx 且预留为 1；据此判定"正好卡在该阶段"
    assert _wait(lambda: r._exec_cursor == idx and r._open_reservations == 1, timeout=20), \
        f"worker 未在 {stage} 阶段就位（cursor={r._exec_cursor}）"
    return store, ex, r, gate


@pytest.mark.parametrize("stage", ["synthesizer", "verifier", "claim_extractor"])
def test_pause_between_stages_then_resume(stage):
    """合作式暂停：已开始的阶段**跑完**后才进入 paused；下一阶段不得启动。"""
    idx = STAGES.index(stage)
    store, ex, r, gate = _gated(stage, rid=f"hitl-research-{stage}")
    r.pause("p", r.state_version)
    gate.set()                                          # 在途阶段返回（不被强杀）
    r.join_worker(10)
    assert r.state == "paused"
    assert len(r._stages_done) == idx + 1               # 当前阶段完成后停在边界
    assert r._stages_done == list(STAGES[:idx + 1])
    assert ex.stage_counts()[STAGES[idx + 1]] == 0      # 下一阶段确实未启动
    assert len(r.artifacts) == 0
    assert r._open_reservations == 0
    r.resume("rz", r.state_version)
    r.join_worker(10)
    assert r.state == "completed"
    assert ex.stage_counts() == {s: 1 for s in STAGES}  # 已完成阶段不重复
    assert ex.artifacts_built == 1 and len(r.artifacts) == 1
    assert _seq_ok(store, f"hitl-research-{stage}")


def test_stop_during_pausing_wins_and_late_stage_cannot_revive():
    store, ex, r, gate = _gated("synthesizer", rid="hitl-research-stop")
    r.pause("p", r.state_version)
    r.stop("s", r.state_version)
    assert r.state == "stopped"
    gate.set()
    r.join_worker(10)
    assert r.state == "stopped"                         # 迟到阶段完成不得改回
    assert len(r.artifacts) == 0 and ex.artifacts_built == 0
    assert r._open_reservations == 0
    assert "artifact_created" not in _types(store, "hitl-research-stop")


def test_pausing_crash_recovery_needs_human_review(tmp_path):
    gate = threading.Event()
    store, ex, spec, r = _mk("hitl-research-pc", gates={"synthesizer": gate}, store=_fresh(tmp_path))
    _answer(r); _approve(r)
    idx = STAGES.index("synthesizer")
    assert _wait(lambda: r._exec_cursor == idx and r._open_reservations == 1, timeout=20)
    r.pause("p", r.state_version)
    assert r.state == "pausing"
    r2 = HitlRun.recover("hitl-research-pc", _fresh(tmp_path), spec=spec, executor=FakeResearchExecutor())
    assert r2.state == "pausing" and r2.needs_human_review is True   # 不自动重放、不自动 running
    # 收尾：先终止原 run（迟到阶段会被 stale-worker 守卫丢弃、不再写盘），再放行并 join，
    # 否则 worker 可能在测试结束后写入已删除的 tmp_path（Windows 上会导致清理失败）。
    r.stop("s", r.state_version)
    gate.set()
    r.join_worker(10)
    assert r.state == "stopped" and len(r.artifacts) == 0


# ============================ §12.8 安全 ============================
def test_events_contain_no_secrets_or_paths():
    store, ex, spec, r = _mk()
    _answer(r); _approve(r); _settle(r)
    blob = "\n".join(e.model_dump_json() for e in store.list("hitl-research-t")).lower()
    for bad in ("api_key", "authorization", "cookie", "bearer", "patient", ".env",
                "c:\\users", "/home/", "sk-"):
        assert bad not in blob
    from pilot.runtime_events import SAFE_PAYLOAD_KEYS
    for e in store.list("hitl-research-t"):
        for k in e.safe_payload:
            assert k in SAFE_PAYLOAD_KEYS


def test_fixture_evidence_clearly_marked_not_real():
    refs = fixture_evidence_refs()
    assert refs and all(x.fixture for x in refs)
    for x in refs:
        assert x.evidence_id.startswith("FIXTURE-")     # 明显测试 ID，不伪装真实 PMID/DOI
        assert not x.evidence_id.isdigit()


def test_artifact_marked_fixture_and_claims_validated():
    store, ex, spec, r = _mk()
    _answer(r); _approve(r); _settle(r)
    art = ex.build_artifact(ctx=r._research_ctx(), state=r._research_state)
    assert art.fixture is True and art.schema_version == "research-artifact-v1"
    art.assert_claims_cite_known_evidence()
    bad = art.model_copy(update={"claims": [art.claims[0].model_copy(
        update={"supporting_evidence_ids": ["NOT-A-REAL-ID"]})]})
    with pytest.raises(RC.ResearchContractError):
        bad.assert_claims_cite_known_evidence()
