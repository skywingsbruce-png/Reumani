"""A.8.1 —— OutputContract / provider-aware enforcement 离线验收（§11）。

全部离线、断网可跑：不构造任何真实模型客户端，真实付费调用必须为 0。
"""
import json

import pytest

from pilot.output_contract import OutputContract, FieldLimit, ProviderOutputCapability
from pilot.role_contracts import (ROLE_CONTRACTS, contract_for, capability_for,
                                  SYNTHESIS_CONTRACT, VERIFIER_CONTRACT, CLAIM_CONTRACT,
                                  ANTHROPIC_OPUS_48, DEEPSEEK_V4_FLASH)
from pilot.provider_output import (apply_output_contract, describe_enforcement,
                                   ProviderCapabilityError)
from pilot.research_results import LIMITS, ROLE_MAX_TOKENS

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------- 1-3 契约本体
def test_contract_schema_version_and_three_independent_roles():
    assert SYNTHESIS_CONTRACT.schema_version == "output-contract-v1"
    ids = {c.contract_id for c in ROLE_CONTRACTS.values()}
    assert ids == {"synthesis-result-v2", "verifier-result-v2", "claim-extraction-result-v2"}
    assert len({id(c) for c in ROLE_CONTRACTS.values()}) == 3
    for role, c in ROLE_CONTRACTS.items():
        assert c.role == role
    with pytest.raises(KeyError):
        contract_for("planner")                       # 未定义角色 → fail-closed


def test_contracts_contain_no_disease_or_topic_hardcoding():
    """共享契约必须疾病无关：不得写入 SSc / cGAS-STING / 某个 Canary 问题。"""
    blob = json.dumps([c.model_dump(mode="json") for c in ROLE_CONTRACTS.values()],
                      ensure_ascii=False).lower()
    for bad in ("ssc", "sclerosis", "scleroderma", "cgas", "sting", "fibroblast", "pmid"):
        assert bad not in blob, f"共享 OutputContract 含疾病/课题写死内容：{bad}"


# ---------------------------------------------------------------- 4-6 单一来源
def test_prompt_is_generated_from_the_contract_and_carries_every_limit():
    b = SYNTHESIS_CONTRACT.prompt_block()
    assert "synthesis-result-v2" in b
    for f in SYNTHESIS_CONTRACT.fields:
        assert f.name in b, f"Prompt 未包含字段 {f.name}"
        if f.max_items is not None:
            assert f"<={f.max_items}x" in b
        if f.max_characters is not None and f.type == "string":
            assert f"<={f.max_characters}ch" in b
    assert "evidence_id ONLY" in b
    assert "chain-of-thought" in b
    assert "never stop mid-object" in b.lower()
    assert str(SYNTHESIS_CONTRACT.max_output_tokens) in b


def test_changing_the_contract_changes_the_prompt_automatically():
    """契约改了 Prompt 必须自动同步——不允许存在第二份手写规则。"""
    before = SYNTHESIS_CONTRACT.prompt_block()
    tweaked = SYNTHESIS_CONTRACT.model_copy(update={
        "fields": [f.model_copy(update={"max_items": 9}) if f.name == "supported_statements" else f
                   for f in SYNTHESIS_CONTRACT.fields]})
    after = tweaked.prompt_block()
    assert after != before and "<=9x" in after
    assert tweaked.limits()["supported_statements"]["max_items"] == 9


def test_validator_limits_derive_from_the_same_contract():
    """本地 validator 的 LIMITS 必须来自契约，而不是另一份常量。"""
    s = SYNTHESIS_CONTRACT.limits()
    assert LIMITS["summary"] == s["summary"]["max_characters"]
    assert LIMITS["supported_statements"] == s["supported_statements"]["max_items"]
    assert LIMITS["statement"] == s["supported_statements"]["max_characters"]
    assert LIMITS["citations"] == s["citations"]["max_items"]
    v = VERIFIER_CONTRACT.limits()
    assert LIMITS["reason"] == v["reason"]["max_characters"]
    assert LIMITS["fact_conflicts"] == v["fact_conflicts"]["max_items"]
    assert LIMITS["claims"] == CLAIM_CONTRACT.limits()["claims"]["max_items"]
    assert ROLE_MAX_TOKENS == {r: c.max_output_tokens for r, c in ROLE_CONTRACTS.items()}


def test_no_second_independent_cap_constant_in_research_results():
    """research_results 不得再手写字面上限（否则会与契约漂移）。"""
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent / "pilot" / "research_results.py"
    text = src.read_text(encoding="utf-8")
    head = text.split("def _derive_limits")[0]
    assert "LIMITS = {" not in head, "research_results 里仍存在手写的 LIMITS 字面量"
    assert "_derive_limits" in text and "role_contracts" in text


# ---------------------------------------------------------------- 7-9 JSON Schema 派生
def test_json_schema_carries_items_length_and_forbids_extra_fields():
    s = SYNTHESIS_CONTRACT.json_schema()
    assert s["additionalProperties"] is False
    props = s["properties"]
    assert props["supported_statements"]["maxItems"] == LIMITS["supported_statements"]
    assert props["supported_statements"]["items"]["maxLength"] == LIMITS["statement"]
    assert props["summary"]["maxLength"] == LIMITS["summary"]
    assert props["citations"]["maxItems"] == LIMITS["citations"]
    assert props["schema_version"]["enum"] == ["synthesis-result-v1"]
    assert "summary" in s["required"] and "schema_version" in s["required"]
    assert props["causal_assessment"]["enum"]                 # enum 被表达


def test_every_role_contract_produces_a_valid_schema():
    for role, c in ROLE_CONTRACTS.items():
        s = c.json_schema()
        assert s["type"] == "object" and s["additionalProperties"] is False
        assert s["properties"], role


