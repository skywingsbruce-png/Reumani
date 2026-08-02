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
    def _len(cls, v):
        if not str(v).strip():
            raise ValueError("summary 不能为空")
        return str(v)[:4000]


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


class ClaimExtractionResult(_Strict):
    schema_version: Literal["claim-extraction-v1"] = "claim-extraction-v1"
    claims: list[ExtractedClaim] = Field(default_factory=list)


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
