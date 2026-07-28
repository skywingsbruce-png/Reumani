"""A.7.4.7 —— 真实模型金丝雀的**零付费 fake 全链验收**（§4/§5 fail-closed）。

全部用 fake-model（零价）+ _FakeRoleModel（无网络）演练真实 HardBudgetGate 机制：
预留/对账/角色额度/科学封顶/fact-conflict/幻觉 evidence_id/开关缺失/停止。
真实付费调用为 0（本文件绝不构造 Anthropic/DeepSeek 客户端）。
"""
import json
import os

import pytest

from pilot.hard_gate import ENV_PAID, ENV_CONFIRM, GateConfigError, BudgetExceeded
from pilot.event_store import InMemoryEventStore
from pilot import canary_a747 as C

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _switches(monkeypatch):
    monkeypatch.setenv(ENV_PAID, "1")
    monkeypatch.setenv(ENV_CONFIRM, C.STAGE)


def _fakes(synth_strength="causal", resolver_echo="verified", claim_ids=None, claim_type="association"):
    return {
        "synthesizer": json.dumps({"resolved_question": "IL-6→SSc 因果？",
            "causal_strength": synth_strength, "unsupported_claims": ["IL-6 升高导致纤维化"],
            "missing_evidence": ["纵向证据", "干预证据"], "limitations": ["横断面"]}, ensure_ascii=False),
        "verifier": json.dumps({"status": "insufficient_for_causal",
            "resolver_resolution_echo": resolver_echo, "reason": "仅关联"}, ensure_ascii=False),
        "claim_extractor": json.dumps({"claims": [{"claim_id": "c1", "text": "IL-6 相关",
            "claim_type": claim_type, "supporting_evidence_ids": claim_ids or []}]}, ensure_ascii=False),
    }


def _run(tmp_path, fakes, run_id="fk", should_stop=lambda: False):
    store = InMemoryEventStore()
    res = C.run_canary(store.append, run_id=run_id, ledger_path=str(tmp_path / "l.jsonl"),
                       fake=True, fakes=fakes, should_stop=should_stop)
    return store, res


# ------------------------------ §4 fake full chain ------------------------------
def test_fake_full_chain_roles_and_budget(tmp_path):
    store, res = _run(tmp_path, _fakes())
    g = res["gate"]
    assert g["calls_by_role"] == {"synthesizer": 1, "verifier": 1, "claim_extractor": 1}
    assert "planner" not in g["calls_by_role"] and "executor" not in g["calls_by_role"]
    assert g["calls_global"] == 3 and res["open_reservations_ledger"] == 0
    assert g["actual_usd"] == 0.0                              # fake-model 零价
    t = [e.event_type for e in store.list("fk")]
    for et in ("synthesis_completed", "verification_completed", "claims_extracted",
               "claim_graph_completed", "shadow_completed", "run_completed"):
        assert et in t                                        # SSE 能送到 UI 的阶段事件
    assert t[-1] == "run_completed"


def test_roles_are_three_independent_wrappers(tmp_path):
    gate = C.build_gate(str(tmp_path / "l.jsonl"), fake=True)
    roles = C.build_roles(gate, fake=True, fakes=_fakes())
    assert len({id(roles["synthesizer"]), id(roles["verifier"]), id(roles["claim_extractor"])}) == 3
    from pilot.hard_gate import _WRAPPED
    for r, m in roles.items():
        assert getattr(m, _WRAPPED, False) and object.__getattribute__(m, "_role") == r


# ------------------------------ §5 scientific calibration ------------------------------
def test_causal_never_confirmed_when_axes_insufficient(tmp_path):
    # 模型越权声称 causal，但冻结证据无时序/干预轴 → 封顶到 association，绝不 causal
    _, res = _run(tmp_path, _fakes(synth_strength="causal"))
    assert res["conclusion"].causal_strength in ("association", "insufficient")
    assert res["conclusion"].causal_strength != "causal"
    assert res["conclusion"].missing_evidence                 # 缺口非空


def test_cap_causal_pure():
    assert C._cap_causal("causal", set()) == "association"
    assert C._cap_causal("intervention_supported", {"association"}) == "association"
    assert C._cap_causal("temporal_association", {"temporal_evidence"}) == "temporal_association"
    assert C._cap_causal("association", {"association"}) == "association"


# ------------------------------ fail-closed ------------------------------
def test_verifier_cannot_override_resolver_fact(tmp_path):
    # verifier 回显了与 resolver 不一致的终态 → verifier_fact_conflict → run 失败，预留归零
    store, res = _run(tmp_path, _fakes(resolver_echo="not_found"))
    t = [e.event_type for e in store.list("fk")]
    assert res["failed"] is True and "run_failed" in t
    assert res["open_reservations_ledger"] == 0               # 失败也结算
    # verifier 调用发生过一次（真实场景即为一次付费），但事实冲突后 fail-closed
    assert res["gate"]["calls_by_role"].get("verifier") == 1


