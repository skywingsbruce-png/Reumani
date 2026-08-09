"""A.8.2a.3 §6 —— 从真实 HITL 入口验证「批准之后才 resolve」。

不直接调用候选工厂：从 HitlRun（RunManager 使用的同一类）起步，
证明 factory/resolve/provider 计数在批准前恒为 0。全部离线，真实付费调用为 0。
"""
import os
import pathlib
import tempfile

import pytest

from pilot.event_store import InMemoryEventStore
from pilot import hitl_contracts as HC
from pilot.hitl import HitlRun
from pilot.hard_gate import HardBudgetGate, GatedModel, ENV_PAID, ENV_CONFIRM
from pilot.frozen_evidence import FrozenEvidenceLoader
from pilot.approval_grant import ApprovalGrant, ApprovalGrantError, issue_grant, _ISSUER
from pilot.controlled_runtime import build_controlled_runtime_registry
from pilot.deferred_research_executor import DeferredRegistryResearchExecutor
from pilot.role_contracts import ANTHROPIC_OPUS_48, DEEPSEEK_V4_FLASH
from tests.test_live_output_wiring import SpyChat
from tests.test_gated_research_executor import SYN, VER, CLM, make_spec

pytestmark = pytest.mark.unit
REPO = str(pathlib.Path(__file__).resolve().parent.parent)
CAPS = {"claude-opus-4-8": ANTHROPIC_OPUS_48, "deepseek-v4-flash": DEEPSEEK_V4_FLASH}


@pytest.fixture(autouse=True)
def _switches(monkeypatch):
    monkeypatch.setenv(ENV_PAID, "1")
    monkeypatch.setenv(ENV_CONFIRM, "A82a3")
    monkeypatch.delenv("CI", raising=False)


def build_stack(store=None):
    """store 先建：Deferred executor 需要一个**只读**的 approval_granted 查询口。"""
    store = store or InMemoryEventStore()

    def approval_event_lookup(run_id, sequence):
        for e in store.list(run_id):
            if e.sequence == sequence:
                return e
        return None

    gate = HardBudgetGate(stage="A82a3", ledger_path=os.path.join(tempfile.mkdtemp(), "l.jsonl"),
                          max_usd_global=.18, max_usd_stage=.18, max_usd_task=.18,
                          max_calls_global=3, max_calls_task=3,
                          max_calls_per_model={"claude-opus-4-8": 2, "deepseek-v4-flash": 1},
                          max_calls_per_role={"synthesizer": 1, "verifier": 1,
                                              "claim_extractor": 1},
                          task_timeout_s=60, max_retries=0, default_max_tokens=1600,
                          allow_ci=True)
    calls = {}
    payloads = {"synthesizer": SYN, "verifier": VER, "claim_extractor": CLM}

    def model_factory(spec, g):
        calls[spec.role] = calls.get(spec.role, 0) + 1
        return GatedModel(SpyChat(payloads[spec.role]), g, role=spec.role,
                          model_id=spec.model_id, max_tokens=spec.max_tokens)

    reg = build_controlled_runtime_registry(gate=gate, model_factory=model_factory)
    ex = DeferredRegistryResearchExecutor(registry=reg, gate=gate,
                                          evidence_loader=FrozenEvidenceLoader(REPO),
                                          capabilities=CAPS,
                                          approval_event_lookup=approval_event_lookup)
    return reg, gate, calls, ex, store


def start_run(ex, rid, store=None):
    store = store or InMemoryEventStore()
    r = HitlRun(rid, store, spec=make_spec(), executor=ex)
    r.start()
    r.answer_clarification(HC.ClarificationAnswer(
        request_id=r.pending["request_id"], expected_state_version=r.state_version,
        idempotency_key="a", selected_option_ids=["strict_causal"]))
    return store, r


def counts(reg, calls, ex):
    return calls.copy(), reg.resolved_count(), ex.model_call_count()


# ---------------------------------------------------------------- 批准前恒为 0
def test_preview_and_approval_card_need_no_resolve():
    reg, _, calls, ex, store0 = build_stack()
    store, r = start_run(ex, store=store0, rid="hitl-def-preview")
    assert r.state == "awaiting_approval"
    facts = r.pending["frozen_facts"]
    assert facts["core_evidence_count"] == 6 and facts["preview_hash"]
    assert counts(reg, calls, ex) == ({}, 0, 0)          # factory/resolved/provider 全 0


