"""A.7.2：确定性事实契约 + Verifier 事实接地。零真实 API、零付费。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pilot.exact_id_facts import (CONFLICT_METADATA_AS_FULLTEXT, CONFLICT_NOT_FOUND_VALID,
                                  CONFLICT_SOURCE_ERROR_AS_ZERO, CONFLICT_VERIFIED_NONEXISTENT,
                                  DeterministicFact, build_fact_context,
                                  detect_verifier_fact_conflicts, facts_from_batch,
                                  render_fact_context_text, two_dimension_verdict)
from pilot.exact_id_resolver import resolve_exact_ids

PMID = "41657283"
DOI = "10.1080/03009742.2024.2302553"


def hit(s, m): return {"source": s, "retrieval_status": "exact_hit", "metadata": m,
                       "error_type": None, "http_status": 200, "attempts": []}
def zero(s): return {"source": s, "retrieval_status": "zero_hits", "metadata": {},
                     "error_type": None, "http_status": 200, "attempts": []}
def serr(s): return {"source": s, "retrieval_status": "source_error", "metadata": {},
                     "error_type": "timeout", "http_status": None, "attempts": []}


class Src:
    """按 id 预置来源结果。"""
    def __init__(self, pubmed=None, epmc_pmid=None, crossref=None, doiorg=None, epmc_doi=None):
        self.pubmed, self.epmc_pmid = pubmed or {}, epmc_pmid or {}
        self.crossref, self.doiorg, self.epmc_doi = crossref or {}, doiorg or {}, epmc_doi or {}
    def pubmed_by_pmid(self, p): return self.pubmed.get(p, zero("pubmed"))
    def epmc_by_pmid(self, p): return self.epmc_pmid.get(p, zero("europepmc"))
    def crossref_by_doi(self, d): return self.crossref.get(d, zero("crossref"))
    def doiorg_by_doi(self, d): return self.doiorg.get(d, zero("doi.org"))
    def epmc_by_doi(self, d): return self.epmc_doi.get(d, zero("europepmc"))


def a1_batch():
    """复现冻结 A1-rerun3 的确定性终态：PMID verified、DOI not_found。"""
    meta = {"pmid": PMID, "doi": "10.1080/07853890.2026.2627057", "title": "CAR-T",
            "journal": "Annals of medicine", "year": "2026"}
    s = Src(pubmed={PMID: hit("pubmed", meta)}, epmc_pmid={PMID: hit("europepmc", meta)},
            crossref={DOI: zero("crossref")}, doiorg={DOI: zero("doi.org")},
            epmc_doi={DOI: zero("europepmc")})
    return resolve_exact_ids(f"核验 PMID {PMID} 与 DOI {DOI} 的精确命中", sources=s)


VOK = {"passed": True, "status": "passed", "reason": "ok", "missing": []}


# ---------------- §2 事实契约 ----------------
@pytest.mark.unit
def test_facts_from_deterministic_batch_have_hash_and_provenance():
    b = a1_batch()
    facts = facts_from_batch(b)
    assert len(facts) == 2
    by = {f.subject: f for f in facts}
    assert by[f"PMID:{PMID}"].resolution_status == "verified"
    assert by[f"DOI:{DOI}"].resolution_status == "not_found"
    for f in facts:                                            # #8 hash + provenance 保留
        assert f.hash_algorithm == "sha256" and len(f.content_hash) == 64
        assert f.content_hash == f.content_hash.lower()
        assert f.source_count == len(f.source_results) >= 1
        assert f.provenance.get("tool_name") == "resolve_exact_ids"


@pytest.mark.unit
def test_fact_hash_stable_and_content_bound():
    h1 = {f.subject: f.content_hash for f in facts_from_batch(a1_batch())}
    h2 = {f.subject: f.content_hash for f in facts_from_batch(a1_batch())}
    assert h1 == h2                                            # 相同终态 → 相同 hash


@pytest.mark.unit
def test_natural_language_cannot_create_facts():
    """#7：事实只来自确定性 batch；NL 检测/上下文构建不新增事实。"""
    facts = facts_from_batch(a1_batch())
    n0 = len(facts)
    detect_verifier_fact_conflicts("该 DOI 其实有效，还有 PMID 99999999 也存在", facts)
    build_fact_context(facts, [], [], [])
    assert len(facts) == n0                                    # 未被自然语言增改


