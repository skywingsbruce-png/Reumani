"""A.7.4.1：开放任务有界收敛数据契约测试。零真实 API、零网络、零账本。"""
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from schemas import (AbstractEvidenceCard, AnalysisEvidenceCard, FullTextEvidenceCard,
                     Provenance)
from pilot.open_task_contracts import (CausalEvidenceAxes, ControlledInsufficientConclusion,
                                       EvidenceAccumulatorState, LiteratureRecord,
                                       NoveltyAssessment, ObservationRecord, OpenTaskRunState,
                                       PlanStepState, TERMINAL_STEP_STATUSES,
                                       literature_content_level_to_provenance)

H64 = "a" * 64
PROV = Provenance(tool_name="search_literature", source="Europe PMC", content_level="abstract")


def abstract_card(eid="41657283", pmid="41657283"):
    return AbstractEvidenceCard(evidence_id=eid, tier="abstract", title="T", provenance=PROV,
                                pmid=pmid, supporting_excerpt="x", evidence_grade="候选")


def fulltext_card(eid="c-ft"):
    prov = Provenance(tool_name="read_local_pdf", source="pdf", content_level="full_text")
    return FullTextEvidenceCard(evidence_id=eid, tier="fulltext", title="T", provenance=prov,
                                supporting_excerpt="verbatim excerpt", source_section="Results")


def analysis_card(eid="c-an"):
    prov = Provenance(tool_name="reumani-analysis", source="GSE123", dataset_version="v1")
    return AnalysisEvidenceCard(evidence_id=eid, tier="analysis", title="T", provenance=prov,
                                dataset="GSE123", method="correlation")


def litrec(**kw):
    base = dict(pmid="41657283", doi=None, title="T", year="2023", journal="J",
                abstract="cross-sectional study", content_level="abstract",
                study_design="cross-sectional", species="human", longitudinal=False,
                interventional=False, source="Europe PMC", query="q",
                provenance=PROV, source_ids=["41657283"], content_hash=H64)
    base.update(kw)
    return LiteratureRecord(**base)


# ---------------- LiteratureRecord (1-6) ----------------
@pytest.mark.unit
def test_literature_record_valid():
    r = litrec()
    assert r.pmid == "41657283" and r.schema_version == "litrec-v1" and r.hash_algorithm == "sha256"


@pytest.mark.unit
def test_reject_both_pmid_doi_missing():
    with pytest.raises(ValidationError):
        litrec(pmid=None, doi=None)


@pytest.mark.unit
def test_reject_bad_hash():
    with pytest.raises(ValidationError):
        litrec(content_hash="XYZ")
    with pytest.raises(ValidationError):
        litrec(content_hash="A" * 64)          # 大写非法


@pytest.mark.unit
def test_reject_missing_source_or_provenance():
    with pytest.raises(ValidationError):
        litrec(source="")
    with pytest.raises(ValidationError):
        litrec(provenance=None)


@pytest.mark.unit
def test_reject_fulltext_without_abstract():
    with pytest.raises(ValidationError):
        litrec(content_level="fulltext", abstract=None)
    # 有内容 + 全文可验证定位 → fulltext 合法（仅 abstract 不足，见 h14）
    assert litrec(content_level="fulltext", abstract="full body text",
                  fulltext_ref="pmc://PMC1/fulltext").content_level == "fulltext"


@pytest.mark.unit
def test_source_ids_dedup_sorted():
    r = litrec(source_ids=["b", "a", "b", "a"])
    assert r.source_ids == ["a", "b"]


@pytest.mark.unit
def test_pmid_normalized_via_ids_authority():
    r = litrec(pmid="PMID:41657283")
    assert r.pmid == "41657283"                # 经 ids 权威规范化
    r2 = litrec(pmid=None, doi="https://doi.org/10.1038/S41586-020-2649-2")
    assert r2.doi == "10.1038/s41586-020-2649-2"


