"""A.8.1.1R —— 版本化预算策略离线验收（§9 Budget Policy）。

零网络、零付费。历史两次 Canary 的口径必须保持 $0.15 且不可被新运行采用。
"""
import json

import pytest

from pilot.budget_policy import (BudgetPolicy, FROZEN_CANARY_BUDGET_V1, RESEARCH_BUDGET_POLICY_V2,
                                 POLICIES, policy_for, active_policy_for_new_run,
                                 DEFAULT_NEW_RUN_POLICY_ID)
from pilot.research_results import ROLE_MAX_TOKENS

pytestmark = pytest.mark.unit


def test_historical_canaries_keep_015_and_are_immutable():
    v1 = FROZEN_CANARY_BUDGET_V1
    assert v1.policy_id == "frozen-canary-budget-v1"
    assert v1.task_budget_usd == 0.15
    assert v1.historical_only is True and v1.immutable is True
    assert v1.valid_for_future_runs_only is False
    # A.8.1.1R.1 §3：两次 Canary 配置不同，v1 只描述共同的预算上限，
    # 绝不用一组 role_max_tokens 冒充两次实际不同的配置
    assert v1.role_max_tokens == {}


def test_each_historical_run_has_its_own_immutable_snapshot():
    """两次 Canary 的 max_tokens 确实不同，必须各有快照，且与冻结产物一致。"""
    import json as _json
    import pathlib
    from pilot.budget_policy import HISTORICAL_RUNS, historical_run
    assert len(HISTORICAL_RUNS) == 2
    a = historical_run("hitl-research-e4cbb903")
    b = historical_run("hitl-research-ac76f309")
    assert a.role_max_tokens != b.role_max_tokens          # 配置确实不同
    assert a.role_max_tokens["synthesizer"] == 1500
    assert b.role_max_tokens["synthesizer"] == 1600
    root = pathlib.Path(__file__).resolve().parent.parent
    for snap in HISTORICAL_RUNS:
        d = _json.loads((root / snap.source_artifact).read_text(encoding="utf-8"))
        # 快照与冻结产物一致（A7536 指标未记录顶层 run_id，故以费用/配置为准）
        assert d["cost_usd"]["cap"] == snap.task_budget_usd == 0.15
        assert abs(d["cost_usd"]["actual"] - snap.actual_cost_usd) < 1e-9
        mt = d["token_usage"][0]["max_tokens"]
        assert snap.role_max_tokens["synthesizer"] == mt   # 出处即权威
    with pytest.raises(KeyError):
        historical_run("hitl-research-nonexistent")


def test_policies_are_truly_immutable():
    """§4：frozen 模型 + 嵌套映射只暴露副本，外部无法污染全局注册表。"""
    from pilot.budget_policy import policy_for
    v2 = RESEARCH_BUDGET_POLICY_V2
    assert v2.immutable is True
    with pytest.raises(Exception):                          # 字段不可赋值
        v2.task_budget_usd = 0.99
    with pytest.raises(Exception):
        v2.policy_id = "hacked"
    # 取出的映射是副本：改它不影响注册表
    caps = v2.caps(); caps["synthesizer"] = 99
    mt = v2.max_tokens(); mt["synthesizer"] = 99999
    assert policy_for("research-budget-policy-v2").role_call_caps["synthesizer"] == 1
    assert policy_for("research-budget-policy-v2").role_max_tokens["synthesizer"] == 1600
    # 快照同样不可变
    from pilot.budget_policy import HISTORICAL_RUNS
    with pytest.raises(Exception):
        HISTORICAL_RUNS[0].task_budget_usd = 0.99


def test_policy_hash_changes_with_budget_caps_or_tokens():
    base = RESEARCH_BUDGET_POLICY_V2.policy_hash()
    for upd in ({"task_budget_usd": 0.20}, {"policy_id": "x"},
                {"role_max_tokens": {"synthesizer": 9}},
                {"estimation_method": "different"}):
        assert RESEARCH_BUDGET_POLICY_V2.model_copy(update=upd).policy_hash() != base


def test_concurrent_reads_are_stable():
    import threading
    from pilot.budget_policy import active_policy_for_new_run as act
    seen, errs = [], []

    def rd():
        try:
            for _ in range(200):
                p = act()
                seen.append((p.policy_id, p.task_budget_usd))
        except Exception as e:                              # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=rd) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    assert not errs
    assert set(seen) == {("research-budget-policy-v2", 0.18)}