# ---------------- §3 结构化上下文 ----------------
@pytest.mark.unit
def test_fact_context_sections_are_separated():
    b = a1_batch()
    ctx = build_fact_context(facts_from_batch(b), b.evidence_cards, [], ["metadata_only"])
    assert set(ctx) == {"authoritative_deterministic_facts", "evidence_cards",
                        "candidate_claims", "evidence_limitations"}
    txt = render_fact_context_text(ctx)
    for header in ("权威确定性事实", "EvidenceCard", "候选 Claim", "证据局限"):
        assert header in txt


# ---------------- §4 冲突检测（tests 1-7）----------------
@pytest.mark.unit
def test_conflict_not_found_doi_claimed_valid():
    """#1：Verifier 错称 not_found DOI 有效 → 捕获冲突。"""
    facts = facts_from_batch(a1_batch())
    stmt = f"第二条记录 DOI {DOI} 被报告为 not_found，但该 DOI 实际对应有效期刊文章。"
    c = detect_verifier_fact_conflicts(stmt, facts)
    assert any(x["conflict_type"] == CONFLICT_NOT_FOUND_VALID for x in c)


@pytest.mark.unit
def test_no_conflict_when_doi_not_found_restated():
    """#2：Verifier 正确复述 DOI not_found → 无冲突。"""
    facts = facts_from_batch(a1_batch())
    stmt = f"DOI {DOI} 经多来源确认 not_found，无法提供标题/年份/期刊。"
    assert detect_verifier_fact_conflicts(stmt, facts) == []


@pytest.mark.unit
def test_no_conflict_when_metadata_only_insufficient():
    """#3：Verifier 因 metadata_only 判证据不足 → 无冲突，仍可 not_passed。"""
    b = a1_batch()
    facts = facts_from_batch(b)
    stmt = "证据仅为 metadata_only 摘要级，不足以支持机制/临床结论，判未通过。"
    c = detect_verifier_fact_conflicts(stmt, facts, cards=b.evidence_cards)
    assert c == []
    verdict = two_dimension_verdict(facts, {"passed": False, "status": "not_passed",
                                            "reason": stmt}, c)
    assert verdict["scientific_support_verdict"]["passed"] is False       # 仍可 not_passed
    assert verdict["verifier_fact_conflict"] is False


@pytest.mark.unit
def test_conflict_metadata_only_claimed_fulltext():
    """#4：Verifier 把 metadata_only 说成全文证据 → 捕获冲突。"""
    b = a1_batch()
    facts = facts_from_batch(b)
    stmt = "已由全文证据充分支持该结论。"
    c = detect_verifier_fact_conflicts(stmt, facts, cards=b.evidence_cards)
    assert any(x["conflict_type"] == CONFLICT_METADATA_AS_FULLTEXT for x in c)


@pytest.mark.unit
def test_source_error_not_rewritten_as_not_found():
    """#5：source_error 不得被改写成 not_found。"""
    s = Src(crossref={DOI: serr("crossref")}, doiorg={DOI: serr("doi.org")},
            epmc_doi={DOI: serr("europepmc")})
    b = resolve_exact_ids(f"核验 DOI {DOI}", sources=s)
    facts = facts_from_batch(b)
    assert facts[0].resolution_status == "manual_needed"       # 网络错误 → manual_needed
    stmt = f"DOI {DOI} 确认无记录，不存在该文献。"
    c = detect_verifier_fact_conflicts(stmt, facts)
    assert any(x["conflict_type"] == CONFLICT_SOURCE_ERROR_AS_ZERO for x in c)


@pytest.mark.unit
def test_conflict_verified_pmid_claimed_nonexistent():
    facts = facts_from_batch(a1_batch())
    stmt = f"PMID {PMID} 不存在，查无此文献。"
    c = detect_verifier_fact_conflicts(stmt, facts)
    assert any(x["conflict_type"] == CONFLICT_VERIFIED_NONEXISTENT for x in c)


@pytest.mark.unit
def test_question_not_flagged_as_assertion():
    """守卫：Verifier 提问'是否有效/需核实'不算断言。"""
    facts = facts_from_batch(a1_batch())
    stmt = f"需核实 DOI {DOI} 是否确实无法检索到而非工具遗漏。"
    assert detect_verifier_fact_conflicts(stmt, facts) == []


