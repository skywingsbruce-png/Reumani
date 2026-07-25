"""A.7.4.1：开放任务有界收敛数据契约测试。零真实 API、零网络、零账本。"""
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from schemas import Provenance
from pilot.open_task_contracts import (CausalEvidenceAxes, ControlledInsufficientConclusion,
                                       EvidenceAccumulatorState, LiteratureRecord,
                                       NoveltyAssessment, ObservationRecord, OpenTaskRunState,
                                       PlanStepState, TERMINAL_STEP_STATUSES)

H64 = "a" * 64
PROV = Provenance(tool_name="search_literature", source="Europe PMC", content_level="abstract")


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
    # 但有内容时 fulltext 合法
    assert litrec(content_level="fulltext", abstract="full body text").content_level == "fulltext"


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
        EvidenceAccumulatorState(evidence_cards=[{"evidence_id": "x"}, {"evidence_id": "x"}])


@pytest.mark.unit
def test_accumulator_dangling_identifier_index_rejected():
    with pytest.raises(ValidationError):
        EvidenceAccumulatorState(evidence_cards=[{"evidence_id": "x"}],
                                 identifier_index={"x": 5})       # 悬空
    ok = EvidenceAccumulatorState(evidence_cards=[{"evidence_id": "x"}],
                                  identifier_index={"x": 0})
    assert ok.identifier_index["x"] == 0


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
                current_step_id=1, status="running")
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
    # 全终态则允许
    ok = run_state(steps=[step(step_id=1, status="satisfied", attempts=1, completion_reason="ok")],
                   current_step_id=1, conclusion=concl)
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
    assert "from schemas import Provenance" in src
    assert "from pilot.exact_id_resolver import normalize_doi, normalize_pmid" in src
    # 不重新定义 Provenance/EvidenceCard/ToolResult
    assert "class Provenance" not in src and "class EvidenceCard" not in src \
        and "class ToolResult" not in src
    # LiteratureRecord.provenance 复用 schemas.Provenance
    assert M.LiteratureRecord.model_fields["provenance"].annotation is Provenance
