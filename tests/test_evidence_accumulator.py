"""A.7.4.3 —— 确定性 EvidenceAccumulator 的单元测试（不依赖 LLM / 网络 / 账本）。

覆盖：构卡（abstract / metadata-only / 多篇）、PMID/DOI 合并、去重幂等、四级 novelty、
版本升级 vs 冲突、相似标题不误合并、error/zero_hits 不构卡、严格拒绝、hash 保留、
identifier index 无悬空、evidence axes、decision novelty、no-progress 增减、
输入不可变、JSON round-trip，以及边界（未接线生产链、无禁用导入）。
"""
import pathlib

import pytest

from tool_envelope import compute_hash
from schemas import Provenance, ToolResult, NOT_REPORTED
from ids import normalize_pmid, normalize_doi
from pilot.open_task_contracts import (LiteratureRecord, ObservationRecord,
                                       EvidenceAccumulatorState,
                                       literature_content_level_to_provenance)
from pilot.evidence_accumulator import (accumulate, summarize, AccumulationResult,
                                        AccumulatorInputError, CANONICAL_AXES)

pytestmark = pytest.mark.unit


def rec(pmid=None, doi=None, *, title="IL-6 in SSc",
        abstract="Serum IL-6 correlates with mRSS in systemic sclerosis.",
        content_level="abstract", study_design="cross-sectional", species="human",
        longitudinal=None, interventional=None, tag=""):
    """构造一条【已校验】的 LiteratureRecord；content_hash 随内容变化（含 tag 便于制造 transport 差异）。"""
    npmid = normalize_pmid(pmid) if pmid else None
    ndoi = normalize_doi(doi) if doi else None
    sids = sorted({x for x in (npmid, ndoi) if x})
    ab = abstract if content_level != "metadata_only" else None
    payload = dict(pmid=npmid, doi=ndoi, title=title, ab=ab, cl=content_level, sd=study_design,
                   sp=species, lo=longitudinal, iv=interventional, sids=sids, tag=tag)
    ch = compute_hash(payload)
    prov = Provenance(tool_name="search_literature", source="Europe PMC", source_ids=sids,
                      content_level=literature_content_level_to_provenance(content_level),
                      content_hash=ch, hash_algorithm="sha256")
    return LiteratureRecord(pmid=pmid, doi=doi, title=title, abstract=ab, content_level=content_level,
                            study_design=study_design, species=species, longitudinal=longitudinal,
                            interventional=interventional, source="Europe PMC", query="q",
                            provenance=prov, source_ids=sids, content_hash=ch, hash_algorithm="sha256")


def empty():
    return EvidenceAccumulatorState()


# ----------------------------- 构卡 -----------------------------
def test_single_abstract_builds_card():
    r = accumulate(empty(), rec(pmid="40000001"))
    assert len(r.state.evidence_cards) == 1
    card = r.state.evidence_cards[0]
    assert card.tier == "abstract"
    assert card.supporting_excerpt.startswith("Serum IL-6")     # excerpt 来自真实 abstract
    assert card.pmid == "40000001"
    assert r.added_evidence_ids == [card.evidence_id]


def test_metadata_only_is_low_grade_candidate_not_fulltext():
    r = accumulate(empty(), rec(pmid="40000002", content_level="metadata_only", study_design=None))
    card = r.state.evidence_cards[0]
    assert card.tier == "abstract"                              # 复用摘要级类型，非第二套卡
    assert card.supporting_excerpt == ""                        # metadata-only 无 excerpt
    assert card.evidence_grade in ("候选", "初筛", "candidate", "screening")
    assert card.provenance.parameters.get("provenance_quality") == "identifier_only_metadata"


def test_multiple_records_build_multiple_cards():
    r = accumulate(empty(), [rec(pmid="40000001"), rec(pmid="40000002", title="Other")])
    assert len(r.state.evidence_cards) == 2
    assert len(r.added_evidence_ids) == 2


# ----------------------------- 合并 / 去重 -----------------------------
def test_pmid_and_doi_merge_to_one_entity():
    r = accumulate(empty(), rec(pmid="40000001", doi="10.1000/xyz123"))
    assert len(r.state.evidence_cards) == 1
    idx = r.state.identifier_index
    assert idx["40000001"] == idx["10.1000/xyz123"]            # 同一 evidence_id
    # 后续只带 DOI 的同内容记录 → 视为同一实体的重复
    r2 = accumulate(r.state, rec(doi="10.1000/xyz123"))
    assert len(r2.state.evidence_cards) == 1
    assert r2.duplicate_ids


def test_exact_duplicate_is_idempotent():
    r1 = accumulate(empty(), rec(pmid="40000001"))
    r2 = accumulate(r1.state, rec(pmid="40000001"))
    assert r2.added_evidence_ids == []
    assert r2.duplicate_ids == [r1.added_evidence_ids[0]]
    assert len(r2.state.evidence_cards) == 1                    # 不新增卡


