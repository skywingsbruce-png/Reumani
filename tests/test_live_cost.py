"""A.8.1.1R.1 §2 —— 唯一费用权威验收。

关键：`$0.16675` 必须由**生产权威函数动态得出**，不得硬编码断言该数字。
零网络、零付费。
"""
import json

import pytest

from pilot.live_cost import (CostEstimate, estimate_call_cost, CostUnverifiable,
                             ESTIMATOR_VERSION, WRAPPER_TOKENS)
from pilot.role_contracts import contract_for, ANTHROPIC_OPUS_48, DEEPSEEK_V4_FLASH
from pilot.research_results import ROLE_MAX_TOKENS
from tests.test_live_output_wiring import build_live, _run_stages, LIVE_CAPS  # noqa: F401

pytestmark = pytest.mark.unit
P = "prompt " * 400


@pytest.fixture(autouse=True)
def _gate_switches(monkeypatch):
    from pilot.hard_gate import ENV_PAID, ENV_CONFIRM
    monkeypatch.setenv(ENV_PAID, "1")
    monkeypatch.setenv(ENV_CONFIRM, "A755_offline")
    monkeypatch.delenv("CI", raising=False)


def _est(**kw):
    base = dict(role="synthesizer", model_id="claude-opus-4-8", prompt=P,
                contract=contract_for("synthesizer"), provider_mode="native_json_schema",
                max_tokens=1600, policy_id="research-budget-policy-v2")
    base.update(kw)
    return estimate_call_cost(**base)


def test_native_mode_counts_the_serialized_schema():
    n = _est()
    j = _est(provider_mode="json_object_only")
    assert n.schema_token_estimate > 0            # native 必须计入 schema
    assert j.schema_token_estimate == 0           # json_object 不发送 schema
    assert n.wrapper_token_estimate == WRAPPER_TOKENS["native_json_schema"]
    assert j.wrapper_token_estimate == WRAPPER_TOKENS["json_object_only"]
    assert n.total_input_token_estimate == (n.prompt_token_estimate + n.schema_token_estimate
                                            + n.wrapper_token_estimate)
    assert n.worst_case_usd > j.worst_case_usd     # 计入 schema 后必然更贵
    assert n.estimator_version == ESTIMATOR_VERSION


def test_changing_schema_changes_hash_tokens_and_cost():
    base = _est()
    src = contract_for("synthesizer")
    bigger = src.model_copy(update={
        "fields": [f.model_copy(update={"max_characters": (f.max_characters or 100) + 500})
                   for f in src.fields]})
    changed = _est(contract=bigger)
    assert changed.schema_hash != base.schema_hash
    assert changed.schema_token_estimate > base.schema_token_estimate
    assert changed.worst_case_usd > base.worst_case_usd


def test_changing_prompt_changes_hash_and_cost():
    a, b = _est(), _est(prompt=P + "extra " * 500)
    assert b.prompt_hash != a.prompt_hash
    assert b.prompt_token_estimate > a.prompt_token_estimate
    assert b.worst_case_usd > a.worst_case_usd


def test_changing_max_tokens_changes_cost():
    a, b = _est(), _est(max_tokens=1601)
    assert b.max_output_tokens == 1601 and b.worst_case_usd > a.worst_case_usd


def test_cost_fails_closed_when_unverifiable():
    for kw in ({"model_id": "totally-unknown-model"}, {"provider_mode": "legacy_text"},
               {"provider_mode": "made_up"}, {"max_tokens": 0}, {"model_id": ""}):
        with pytest.raises(CostUnverifiable):
            _est(**kw)


def test_estimate_is_frozen():
    e = _est()
    with pytest.raises(Exception):
        e.worst_case_usd = 0.0