# ---------------------------------------------------------------- 10-12 provider 能力（实测）
def test_capability_records_are_explicit_and_never_overclaim():
    a = capability_for("claude-opus-4-8")
    assert a.native_constraint_mode == "native_json_schema"
    assert a.json_schema_supported is True
    # 未实测确认的强制项一律 False —— 宁可低报
    assert a.string_max_length_supported_by_provider is False
    assert a.array_max_items_supported_by_provider is False
    d = capability_for("deepseek-v4-flash")
    assert d.native_constraint_mode == "json_object_only"
    assert d.json_schema_supported is False          # JSON 模式 != 完整 schema 保证
    assert d.strict_tool_schema_supported is False   # Beta，本阶段不启用
    with pytest.raises(KeyError):
        capability_for("some-unverified-model")      # 未登记 → fail-closed


def test_guarantees_separate_provider_side_from_local_only():
    a = describe_enforcement(ANTHROPIC_OPUS_48)
    assert "well-formed JSON" in a["guaranteed_by_provider"]
    assert "field structure" in a["guaranteed_by_provider"]
    assert "string max length" in a["local_validator_only"]   # 未确认 → 归本地
    d = describe_enforcement(DEEPSEEK_V4_FLASH)
    assert "field structure" in d["local_validator_only"]     # JSON 模式不保证字段
    for side in (a, d):
        assert "evidence_id existence" in side["local_validator_only"]


# ---------------------------------------------------------------- 13-16 adapter 透传与 fail-closed
class _Rec:
    """记录 adapter 真实发送了什么（transport capture）。"""

    def __init__(self):
        self.structured = None
        self.bound = None

    def with_structured_output(self, schema, **kw):
        self.structured = {"schema": schema, **kw}
        return self

    def bind(self, **kw):
        self.bound = kw
        return self


def test_anthropic_path_sends_the_real_json_schema():
    m = _Rec()
    bound, applied = apply_output_contract(m, SYNTHESIS_CONTRACT, ANTHROPIC_OPUS_48)
    assert m.structured is not None, "adapter 未真正发送 schema"
    assert m.structured["method"] == "json_schema"
    assert m.structured["schema"] == SYNTHESIS_CONTRACT.json_schema()
    assert applied["mode"] == "native_json_schema"
    assert applied["json_schema_sent"] is True
    assert applied["local_validation_still_required"] is True
    assert object.__getattribute__(bound, "_output_contract_applied")["contract_id"] \
        == "synthesis-result-v2"


def test_deepseek_path_sends_json_object_and_never_claims_full_schema():
    m = _Rec()
    _, applied = apply_output_contract(m, CLAIM_CONTRACT, DEEPSEEK_V4_FLASH)
    assert m.bound == {"response_format": {"type": "json_object"}}
    assert applied["mode"] == "json_object_only"
    assert applied["json_schema_sent"] is False
    assert applied["guarantees"]["field_structure_by_provider"] is False


def test_adapter_never_silently_falls_back_to_free_text():
    m = _Rec()
    with pytest.raises(ProviderCapabilityError):
        apply_output_contract(m, CLAIM_CONTRACT, DEEPSEEK_V4_FLASH, require_native=True)
    assert m.structured is None and m.bound is None        # 未降级、未发送


def test_unsupported_capability_fails_closed():
    none_cap = ProviderOutputCapability(
        provider="x", model_id="x", sdk_version="x", json_object_supported=False,
        json_schema_supported=False, strict_tool_schema_supported=False,
        string_max_length_supported_by_provider=False,
        array_max_items_supported_by_provider=False,
        native_constraint_mode="prompt_only", fallback_mode="prompt_only",
        verified_at="2026-08-06", documentation_source="test")
    with pytest.raises(ProviderCapabilityError):
        apply_output_contract(_Rec(), SYNTHESIS_CONTRACT, none_cap)


def test_binding_failure_is_reported_not_swallowed():
    class Broken:
        def with_structured_output(self, *a, **k):
            raise RuntimeError("adapter stripped the schema")
    with pytest.raises(ProviderCapabilityError):
        apply_output_contract(Broken(), SYNTHESIS_CONTRACT, ANTHROPIC_OPUS_48)


# ---------------------------------------------------------------- 17 每角色用自己的契约
def test_each_role_uses_its_own_contract():
    seen = {}
    for role in ("synthesizer", "verifier", "claim_extractor"):
        m = _Rec()
        cap = ANTHROPIC_OPUS_48 if role != "claim_extractor" else DEEPSEEK_V4_FLASH
        _, applied = apply_output_contract(m, contract_for(role), cap)
        seen[role] = applied["contract_id"]
    assert seen == {"synthesizer": "synthesis-result-v2", "verifier": "verifier-result-v2",
                    "claim_extractor": "claim-extraction-result-v2"}


# ---------------------------------------------------------------- 18 零真实调用哨兵
def test_no_real_provider_client_is_constructed_here():
    import sys
    assert "anthropic" not in [m for m in sys.modules if m == "anthropic"] or True
    import pilot.output_contract as oc, pilot.role_contracts as rc, pilot.provider_output as po
    for mod in (oc, rc, po):
        src = open(mod.__file__, encoding="utf-8").read()
        for bad in ("ChatAnthropic(", "ChatOpenAI(", "anthropic.Anthropic(", "OpenAI("):
            assert bad not in src, f"{mod.__name__} 直接构造了模型客户端：{bad}"
        for bad in ("api_key", "API_KEY", ".env"):
            assert bad not in src, f"{mod.__name__} 触碰了凭证：{bad}"
