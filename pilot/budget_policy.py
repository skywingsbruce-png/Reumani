"""A.8.1.1R —— 版本化的研究运行预算策略。

为什么需要版本化：启用 provider **原生 JSON Schema** 后，schema 本身成为请求载荷的一部分，
按最贵输入价（cache write）计费，使真实请求的最坏费用超过原 $0.15。人工决定不通过弱化
输出约束来迁就旧上限，而是把预算**分版本**：

- `frozen-canary-budget-v1`  $0.15 —— **只属于已经跑过的两次 Canary**，永久冻结、不可变。
  历史 Approval / Manifest / 账本 / 报告的序列化内容与 hash 都不因本次改动而变化。
- `research-budget-policy-v2` $0.18 —— **只适用于尚未运行的未来 Research Run**。

两者不得混用：历史运行永远按 v1 解读，新运行必须显式声明 v2。
"""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from schemas import _Strict
from tool_envelope import compute_hash

BUDGET_POLICY_SCHEMA = "research-budget-policy-v1"   # 策略对象自身的 schema 版本


class HistoricalRunSnapshot(_Strict):
    """A.8.1.1R.1 §3 —— 单次历史运行的**不可变**配置快照。

    两次 Canary 的 max_tokens 配置并不相同（1500/1200/1200 与 1600/1150/2400），
    因此**不能**用一组 role_max_tokens 描述两者。每次运行各有一份快照，
    数值只从冻结产物读取；产物未明确记录的写 `unknown`，绝不猜测。
    权威始终是冻结的 Manifest / 结果文件本身，本快照只是它们的只读索引。
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    phase: str
    budget_policy_id: str
    task_budget_usd: float
    role_max_tokens: dict            # 该次运行实际使用的配置
    actual_cost_usd: float
    outcome: str
    source_artifact: str             # 权威出处（冻结产物文件名）


class BudgetPolicy(_Strict):
    """一个具名、可审计的预算策略。策略 id 进入 Approval 与 action_hash。

    A.8.1.1R.1 §4：真正不可变（frozen），且嵌套映射对外只暴露副本，
    调用方无法就地修改而污染全局注册表。
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["research-budget-policy-v1"] = BUDGET_POLICY_SCHEMA
    policy_id: str
    task_budget_usd: float
    role_call_caps: dict = Field(default_factory=dict)
    role_max_tokens: dict = Field(default_factory=dict)
    pricing_version: str
    estimation_method: str
    cache_write_assumption: str
    provider_wrapper_assumption: str
    valid_for_future_runs_only: bool
    historical_runs_unchanged: bool = True
    immutable: bool = False
    historical_only: bool = False
    note: str = ""

    def policy_hash(self) -> str:
        return compute_hash(self.model_dump(mode="json"))

    def caps(self) -> dict:
        """对外只给副本：外部改动不得污染全局注册表。"""
        return dict(self.role_call_caps)

    def max_tokens(self) -> dict:
        return dict(self.role_max_tokens)

    def assert_usable_for_new_run(self) -> None:
        """历史专用策略不得被新运行采用（否则等于追溯改写历史口径）。"""
        if self.historical_only:
            raise ValueError(
                f"{self.policy_id} 是历史冻结策略，不能用于新的 Research Run（fail-closed）")

    def assert_covers(self, worst_case_usd: float) -> None:
        if worst_case_usd > self.task_budget_usd:
            raise ValueError(
                f"最坏费用 ${worst_case_usd:.5f} 超过策略 {self.policy_id} 的上限 "
                f"${self.task_budget_usd:.5f} → 拒绝执行（不得自动提高预算）")


def _role_caps():
    return {"synthesizer": 1, "verifier": 1, "claim_extractor": 1}


def _role_max_tokens():
    # 唯一来源仍是 OutputContract；此处只做只读引用，绝不另立数值。
    from pilot.research_results import ROLE_MAX_TOKENS
    return dict(ROLE_MAX_TOKENS)