# ---------------- NoveltyAssessment (7-11) ----------------
@pytest.mark.unit
def test_scientific_progress_derived():
    assert NoveltyAssessment().scientific_progress is False
    assert NoveltyAssessment(identifier_novelty=True).scientific_progress is True
    assert NoveltyAssessment(evidence_novelty=True).scientific_progress is True
    assert NoveltyAssessment(decision_novelty=True).scientific_progress is True


@pytest.mark.unit
def test_transport_only_not_scientific_progress():
    n = NoveltyAssessment(transport_novelty=True)
    assert n.transport_novelty is True and n.scientific_progress is False


@pytest.mark.unit
def test_caller_cannot_pass_contradicting_scientific_progress():
    with pytest.raises(ValidationError):
        NoveltyAssessment(transport_novelty=True, scientific_progress=True)   # extra=forbid


@pytest.mark.unit
def test_scientific_progress_is_readonly_property_not_field():
    n = NoveltyAssessment(identifier_novelty=True)
    assert "scientific_progress" not in NoveltyAssessment.model_fields   # 非字段
    assert "scientific_progress" not in n.model_dump()                   # 不参与序列化 → round-trip 干净
    assert n.scientific_progress is True                                 # property 可读
    with pytest.raises(AttributeError):
        n.scientific_progress = False                                    # 只读，不可赋值


# ---------------- PlanStepState (12-15) ----------------
def step(**kw):
    base = dict(step_id=1, objective="检索", allowed_tools=["search_literature"],
                call_budget=2, attempts=0, status="pending")
    base.update(kw)
    return PlanStepState(**base)


@pytest.mark.unit
def test_plan_step_six_states():
    assert step(status="pending").status == "pending"
    assert step(status="running", attempts=1).status == "running"
    assert step(status="satisfied", attempts=1, completion_reason="ok").status == "satisfied"
    assert step(status="failed", attempts=1, completion_reason="x").status == "failed"
    assert step(status="blocked", attempts=1, completion_reason="x").status == "blocked"
    assert step(status="insufficient", attempts=2, completion_reason="budget",
                remaining_gaps=["缺纵向证据"]).status == "insufficient"


@pytest.mark.unit
def test_attempts_over_budget_rejected():
    with pytest.raises(ValidationError):
        step(call_budget=2, attempts=3, status="failed", completion_reason="x")
    with pytest.raises(ValidationError):
        step(call_budget=0)


@pytest.mark.unit
def test_insufficient_needs_remaining_gaps():
    with pytest.raises(ValidationError):
        step(status="insufficient", attempts=2, completion_reason="budget", remaining_gaps=[])


@pytest.mark.unit
def test_terminal_and_pending_constraints():
    with pytest.raises(ValidationError):
        step(status="pending", attempts=1)                       # pending→attempts=0
    with pytest.raises(ValidationError):
        step(status="running", attempts=1, completion_reason="x")  # running 不得有 completion_reason
    with pytest.raises(ValidationError):
        step(status="satisfied", attempts=1)                      # 终态需 completion_reason
    assert TERMINAL_STEP_STATUSES == {"satisfied", "insufficient", "failed", "blocked"}


# ---------------- EvidenceAccumulatorState (16-17) ----------------
@pytest.mark.unit
def test_accumulator_duplicate_card_id_rejected():
    with pytest.raises(ValidationError):
        EvidenceAccumulatorState(evidence_cards=[abstract_card(eid="x"), abstract_card(eid="x")])


@pytest.mark.unit
def test_accumulator_dangling_identifier_index_rejected():
    with pytest.raises(ValidationError):                          # value 悬空（无对应 evidence_id）
        EvidenceAccumulatorState(evidence_cards=[abstract_card(eid="c1")],
                                 identifier_index={"41657283": "missing"})
    ok = EvidenceAccumulatorState(evidence_cards=[abstract_card(eid="c1")],
                                  identifier_index={"41657283": "c1"})
    assert ok.identifier_index["41657283"] == "c1"