# ---------------- §5 裁决边界（tests 3,6）----------------
@pytest.mark.unit
def test_conflict_does_not_auto_flip_to_passed():
    facts = facts_from_batch(a1_batch())
    stmt = f"DOI {DOI} 实际有效。"
    c = detect_verifier_fact_conflicts(stmt, facts)
    old = {"passed": False, "status": "not_passed", "reason": stmt}
    verdict = two_dimension_verdict(facts, old, c)
    assert verdict["verifier_fact_conflict"] is True
    assert verdict["auto_flip_to_passed"] is False
    assert verdict["scientific_support_verdict"]["passed"] is False   # 不被改成 passed
    assert verdict["final_answer_authority"] == "old_verifier"        # 裁决权不变
    assert verdict["human_review_required"] is True


@pytest.mark.unit
def test_true_source_disagreement_needs_human_no_auto_favor():
    """#6：多来源真正矛盾（mismatch）→ manual/human_review，不自动选有利结果。"""
    meta_a = {"pmid": PMID, "doi": "10.1/a", "year": "2026"}
    meta_b = {"pmid": PMID, "doi": "10.1/b", "year": "1999"}
    s = Src(pubmed={PMID: hit("pubmed", meta_a)}, epmc_pmid={PMID: hit("europepmc", meta_b)})
    b = resolve_exact_ids(f"核验 PMID {PMID}", sources=s)
    facts = facts_from_batch(b)
    assert facts[0].resolution_status == "mismatch"
    verdict = two_dimension_verdict(facts, VOK, [])
    assert verdict["resolution_verdict"]["human_review_required"] is True
    assert verdict["human_review_required"] is True
    assert verdict["resolution_verdict"]["per_subject"][f"PMID:{PMID}"] == "mismatch"
    assert len(b.evidence_cards) == 0                          # mismatch 不构卡（不自动选有利）


# ---------------- §6 兼容 / 集成 ----------------
@pytest.mark.unit
def test_two_dimension_verdict_separates_dimensions():
    facts = facts_from_batch(a1_batch())
    v = two_dimension_verdict(facts, {"passed": False, "status": "not_passed", "reason": "r"}, [])
    assert v["resolution_verdict"]["decided_by"] == "deterministic_resolver"
    assert v["resolution_verdict"]["all_terminal"] is True
    assert v["scientific_support_verdict"]["decided_by"] == "old_verifier"


@pytest.mark.unit
def test_exact_id_flow_attaches_fact_grounding(monkeypatch):
    """集成：exact-id 流把两维 verdict 附到 manifest，且不改旧 Verifier 裁决权。"""
    from pilot.exact_id_agent import run_exact_id_flow

    class FakeModel:
        def __init__(self, content):
            self.content_val = content
            self.max_retries, self.timeout, self.max_tokens = 0, 120.0, 3000
        def invoke(self, *a, **k):
            from langchain_core.messages import AIMessage
            return AIMessage(content=self.content_val)
        def bind_tools(self, *a, **k): return self

    def claim_ex(final_text, card_ids):
        return [{"text": "c", "claim_type": "existence", "causal_strength": "none",
                 "supporting_ids": card_ids[:1]}]

    meta = {"pmid": PMID, "doi": "10.1080/07853890.2026.2627057", "title": "CAR-T",
            "journal": "Annals of medicine", "year": "2026"}
    s = Src(pubmed={PMID: hit("pubmed", meta)}, epmc_pmid={PMID: hit("europepmc", meta)},
            crossref={DOI: zero("crossref")}, doiorg={DOI: zero("doi.org")},
            epmc_doi={DOI: zero("europepmc")})
    # Verifier 声称 DOI 有效 → 应触发事实冲突，但不改判 passed
    verifier = FakeModel('{"passed": false, "reason": "该 DOI ' + DOI +
                         ' 实际对应有效期刊文章", "missing": "标题年份期刊"}')
    st = run_exact_id_flow(f"核验 PMID {PMID} 与 DOI {DOI} 的精确命中",
                           sources=s, verifier_model=verifier, claim_extractor=claim_ex,
                           stamp="FG")
    fg = st.shadow["fact_grounding"]
    assert fg["verifier_fact_conflict"] is True
    assert CONFLICT_NOT_FOUND_VALID in fg["conflict_types"]
    assert fg["auto_flip_to_passed"] is False
    assert fg["final_answer_authority"] == "old_verifier"
    assert "未验证" in st.final_answer                          # 旧 Verifier 判定仍生效
    assert st.shadow.get("human_review") is True
    # resolution 事实仍正确、两维并存
    assert fg["resolution_verdict"]["per_subject"][f"PMID:{PMID}"] == "verified"
    assert fg["resolution_verdict"]["per_subject"][f"DOI:{DOI}"] == "not_found"