def test_future_runs_use_v2_at_018():
    v2 = RESEARCH_BUDGET_POLICY_V2
    assert v2.policy_id == "research-budget-policy-v2"
    assert v2.task_budget_usd == 0.18
    assert v2.valid_for_future_runs_only is True
    assert v2.historical_runs_unchanged is True
    assert v2.role_call_caps == {"synthesizer": 1, "verifier": 1, "claim_extractor": 1}
    # max_tokens 仍以 OutputContract 为唯一来源，本阶段未改动
    assert v2.role_max_tokens == dict(ROLE_MAX_TOKENS)
    assert v2.role_max_tokens == {"synthesizer": 1600, "verifier": 1150, "claim_extractor": 2400}
    for key in ("pricing_version", "estimation_method", "cache_write_assumption",
                "provider_wrapper_assumption"):
        assert getattr(v2, key)


def test_policies_cannot_be_mixed():
    """历史冻结策略不得被新运行采用（否则等于追溯改写历史口径）。"""
    with pytest.raises(ValueError):
        FROZEN_CANARY_BUDGET_V1.assert_usable_for_new_run()
    with pytest.raises(ValueError):
        active_policy_for_new_run("frozen-canary-budget-v1")
    assert active_policy_for_new_run().policy_id == DEFAULT_NEW_RUN_POLICY_ID
    with pytest.raises(KeyError):
        policy_for("research-budget-policy-v9")           # 未知策略 fail-closed


def test_budget_ceiling_is_enforced_by_the_policy():
    RESEARCH_BUDGET_POLICY_V2.assert_covers(0.18)
    RESEARCH_BUDGET_POLICY_V2.assert_covers(0.16142)
    with pytest.raises(ValueError):
        RESEARCH_BUDGET_POLICY_V2.assert_covers(0.18001)
    with pytest.raises(ValueError):
        FROZEN_CANARY_BUDGET_V1.assert_covers(0.16142)    # 历史口径确实覆盖不了新形状


def test_policy_is_not_an_unversioned_magic_constant():
    """0.18 必须挂在具名、可 hash 的策略上，而不是散落的字面量。"""
    assert RESEARCH_BUDGET_POLICY_V2.schema_version == "research-budget-policy-v1"
    h1 = RESEARCH_BUDGET_POLICY_V2.policy_hash()
    assert len(h1) == 64
    drifted = RESEARCH_BUDGET_POLICY_V2.model_copy(update={"task_budget_usd": 0.20})
    assert drifted.policy_hash() != h1
    assert set(POLICIES) == {"frozen-canary-budget-v1", "research-budget-policy-v2"}


def test_committed_canary_results_still_state_015():
    """历史两次 Canary 的已提交结果不得被追溯改写。"""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "pilot" / "round2_results"
    for name in ("A7536_CANARY_METRICS.json", "A7562_CANARY_METRICS.json"):
        d = json.loads((root / name).read_text(encoding="utf-8"))
        assert d["cost_usd"]["cap"] == 0.15, f"{name} 的历史预算口径被改动了"
        assert d["cost_usd"]["within_cap"] is True


def test_preview_binds_the_policy_and_rejects_drift():
    """Approval 预览必须携带策略 id，且策略/费用漂移会改变 preview_hash → 调用前拒绝。"""
    from pilot.research_contracts import ResearchExecutionPreview, RolePreview, \
        ResearchContractError
    pv = ResearchExecutionPreview(
        executor_id="gated-research-v1", subset_id="s", subset_hash="a" * 64,
        source_pack_hash="b" * 64, protocol_hash="c" * 64,
        core_evidence_count=6, context_only_count=2, direct_count=3, indirect_count=3,
        direct_human_causal_count=0, causal_ceiling="preclinical_perturbation_support",
        roles=[RolePreview(role="synthesizer", model_id="claude-opus-4-8", call_cap=1,
                           max_tokens=1600, worst_case_cost_usd=0.08)],
        total_call_cap=3, budget_policy_id="research-budget-policy-v2",
        task_budget_usd=0.18, worst_case_cost_usd=0.17).finalize()
    pv.assert_within_budget()
    pv.assert_policy_consistent()
    # 策略 id 参与 preview_hash → 换策略即漂移
    assert pv.model_copy(update={"budget_policy_id": "frozen-canary-budget-v1"}) \
        .compute_preview_hash() != pv.preview_hash
    # 声明 v2 却写了 v1 的预算 → 拒绝
    with pytest.raises(ResearchContractError):
        pv.model_copy(update={"task_budget_usd": 0.15}).assert_policy_consistent()
    # 新运行不得采用历史冻结策略
    with pytest.raises(Exception):
        pv.model_copy(update={"budget_policy_id": "frozen-canary-budget-v1",
                              "task_budget_usd": 0.15}).assert_policy_consistent()
    # 超过 v2 上限 → 拒绝
    with pytest.raises(Exception):
        pv.model_copy(update={"worst_case_cost_usd": 0.19}).assert_policy_consistent()


def test_role_quota_is_declared_and_not_borrowable():
    for p in POLICIES.values():
        assert p.role_call_caps == {"synthesizer": 1, "verifier": 1, "claim_extractor": 1}
        assert sum(p.role_call_caps.values()) == 3
