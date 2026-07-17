"""
Claim–Evidence Graph：把答案拆成原子 Claim，每个 Claim 按【自己的证据要求】单独裁决。
铁律：证据要求不能互相替代——
  - 因果 claim 不能只靠相关性证据（相关≠因果）；
  - 临床疗效 claim 不能只靠动物/体外（临床前≠临床）；
  - 存在性/关联性 claim 要求较低但仍需可追溯证据。
Verifier 对每个 Claim 输出：supported / partially_supported / not_supported /
contradicted / insufficient_evidence / technically_unverifiable。
"""

from schemas import Claim


# ---- 证据是否满足某类 claim 的要求（基于 EvidenceCard 字段，不看期刊影响因子）----
def _st(card):
    return (getattr(card, "study_type", "") or "").lower()


def _is_human(card):
    return (getattr(card, "species", "") or "").lower() in ("human", "人", "患者", "homo sapiens")


def _has_data(card):
    return getattr(card, "tier", "") in ("fulltext", "analysis")     # 有全文/自有分析数据


def _interventional(card):
    return any(k in _st(card) for k in ["knockdown", "crispr", "perturbation", "扰动", "敲除",
                                        "overexpress", "过表达", "rct", "randomized", "interventional",
                                        "干预", "inhibitor", "抑制剂", "treated", "knockout"])


def _clinical_design(card):
    return any(k in _st(card) for k in ["rct", "randomized", "cohort", "队列", "clinical trial",
                                        "临床试验", "clinical"])


def _direction(card):
    return getattr(card, "evidence_direction", "inconclusive")


def _excerpt(card):
    return bool((getattr(card, "supporting_excerpt", "") or "").strip())


def meets_requirement(claim: Claim, card) -> bool:
    d = _direction(card)
    if d == "refutes":
        return False
    ct = claim.claim_type
    if ct == "existence":
        return _excerpt(card) and d in ("supports", "mixed", "correlational")
    if ct == "association":
        return d in ("supports", "correlational", "mixed") and _has_data(card)
    if ct == "causal":
        # 关键：必须是干预/扰动证据且方向支持；相关性不算
        return d == "supports" and _interventional(card) and _has_data(card)
    if ct == "mechanistic":
        return d in ("supports", "mixed") and (_interventional(card) or _is_human(card)) and _has_data(card)
    if ct == "clinical_efficacy":
        # 必须人体 + 临床设计；动物/体外不算
        return (d == "supports" and _is_human(card) and _clinical_design(card)
                and getattr(card, "publication_status", "") != "retracted")
    return d == "supports" and _excerpt(card)


def _credible_contra(card) -> bool:
    return _direction(card) == "refutes" and (_has_data(card) or _excerpt(card))


def _gap_reason(claim: Claim) -> str:
    return {
        "causal": "现有证据为相关性/观察性，不能确立因果（相关≠因果，需干预/扰动实验）",
        "clinical_efficacy": "仅临床前/非临床试验证据，不能支持临床疗效（临床前≠临床）",
        "mechanistic": "机制证据不足（缺人体组织或扰动实验）",
        "existence": "证据强度不足（如仅摘要级，缺可追溯原文）",
        "association": "关联证据不足或不够直接",
    }.get(claim.claim_type, "证据不足以完全支持该主张")


def adjudicate_claim(claim: Claim, store: dict) -> Claim:
    """store: {evidence_id: EvidenceCard}。返回带 verdict/uncertainty/human_review 的新 Claim。"""
    all_ids = claim.supporting_evidence_ids + claim.contradicting_evidence_ids + claim.unresolved_evidence_ids
    unresolved = [i for i in all_ids if i not in store]
    sup = [store[i] for i in claim.supporting_evidence_ids if i in store]
    con = [store[i] for i in claim.contradicting_evidence_ids if i in store]

    meeting = [c for c in sup if meets_requirement(claim, c)]
    relevant = [c for c in sup if _direction(c) in ("supports", "correlational", "mixed") and c not in meeting]
    contra_meeting = [c for c in con if _credible_contra(c)]

    if not sup and not con:
        verdict, unc = "insufficient_evidence", "无相关证据"
    elif contra_meeting and not meeting:
        verdict, unc = "contradicted", "存在可信反证且缺乏达标的支持证据"
    elif meeting and contra_meeting:
        verdict, unc = "partially_supported", "支持与反证并存，需人工裁决"
    elif meeting:
        verdict, unc = "supported", ""
    elif relevant:
        verdict, unc = "partially_supported", _gap_reason(claim)   # 有相关证据但达不到该类 claim 的要求
    else:
        verdict, unc = "not_supported", "有证据但不支持该主张"

    human_review = (claim.human_review_required
                    or verdict in ("contradicted", "partially_supported")
                    or (claim.claim_type in ("causal", "clinical_efficacy") and verdict != "supported"))

    return claim.model_copy(update={
        "verdict": verdict, "uncertainty": unc,
        "unresolved_evidence_ids": unresolved, "human_review_required": human_review,
    })


class ClaimEvidenceGraph:
    """一组 Claim + 一批 EvidenceCard；逐个 Claim 独立裁决，绝不合并成一个笼统结论。"""

    def __init__(self, claims, evidence_cards):
        self.store = {c.evidence_id: c for c in evidence_cards}
        self.claims = list(claims)

    def adjudicate(self):
        return [adjudicate_claim(c, self.store) for c in self.claims]

    def summary(self):
        judged = self.adjudicate()
        by = {}
        for c in judged:
            by[c.verdict] = by.get(c.verdict, 0) + 1
        need_review = [c.claim_id for c in judged if c.human_review_required]
        return {"n_claims": len(judged), "by_verdict": by, "human_review": need_review, "claims": judged}