def test_wrong_action_hash_expired_version_and_deny_never_resolve():
    for mode in ("bad_hash", "stale_version", "deny"):
        reg, _, calls, ex, store0 = build_stack()
        store, r = start_run(ex, store=store0, rid=f"hitl-def-{mode}")
        rid_, sv = r.pending["request_id"], r.state_version
        with pytest.raises(Exception):
            if mode == "bad_hash":
                r.approve(HC.ApprovalDecision(request_id=rid_, expected_state_version=sv,
                                              idempotency_key="k", action_hash="0" * 64))
            elif mode == "stale_version":
                r.approve(HC.ApprovalDecision(request_id=rid_, expected_state_version=sv + 5,
                                              idempotency_key="k",
                                              action_hash=r.pending["action_hash"]))
            else:
                r.deny(HC.ApprovalDecision(request_id=rid_, expected_state_version=sv,
                                           idempotency_key="k",
                                           action_hash=r.pending["action_hash"]))
                raise RuntimeError("deny 已完成（用异常统一收口）")
        assert counts(reg, calls, ex) == ({}, 0, 0), mode
        assert not ex.authorized


def test_unauthorized_run_stage_fails_closed():
    reg, _, calls, ex, store0 = build_stack()
    from pilot.research_contracts import ResearchRunContext
    ctx = ResearchRunContext(run_id="r", question="q", question_hash="h",
                             clarification_answer="strict_causal", answer_hash="a")
    with pytest.raises(ApprovalGrantError):
        ex.run_stage(stage="validate_evidence", ctx=ctx, state={})
    assert counts(reg, calls, ex) == ({}, 0, 0)


# ---------------------------------------------------------------- 批准后才 resolve
def test_approval_issues_grant_then_resolves_each_role_once():
    reg, gate, calls, ex, store0 = build_stack()
    store, r = start_run(ex, store=store0, rid="hitl-def-ok")
    assert counts(reg, calls, ex) == ({}, 0, 0)
    r.approve(HC.ApprovalDecision(request_id=r.pending["request_id"],
                                  expected_state_version=r.state_version,
                                  idempotency_key="ap",
                                  action_hash=r.pending["action_hash"]))
    r.join_worker(60)
    assert r.state == "completed"
    assert isinstance(r.approval_grant, ApprovalGrant)
    assert ex.authorized
    assert calls == {"synthesizer": 1, "verifier": 1, "claim_extractor": 1}
    assert reg.resolved_count() == 3
    assert ex.role_calls == {"synthesizer": 1, "verifier": 1, "claim_extractor": 1}
    assert gate.calls_by_role.get("claim_extractor") == 1     # 角色计量独立
    assert not gate.ledger.open_reservations() and gate.retries == 0
    # resolve 发生在第一个模型阶段之前，而不是服务启动时
    assert ex.resolved_at_stage == "validate_evidence"


def test_event_order_approval_granted_precedes_any_model_call():
    reg, _, calls, ex, store0 = build_stack()
    store, r = start_run(ex, store=store0, rid="hitl-def-order")
    r.approve(HC.ApprovalDecision(request_id=r.pending["request_id"],
                                  expected_state_version=r.state_version,
                                  idempotency_key="ap",
                                  action_hash=r.pending["action_hash"]))
    r.join_worker(60)
    types = [e.event_type for e in store.list("hitl-def-order")]
    i_req = types.index("approval_requested")
    i_gr = types.index("approval_granted")
    i_first_stage = types.index("research_stage_started")
    assert i_req < i_gr < i_first_stage       # provider 解析只可能在首个阶段之后
    assert "run_completed" in types


