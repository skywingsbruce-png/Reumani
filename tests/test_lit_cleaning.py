"""四层文献清洗测试：确定性检查 + 程序规则 + 人工审核优先级（extractor 注入，不调 LLM）。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lit_cleaning as LC
from schemas import NOT_REPORTED


def _fake_extractor(ret):
    return lambda paper: dict(ret)


# ---- 第1层 ----
@pytest.mark.unit
def test_layer1_detects_class_and_status():
    d = LC.layer1_deterministic({"title": "mouse model of fibrosis", "pub_type": "Retracted Publication",
                                 "pmid": "123", "doi": "bad", "year": "2020x"})
    assert d["evidence_class"] == "animal" and d["retracted"] is True
    assert d["pmid_valid"] is True and d["doi_valid"] is False


@pytest.mark.unit
def test_dedup():
    ps = [{"pmid": "1"}, {"pmid": "1"}, {"doi": "10.1/x"}]
    out, removed = LC.dedup(ps)
    assert removed == 1 and len(out) == 2


# ---- 第3层 程序规则 ----
@pytest.mark.unit
def test_rule_animal_cannot_support_clinical():
    det = LC.layer1_deterministic({"title": "mouse STING inhibitor", "full_text_available": True})
    r = LC.layer3_rules(det, {"supporting_excerpt": "x"})
    assert any("动物" in c for c in r["caveats"])


@pytest.mark.unit
def test_rule_no_sample_size_not_fabricated():
    det = LC.layer1_deterministic({"title": "human cohort", "full_text_available": True})
    r = LC.layer3_rules(det, {"supporting_excerpt": "x"})   # extraction 没给样本量
    assert r["sample_size"] == NOT_REPORTED                  # 不编造


@pytest.mark.unit
def test_rule_abstract_not_key_evidence():
    det = LC.layer1_deterministic({"title": "human study", "full_text_available": False})
    r = LC.layer3_rules(det, {"supporting_excerpt": "some excerpt"})
    assert r["tier"] == "abstract" and r["key_evidence_eligible"] is False   # 摘要≠全文证据


@pytest.mark.unit
def test_rule_no_locator_not_key_evidence():
    det = LC.layer1_deterministic({"title": "human study", "full_text_available": True})
    r = LC.layer3_rules(det, {"supporting_excerpt": ""})     # 无来源定位
    assert r["key_evidence_eligible"] is False


@pytest.mark.unit
def test_rule_fulltext_with_locator_is_key_eligible():
    det = LC.layer1_deterministic({"title": "human knockdown study", "full_text_available": True})
    r = LC.layer3_rules(det, {"supporting_excerpt": "knockdown reduced fibrosis (Fig 3)"})
    assert r["key_evidence_eligible"] is True


@pytest.mark.unit
def test_rule_preprint_marked():
    det = LC.layer1_deterministic({"title": "x", "journal": "bioRxiv", "full_text_available": True})
    r = LC.layer3_rules(det, {"supporting_excerpt": "y"})
    assert "preprint_marked" in r["flags"]


# ---- 第4层 人工审核优先级 ----
@pytest.mark.unit
def test_layer4_flags_clinical_and_destinations():
    det = LC.layer1_deterministic({"title": "randomized controlled trial", "full_text_available": True})
    ext = {"main_findings": ["treatment improved outcome"], "extraction_confidence": 0.9}
    rev = LC.layer4_review_priority(det, ext, {}, destinations=["benchmark"])
    assert rev["human_review_required"] is True
    assert any("临床" in r for r in rev["reasons"]) and any("benchmark" in r for r in rev["reasons"])


@pytest.mark.unit
def test_layer4_model_disagreement():
    det = LC.layer1_deterministic({"title": "study"})
    a = {"sample_size": "n=100", "main_findings": ["A"]}
    b = {"sample_size": "n=20", "main_findings": ["B"]}
    rev = LC.layer4_review_priority(det, a, {}, second_ext=b)
    assert any("不一致" in r for r in rev["reasons"])


@pytest.mark.unit
def test_clean_paper_end_to_end():
    paper = {"title": "human fibroblast knockdown study", "pmid": "12345678",
             "full_text_available": True, "year": "2022"}
    out = LC.clean_paper(paper, _fake_extractor({"supporting_excerpt": "knockdown reduced fibrosis",
                                                 "sample_size": "n=30", "extraction_confidence": 0.8}),
                         destinations=["protocol"])
    assert out["layer3_rules"]["key_evidence_eligible"] is True
    assert out["layer4_review"]["human_review_required"] is True   # 进 protocol → 需审核


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                print(f"FAIL {name}: {e}")