def test_pure_function_is_deterministic_and_idempotent():
    s = empty()
    a = accumulate(s, rec(pmid="40000001"))
    b = accumulate(s, rec(pmid="40000001"))                     # 同输入、同 state
    assert a.state.model_dump() == b.state.model_dump()
    assert a.novelty.model_dump() == b.novelty.model_dump()


def test_new_identifier_novelty():
    r1 = accumulate(empty(), rec(pmid="40000001"))
    r2 = accumulate(r1.state, rec(pmid="40000002", title="Second"))
    assert r2.novelty.identifier_novelty is True
    assert "40000002" in r2.novelty.new_identifiers


# ----------------------------- 版本升级 vs 冲突 -----------------------------
def test_same_id_content_upgrade_keeps_old_card():
    r1 = accumulate(empty(), rec(pmid="40000001", content_level="metadata_only", study_design=None))
    old_eid = r1.added_evidence_ids[0]
    r2 = accumulate(r1.state, rec(pmid="40000001", content_level="abstract",
                                  study_design="cross-sectional"))
    assert r2.novelty.evidence_novelty is True
    assert len(r2.state.evidence_cards) == 2                    # 旧卡保留（不静默覆盖）
    new_eid = r2.added_evidence_ids[0]
    assert r2.state.identifier_index["40000001"] == new_eid     # index 指向更丰富版本
    assert old_eid != new_eid


def test_same_id_conflict_goes_to_human_review_no_overwrite():
    r1 = accumulate(empty(), rec(pmid="40000001", study_design="cross-sectional"))
    old_eid = r1.added_evidence_ids[0]
    r2 = accumulate(r1.state, rec(pmid="40000001", study_design="randomized controlled trial"))
    assert r2.conflicts == [old_eid]
    assert r2.added_evidence_ids == []                          # 不自动选有利版本
    assert len(r2.state.evidence_cards) == 1
    assert r2.state.identifier_index["40000001"] == old_eid     # 未覆盖


def test_similar_titles_different_ids_not_merged():
    r = accumulate(empty(), [rec(pmid="40000001", title="IL-6 and mRSS in SSc"),
                             rec(pmid="40000002", title="IL-6 and mRSS in SSc")])
    assert len(r.state.evidence_cards) == 2                     # 仅标题相似不合并
    assert r.state.identifier_index["40000001"] != r.state.identifier_index["40000002"]


# ----------------------------- error / zero_hits 不构卡 -----------------------------
def test_zero_hits_builds_no_card():
    r = accumulate(empty(), {"retrieval_status": "zero_hits", "records": []})
    assert r.observation_status == "zero_hits"
    assert len(r.state.evidence_cards) == 0
    assert r.scientific_progress is False


def _tool_error(kind):
    return ToolResult(ok=False, tool_name="search_literature", error_type=kind,
                      error_message="boom", provenance=Provenance(tool_name="search_literature"))


def test_source_error_builds_no_card():
    r = accumulate(empty(), _tool_error("source_error"))
    assert r.observation_status == "source_error"
    assert len(r.state.evidence_cards) == 0


def test_parse_error_builds_no_card():
    r = accumulate(empty(), _tool_error("parse_error"))
    assert r.observation_status == "parse_error"
    assert len(r.state.evidence_cards) == 0


def test_observation_record_error_is_recorded_not_faked():
    obs = ObservationRecord(observation_id="o1", step_id=1, tool_name="search_literature",
                            tool_call_id_hash="h", status="source_error", structured=True,
                            error_type="http_500", provenance=Provenance(tool_name="search_literature"))
    r = accumulate(empty(), obs)
    assert r.observation_status == "source_error"
    assert len(r.state.evidence_cards) == 0


# ----------------------------- 严格拒绝 -----------------------------
def test_reject_natural_language_string():
    with pytest.raises(AccumulatorInputError):
        accumulate(empty(), "- IL-6 correlates with mRSS | J Rheum | PMID:40000001")


def test_reject_dict_missing_provenance():
    bad = {"schema_version": "litrec-v1", "pmid": "40000001", "content_level": "abstract",
           "abstract": "x", "source": "s", "content_hash": "a" * 64}   # 无 provenance
    with pytest.raises(AccumulatorInputError):
        accumulate(empty(), bad)


def test_reject_illegal_pmid():
    with pytest.raises(AccumulatorInputError):
        accumulate(empty(), {"schema_version": "litrec-v1", "pmid": "not-a-pmid",
                             "content_level": "metadata_only", "source": "s",
                             "content_hash": "a" * 64,
                             "provenance": {"tool_name": "search_literature"}})


# ----------------------------- 溯源 / 索引完整性 -----------------------------
def test_provenance_hash_and_source_ids_preserved():
    r = accumulate(empty(), rec(pmid="40000001", doi="10.1000/xyz123"))
    card = r.state.evidence_cards[0]
    src = rec(pmid="40000001", doi="10.1000/xyz123")
    assert card.provenance.content_hash == src.content_hash
    assert card.provenance.source_ids == src.source_ids


def test_identifier_index_never_dangling():
    r = accumulate(empty(), [rec(pmid="40000001"), rec(pmid="40000002", title="B"),
                             rec(doi="10.5555/aaa")])
    valid = {c.evidence_id for c in r.state.evidence_cards}
    assert all(eid in valid for eid in r.state.identifier_index.values())


