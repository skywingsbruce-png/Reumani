"""三层证据卡 + 科学诚信规则测试（unit，仅依赖 schemas）。"""
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from schemas import (Provenance, EvidenceCard, AbstractEvidenceCard,
                     FullTextEvidenceCard, AnalysisEvidenceCard)

PROV = Provenance(tool_name="europepmc", source="https://europepmc.org")


def _abs(**over):
    d = dict(evidence_id="e1", title="T", provenance=PROV,
             supporting_excerpt="ACA correlates with PAH", evidence_grade="候选")
    d.update(over)
    return AbstractEvidenceCard(**d)


@pytest.mark.unit
def test_base_requires_core_fields():
    with pytest.raises(ValidationError):
        EvidenceCard(tier="abstract", title="T", provenance=PROV)   # 缺 evidence_id
    with pytest.raises(ValidationError):
        EvidenceCard(evidence_id="e", tier="abstract", title="T")   # 缺 provenance


@pytest.mark.unit
def test_abstract_grade_capped():
    _abs(evidence_grade="初筛")                                     # ok
    with pytest.raises(ValidationError):
        _abs(evidence_grade="strong")                              # 只读摘要不能声称强证据
    with pytest.raises(ValidationError):
        _abs(evidence_grade="confirmed")


@pytest.mark.unit
def test_abstract_cannot_be_key_conclusion():
    ok, reasons = _abs().usable_for_key_conclusion()
    assert ok is False
    assert any("摘要级" in r for r in reasons)                      # 不能声称全文证明


@pytest.mark.unit
def test_no_excerpt_blocks_key_conclusion():
    ok, reasons = _abs(supporting_excerpt="").usable_for_key_conclusion()
    assert ok is False and any("supporting_excerpt" in r for r in reasons)


@pytest.mark.unit
def test_traceability_low_without_locator():
    assert _abs().traceability() == "low"                          # 无来源定位
    card = FullTextEvidenceCard(evidence_id="f", title="T", provenance=PROV,
                                supporting_excerpt="see Fig 2", source_page="p.5",
                                publication_status="published")
    assert card.traceability() == "high"


@pytest.mark.unit
def test_retracted_not_for_positive_conclusion():
    ok, reasons = _abs(publication_status="retracted").usable_for_key_conclusion()
    assert ok is False and any("撤稿" in r for r in reasons)


@pytest.mark.unit
def test_corrected_requires_version():
    with pytest.raises(ValidationError):
        _abs(publication_status="corrected")                       # 缺更正版本
    _abs(publication_status="corrected", publication_version="v2")  # ok


@pytest.mark.unit
def test_clinical_caveats():
    assert any("动物" in c for c in _abs(species="mouse").clinical_caveats())
    assert any("体外" in c for c in _abs(tissue_or_cell="fibroblast cell line").clinical_caveats())
    assert any("因果" in c for c in _abs(evidence_direction="correlational").clinical_caveats())
    assert any("预印本" in c for c in _abs(publication_status="preprint").clinical_caveats())


@pytest.mark.unit
def test_fulltext_requires_excerpt():
    with pytest.raises(ValidationError):
        FullTextEvidenceCard(evidence_id="f", title="T", provenance=PROV, supporting_excerpt="")


@pytest.mark.unit
def test_analysis_card_needs_dataset_method():
    with pytest.raises(ValidationError):
        AnalysisEvidenceCard(evidence_id="a", title="T", provenance=PROV)   # 缺 dataset/method
    card = AnalysisEvidenceCard(evidence_id="a", title="cGAS-STING vs mRSS", provenance=PROV,
                                dataset="GSE58095", method="signature correlation")
    assert card.tier == "analysis" and card.dataset == "GSE58095"


@pytest.mark.unit
def test_fulltext_published_with_excerpt_is_usable():
    card = FullTextEvidenceCard(evidence_id="f", title="T", provenance=PROV,
                                supporting_excerpt="Knockdown reduced fibrosis (Fig 3)",
                                source_figure_or_table="Fig 3", publication_status="published",
                                species="human")
    ok, reasons = card.usable_for_key_conclusion()
    assert ok is True, reasons


@pytest.mark.unit
def test_builder_abstract_card_retains_provenance():
    import evidence_build as EB
    paper = {"title": "ACA and PAH in SSc", "pmid": "123", "doi": "10.1/x",
             "journal": "bioRxiv", "link": "https://doi.org/10.1/x", "pub_type": "preprint"}
    card = EB.abstract_card_from_extraction(
        {"study_type": "队列", "sample_size": "n=80", "main_findings": ["ACA→PAH"]}, paper)
    assert card.tier == "abstract" and card.evidence_grade == "初筛"
    assert card.publication_status == "preprint"                 # 预印本已标记
    assert card.pmid == "123" and card.provenance.parameters.get("pmid") == "123"  # provenance 保留
    assert card.provenance.source                                            # 来源保留
    assert card.usable_for_key_conclusion()[0] is False          # 摘要级不能作关键结论


@pytest.mark.unit
def test_builder_analysis_card():
    import evidence_build as EB
    card = EB.analysis_card("an1", "cGAS-STING vs mRSS", "GSE58095", "signature correlation",
                            statistic="r=0.35, p=0.005", excerpt="r=0.35, p=0.005",
                            code_commit="abc123", direction="correlational")
    assert card.tier == "analysis" and card.provenance.code_commit == "abc123"
    assert any("因果" in c for c in card.clinical_caveats())      # 相关性≠因果


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                print(f"FAIL {name}: {e}")