def test_grant_binds_the_real_approval_facts():
    reg, _, _, ex, store0 = build_stack()
    store, r = start_run(ex, store=store0, rid="hitl-def-bind")
    facts = r.pending["frozen_facts"]
    req_id, ah, sv = (r.pending["request_id"], r.pending["action_hash"], r.state_version)
    r.approve(HC.ApprovalDecision(request_id=req_id, expected_state_version=sv,
                                  idempotency_key="ap", action_hash=ah))
    r.join_worker(60)
    g = r.approval_grant
    assert g.run_id == "hitl-def-bind" and g.request_id == req_id
    assert g.action_hash == ah
    assert g.preview_hash == facts["preview_hash"]
    assert g.executor_id == "gated-research-v1"
    assert g.policy_id == "research-budget-policy-v2"
    assert g.granted_event_sequence >= 0


def test_forged_grant_is_rejected_by_field_revalidation():
    """凭证在同进程内可被构造，但**通不过字段比对** —— 这才是真正的防线。"""
    reg, _, calls, ex, store0 = build_stack()
    forged = issue_grant(_ISSUER, run_id="x", request_id="x", action_hash="a" * 64,
                         preview_hash="b" * 64, request_state_version=1,
                         granted_state_version=2, executor_id="gated-research-v1",
                         policy_id="research-budget-policy-v2", granted_event_sequence=0)
    with pytest.raises(ApprovalGrantError):
        ex.authorize(forged)                     # preview_hash 不符
    assert counts(reg, calls, ex) == ({}, 0, 0)
    # 直接构造（绕过签发口）同样被拒
    with pytest.raises(ApprovalGrantError):
        issue_grant(object(), run_id="x", request_id="x", action_hash="a",
                    preview_hash="b", request_state_version=1, granted_state_version=2,
                    executor_id="e", policy_id="p", granted_event_sequence=0)


def test_repeated_approve_does_not_reconstruct_clients():
    reg, _, calls, ex, store0 = build_stack()
    store, r = start_run(ex, store=store0, rid="hitl-def-idem")
    dec = HC.ApprovalDecision(request_id=r.pending["request_id"],
                              expected_state_version=r.state_version,
                              idempotency_key="ap", action_hash=r.pending["action_hash"])
    r.approve(dec)
    r.approve(dec)                               # 幂等重放
    r.join_worker(60)
    assert calls == {"synthesizer": 1, "verifier": 1, "claim_extractor": 1}
    assert reg.resolved_count() == 3


# ---------------------------------------------------------------- 恢复语义（最保守）
def test_recovered_run_does_not_auto_reconstruct_authorization():
    """重启后不得仅凭历史 approval_granted 字符串自动授权。"""
    reg, _, calls, ex, store0 = build_stack()
    store, r = start_run(ex, store=store0, rid="hitl-def-recover")
    r.approve(HC.ApprovalDecision(request_id=r.pending["request_id"],
                                  expected_state_version=r.state_version,
                                  idempotency_key="ap",
                                  action_hash=r.pending["action_hash"]))
    r.join_worker(60)
    # 新进程/新 executor：历史日志里有 approval_granted，但授权不得被自动重建
    reg2, _, calls2, ex2, store2 = build_stack()
    assert not ex2.authorized
    assert counts(reg2, calls2, ex2) == ({}, 0, 0)
    from pilot.research_contracts import ResearchRunContext
    ctx = ResearchRunContext(run_id="hitl-def-recover", question="q", question_hash="h",
                             clarification_answer="strict_causal", answer_hash="a")
    with pytest.raises(ApprovalGrantError):
        ex2.run_stage(stage="validate_evidence", ctx=ctx, state={})
    assert counts(reg2, calls2, ex2) == ({}, 0, 0)


# ---------------------------------------------------------------- §7 逐字段 / 事件失败注入
def _good_grant(ex, r):
    """取一份与当前 binding 完全一致的合法 Grant（用于逐字段做单点篡改）。"""
    b = ex.pending_binding
    return issue_grant(_ISSUER, run_id=b.run_id, request_id=b.request_id,
                       action_hash=b.action_hash, preview_hash=b.preview_hash,
                       request_state_version=b.request_state_version,
                       granted_state_version=b.request_state_version + 1,
                       executor_id=b.executor_id, policy_id=b.policy_id,
                       granted_event_sequence=0)