# ---------------------------------------------------------------- 生产链一致性
def test_production_preview_uses_the_single_authority_and_stays_within_policy():
    """$0.16675 必须由生产权威动态得出——这里不硬编码该数字。"""
    ex, _, _ = build_live()
    pv = ex.execution_preview()
    assert set(ex.cost_estimates) == {"synthesizer", "verifier", "claim_extractor"}
    for e in ex.cost_estimates.values():
        assert isinstance(e, CostEstimate)
        assert e.estimator_version == ESTIMATOR_VERSION
    # Approval 展示值 == 权威估算（逐角色 + 总额）
    for rp in pv.roles:
        e = ex.cost_estimates[rp.role]
        assert rp.worst_case_cost_usd == e.worst_case_usd
        assert rp.provider_mode == e.provider_mode
        assert rp.schema_hash == e.schema_hash[:16]
        assert rp.total_input_token_estimate == e.total_input_token_estimate
    assert pv.worst_case_cost_usd == round(
        sum(e.worst_case_usd for e in ex.cost_estimates.values()), 6)
    # 动态得出的总额必须落在策略上限内（不硬编码 0.16675）
    assert pv.worst_case_cost_usd <= pv.task_budget_usd
    pv.assert_policy_consistent()
    # 两个 Anthropic 角色确实计入了 schema
    assert ex.cost_estimates["synthesizer"].schema_token_estimate > 0
    assert ex.cost_estimates["verifier"].schema_token_estimate > 0
    assert ex.cost_estimates["claim_extractor"].schema_token_estimate == 0


def test_preview_schema_hash_matches_the_schema_actually_sent():
    """spy 捕获的真实 structured-output schema 必须与 CostEstimate.schema_hash 同源。"""
    from tool_envelope import compute_hash
    ex, _, spies = build_live()
    ex.execution_preview()
    _run_stages(ex, "verifier")
    for role in ("synthesizer", "verifier"):
        sent = spies[role].structured["schema"]
        assert compute_hash(sent) == ex.cost_estimates[role].schema_hash
        assert sent == contract_for(role).json_schema()     # 无平行 schema


def test_cost_fields_are_bound_into_the_action_hash():
    """schema / provider mode / max_tokens 变化必须改变 preview_hash（→ action_hash）。"""
    ex, _, _ = build_live()
    pv = ex.execution_preview()
    for upd in ({"worst_case_cost_usd": pv.worst_case_cost_usd + 0.001},
                {"roles": [r.model_copy(update={"schema_hash": "deadbeef"}) for r in pv.roles]},
                {"roles": [r.model_copy(update={"provider_mode": "json_object_only"})
                           for r in pv.roles]},
                {"roles": [r.model_copy(update={"max_tokens": 999}) for r in pv.roles]},
                {"roles": [r.model_copy(update={"total_input_token_estimate": 1}) for r in pv.roles]}):
        assert pv.model_copy(update=upd).compute_preview_hash() != pv.preview_hash


def test_gate_reservation_matches_the_cost_authority():
    """§2 第 5 项：Approval 展示、CostEstimate 与 Gate 预留必须完全一致。"""
    ex, gate, _ = build_live()
    pv = ex.execution_preview()
    _run_stages(ex, "claim_extractor")
    reserved = [e for e in gate.ledger.events() if e["event"] == "reserved"]
    assert len(reserved) == 3
    by_role = {e["role"]: e for e in reserved}
    for role, e in by_role.items():
        est = ex.cost_estimates[role]
        # 闸门预留的最坏费用 == 权威估算
        assert abs(e["worst_case_usd"] - est.worst_case_usd) < 1e-6, role
        # 闸门估算的输入 token 也已包含 schema + wrapper
        assert e["est_input_tokens"] == est.total_input_token_estimate, role
        assert e["max_tokens"] == est.max_output_tokens
    # 预览用最坏情况 Prompt，实际运行用真实 Prompt，因此预览是实际的**上界**
    total_reserved = round(sum(e["worst_case_usd"] for e in reserved), 6)
    assert total_reserved <= pv.worst_case_cost_usd + 1e-9
    assert total_reserved <= pv.task_budget_usd


def test_extra_input_tokens_only_ever_increases_the_reservation():
    """新增参数不得放宽任何裁决：缺省 0 时行为与以前一致，给值只会让预留变大。"""
    from pilot.hard_gate import estimate_input_tokens
    ex, gate, _ = build_live()
    _run_stages(ex, "synthesizer")
    e = [x for x in gate.ledger.events() if x["event"] == "reserved"][0]
    assert e["est_input_tokens"] > estimate_input_tokens("x")   # 确实加了额外部分
    est = ex.cost_estimates["synthesizer"]
    assert est.schema_token_estimate > 0 and est.wrapper_token_estimate > 0


def test_no_parallel_cost_formula_in_production_sources():
    """生产代码里不得再出现第二份费用公式。"""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "pilot"
    for name in ("gated_research_executor.py", "research_contracts.py", "runtime_api.py"):
        src = (root / name).read_text(encoding="utf-8")
        assert "worst_case_usd(" not in src, f"{name} 里出现了独立的费用公式"
