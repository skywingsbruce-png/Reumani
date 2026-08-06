"""A.8.1 —— 三个受控角色的 OutputContract（v2）与已实测的 provider 能力表。

这里的数值就是**唯一**的上限来源：`research_results.LIMITS` 从这里派生，Prompt 也从这里派生，
provider JSON Schema 同样从这里派生。三处不得再出现独立常量（否则必然漂移）。

疾病无关：本文件不含任何 SSc / cGAS–STING / 具体课题内容。
"""

from __future__ import annotations

from pilot.output_contract import FieldLimit as FL, OutputContract, ProviderOutputCapability

ALLOWED_CAUSAL_LABELS = ("association", "mechanistic_support", "preclinical_perturbation_support",
                         "hypothesis_generating", "insufficient_for_direct_human_causality")
VERDICTS = ("supported", "partially_supported", "not_supported", "contradicted",
            "insufficient_evidence", "technically_unverifiable")

# 这些数值沿用 A.7.5.6.1 由「最坏合法 JSON ≤ max_tokens 且三角色最坏费用 ≤ $0.15」反推的结果。
# 本阶段**不得**提高 max_tokens，也不得提高预算。
SYNTHESIS_CONTRACT = OutputContract(
    contract_id="synthesis-result-v2", role="synthesizer",
    result_schema_version="synthesis-result-v1", max_output_tokens=1600,
    fields=[
        FL(name="summary", type="string", max_characters=320,
           description="one compact paragraph, no restated abstracts"),
        FL(name="supported_statements", type="string_list", max_items=4, max_characters=140),
        FL(name="unsupported_statements", type="string_list", max_items=4, max_characters=140),
        FL(name="contradictions", type="string_list", max_items=2, max_characters=140),
        FL(name="evidence_gaps", type="string_list", max_items=2, max_characters=140),
        FL(name="causal_assessment", type="enum", enum_values=list(ALLOWED_CAUSAL_LABELS),
           free_text_allowed=False),
        FL(name="limitations", type="string_list", max_items=3, max_characters=140),
        FL(name="citations", type="string_list", max_items=6, max_characters=32,
           evidence_reference_only=True, free_text_allowed=False),
    ])

VERIFIER_CONTRACT = OutputContract(
    contract_id="verifier-result-v2", role="verifier",
    result_schema_version="verifier-result-v1", max_output_tokens=1150,
    fields=[
        FL(name="verdict", type="enum", enum_values=list(VERDICTS), free_text_allowed=False),
        FL(name="reason", type="string", max_characters=260),
        FL(name="fact_conflicts", type="string_list", max_items=2, max_characters=140),
        FL(name="citation_conflicts", type="string_list", max_items=2, max_characters=140),
        FL(name="causal_overstatement", type="bool", free_text_allowed=False),
        FL(name="unsupported_claims", type="string_list", max_items=3, max_characters=140),
        FL(name="required_corrections", type="string_list", max_items=3, max_characters=140),
        FL(name="human_review", type="bool", free_text_allowed=False),
    ])

CLAIM_CONTRACT = OutputContract(
    contract_id="claim-extraction-result-v2", role="claim_extractor",
    result_schema_version="claim-extraction-v1", max_output_tokens=2400,
    fields=[
        FL(name="claims", type="object_list", max_items=5, max_characters=250,
           description="each claim: claim_id, claim_text, claim_type, causal_strength, "
                       "evidence_ids (evidence_id only), support_status, limitations"),
    ])

ROLE_CONTRACTS = {"synthesizer": SYNTHESIS_CONTRACT, "verifier": VERIFIER_CONTRACT,
                  "claim_extractor": CLAIM_CONTRACT}


def contract_for(role: str) -> OutputContract:
    try:
        return ROLE_CONTRACTS[role]
    except KeyError:
        raise KeyError(f"没有为角色 {role!r} 定义 OutputContract（fail-closed）") from None


# --------------------------------------------------------------------------------------
# 已实测的 provider 能力（A.8.1 §3）。**根据已安装 SDK 源码内省 + 官方文档核实**，
# 不是按方法名猜测。任何一项没核实过，就不得写 True。
# --------------------------------------------------------------------------------------
ANTHROPIC_OPUS_48 = ProviderOutputCapability(
    provider="anthropic", model_id="claude-opus-4-8",
    sdk_version="anthropic==0.101.0 / langchain-anthropic==1.4.3",
    json_object_supported=True,
    json_schema_supported=True,          # messages.create 接受 output_config（已内省确认）
    strict_tool_schema_supported=True,   # tools + tool_choice 可用
    # provider 是否**强制** maxLength / maxItems 未经实测确认 → 一律按 False 记录，
    # 相应保证只能来自本地 validator。宁可低报，不得高报。
    string_max_length_supported_by_provider=False,
    array_max_items_supported_by_provider=False,
    native_constraint_mode="native_json_schema",
    fallback_mode="prompt_only",
    verified_at="2026-08-06",
    documentation_source="installed anthropic 0.101.0 message_create_params introspection "
                         "(output_config present) + platform.claude.com structured outputs docs")

DEEPSEEK_V4_FLASH = ProviderOutputCapability(
    provider="deepseek", model_id="deepseek-v4-flash",
    sdk_version="openai==2.44.0 / langchain-openai==1.3.3",
    json_object_supported=True,
    json_schema_supported=False,         # 仅 JSON 模式；完整 schema 强制未确认
    strict_tool_schema_supported=False,  # strict tool schema 属 Beta，本阶段不启用
    string_max_length_supported_by_provider=False,
    array_max_items_supported_by_provider=False,
    native_constraint_mode="json_object_only",
    fallback_mode="prompt_only",
    verified_at="2026-08-06",
    documentation_source="api-docs.deepseek.com JSON Output; strict tool schema is Beta and is "
                         "deliberately NOT enabled in this phase")

CAPABILITIES = {"claude-opus-4-8": ANTHROPIC_OPUS_48, "deepseek-v4-flash": DEEPSEEK_V4_FLASH}


def capability_for(model_id: str) -> ProviderOutputCapability:
    """未登记能力的模型一律拒绝（fail-closed），不得假设它支持结构化输出。"""
    try:
        return CAPABILITIES[model_id]
    except KeyError:
        raise KeyError(
            f"模型 {model_id!r} 没有经过核实的 ProviderOutputCapability → 拒绝启用结构化输出"
        ) from None


__all__ = ["ROLE_CONTRACTS", "contract_for", "CAPABILITIES", "capability_for",
           "SYNTHESIS_CONTRACT", "VERIFIER_CONTRACT", "CLAIM_CONTRACT",
           "ALLOWED_CAUSAL_LABELS", "VERDICTS"]
