"""确定性事实契约 + Verifier 事实接地（A.7.2）。

把两类判断明确分开：
- **确定性事实**（PMID/DOI 是否存在、逐来源 retrieval_status、综合 resolution）——由
  ExactIdResolver 的终态给出，程序判定，**绝不**从模型自然语言生成；
- **科学证据判断**（现有 EvidenceCard 是否足以支持机制/因果/临床结论）——由旧 Verifier /
  Shadow 判定，**裁决权不变**。

本模块只：把 Resolver 终态转成只读事实、给 Verifier 结构化事实上下文、在 Verifier 返回后
做确定性事实冲突检测、输出两维 verdict（resolution / scientific_support）。
不改旧 Verifier 裁决权，不因冲突自动改判 passed，不静默覆盖旧结果。
"""

from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from tool_envelope import compute_hash

FACT_SCHEMA = "exactid-fact-v1"


class DeterministicFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = FACT_SCHEMA
    fact_id: str
    subject: str                 # 例 "PMID:41657283" / "DOI:10.1080/..."
    predicate: str = "resolution_status"
    value: str                   # object/value，例 "verified" / "not_found"
    resolution_status: str
    source_results: list[dict] = Field(default_factory=list)   # 逐来源 retrieval_status
    source_count: int = 0
    provenance: dict = Field(default_factory=dict)
    content_hash: str
    hash_algorithm: str = "sha256"


def facts_from_batch(batch) -> list[DeterministicFact]:
    """从 ExactIdBatchResult 的确定性终态构造只读事实。事实只来自工具结果，绝不来自 NL。"""
    facts = []
    for r in batch.ids:                     # r = ExactIdResolution.model_dump()
        subj = f"{r['id_type'].upper()}:{r['normalized_id']}"
        sources = [{"source": s.get("source"), "retrieval_status": s.get("retrieval_status")}
                   for s in (r.get("source_results") or [])]
        core = {"subject": subj, "predicate": "resolution_status",
                "value": r["resolution_status"],
                "retrieval_status_by_source": r.get("retrieval_status_by_source", {}),
                "canonical_pmid": r.get("canonical_pmid"),
                "canonical_doi": r.get("canonical_doi")}
        facts.append(DeterministicFact(
            fact_id=f"fact:{subj}", subject=subj, predicate="resolution_status",
            value=r["resolution_status"], resolution_status=r["resolution_status"],
            source_results=sources, source_count=len(sources),
            provenance=r.get("provenance", {}), content_hash=compute_hash(core)))
    return facts


# ---------------- §3 给 Verifier 的结构化事实上下文 ----------------
def _card_summary(c):
    prov = c.get("provenance") or {}
    return {"pmid": c.get("pmid"), "doi": c.get("doi"),
            "title": c.get("title"), "content_level": prov.get("content_level"),
            "content_hash": prov.get("content_hash")}


def _claim_summary(c):
    return {"claim_id": c.get("claim_id"), "text": c.get("text"),
            "claim_type": c.get("claim_type"), "causal_strength": c.get("causal_strength"),
            "verdict": c.get("verdict")}


def build_fact_context(facts, cards, claims, limitations=None) -> dict:
    """四段明确分开，禁止拼成含义模糊的普通叙述。"""
    return {
        "authoritative_deterministic_facts": [f.model_dump() for f in facts],
        "evidence_cards": [_card_summary(c) for c in (cards or [])],
        "candidate_claims": [_claim_summary(c) for c in (claims or [])],
        "evidence_limitations": list(limitations or []),
    }