def test_claim_extractor_drops_hallucinated_evidence_id(tmp_path):
    # 虚构 evidence_id 被丢弃（claim 只保留已有 id）；不误当作真实证据
    store, res = _run(tmp_path, _fakes(claim_ids=["NONEXISTENT-ID-999"]))
    assert res["failed"] is False                             # 内容层丢弃，非解析错误
    cg = [e for e in store.list("fk") if e.event_type == "claim_graph_completed"]
    # 丢掉虚构 id 后 claim 无有效支持 → 真实 Claim Graph 裁为 insufficient_evidence（未误当作证据）
    assert cg and cg[0].safe_payload.get("graph_verdicts") == ["insufficient_evidence"]


def test_missing_switches_zero_paid_calls(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_PAID, raising=False)
    with pytest.raises(GateConfigError):
        C.run_canary(InMemoryEventStore().append, run_id="ns",
                     ledger_path=str(tmp_path / "l.jsonl"), fake=True, fakes=_fakes())
    # 无开关 → 在任何调用前 fail-closed；账本无 reserved 事件
    import pathlib
    text = pathlib.Path(tmp_path / "l.jsonl").read_text(encoding="utf-8") if (tmp_path / "l.jsonl").exists() else ""
    assert '"event": "reserved"' not in text


def test_per_role_quota_not_borrowable(tmp_path):
    gate = C.build_gate(str(tmp_path / "l.jsonl"), fake=True)
    gate.start_task("q")
    gate.before_call(model_id=C.FAKE_MODEL, role="synthesizer", payload="x", max_tokens=100)
    with pytest.raises(BudgetExceeded):                       # 第二次 synthesizer → 超角色额度
        gate.before_call(model_id=C.FAKE_MODEL, role="synthesizer", payload="x", max_tokens=100)
    with pytest.raises(BudgetExceeded):                       # 未配置角色 → fail-closed
        gate.before_call(model_id=C.FAKE_MODEL, role="planner", payload="x", max_tokens=100)


def test_stop_produces_no_model_calls(tmp_path):
    store, res = _run(tmp_path, _fakes(), run_id="st", should_stop=lambda: True)
    assert res["gate"]["calls_global"] == 0                   # 停止 → 未授权任何模型调用
    assert res["open_reservations_ledger"] == 0
    assert res["stopped"] is True


# ------------------------------ frozen evidence / preflight ------------------------------
def test_frozen_evidence_hash_stable():
    a = C.freeze_evidence()
    b = C.freeze_evidence()
    assert a["frozen_hash"] == b["frozen_hash"] and a["evidence_count"] >= 1
    assert a["pmids"] == b["pmids"]


def test_preflight_prices_and_frozen_evidence():
    pf = C.preflight(fake=True)
    assert set(pf["prices_verified"]) == {C.SYNTH_MODEL, C.VERIFY_MODEL, C.CLAIM_MODEL}
    assert pf["frozen_evidence"]["evidence_count"] >= 1


def test_stage_started_completed_pairs_in_order(tmp_path):
    from pilot.event_store import InMemoryEventStore
    store = InMemoryEventStore()
    C.run_fake_canary(store.append, run_id="pairs", ledger_path=str(tmp_path / "l.jsonl"))
    t = [e.event_type for e in store.list("pairs")]
    for a, b in (("synthesis_started", "synthesis_completed"),
                 ("verification_started", "verification_completed"),
                 ("claim_extraction_started", "claims_extracted"),
                 ("shadow_started", "shadow_completed")):
        assert a in t and b in t and t.index(a) < t.index(b), (a, b)
    assert t.index("shadow_completed") < t.index("run_completed")
    # every stage_started carries status "running" (UI shows running before run_completed)
    started = [e for e in store.list("pairs") if e.event_type.endswith("_started")]
    assert all(e.status == "running" for e in started)


def test_stop_between_stages_prevents_next_and_zero_paid(tmp_path, monkeypatch):
    from pilot.event_store import InMemoryEventStore
    import pilot.paid_transport as PT
    monkeypatch.setattr(PT, "build_anthropic", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no real model")))
    monkeypatch.setattr(PT, "build_deepseek", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no real model")))
    store = InMemoryEventStore()

    def stop_after_verification():
        return any(e.event_type == "verification_completed" for e in store.list("mid"))

    res = C.run_fake_canary(store.append, run_id="mid", ledger_path=str(tmp_path / "l.jsonl"),
                            should_stop=stop_after_verification)
    t = [e.event_type for e in store.list("mid")]
    assert "verification_completed" in t
    assert "claim_extraction_started" not in t and "shadow_started" not in t   # next stage not started
    assert t[-1] == "run_stopped"
    assert res["gate"]["calls_by_role"].get("claim_extractor") is None          # claim call never made
    assert res["open_reservations_ledger"] == 0 and res["stopped"] is True


def test_no_real_paid_client_constructed_in_fake(tmp_path, monkeypatch):
    # fake 路径绝不构造真实付费客户端
    import pilot.paid_transport as PT
    def boom(*a, **k):
        raise AssertionError("fake path must not build a real paid client")
    monkeypatch.setattr(PT, "build_anthropic", boom)
    monkeypatch.setattr(PT, "build_deepseek", boom)
    _run(tmp_path, _fakes())        # should not raise
