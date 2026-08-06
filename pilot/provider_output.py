"""A.8.1 —— 第 3 层：provider-aware 结构化输出适配。

四层约束缺一不可：
  1 OutputContract（唯一语义来源）  2 Prompt renderer（把上限告诉模型）
  3 provider adapter（尽可能强制）  4 本地 validator（最终拒绝）

本层**永远不能替代**第 4 层：即使 provider 声称按 schema 强制，本地 validator 仍然必须执行。
本层也**绝不静默降级**：能力不足时抛错，由调用方 fail-closed，而不是悄悄退回自由文本。
"""

from __future__ import annotations

from pilot.output_contract import OutputContract, ProviderOutputCapability
from pilot.research_results import ResearchOutputError


class ProviderCapabilityError(ResearchOutputError):
    """provider 不具备所需的结构化输出能力 → fail-closed，不得降级为自由文本。"""


class ProviderRefusal(ResearchOutputError):
    """provider 明确拒答（≠ 截断、≠ schema 违规）。"""


def _mark(model, **attrs):
    """把适配结果记录到 wrapper 上，供 transport capture 测试检查真实发送参数。"""
    for k, v in attrs.items():
        try:
            object.__setattr__(model, k, v)
        except Exception:                                    # noqa: BLE001
            pass
    return model


def apply_output_contract(model, contract: OutputContract, capability: ProviderOutputCapability,
                          *, require_native: bool = False):
    """按 provider 实测能力施加尽可能强的结构化输出约束。

    返回 (bound_model, applied) —— `applied` 如实记录本次真正施加了什么，
    **不得**把 json_object_only 记成 native_json_schema。
    """
    mode = capability.native_constraint_mode
    if require_native and mode != "native_json_schema":
        raise ProviderCapabilityError(
            f"{capability.model_id} 仅支持 {mode}，不满足 native JSON Schema 要求（fail-closed）")

    applied = {"contract_id": contract.contract_id, "role": contract.role,
               "model_id": capability.model_id, "mode": mode,
               "local_validation_still_required": True}

    if mode == "native_json_schema":
        schema = contract.json_schema()
        try:
            # langchain-anthropic 1.4.3 的 json_schema 方法（已内省确认存在）
            bound = model.with_structured_output(schema, method="json_schema",
                                                 include_raw=True)
        except Exception as e:                               # noqa: BLE001
            raise ProviderCapabilityError(
                f"{capability.model_id} 声称支持 native JSON Schema，但绑定失败：{str(e)[:120]}"
            ) from e
        applied["json_schema_sent"] = True
        applied["response_format_sent"] = False
    elif mode == "json_object_only":
        # DeepSeek：只声明「必须是 JSON」，**不代表**字段/长度被强制。
        try:
            bound = model.bind(response_format={"type": "json_object"})
        except Exception as e:                               # noqa: BLE001
            raise ProviderCapabilityError(
                f"{capability.model_id} 无法绑定 response_format=json_object：{str(e)[:120]}") from e
        applied["json_schema_sent"] = False
        applied["response_format_sent"] = True
    else:
        raise ProviderCapabilityError(
            f"{capability.model_id} 无任何 provider 侧结构化输出能力（{mode}）→ 拒绝启用")

    applied["guarantees"] = capability.guarantees()
    return _mark(bound, _output_contract_applied=applied), applied


def describe_enforcement(capability: ProviderOutputCapability) -> dict:
    """如实区分「provider 保证的」与「只靠本地 validator 的」，供报告与审批卡使用。"""
    g = capability.guarantees()
    provider_side, local_only = [], []
    (provider_side if g["json_wellformed_by_provider"] else local_only).append("well-formed JSON")
    (provider_side if g["field_structure_by_provider"] else local_only).append("field structure")
    (provider_side if g["string_length_by_provider"] else local_only).append("string max length")
    (provider_side if g["array_items_by_provider"] else local_only).append("array max items")
    local_only += ["evidence_id existence", "causal ceiling", "no invented identifiers",
                   "claim not upgraded beyond verifier"]
    return {"model_id": capability.model_id, "mode": capability.native_constraint_mode,
            "guaranteed_by_provider": provider_side, "local_validator_only": local_only}


__all__ = ["apply_output_contract", "describe_enforcement",
           "ProviderCapabilityError", "ProviderRefusal"]
