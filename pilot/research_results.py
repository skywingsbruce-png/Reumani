"""A.7.5.5 —— 三角色的**结构化**输出契约 + 确定性因果上限守卫。

纯数据层：不 import 任何模型客户端。malformed / 自然语言散文 → fail-closed。
"""

from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import Field, field_validator

from schemas import _Strict

# 冻结证据不支持人体直接因果时，这些表述一律禁止出现在最终产物里
FORBIDDEN_CAUSAL_PHRASES = (
    "direct human causality established", "proven to cause ssc", "proven to cause systemic sclerosis",
    "clinically demonstrated causal effect", "demonstrates direct causation",
    "establishes direct causality", "causally proven", "conclusively causes",
)
ALLOWED_CAUSAL_LABELS = ("association", "mechanistic_support", "preclinical_perturbation_support",
                         "hypothesis_generating", "insufficient_for_direct_human_causality")


class ResearchOutputError(ValueError):
    """结构化输出违规（伪造引用 / 越权因果 / schema 违规）→ fail-closed。"""


# A.7.5.6.1 §6 —— 结构化输出的硬性尺寸上限。
# 目的：让「最坏合法 JSON」可计算，从而能确定 max_tokens 和最坏费用。
# 超限一律**拒绝**（不再静默截断）：静默截断会把越界输出伪装成合法结果。
# 这些数值不是随手选的：它们由「最坏合法 JSON 必须放得进 max_tokens，且三角色最坏费用
# ≤ USD 0.15」反推而来（见 ROLE_MAX_TOKENS）。放宽任何一项都会突破预算。
LIMITS = {
    "summary": 320,
    "statement": 140,          # 单条陈述
    "supported_statements": 4,
    "unsupported_statements": 4,
    "contradictions": 2,
    "evidence_gaps": 2,
    "limitations": 3,
    "citations": 6,            # 冻结子集恰有 6 张核心卡，不需要更多引用位
    "evidence_id": 32,         # 实际 evidence_id 形如 SSCCGAS-40374521（16 字符）
    "reason": 260,
    "conflict": 140,
    "fact_conflicts": 2,
    "citation_conflicts": 2,
    "unsupported_claims": 3,
    "required_corrections": 3,
    "claims": 5,
    "claim_text": 250,
    "claim_limitations": 3,
    "claim_limitation": 150,
}

# A.7.5.6.1 §6 —— 每角色输出上限。由上面的 caps 推出的最坏合法 JSON 长度决定，
# 再按 2.0 字符/token 折算并留少量余量。第一次 Canary 的失败正是 1500 不够。
ROLE_MAX_TOKENS = {"synthesizer": 1600, "verifier": 1150, "claim_extractor": 2400}
CHARS_PER_OUTPUT_TOKEN = 2.0


def worst_case_output_chars(role: str) -> int:
    """该角色**最坏合法**结构化 JSON 的字符数（用于校核 max_tokens 是否够用）。"""
    L = LIMITS
    if role == "synthesizer":
        content = (L["summary"]
                   + L["statement"] * (L["supported_statements"] + L["unsupported_statements"]
                                       + L["contradictions"] + L["evidence_gaps"]
                                       + L["limitations"])
                   + L["citations"] * L["evidence_id"])
        return content + 400                      # JSON 键名/标点/schema_version 等结构开销
    if role == "verifier":
        content = (L["reason"] + L["conflict"] * (L["fact_conflicts"] + L["citation_conflicts"])
                   + L["statement"] * (L["unsupported_claims"] + L["required_corrections"]))
        return content + 400
    if role == "claim_extractor":
        per = L["claim_text"] + L["claim_limitations"] * L["claim_limitation"] + 200
        return L["claims"] * per + 200
    raise KeyError(role)


def assert_max_tokens_sufficient(role: str, max_tokens: int) -> None:
    """max_tokens 必须能容纳最坏合法输出，否则合法结果也会被截断（第一次 Canary 的教训）。"""
    need = int(worst_case_output_chars(role) / CHARS_PER_OUTPUT_TOKEN)
    if max_tokens < need:
        raise ValueError(
            f"{role} 的 max_tokens={max_tokens} 不足以容纳最坏合法输出（需要 ≥ {need}）")