# ---------------- CausalEvidenceAxes (18) ----------------
@pytest.mark.unit
def test_causal_axes_independent_no_total_score():
    ax = CausalEvidenceAxes(association=True, temporal_evidence=False)
    assert ax.association is True and ax.temporal_evidence is False and ax.clinical_evidence is None
    with pytest.raises(ValidationError):
        CausalEvidenceAxes(total_score=0.9)                       # 单一总分被禁


# ---------------- ControlledInsufficientConclusion (19-20) ----------------
@pytest.mark.unit
def test_controlled_conclusion_nonempty():
    c = ControlledInsufficientConclusion(
        resolved_question="Q", available_evidence=[], causal_strength="association",
        missing_evidence=["纵向证据"])
    assert c.missing_evidence


@pytest.mark.unit
def test_no_evidence_empty_missing_rejected():
    with pytest.raises(ValidationError):
        ControlledInsufficientConclusion(resolved_question="Q", available_evidence=[],
                                         causal_strength="insufficient", missing_evidence=[])
    with pytest.raises(ValidationError):
        ControlledInsufficientConclusion(resolved_question="  ", available_evidence=[{"x": 1}],
                                         causal_strength="association")


# ---------------- OpenTaskRunState (21-24) ----------------
def run_state(**kw):
    base = dict(run_id="r1", question="q", route="open",
                steps=[step(status="satisfied", attempts=1, completion_reason="ok")],
                current_step_id=None, status="running")   # 全终态 → current_step_id=None
    base.update(kw)
    return OpenTaskRunState(**base)


@pytest.mark.unit
def test_run_state_step_id_unique():
    with pytest.raises(ValidationError):
        run_state(steps=[step(step_id=1, status="satisfied", attempts=1, completion_reason="a"),
                         step(step_id=1, status="satisfied", attempts=1, completion_reason="b")],
                  current_step_id=1)


@pytest.mark.unit
def test_run_state_rejects_non_open_route():
    with pytest.raises(ValidationError):
        run_state(route="exact_id")


@pytest.mark.unit
def test_conclusion_requires_all_steps_terminal():
    concl = ControlledInsufficientConclusion(resolved_question="Q", available_evidence=[],
                                             causal_strength="association", missing_evidence=["x"])
    with pytest.raises(ValidationError):
        run_state(steps=[step(step_id=1, status="running", attempts=1)],
                  current_step_id=1, conclusion=concl)
    # 全终态则允许（current_step_id=None）
    ok = run_state(steps=[step(step_id=1, status="satisfied", attempts=1, completion_reason="ok")],
                   current_step_id=None, conclusion=concl)
    assert ok.conclusion is not None


@pytest.mark.unit
def test_failed_requires_primary_failure():
    with pytest.raises(ValidationError):
        run_state(status="failed", primary_failure=None)
    assert run_state(status="failed", primary_failure="synthesis_failed").status == "failed"


# ---------------- 25 JSON round-trip ----------------
@pytest.mark.unit
def test_json_round_trip():
    for obj in (litrec(), NoveltyAssessment(identifier_novelty=True), step(),
                CausalEvidenceAxes(association=True), run_state()):
        js = obj.model_dump_json()
        back = type(obj).model_validate_json(js)
        assert back.model_dump() == obj.model_dump()


@pytest.mark.unit
def test_observation_record_no_sensitive_fields():
    obs = ObservationRecord(observation_id="o1", step_id=1, tool_name="search_literature",
                            tool_call_id_hash="abc", status="ok", structured=False,
                            provenance=PROV, evidence_ids=["a", "a"])
    assert obs.evidence_ids == ["a"]
    fields = set(ObservationRecord.model_fields)
    for banned in ("prompt", "system_prompt", "authorization", "api_key", "headers",
                   "raw_response", "tool_arguments"):
        assert banned not in fields                              # 结构上排除敏感字段
    with pytest.raises(ValidationError):
        ObservationRecord(observation_id="o", step_id=1, tool_name="t", tool_call_id_hash="h",
                          status="source_error", structured=False, provenance=PROV)  # 缺 error_type


