"""A.8.2a.2 / A.8.2a.4a —— 受控科研运行时的 provider 声明阶段。

阶段 A（服务启动 / Clarification / Approval preview）：
    build_controlled_runtime_registry(...)
    只注册声明并 validate；factory_calls == 0、resolved_count == 0；
    不读 key、不建客户端。缺 API key 也必须能走到这里。

阶段 B（授权之后）**不在本模块**：由 `DeferredRegistryResearchExecutor.authorize()`
在与真实审批逐项比对通过后，于首次 run_stage 时 resolve。

A.8.2a.4a 已删除旧的"批准后"工厂与那个普通布尔闸门 —— 调用方可以直接传 True，
等同于没有闸门。授权只能来自与真实审批事件绑定的 ApprovalGrant。
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


def build_controlled_runtime_registry(*, gate, model_factory, timeout=120.0,
                                      pricing_policy_id="research-budget-policy-v2",
                                      specs=CONTROLLED_ROLE_SPECS) -> ProviderRegistry:
    """阶段 A：只注册声明，**不构造任何客户端、不读任何 key**。

    `model_factory(spec, gate) -> GatedModel` 由调用方提供（生产用 paid_transport 的
    安全构造函数 + GatedModel 包装；离线测试注入 fake）。它只会在授权后的首次
    run_stage 被调用。
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
            return _f(sp, _g)                      # 唯一允许构造客户端的地方（授权后）
        reg.register(spec, factory)
    reg.validate()                                  # 声明层全量校验；仍然零构造
    return reg


__all__ = ["build_controlled_runtime_registry", "CONTROLLED_ROLE_SPECS"]
