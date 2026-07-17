"""文献分层使用测试：A–F 分级(不看影响因子) + 任务动态 + 透明多因子 + 全量保留。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from schemas import LiteratureQuality
import lit_ranking as LR

PAPER = {"title": "systemic sclerosis skin fibrosis study",
         "abstract": "systemic sclerosis fibrosis outcome"}
Q = "systemic sclerosis fibrosis"


def _q(**over):
    return LiteratureQuality(**over)


@pytest.mark.unit
def test_tier_by_design_not_impact_factor():
    assert LR.classify_tier(_q(study_type="randomized controlled trial", human_evidence=True, randomized=True, sample_size=400)) == "A"
    assert LR.classify_tier(_q(study_type="prospective cohort", human_evidence=True, longitudinal=True, sample_size=900)) == "B"
    assert LR.classify_tier(_q(study_type="mechanistic human tissue biopsy", human_evidence=True)) == "C"
    assert LR.classify_tier(_q(study_type="mouse model", animal_evidence=True)) == "D"
    assert LR.classify_tier(_q(study_type="in vitro cell line", in_vitro_evidence=True)) == "D"
    assert LR.classify_tier(_q(study_type="cohort", human_evidence=True, preprint=True)) == "E"
    assert LR.classify_tier(_q(study_type="editorial comment")) == "F"
    assert LR.classify_tier(_q(study_type="rct", human_evidence=True, randomized=True, retracted=True)) == "F"


@pytest.mark.unit
def test_factors_are_transparent_not_single_score():
    f = LR.score_factors(PAPER, _q(study_type="rct", human_evidence=True, randomized=True, full_text_available=True), Q, task="clinical_treatment")
    for k in ["tier", "research_quality", "relevance", "directness", "reproducibility", "traceability", "task_fit", "combined", "flags"]:
        assert k in f                                    # 每个因子都单独暴露


@pytest.mark.unit
def test_directness_depends_on_task():
    q_animal = _q(study_type="mouse model", animal_evidence=True)
    clin = LR.score_factors(PAPER, q_animal, Q, task="clinical_treatment")["directness"]
    feas = LR.score_factors(PAPER, q_animal, Q, task="mechanism_feasibility")["directness"]
    assert clin < feas                                   # 动物研究对临床问题直接性低，对可行性问题高


@pytest.mark.unit
def test_flags_caveats():
    assert any("撤稿" in x for x in LR.score_factors(PAPER, _q(retracted=True), Q)["flags"])
    assert any("预印本" in x for x in LR.score_factors(PAPER, _q(preprint=True), Q)["flags"])
    assert any("非人体" in x for x in LR.score_factors(PAPER, _q(animal_evidence=True), Q, task="clinical_treatment")["flags"])


@pytest.mark.unit
def test_rank_full_retention_retracted_excluded_not_deleted():
    items = [
        {"paper": PAPER, "quality": _q(study_type="rct", human_evidence=True, randomized=True, sample_size=400, full_text_available=True)},
        {"paper": PAPER, "quality": _q(study_type="mouse model", animal_evidence=True)},
        {"paper": PAPER, "quality": _q(study_type="rct", human_evidence=True, randomized=True, retracted=True)},
    ]
    res = LR.rank_literature(items, Q, task="clinical_treatment")
    assert res["ranked"][0]["tier"] == "A"                       # RCT 排最前
    assert res["ranked"][0]["factors"]["combined"] >= res["ranked"][-1]["factors"]["combined"]
    assert len(res["excluded_retracted"]) == 1                    # 撤稿移出结论排序
    assert "未删除" in res["note"]                                # 但全量保留，不删数据湖


@pytest.mark.unit
def test_task_changes_ranking():
    hi_human = {"paper": PAPER, "quality": _q(study_type="mechanistic human tissue", human_evidence=True, full_text_available=True, independent_replication=True)}
    animal = {"paper": PAPER, "quality": _q(study_type="mouse model", animal_evidence=True)}
    mech = LR.rank_literature([hi_human, animal], Q, task="mechanism")
    assert mech["ranked"][0]["tier"] == "C"                       # 机制问题人体组织优先


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                print(f"FAIL {name}: {e}")