def _capped(field, max_items, max_chars):
    """构造一个「拒绝超长」的 list[str] 校验器。"""
    def _check(v):
        items = list(v or [])
        if len(items) > max_items:
            raise ValueError(f"{field} 超出条数上限（{len(items)} > {max_items}）")
        for i, s in enumerate(items):
            if len(str(s)) > max_chars:
                raise ValueError(f"{field}[{i}] 超出长度上限（{len(str(s))} > {max_chars} 字符）")
        return [str(s) for s in items]
    return _check


class SynthesisResult(_Strict):
    schema_version: Literal["synthesis-result-v1"] = "synthesis-result-v1"
    summary: str
    supported_statements: list[str] = Field(default_factory=list)
    unsupported_statements: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    causal_assessment: Literal[ALLOWED_CAUSAL_LABELS] = "insufficient_for_direct_human_causality"  # type: ignore[valid-type]
    limitations: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)

    @field_validator("summary")
    @classmethod
    def _summary(cls, v):
        if not str(v).strip():
            raise ValueError("summary 不能为空")
        if len(str(v)) > LIMITS["summary"]:
            raise ValueError(f"summary 超出长度上限（{len(str(v))} > {LIMITS['summary']} 字符）")
        return str(v)

    @field_validator("supported_statements", "unsupported_statements", "contradictions",
                     "evidence_gaps", "limitations")
    @classmethod
    def _lists(cls, v, info):
        return _capped(info.field_name, LIMITS[info.field_name], LIMITS["statement"])(v)

    @field_validator("citations")
    @classmethod
    def _citations(cls, v):
        # citations 只保存 evidence_id：不得夹带摘要 / 书目 / 整段正文
        return _capped("citations", LIMITS["citations"], LIMITS["evidence_id"])(v)


class VerifierResult(_Strict):
    schema_version: Literal["verifier-result-v1"] = "verifier-result-v1"
    verdict: Literal["supported", "partially_supported", "not_supported",
                     "contradicted", "insufficient_evidence", "technically_unverifiable"]
    reason: str = ""
    fact_conflicts: list[str] = Field(default_factory=list)
    citation_conflicts: list[str] = Field(default_factory=list)
    causal_overstatement: bool = False
    unsupported_claims: list[str] = Field(default_factory=list)
    required_corrections: list[str] = Field(default_factory=list)
    human_review: bool = False

    @field_validator("reason")
    @classmethod
    def _reason(cls, v):
        if len(str(v)) > LIMITS["reason"]:
            raise ValueError(f"reason 超出长度上限（{len(str(v))} > {LIMITS['reason']} 字符）")
        return str(v)

    @field_validator("fact_conflicts", "citation_conflicts")
    @classmethod
    def _conflicts(cls, v, info):
        return _capped(info.field_name, LIMITS[info.field_name], LIMITS["conflict"])(v)

    @field_validator("unsupported_claims", "required_corrections")
    @classmethod
    def _vlists(cls, v, info):
        return _capped(info.field_name, LIMITS[info.field_name], LIMITS["statement"])(v)


class ExtractedClaim(_Strict):
    claim_id: str
    claim_text: str
    claim_type: Literal["existence", "association", "causal", "mechanistic",
                        "clinical_efficacy", "other"] = "other"
    causal_strength: Literal["none", "correlational", "associative", "mechanistic",
                             "causal", "unknown"] = "unknown"
    evidence_ids: list[str] = Field(default_factory=list)
    support_status: Literal["supported", "partially_supported", "unsupported",
                            "insufficient_evidence"] = "insufficient_evidence"
    limitations: list[str] = Field(default_factory=list)

    @field_validator("claim_text")
    @classmethod
    def _text(cls, v):
        if not str(v).strip():
            raise ValueError("claim_text 不能为空")
        if len(str(v)) > LIMITS["claim_text"]:
            raise ValueError(f"claim_text 超出长度上限（{len(str(v))} > {LIMITS['claim_text']} 字符）")
        return str(v)

    @field_validator("evidence_ids")
    @classmethod
    def _eids(cls, v):
        return _capped("evidence_ids", LIMITS["citations"], LIMITS["evidence_id"])(v)

    @field_validator("limitations")
    @classmethod
    def _lims(cls, v):
        return _capped("limitations", LIMITS["claim_limitations"], LIMITS["claim_limitation"])(v)