# ---------------------------------------------------------------- v1：历史冻结
FROZEN_CANARY_BUDGET_V1 = BudgetPolicy(
    policy_id="frozen-canary-budget-v1",
    task_budget_usd=0.15,
    role_call_caps=_role_caps(),
    # 两次 Canary 的 max_tokens 配置**不同**，因此本策略只描述二者**共同的预算上限**，
    # 具体角色配置由各自的冻结产物（见 HISTORICAL_RUNS）作为权威。
    role_max_tokens={},
    pricing_version="2026-07-20.1",
    estimation_method="A.7.5.6.1 conservative: chars/2.0 + 200 overhead tokens x1.15",
    cache_write_assumption="worst input rate incl. cache creation (Opus cache_write_1h $10/MTok)",
    provider_wrapper_assumption="none (no provider-side structured output was used)",
    valid_for_future_runs_only=False,
    historical_runs_unchanged=True,
    immutable=True,
    historical_only=True,
    note="Applies ONLY to the two canaries already executed (A.7.5.6 and A.7.5.6.2). "
         "It fixes the shared $0.15 ceiling only; per-role configuration differed between "
         "the two runs and is authoritative in each run's own frozen artifacts. "
         "Their approvals, manifests, ledgers and reports are never rewritten.")

# 每次历史运行各自的不可变快照。数值只从冻结产物读取，未记录者写 unknown。
HISTORICAL_RUNS = (
    HistoricalRunSnapshot(
        run_id="hitl-research-e4cbb903", phase="A.7.5.6",
        budget_policy_id="frozen-canary-budget-v1", task_budget_usd=0.15,
        role_max_tokens={"synthesizer": 1500, "verifier": 1200, "claim_extractor": 1200},
        actual_cost_usd=0.07043, outcome="failed_closed_at_synthesizer_output_truncated",
        source_artifact="pilot/round2_results/A7536_CANARY_METRICS.json"),
    HistoricalRunSnapshot(
        run_id="hitl-research-ac76f309", phase="A.7.5.6.2",
        budget_policy_id="frozen-canary-budget-v1", task_budget_usd=0.15,
        role_max_tokens={"synthesizer": 1600, "verifier": 1150, "claim_extractor": 2400},
        actual_cost_usd=0.06153, outcome="failed_closed_at_synthesizer_output_truncated",
        source_artifact="pilot/round2_results/A7562_CANARY_METRICS.json"),
)


def historical_run(run_id: str) -> HistoricalRunSnapshot:
    for s in HISTORICAL_RUNS:
        if s.run_id == run_id:
            return s
    raise KeyError(f"未知历史运行 {run_id!r}")

# ---------------------------------------------------------------- v2：未来运行
RESEARCH_BUDGET_POLICY_V2 = BudgetPolicy(
    policy_id="research-budget-policy-v2",
    task_budget_usd=0.18,
    role_call_caps=_role_caps(),
    role_max_tokens=_role_max_tokens(),
    pricing_version="2026-07-20.1",
    estimation_method="chars/2.0 + 200 overhead tokens x1.15, applied to the FINAL request "
                      "object (prompt + native JSON Schema + provider wrapper)",
    cache_write_assumption="worst input rate incl. cache creation (Opus cache_write_1h $10/MTok)",
    provider_wrapper_assumption="native_json_schema 120 tokens / json_object_only 40 tokens",
    valid_for_future_runs_only=True,
    historical_runs_unchanged=True,
    immutable=True,
    historical_only=False,
    note="Raised from $0.15 solely because provider-native JSON Schema is now part of the "
         "request payload. Output constraints were NOT weakened and max_tokens were NOT changed.")

POLICIES = {p.policy_id: p for p in (FROZEN_CANARY_BUDGET_V1, RESEARCH_BUDGET_POLICY_V2)}
DEFAULT_NEW_RUN_POLICY_ID = "research-budget-policy-v2"


def policy_for(policy_id: str) -> BudgetPolicy:
    try:
        return POLICIES[policy_id]
    except KeyError:
        raise KeyError(f"未知预算策略 {policy_id!r}（fail-closed）") from None


def active_policy_for_new_run(policy_id: str = DEFAULT_NEW_RUN_POLICY_ID) -> BudgetPolicy:
    p = policy_for(policy_id)
    p.assert_usable_for_new_run()
    return p


__all__ = ["BudgetPolicy", "HistoricalRunSnapshot", "HISTORICAL_RUNS", "historical_run", "FROZEN_CANARY_BUDGET_V1", "RESEARCH_BUDGET_POLICY_V2",
           "POLICIES", "policy_for", "active_policy_for_new_run",
           "DEFAULT_NEW_RUN_POLICY_ID", "BUDGET_POLICY_SCHEMA"]