# ----------------------------- novelty 语义 -----------------------------
def test_four_tier_novelty_semantics():
    s = empty()
    r1 = accumulate(s, rec(pmid="40000001", study_design="cross-sectional"))
    assert r1.novelty.identifier_novelty and r1.novelty.evidence_novelty and r1.novelty.decision_novelty
    r2 = accumulate(r1.state, rec(pmid="40000001", study_design="cross-sectional"))  # 完全相同
    assert r2.novelty.transport_novelty is True
    assert not (r2.novelty.identifier_novelty or r2.novelty.evidence_novelty or r2.novelty.decision_novelty)


def test_transport_only_is_not_scientific_progress():
    r1 = accumulate(empty(), rec(pmid="40000001"))
    assert r1.scientific_no_progress_rounds == 0
    r2 = accumulate(r1.state, rec(pmid="40000001"))            # 重复
    assert r2.novelty.transport_novelty is True
    assert r2.scientific_progress is False


def test_new_evidence_axis_is_progress():
    r1 = accumulate(empty(), rec(pmid="40000001", study_design="cross-sectional"))
    r2 = accumulate(r1.state, rec(pmid="40000002", study_design="cohort", longitudinal=True))
    assert "temporal_evidence" in r2.novelty.new_evidence_axes
    assert r2.scientific_progress is True


def test_decision_novelty_on_causal_tier_change():
    r1 = accumulate(empty(), rec(pmid="40000001", study_design="cross-sectional"))
    assert r1.novelty.decision_changes == ["none->association"]
    r2 = accumulate(r1.state, rec(pmid="40000002", study_design="RCT", interventional=True))
    assert r2.novelty.decision_novelty is True
    assert summarize(r2.state)["causal_tier"] == "intervention_supported"


def test_no_progress_increments_then_resets():
    r1 = accumulate(empty(), rec(pmid="40000001"))
    assert r1.scientific_no_progress_rounds == 0
    r2 = accumulate(r1.state, rec(pmid="40000001"))            # transport-only → +1
    assert r2.scientific_no_progress_rounds == 1
    r3 = accumulate(r2.state, {"retrieval_status": "zero_hits", "records": []})  # zero_hits → +1
    assert r3.scientific_no_progress_rounds == 2
    r4 = accumulate(r3.state, rec(pmid="40000009", title="New"))  # 新证据 → 归零
    assert r4.scientific_no_progress_rounds == 0


# ----------------------------- 不可变性 / 序列化 -----------------------------
def test_input_state_not_mutated():
    s = empty()
    before = s.model_dump()
    accumulate(s, rec(pmid="40000001"))
    assert s.model_dump() == before
    assert len(s.evidence_cards) == 0


def test_json_round_trip():
    r = accumulate(empty(), [rec(pmid="40000001", study_design="RCT", interventional=True),
                             rec(pmid="40000002", title="B", longitudinal=True, study_design="cohort")])
    js = r.state.model_dump_json()
    back = EvidenceAccumulatorState.model_validate_json(js)
    assert [c.evidence_id for c in back.evidence_cards] == [c.evidence_id for c in r.state.evidence_cards]
    assert back.identifier_index == r.state.identifier_index
    assert back.evidence_axes == r.state.evidence_axes
    # AccumulationResult 也可 round-trip
    AccumulationResult.model_validate_json(r.model_dump_json())


def test_summary_read_only_shape():
    r = accumulate(empty(), rec(pmid="40000001"))
    s = summarize(r.state)
    assert set(s) == {"evidence_card_count", "identifier_index_size", "evidence_axes",
                      "unconfirmed_axes", "causal_tier", "novelty_rounds",
                      "scientific_no_progress_rounds"}
    assert set(s["evidence_axes"]) | set(s["unconfirmed_axes"]) == set(CANONICAL_AXES)


# ----------------------------- 边界 / 未接线 -----------------------------
_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_accumulator_not_wired_into_production_chain():
    """§10：ssc_a1 / Executor / Router / Shadow / search_literature 均未导入本累加器。"""
    for name in ("ssc_a1.py", "shadow.py", "pilot/exec_wiring.py", "pilot/tool_middleware.py",
                 "pilot/round2_runner.py", "pilot/literature_adapter.py"):
        p = _ROOT / name
        if p.exists():
            assert "evidence_accumulator" not in p.read_text(encoding="utf-8"), name


def test_no_llm_network_or_ledger_imports():
    src = (_ROOT / "pilot" / "evidence_accumulator.py").read_text(encoding="utf-8")
    for bad in ("import requests", "import httpx", "import urllib", "import socket",
                "anthropic", "openai", "ledger_integrity", "import ledger"):
        assert bad not in src, bad


def test_step_controller_not_implemented_here():
    import pilot.evidence_accumulator as acc
    for forbidden in ("StepController", "run_step", "route", "no_progress", "loop_guard"):
        assert not hasattr(acc, forbidden)