class ClaimExtractionResult(_Strict):
    schema_version: Literal["claim-extraction-v1"] = "claim-extraction-v1"
    claims: list[ExtractedClaim] = Field(default_factory=list)

    @field_validator("claims")
    @classmethod
    def _claims(cls, v):
        items = list(v or [])
        if len(items) > LIMITS["claims"]:
            raise ValueError(f"claims 超出条数上限（{len(items)} > {LIMITS['claims']}）")
        return items


# ----------------------------- 确定性守卫（不替代 Verifier，只防止违反冻结事实） -----------------------------
def assert_citations_allowed(citations, allowed_ids, context_only_ids, where):
    unknown = [c for c in citations if c not in allowed_ids]
    if unknown:
        ctx = [c for c in unknown if c in context_only_ids]
        if ctx:
            raise ResearchOutputError(
                f"{where} 引用了 context-only 综述作为实验依据（禁止）：{sorted(ctx)}")
        raise ResearchOutputError(f"{where} 引用了不存在的 evidence_id（疑似伪造）：{sorted(unknown)}")


_ID_PAT = re.compile(r"\b(?:PMID[:\s]*\d{6,9}|10\.\d{4,9}/[^\s\"'<>]+)", re.I)


def assert_no_new_identifiers(text_blobs, allowed_pmids, allowed_dois, where):
    """模型不得在正文中凭空生成新的 PMID/DOI。"""
    for blob in text_blobs:
        for m in _ID_PAT.findall(blob or ""):
            token = m.strip()
            digits = re.sub(r"\D", "", token)
            if token.lower().startswith("10."):
                if token.lower().rstrip(".,;)") not in allowed_dois:
                    raise ResearchOutputError(f"{where} 生成了冻结证据之外的 DOI：{token}")
            elif digits and digits not in allowed_pmids:
                raise ResearchOutputError(f"{where} 生成了冻结证据之外的 PMID：{token}")


def assert_causal_ceiling(payload_texts, causal_label, direct_human_causal_count, where):
    """冻结事实 direct_human_causal_count=0 时，禁止任何“已证明人体直接因果”的表述。"""
    if direct_human_causal_count > 0:
        return
    if causal_label not in ALLOWED_CAUSAL_LABELS:
        raise ResearchOutputError(f"{where} 使用了未知因果标签：{causal_label}")
    if causal_label == "direct_human_causal_supported":       # pragma: no cover - 防御
        raise ResearchOutputError(f"{where} 越过因果上限")
    low = " ".join(t.lower() for t in payload_texts if t)
    for bad in FORBIDDEN_CAUSAL_PHRASES:
        if bad in low:
            raise ResearchOutputError(
                f"{where} 违反因果上限（direct_human_causal_count=0）：出现 “{bad}”")


def assert_claim_not_upgraded(claims, verifier: VerifierResult, where):
    """Claim 的因果强度不得超过 Verifier 允许的范围。"""
    if verifier.verdict in ("insufficient_evidence", "not_supported", "contradicted",
                            "technically_unverifiable"):
        for c in claims:
            if c.causal_strength == "causal" or c.support_status == "supported":
                raise ResearchOutputError(
                    f"{where} 把 Verifier 判定为 {verifier.verdict} 的内容升级为 "
                    f"{c.support_status}/{c.causal_strength}：{c.claim_id}")
    for c in claims:
        if c.claim_text.strip().lower() in {u.strip().lower() for u in verifier.unsupported_claims}:
            if c.support_status == "supported":
                raise ResearchOutputError(f"{where} 把 Verifier 标记为 unsupported 的陈述改为 supported")


__all__ = ["SynthesisResult", "VerifierResult", "ExtractedClaim", "ClaimExtractionResult",
           "ResearchOutputError", "FORBIDDEN_CAUSAL_PHRASES", "ALLOWED_CAUSAL_LABELS",
           "assert_citations_allowed", "assert_no_new_identifiers", "assert_causal_ceiling",
           "assert_claim_not_upgraded"]
