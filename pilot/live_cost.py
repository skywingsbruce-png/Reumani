"""A.8.1.1R.1 —— **唯一**的真实请求费用权威。

背景（必须记住的教训）：A.8.1.1R 报告的 $0.16675 来自一个**脚手架脚本**里的平行实现，
而生产 `execution_preview` 只按 Prompt 文本估算，**没有计入随请求发送的 JSON Schema
与 provider wrapper 开销**——也就是说生产链当时低估了真实费用。

本模块的存在就是为了消灭平行实现：Approval 预览、action_hash、Gate 预留必须消费
**同一个** `CostEstimate`。任何地方再写第二份费用公式都属于回归。

零网络、零付费、零模型客户端。
"""

from __future__ import annotations

import json
from typing import Literal, Optional

from pydantic import ConfigDict

from schemas import _Strict
from tool_envelope import compute_hash
from pilot import prices
from pilot.hard_gate import estimate_input_tokens
from pilot.output_contract import OutputContract

ESTIMATOR_VERSION = "live-cost-v1"

ProviderMode = Literal["native_json_schema", "json_object_only", "legacy_text"]

# provider 侧包装开销（token）。native schema 需要额外的 grammar/tool 包装，
# json_object 只需一条格式指令。legacy_text 仅为兼容旧路径，不得用于新生产路径。
WRAPPER_TOKENS = {"native_json_schema": 120, "json_object_only": 40, "legacy_text": 0}


class CostUnverifiable(RuntimeError):
    """费用无法核实（未知模型/未知价格/未知模式）→ fail-closed，不得回退默认价格。"""


class CostEstimate(_Strict):
    """一次真实调用的结构化费用估算。Approval / action_hash / Gate 共用同一份。"""
    model_config = ConfigDict(frozen=True, extra="forbid")

    estimator_version: str = ESTIMATOR_VERSION
    policy_id: str
    role: str
    model_id: str
    provider_mode: str
    prompt_token_estimate: int
    schema_token_estimate: int
    wrapper_token_estimate: int
    total_input_token_estimate: int
    max_output_tokens: int
    worst_case_usd: float
    prompt_hash: str
    schema_hash: str

    def fingerprint(self) -> str:
        return compute_hash(self.model_dump(mode="json"))


def estimate_call_cost(*, role: str, model_id: str, prompt: str,
                       contract: OutputContract, provider_mode: str,
                       max_tokens: int, policy_id: str,
                       schema: Optional[dict] = None) -> CostEstimate:
    """**唯一**的真实请求费用入口。

    `schema` 必须是实际传给 `with_structured_output` 的那一个对象；缺省时从 contract
    派生（两者必须是同一来源，禁止另建"仅供计费"的平行 schema）。
    """
    if provider_mode not in WRAPPER_TOKENS:
        raise CostUnverifiable(f"未知 provider_mode {provider_mode!r} → 无法核实费用")
    if provider_mode == "legacy_text":
        raise CostUnverifiable("legacy_text 不得用于新生产路径（无结构化输出保证）")
    if not model_id:
        raise CostUnverifiable("缺少 model_id → 无法核实价格档位")
    if not max_tokens or int(max_tokens) <= 0:
        raise CostUnverifiable(f"角色 {role} 的 max_tokens 非法：{max_tokens!r}")

    prompt = str(prompt or "")
    # 只有 native schema 才把序列化 schema 作为请求载荷发送；json_object 模式不发送。
    if provider_mode == "native_json_schema":
        schema_obj = schema if schema is not None else contract.json_schema()
        schema_str = json.dumps(schema_obj, ensure_ascii=False, sort_keys=True)
        schema_tok = estimate_input_tokens(schema_str)
        schema_hash = compute_hash(schema_obj)
    else:
        schema_obj, schema_str, schema_tok = None, "", 0
        schema_hash = compute_hash({"mode": provider_mode, "contract": contract.contract_id})

    prompt_tok = estimate_input_tokens(prompt)
    wrapper_tok = WRAPPER_TOKENS[provider_mode]
    total_in = prompt_tok + schema_tok + wrapper_tok

    try:
        # 最坏输入单价（含 cache creation）+ 满额输出，均由 pilot.prices 唯一裁定
        worst = prices.worst_case_usd(model_id, total_in, int(max_tokens))
    except Exception as e:                                   # noqa: BLE001
        raise CostUnverifiable(f"模型 {model_id!r} 价格未核实：{str(e)[:120]}") from e

    return CostEstimate(
        policy_id=policy_id, role=role, model_id=model_id, provider_mode=provider_mode,
        prompt_token_estimate=prompt_tok, schema_token_estimate=schema_tok,
        wrapper_token_estimate=wrapper_tok, total_input_token_estimate=total_in,
        max_output_tokens=int(max_tokens), worst_case_usd=round(worst, 6),
        prompt_hash=compute_hash({"p": prompt}), schema_hash=schema_hash)


def total_worst_case(estimates) -> float:
    return round(sum(e.worst_case_usd for e in estimates), 6)


__all__ = ["CostEstimate", "estimate_call_cost", "total_worst_case",
           "CostUnverifiable", "ESTIMATOR_VERSION", "WRAPPER_TOKENS"]