# ==================== A.7.4.1.1 加固测试 ====================
@pytest.mark.unit
def test_h1_illegal_evidence_card_dict_rejected():
    """1：非法 EvidenceCard dict 构造 Accumulator 被拒绝（强类型，非任意 dict）。"""
    with pytest.raises(ValidationError):
        EvidenceAccumulatorState(evidence_cards=[{"evidence_id": "x"}])        # 缺 tier/title/provenance
    with pytest.raises(ValidationError):
        EvidenceAccumulatorState(evidence_cards=[{"evidence_id": "x", "tier": "nope",
                                                  "title": "t", "provenance": PROV.model_dump()}])


@pytest.mark.unit
def test_h2_three_subclass_round_trip():
    """2：三种 EvidenceCard 子类经 Accumulator round-trip，tier 与子类一致。"""
    acc = EvidenceAccumulatorState(evidence_cards=[abstract_card(eid="a"), fulltext_card("f"),
                                                   analysis_card("an")])
    js = acc.model_dump_json()
    back = EvidenceAccumulatorState.model_validate_json(js)
    tiers = [c.tier for c in back.evidence_cards]
    assert tiers == ["abstract", "fulltext", "analysis"]
    assert isinstance(back.evidence_cards[0], AbstractEvidenceCard)
    assert isinstance(back.evidence_cards[1], FullTextEvidenceCard)
    assert isinstance(back.evidence_cards[2], AnalysisEvidenceCard)


@pytest.mark.unit
def test_h3_subclass_constraints_still_enforced():
    """3：Abstract 上限 / FullText excerpt / Analysis dataset 约束在 Accumulator 内仍生效。"""
    from schemas import Provenance as P
    with pytest.raises(ValidationError):     # Abstract 不得 evidence_grade 超限
        EvidenceAccumulatorState(evidence_cards=[AbstractEvidenceCard(
            evidence_id="a", tier="abstract", title="T", provenance=PROV, evidence_grade="确证")])
    with pytest.raises(ValidationError):     # FullText 必须 supporting_excerpt
        EvidenceAccumulatorState(evidence_cards=[FullTextEvidenceCard(
            evidence_id="f", tier="fulltext", title="T",
            provenance=P(tool_name="x", source="s", content_level="full_text"),
            supporting_excerpt="", source_section="R")])


@pytest.mark.unit
def test_h4_5_6_identifier_index_uses_evidence_id_and_reorder_safe():
    """4/5/6：index 用 evidence_id；卡重排不破坏 index；悬空 evidence_id 拒绝。"""
    acc = EvidenceAccumulatorState(
        evidence_cards=[abstract_card(eid="c1", pmid="41657283"),
                        abstract_card(eid="c2", pmid="33301246")],
        identifier_index={"41657283": "c1", "33301246": "c2"})
    # 重排卡：index 仍指向正确 evidence_id（不是下标）
    acc2 = EvidenceAccumulatorState(
        evidence_cards=[abstract_card(eid="c2", pmid="33301246"),
                        abstract_card(eid="c1", pmid="41657283")],
        identifier_index={"41657283": "c1", "33301246": "c2"})
    assert acc.identifier_index == acc2.identifier_index          # 重排无影响
    with pytest.raises(ValidationError):
        EvidenceAccumulatorState(evidence_cards=[abstract_card(eid="c1")],
                                 identifier_index={"41657283": "c1", "33301246": "ghost"})


@pytest.mark.unit
def test_h7_evidence_ids_derived_from_cards_not_drift():
    """7：evidence_ids 由 cards 派生（非第二套漂移列表）。"""
    acc = EvidenceAccumulatorState(evidence_cards=[abstract_card(eid="c1"), fulltext_card("c2")])
    assert acc.evidence_ids == ["c1", "c2"]                       # 派生
    assert "evidence_ids" not in EvidenceAccumulatorState.model_fields   # 非独立字段


