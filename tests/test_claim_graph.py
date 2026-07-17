"""Claim–Evidence Graph 测试：每个 Claim 按自己的证据要求单独裁决；相关≠因果、动物≠临床。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from schemas import (Claim, Provenance, FullTextEvidenceCard, AnalysisEvidenceCard)
from claim_graph import ClaimEvidenceGraph, adjudicate_claim

PROV = Provenance(tool_name="t")


def _ft(eid, direction, study_type, species="human", excerpt="见结果"):
    return FullTextEvidenceCard(evidence_id=eid, title="t", provenance=PROV,
                                supporting_excerpt=excerpt, evidence_direction=direction,
                                study_type=study_type, species=species,
                                publication_status="published", source_figure_or_table="Fig1")


def _analysis(eid, direction, method="signature correlation"):
    return AnalysisEvidenceCard(evidence_id=eid, title="t", provenance=PROV,
                                dataset="GSE58095", method=method, evidence_direction=direction,
                                supporting_excerpt="r=0.35")


def _claim(ctype, sup=None, con=None, **over):
    return Claim(claim_id="c", text="x", claim_type=ctype,
                 supporting_evidence_ids=sup or [], contradicting_evidence_ids=con or [], **over)


def _v(claim, cards):
    store = {c.evidence_id: c for c in cards}
    return adjudicate_claim(claim, store)


@pytest.mark.unit
def test_no_evidence_insufficient():
    assert _v(_claim("existence"), []).verdict == "insufficient_evidence"


@pytest.mark.unit
def test_existence_supported_by_fulltext():
    card = _ft("e1", "supports", "human skin biopsy immunostaining")
    assert _v(_claim("existence", sup=["e1"]), [card]).verdict == "supported"


@pytest.mark.unit
def test_association_supported_by_correlation():
    card = _analysis("a1", "correlational")
    assert _v(_claim("association", sup=["a1"]), [card]).verdict == "supported"


@pytest.mark.unit
def test_causal_NOT_supported_by_correlation_only():
    # 关键：因果 claim 只有相关性证据 → 不能判 supported
    card = _analysis("a1", "correlational")
    out = _v(_claim("causal", sup=["a1"]), [card])
    assert out.verdict == "partially_supported"
    assert "因果" in out.uncertainty and out.human_review_required is True


@pytest.mark.unit
def test_causal_supported_by_perturbation():
    card = _ft("k1", "supports", "STING knockdown perturbation in human fibroblasts")
    assert _v(_claim("causal", sup=["k1"]), [card]).verdict == "supported"


@pytest.mark.unit
def test_clinical_efficacy_animal_only_not_enough():
    card = _ft("m1", "supports", "mouse model STING inhibitor treatment", species="mouse")
    out = _v(_claim("clinical_efficacy", sup=["m1"]), [card])
    assert out.verdict == "partially_supported"
    assert "临床" in out.uncertainty and out.human_review_required is True


@pytest.mark.unit
def test_clinical_efficacy_supported_by_rct():
    card = _ft("r1", "supports", "randomized controlled trial in patients")
    assert _v(_claim("clinical_efficacy", sup=["r1"]), [card]).verdict == "supported"


@pytest.mark.unit
def test_contradicted():
    card = _ft("x1", "refutes", "human cohort")
    assert _v(_claim("association", con=["x1"]), [card]).verdict == "contradicted"


@pytest.mark.unit
def test_unresolved_ids_tracked():
    out = _v(_claim("existence", sup=["missing1"]), [])
    assert "missing1" in out.unresolved_evidence_ids
    assert out.verdict == "insufficient_evidence"


@pytest.mark.unit
def test_graph_four_atomic_claims_judged_separately():
    corr = _analysis("corr", "correlational")
    perturb = _ft("perturb", "supports", "STING knockdown in human fibroblasts")
    exist = _ft("exist", "supports", "human SSc fibroblast STING immunostaining")
    claims = [
        _claim("existence", sup=["exist"]),               # STING 激活存在
        _claim("association", sup=["corr"]),              # STING 与纤维化相关
        _claim("causal", sup=["corr"]),                   # STING 因果驱动（只有相关性）
        _claim("clinical_efficacy", sup=["perturb"]),     # 抑制 STING 对患者有效（只有机制）
    ]
    g = ClaimEvidenceGraph(claims, [corr, perturb, exist])
    v = g.adjudicate()
    assert v[0].verdict == "supported"
    assert v[1].verdict == "supported"
    assert v[2].verdict == "partially_supported"          # 相关≠因果
    assert v[3].verdict in ("partially_supported", "insufficient_evidence")  # 机制≠临床
    assert v[2].verdict != v[1].verdict                   # 不能互相替代


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                print(f"FAIL {name}: {e}")
