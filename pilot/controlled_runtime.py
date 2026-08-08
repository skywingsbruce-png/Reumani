"""A.8.2a.2 —— 受控科研运行时的**两阶段**生产入口。

为什么要分两阶段：`GatedResearchExecutor.from_registry()` 会立即 resolve 三个角色，
也就是立即构造付费客户端。如果在服务启动或 Clarification 阶段就调用它，等于在人还没批准
之前就建好了付费客户端 —— 与 HITL 的全部前提相悖。

因此明确拆开：

  阶段 A（服务启动 / Clarification / Approval preview）
      build_controlled_runtime_registry(...)
      只注册声明并 validate；factory_calls == 0、resolved_count == 0；
      不读 key、不建客户端。缺 API key 也必须能走到这里。

  阶段 B（用户已批准且 action hash 校验通过之后）
      build_approved_research_executor(registry, ...)
      才 resolve 三个角色并创建 executor。缺 key 在这一步明确 fail-closed，
      绝不降级回旧模型路径。

没有 LazyProxy、没有 __getattr__ 魔法、不按调用顺序猜角色：角色一律由 ProviderSpec 声明。
"""

from __future__ import annotations

from pilot.provider_registry import ProviderRegistry, ProviderSpec, ProviderRegistryError
from pilot.research_results import ROLE_MAX_TOKENS

# 受控科研链的三个角色声明。model/mode 与已实测的 ProviderOutputCapability 一致。
CONTROLLED_ROLE_SPECS = (
    {"provider_id": "controlled-synthesizer", "provider": "anthropic",
     "model_id": "claude-opus-4-8", "role": "synthesizer",
     "provider_mode": "native_json_schema", "output_contract_id": "synthesis-result-v2"},
    {"provider_id": "controlled-verifier", "provider": "anthropic",
     "model_id": "claude-opus-4-8", "role": "verifier",
     "provider_mode": "native_json_schema", "output_contract_id": "verifier-result-v2"},
    {"provider_id": "controlled-claim-extractor", "provider": "deepseek",
     "model_id": "deepseek-v4-flash", "role": "claim_extractor",
     "provider_mode": "json_object_only", "output_contract_id": "claim-extraction-result-v2"},
)


class ApprovalNotVerified(RuntimeError):
    """未经批准（或 action hash 未校验）不得 resolve 付费客户端。"""


def build_controlled_runtime_registry(*, gate, model_factory, timeout=120.0,
                                      pricing_policy_id="research-budget-policy-v2",
                                      specs=CONTROLLED_ROLE_SPECS) -> ProviderRegistry:
    """阶段 A：只注册声明，**不构造任何客户端、不读任何 key**。

    `model_factory(spec, gate) -> GatedModel` 由调用方提供（生产用 paid_transport 的
    安全构造函数 + GatedModel 包装；离线测试注入 fake）。它**只会在阶段 B 被调用**。
    """
    if gate is None:
        raise ProviderRegistryError("必须提供 HardBudgetGate（价格/额度未核实则拒绝）")
    if not callable(model_factory):
        raise ProviderRegistryError("model_factory 必须可调用")
    reg = ProviderRegistry()
    for s in specs:
        role = s["role"]
        spec = ProviderSpec(timeout=float(timeout), max_tokens=ROLE_MAX_TOKENS[role],
                            retry_policy="no_retry", pricing_policy_id=pricing_policy_id,
                            enabled=True, **s)

        def factory(sp, _f=model_factory, _g=gate):
            return _f(sp, _g)                      # 唯一允许构造客户端的地方（阶段 B）
        reg.register(spec, factory)
    reg.validate()                                  # 声明层全量校验；仍然零构造
    return reg


def build_approved_research_executor(registry, *, gate, evidence_loader,
                                     approval_verified: bool = False,
                                     budget_policy_id="research-budget-policy-v2"):
    """阶段 B：**批准之后**才按角色 resolve 并创建 executor。

    `approval_verified` 必须由调用方在 action hash 校验通过后显式置 True ——
    这是防止「提前 resolve」的结构性闸门，不是提示。
    """
    if not approval_verified:
        raise ApprovalNotVerified(
            "未通过 approval/action-hash 校验，禁止 resolve 付费 provider（fail-closed）")
    if registry is None:
        raise ProviderRegistryError("缺少 ProviderRegistry")
    from pilot.gated_research_executor import GatedResearchExecutor
    return GatedResearchExecutor.from_registry(
        registry=registry, gate=gate, evidence_loader=evidence_loader,
        budget_policy_id=budget_policy_id)


__all__ = ["build_controlled_runtime_registry", "build_approved_research_executor",
           "CONTROLLED_ROLE_SPECS", "ApprovalNotVerified"]