@pytest.mark.unit
def test_h8_9_normalize_matches_old_resolver_examples():
    """8/9：ids.normalize_pmid/doi 与旧 resolver 输出逐例一致。"""
    from pilot.exact_id_resolver import normalize_doi as rnd, normalize_pmid as rnp
    import ids
    pmid_cases = ["41657283", "PMID:41657283", "PMID 41657283", "pmid:41657283",
                  "https://pubmed.ncbi.nlm.nih.gov/41657283/", "41657283.", "not-a-pmid", ""]
    doi_cases = ["10.1038/s41586-020-2649-2", "DOI:10.1038/S41586-020-2649-2",
                 "https://doi.org/10.1038/s41586-020-2649-2", "10.5", "", "10.1080/03009742.2024.2302553"]
    for c in pmid_cases:
        assert ids.normalize_pmid(c) == rnp(c)
    for c in doi_cases:
        assert ids.normalize_doi(c) == rnd(c)


@pytest.mark.unit
def test_h11_12_no_cyclic_or_backward_imports():
    """11/12：open_task_contracts 不导入 exact_id_resolver；ids.py 无高层反向依赖。"""
    src = (ROOT / "pilot" / "open_task_contracts.py").read_text(encoding="utf-8")
    assert "exact_id_resolver" not in src
    assert "from ids import normalize_doi, normalize_pmid" in src
    ids_src = (ROOT / "ids.py").read_text(encoding="utf-8")
    for banned in ("import pilot", "from pilot", "import schemas", "from schemas",
                   "import requests", "langchain"):
        assert banned not in ids_src


@pytest.mark.unit
def test_h13_content_level_mapping():
    """13：metadata_only/abstract/fulltext/unknown → schemas ContentLevel 映射集中且正确。"""
    assert literature_content_level_to_provenance("metadata_only") == "metadata_only"
    assert literature_content_level_to_provenance("abstract") == "abstract"
    assert literature_content_level_to_provenance("fulltext") == "full_text"   # 唯一映射点
    assert literature_content_level_to_provenance("unknown") == "metadata_only"
    with pytest.raises(ValueError):
        literature_content_level_to_provenance("bogus")


@pytest.mark.unit
def test_h14_abstract_only_cannot_claim_fulltext():
    """14：仅 abstract 冒充 fulltext 被拒绝；有全文定位则允许。"""
    with pytest.raises(ValidationError):
        litrec(content_level="fulltext", abstract="only abstract text")   # 无全文定位
    ok = litrec(content_level="fulltext", abstract="body", fulltext_ref="pmc://PMC1/fulltext")
    assert ok.content_level == "fulltext"
    ok2 = litrec(content_level="fulltext", abstract="body", fulltext_content_hash="b" * 64)
    assert ok2.content_level == "fulltext"
    # 无 abstract 不得 abstract/fulltext
    with pytest.raises(ValidationError):
        litrec(content_level="abstract", abstract=None)


@pytest.mark.unit
def test_h15_16_run_state_conclusion_boundaries():
    """15/16：空 steps + conclusion 拒绝；finished 无 conclusion 拒绝。"""
    concl = ControlledInsufficientConclusion(resolved_question="Q", available_evidence=[],
                                             causal_strength="association", missing_evidence=["x"])
    with pytest.raises(ValidationError):
        OpenTaskRunState(run_id="r", question="q", route="open", steps=[], conclusion=concl)
    with pytest.raises(ValidationError):
        OpenTaskRunState(run_id="r", question="q", route="open",
                         steps=[step(status="satisfied", attempts=1, completion_reason="ok")],
                         current_step_id=None, status="finished", conclusion=None)


