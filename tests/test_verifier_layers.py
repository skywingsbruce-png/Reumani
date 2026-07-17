"""四层 Verifier 测试：Schema / Citation / Claim-Evidence / Adversarial + fail-closed 编排。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from schemas import (Provenance, ToolResult, Claim, FullTextEvidenceCard, AnalysisEvidenceCard)
import verifier as V

PROV = Provenance(tool_name="epmc", source="https://pubmed.ncbi.nlm.nih.gov/1/")


def _good_card(eid="e1"):
    return FullTextEvidenceCard(evidence_id=eid, title="t", provenance=PROV,
                                supporting_excerpt="knockdown reduced fibrosis",
                                evidence_direction="supports", study_type="human fibroblast knockdown",
                                species="human", pmid="12345678", source_figure_or_table="Fig1",
                                publication_status="published")


def _ok_tr():
    return ToolResult(ok=True, data="x", provenance=PROV)


def _fail_tr():
    return ToolResult(ok=False, error_type="runtime_error", error_message="boom", provenance=PROV)


# ---- Layer 1 ----
@pytest.mark.unit
def test_schema_tool_failure_fails():
    assert V.schema_verify([_fail_tr()], [Claim(claim_id="c", text="t")])["passed"] is False


@pytest.mark.unit
def test_schema_no_claims_fails():
    assert V.schema_verify([_ok_tr()], [])["passed"] is False


@pytest.mark.unit
def test_schema_ok():
    assert V.schema_verify([_ok_tr()], [Claim(claim_id="c", text="t")])["passed"] is True


# ---- Layer 2 ----
@pytest.mark.unit
def test_citation_fake_doi_fails():
    card = FullTextEvidenceCard(evidence_id="e", title="t", provenance=PROV,
                                supporting_excerpt="x", doi="not-a-real-doi", source_page="p1")
    assert V.citation_verify([card])["passed"] is False


@pytest.mark.unit
def test_citation_missing_source_fails():
    bare = Provenance(tool_name="t")               # 无 source
    card = FullTextEvidenceCard(evidence_id="e", title="t", provenance=bare,
                                supporting_excerpt="x", source_page="p1")
    assert V.citation_verify([card])["passed"] is False


@pytest.mark.unit
def test_citation_valid_passes():
    assert V.citation_verify([_good_card()])["passed"] is True


@pytest.mark.unit
def test_citation_online_checker_can_fail():
    assert V.citation_verify([_good_card()], checker=lambda p, d: False)["passed"] is False


# ---- Layer 3 ----
@pytest.mark.unit
def test_claim_evidence_causal_correlation_only_fails():
    corr = AnalysisEvidenceCard(evidence_id="a", title="t", provenance=PROV,
                                dataset="GSE1", method="corr", evidence_direction="correlational",
                                supporting_excerpt="r=0.35")
    claim = Claim(claim_id="c3", text="STING drives fibrosis", claim_type="causal",
                  supporting_evidence_ids=["a"])
    res, judged = V.claim_evidence_verify([claim], [corr])
    assert res["passed"] is False                     # 相关性撑不起因果


@pytest.mark.unit
def test_claim_evidence_supported_passes():
    claim = Claim(claim_id="c", text="STING knockdown reduces fibrosis", claim_type="causal",
                  supporting_evidence_ids=["e1"])
    res, judged = V.claim_evidence_verify([claim], [_good_card("e1")])
    assert res["passed"] is True


# ---- Layer 4 ----
@pytest.mark.unit
def test_adversarial_finds_counterevidence():
    claim = Claim(claim_id="c", text="STING drives fibrosis", claim_type="causal")
    res = V.adversarial_verify("q", [claim], searcher=lambda q: ["a counter paper"])
    assert res["passed"] is False


@pytest.mark.unit
def test_adversarial_clean_passes():
    claim = Claim(claim_id="c", text="STING drives fibrosis")
    assert V.adversarial_verify("q", [claim], searcher=lambda q: [])["passed"] is True


@pytest.mark.unit
def test_adversarial_not_run_flags_but_not_pass_silently():
    res = V.adversarial_verify("q", [Claim(claim_id="c", text="t")])   # 无 searcher
    assert "adversarial_not_run" in res["warnings"]


# ---- 编排 ----
@pytest.mark.unit
def test_verify_all_fail_closed_on_any_layer():
    claim = Claim(claim_id="c", text="STING knockdown reduces fibrosis", claim_type="causal",
                  supporting_evidence_ids=["e1"])
    # 工具失败 → 整体不过
    out = V.verify_all("q", [_fail_tr()], [claim], [_good_card("e1")])
    assert out["passed"] is False


@pytest.mark.unit
def test_verify_all_pass_but_human_review_when_adversary_absent():
    claim = Claim(claim_id="c", text="STING knockdown reduces fibrosis", claim_type="causal",
                  supporting_evidence_ids=["e1"])
    out = V.verify_all("q", [_ok_tr()], [claim], [_good_card("e1")])
    assert out["passed"] is True and out["human_review_required"] is True   # 未跑反证→需人工


@pytest.mark.unit
def test_verify_all_high_risk_forces_human_review():
    claim = Claim(claim_id="c", text="STING knockdown reduces fibrosis", claim_type="causal",
                  supporting_evidence_ids=["e1"])
    out = V.verify_all("q", [_ok_tr()], [claim], [_good_card("e1")],
                       adversary_searcher=lambda q: [], high_risk=True)
    assert out["passed"] is True and out["human_review_required"] is True


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                print(f"FAIL {name}: {e}")