def render_fact_context_text(context: dict) -> str:
    """给 Verifier 的**清晰分段**文本；各段落有明确标题，不混为一段叙述。
    明确声明：ID 存在性是确定性事实，Verifier 只判证据是否充分，不得无来源重定义 ID 状态。"""
    lines = ["【权威确定性事实（ID 存在性，由多来源解析确定；Verifier 不得无来源推翻）】"]
    for f in context["authoritative_deterministic_facts"]:
        bysrc = ", ".join(f"{s['source']}={s['retrieval_status']}" for s in f["source_results"])
        lines.append(f"- {f['subject']} → resolution={f['value']}（{bysrc}；"
                     f"content_hash={f['content_hash'][:12]}…）")
    lines.append("\n【EvidenceCard（仅列可核验来源；证据层级见 content_level）】")
    for c in context["evidence_cards"]:
        lines.append(f"- pmid={c['pmid']} doi={c['doi']} content_level={c['content_level']} "
                     f"title={str(c['title'])[:60]}")
    if not context["evidence_cards"]:
        lines.append("-（无）")
    lines.append("\n【候选 Claim（待你判断证据是否充分，不是既定结论）】")
    for c in context["candidate_claims"]:
        lines.append(f"- {c['claim_id']}: {c['text']}（{c['claim_type']}）")
    if not context["candidate_claims"]:
        lines.append("-（无）")
    lines.append("\n【证据局限】")
    for lim in context["evidence_limitations"]:
        lines.append(f"- {lim}")
    if not context["evidence_limitations"]:
        lines.append("-（无）")
    return "\n".join(lines)


# ---------------- §4 事实冲突检测（确定性，程序核对）----------------
CONFLICT_NOT_FOUND_VALID = "not_found_claimed_valid"
CONFLICT_VERIFIED_NONEXISTENT = "verified_claimed_nonexistent"
CONFLICT_SOURCE_ERROR_AS_ZERO = "source_error_as_zero_hits"
CONFLICT_METADATA_AS_FULLTEXT = "metadata_only_as_fulltext"

# 断言 ID 有效/存在（不是提问、不是复述 not_found）
_VALID_ASSERT = [r"实际[^，。；\n]{0,6}有效", r"确实[^，。；\n]{0,4}有效",
                 r"对应[^，。；\n]{0,6}有效期刊", r"该\s*DOI[^，。；\n]{0,6}有效",
                 r"确实[^，。；\n]{0,4}存在", r"是[^，。；\n]{0,4}有效的",
                 r"valid\s+(journal\s+)?article", r"does\s+(indeed\s+)?exist",
                 r"is\s+a\s+valid"]
# 断言已 verified 的 ID 不存在/无效
_NONEXIST_ASSERT = [r"不存在", r"无效", r"查无", r"does\s+not\s+exist", r"is\s+invalid",
                    r"无法找到该\s*(PMID|文献)"]
# 把 metadata-only 说成全文证据
_FULLTEXT_ASSERT = [r"全文证据", r"依据全文", r"full[\s-]?text\s+evidence",
                    r"已(由|有)全文", r"提供了全文"]
# 提问/否定守卫（避免把"是否有效""被报告为 not_found"误判为断言）
_QUESTION_GUARD = [r"是否", r"whether", r"待核实", r"需核实", r"？", r"\?"]


def _has(patterns, text):
    return any(re.search(p, text, re.I) for p in patterns)


def _mentions_subject(text, fact):
    """文本是否点到该 fact 的具体 ID（用规范化 id 子串）。"""
    nid = fact.subject.split(":", 1)[1] if ":" in fact.subject else fact.subject
    return nid and nid in text