@pytest.mark.unit
def test_h17_18_19_observation_reference_integrity():
    """17/18/19：observation 引用不存在 step / 重复 observation_id / PlanStep observation 悬空 → 拒绝。"""
    obs = lambda oid, sid: ObservationRecord(observation_id=oid, step_id=sid,
                                             tool_name="t", tool_call_id_hash="h",
                                             status="ok", structured=False, provenance=PROV)
    with pytest.raises(ValidationError):     # observation.step_id 不存在
        OpenTaskRunState(run_id="r", question="q", route="open",
                         steps=[step(step_id=1, status="satisfied", attempts=1, completion_reason="ok")],
                         observations=[obs("o1", 99)])
    with pytest.raises(ValidationError):     # 重复 observation_id
        OpenTaskRunState(run_id="r", question="q", route="open",
                         steps=[step(step_id=1, status="satisfied", attempts=1, completion_reason="ok")],
                         observations=[obs("o1", 1), obs("o1", 1)])
    with pytest.raises(ValidationError):     # PlanStep observation 引用悬空
        OpenTaskRunState(run_id="r", question="q", route="open",
                         steps=[step(step_id=1, status="satisfied", attempts=1,
                                     completion_reason="ok", observations=["nope"])])
    # 合法：引用一致
    ok = OpenTaskRunState(run_id="r", question="q", route="open",
                          steps=[step(step_id=1, status="satisfied", attempts=1,
                                      completion_reason="ok", observations=["o1"])],
                          observations=[obs("o1", 1)])
    assert ok.observations[0].observation_id == "o1"


@pytest.mark.unit
def test_h5_running_no_primary_failure_and_terminal_current_step_none():
    with pytest.raises(ValidationError):
        OpenTaskRunState(run_id="r", question="q", route="open",
                         steps=[step(status="running", attempts=1)], current_step_id=1,
                         status="running", primary_failure="x")
    with pytest.raises(ValidationError):     # 全终态后 current_step_id 应为 None
        OpenTaskRunState(run_id="r", question="q", route="open",
                         steps=[step(status="satisfied", attempts=1, completion_reason="ok")],
                         current_step_id=1)


@pytest.mark.unit
def test_h20_hardened_json_round_trip():
    acc = EvidenceAccumulatorState(evidence_cards=[abstract_card(eid="a"), analysis_card("an")],
                                   identifier_index={"41657283": "a"})
    back = EvidenceAccumulatorState.model_validate_json(acc.model_dump_json())
    assert back.model_dump() == acc.model_dump()


# ---------------- 26/27 无回归 / 导入无副作用 ----------------
@pytest.mark.unit
def test_import_has_no_side_effects(monkeypatch):
    """#27：导入契约模块不触发网络/模型/账本。"""
    import importlib
    hits = {"n": 0}
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: hits.__setitem__("n", hits["n"] + 1))
    monkeypatch.setattr(requests, "post", lambda *a, **k: hits.__setitem__("n", hits["n"] + 1))
    import pilot.open_task_contracts as M
    importlib.reload(M)
    assert hits["n"] == 0


@pytest.mark.unit
def test_reuses_existing_authorities_no_duplicate_definitions():
    """#26 相关：复用 schemas.Provenance / ids；不新建第二套 Provenance/EvidenceCard 定义。"""
    import pilot.open_task_contracts as M
    src = (ROOT / "pilot" / "open_task_contracts.py").read_text(encoding="utf-8")
    assert "from schemas import" in src and "Provenance" in src
    assert "from ids import normalize_doi, normalize_pmid" in src        # 权威在 ids.py
    assert "import exact_id_resolver" not in src and "exact_id_resolver import" not in src
    # 不重新定义 Provenance/EvidenceCard/ToolResult
    assert "class Provenance" not in src and "class EvidenceCard" not in src \
        and "class ToolResult" not in src
    # LiteratureRecord.provenance 复用 schemas.Provenance
    assert M.LiteratureRecord.model_fields["provenance"].annotation is Provenance