def test_every_bound_field_is_revalidated_on_first_authorize():
    """逐字段单点篡改，**第一次授权**就必须逐项拒绝。"""
    for field, bad in (("run_id", "other-run"), ("request_id", "other-req"),
                       ("action_hash", "f" * 64), ("preview_hash", "e" * 64),
                       ("request_state_version", 999),
                       ("executor_id", "some-other-executor"),
                       ("policy_id", "frozen-canary-budget-v1")):
        reg, _, calls, ex, store0 = build_stack()
        store, r = start_run(ex, store=store0, rid="hitl-def-field")
        g = _good_grant(ex, r).model_copy(update={field: bad})
        with pytest.raises(ApprovalGrantError):
            ex.authorize(g)
        assert counts(reg, calls, ex) == ({}, 0, 0), field
        assert not ex.authorized, field


def test_illegal_sequence_and_state_version_are_refused():
    reg, _, calls, ex, store0 = build_stack()
    store, r = start_run(ex, store=store0, rid="hitl-def-seq")
    b = ex.pending_binding
    # sequence = -1 必须在签发时就被拒（不接受 -1 / unknown）
    with pytest.raises(ApprovalGrantError):
        issue_grant(_ISSUER, run_id=b.run_id, request_id=b.request_id,
                    action_hash=b.action_hash, preview_hash=b.preview_hash,
                    request_state_version=b.request_state_version,
                    granted_state_version=b.request_state_version + 1,
                    executor_id=b.executor_id, policy_id=b.policy_id,
                    granted_event_sequence=-1)
    # granted_state_version 早于 request_state_version
    g = _good_grant(ex, r).model_copy(
        update={"granted_state_version": b.request_state_version - 1})
    with pytest.raises(ApprovalGrantError):
        ex.authorize(g)
    assert counts(reg, calls, ex) == ({}, 0, 0)


def test_sequence_pointing_at_missing_or_wrong_event_is_refused():
    reg, _, calls, ex, store0 = build_stack()
    store, r = start_run(ex, store=store0, rid="hitl-def-ev")
    # 不存在的 sequence
    g1 = _good_grant(ex, r).model_copy(update={"granted_event_sequence": 9999})
    with pytest.raises(ApprovalGrantError):
        ex.authorize(g1)
    # 指向真实存在但**不是** approval_granted 的事件（sequence 0 = run_created）
    g2 = _good_grant(ex, r).model_copy(update={"granted_event_sequence": 0})
    with pytest.raises(ApprovalGrantError):
        ex.authorize(g2)
    assert counts(reg, calls, ex) == ({}, 0, 0)


def test_event_lookup_failure_refuses_authorization():
    """读取事件失败 → 拒绝授权（绝不像旧实现那样吞掉异常继续）。"""
    reg, gate, calls, ex, store0 = build_stack()
    store, r = start_run(ex, store=store0, rid="hitl-def-lookuperr")

    def boom(run_id, sequence):
        raise IOError("event store unavailable")
    ex._event_lookup = boom
    with pytest.raises(ApprovalGrantError):
        ex.authorize(_good_grant(ex, r))
    assert counts(reg, calls, ex) == ({}, 0, 0)
    # 完全没有查询口同样拒绝（不是放行）
    ex._event_lookup = None
    with pytest.raises(ApprovalGrantError):
        ex.authorize(_good_grant(ex, r))
    assert counts(reg, calls, ex) == ({}, 0, 0)


def test_binding_overwrite_and_revoke_fail_closed():
    from pilot.approval_grant import issue_binding
    reg, _, calls, ex, store0 = build_stack()
    store, r = start_run(ex, store=store0, rid="hitl-def-bindov")
    b = ex.pending_binding
    ex.bind_pending_approval(b)                       # 完全相同 → 幂等
    other = issue_binding(_ISSUER, **{**b.binding(), "request_id": "different"})
    with pytest.raises(ApprovalGrantError):
        ex.bind_pending_approval(other)               # 内容不同 → 拒绝覆盖
    good = _good_grant(ex, r)                         # revoke 前先取一份合法凭证
    ex.revoke("denied")
    assert ex.pending_binding is None                 # 绑定已失效
    with pytest.raises(ApprovalGrantError):
        ex.authorize(good)                            # revoke 后合法凭证也不再被接受
    assert counts(reg, calls, ex) == ({}, 0, 0)