def detect_verifier_fact_conflicts(verifier_statement, facts, cards=None) -> list[dict]:
    """在 Verifier 返回后做确定性核对。Verifier 的自然语言**不得**自动成为新事实。
    verifier_statement：Verifier 的自然语言（reason + missing 拼接即可）。"""
    text = verifier_statement if isinstance(verifier_statement, str) else str(verifier_statement)
    conflicts = []
    for f in facts:
        if not _mentions_subject(text, f):
            continue
        rs = f.resolution_status
        # not_found 被说成有效（提问"是否有效"不算断言）
        if rs == "not_found" and _has(_VALID_ASSERT, text) and not _has(_QUESTION_GUARD, text):
            conflicts.append(_conflict(CONFLICT_NOT_FOUND_VALID, f, text,
                             "已被多源判定为 not_found 的 ID 被 Verifier 声称有效/存在"))
        # verified 被说成不存在（且不是提问）
        if rs == "verified" and _has(_NONEXIST_ASSERT, text) and not _has(_QUESTION_GUARD, text):
            conflicts.append(_conflict(CONFLICT_VERIFIED_NONEXISTENT, f, text,
                             "已 verified 的 ID 被 Verifier 声称不存在/无效"))
        # source_error 被当成确定 zero_hits/not_found
        if any(s.get("retrieval_status") == "source_error" for s in f.source_results) \
                and rs == "manual_needed" and _has([r"not[_\s]?found", r"确认无记录", r"不存在",
                                                     r"无该记录"], text) \
                and not _has(_QUESTION_GUARD, text):
            conflicts.append(_conflict(CONFLICT_SOURCE_ERROR_AS_ZERO, f, text,
                             "来源为 source_error（网络/解析失败）却被当成确定的 zero_hits/not_found"))
    # metadata_only 被说成全文证据（针对全体卡）
    if cards and _has(_FULLTEXT_ASSERT, text):
        if any((c.get("provenance") or {}).get("content_level") == "metadata_only" for c in cards):
            conflicts.append({"conflict_type": CONFLICT_METADATA_AS_FULLTEXT,
                              "authoritative_fact_ids": [f.fact_id for f in facts],
                              "conflicting_statement": _excerpt(text),
                              "detail": "仅 metadata_only 的证据被 Verifier 说成全文证据"})
    return conflicts


def _conflict(ctype, fact, text, detail):
    return {"conflict_type": ctype, "authoritative_fact_ids": [fact.fact_id],
            "subject": fact.subject, "authoritative_value": fact.resolution_status,
            "conflicting_statement": _excerpt(text), "detail": detail}


def _excerpt(text, n=220):
    return (text or "")[:n]


# ---------------- §5 两维 verdict（不转移裁决权）----------------
def two_dimension_verdict(facts, old_verifier_result, conflicts) -> dict:
    """resolution_verdict 由确定性 Resolver 决定；scientific_support_verdict 由旧 Verifier 决定。
    不因事实冲突自动把整体判为 passed；旧 Verifier 保留最终裁决权。"""
    ovr = old_verifier_result or {}
    all_terminal = all(f.resolution_status in ("verified", "not_found", "mismatch", "manual_needed")
                       for f in facts)
    needs_human = any(f.resolution_status in ("mismatch", "manual_needed") for f in facts)
    resolution_verdict = {
        "decided_by": "deterministic_resolver",
        "all_terminal": all_terminal,
        "per_subject": {f.subject: f.resolution_status for f in facts},
        "human_review_required": needs_human,
        "note": "ID 存在性/解析终态是确定性事实，不由 Verifier 的自然语言改写。",
    }
    scientific_support_verdict = {
        "decided_by": "old_verifier",
        "passed": ovr.get("passed"),
        "status": ovr.get("status"),
        "reason": ovr.get("reason"),
        "note": "现有证据是否足以支持科学结论，由旧 Verifier/Shadow 判定，裁决权不变。",
    }
    return {
        "resolution_verdict": resolution_verdict,
        "scientific_support_verdict": scientific_support_verdict,
        "verifier_fact_conflict": bool(conflicts),
        "conflict_types": sorted({c["conflict_type"] for c in conflicts}),
        "fact_conflicts": conflicts,
        "human_review_required": bool(conflicts) or needs_human or (ovr.get("passed") is not True),
        "final_answer_authority": "old_verifier",     # 不变
        "auto_flip_to_passed": False,                  # 绝不因冲突自动改判
        "note": "两维分离：解析终态=确定性事实；科学充分性=旧 Verifier。冲突只标记+人工复核，"
                "不静默覆盖旧结果，不自动改判 passed。",
    }
