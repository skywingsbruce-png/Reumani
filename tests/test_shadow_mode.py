"""Shadow Mode 端到端测试（a–m）。全用 fake tools/claims/verifier，不调 API、不依赖 data_lake。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shadow as SH
from schemas import (Provenance, FullTextEvidenceCard, AnalysisEvidenceCard)

PROV = Provenance(tool_name="t", source="s")


def _ev(name, ok=True, data="", args=None):
    return {"tool_name": name, "arguments": args or {}, "ok": ok,
            "data": data if ok else None, "error": None if ok else "boom",
            "provenance": {"tool_name": name, "provenance_quality": "legacy_unstructured"},
            "warnings": [], "artifacts": []}


def _fake_claims(*claims):
    return lambda final_text, card_ids: list(claims)


def _run(**kw):
    kw.setdefault("question", "q")
    kw.setdefault("old_verify", {"passed": True})
    return SH.run_shadow(**kw)


# a. 工具成功 + 真实 PMID → 证据卡有真实来源，claim 被关联（非 insufficient）
@pytest.mark.unit
def test_a_success_real_pmid_claim_linked():
    ev = _ev("search_literature", data="见 https://pubmed.ncbi.nlm.nih.gov/12345678/")
    m = _run(tool_events=[ev], allowed_tools=["search_literature"],
             claim_extractor=_fake_claims({"text": "STING 激活存在", "claim_type": "existence",
                                           "supporting_ids": ["PMID:12345678"]}))
    assert any(c["pmid"] == "12345678" for c in m["evidence_cards"])       # 真实来源进卡
    assert m["claims"][0]["verdict"] != "insufficient_evidence"            # 找到了证据


# b. 工具失败但 Executor 写出漂亮结论 → shadow 不通过
@pytest.mark.unit
def test_b_tool_failed_but_nice_answer():
    m = _run(tool_events=[_ev("run_python", ok=False)], final_text="我们确证了强因果关系！",
             claim_extractor=_fake_claims({"text": "X 导致 Y", "claim_type": "causal"}))
    assert m["any_tool_failed"] is True
    assert m["shadow_verifier_result"]["passed"] is False                 # 不被漂亮结论蒙混


# c. Claim 没有 EvidenceCard → insufficient_evidence
@pytest.mark.unit
def test_c_claim_without_evidence():
    m = _run(tool_events=[_ev("search_literature", data="无链接的文本")],
             claim_extractor=_fake_claims({"text": "某结论", "claim_type": "existence"}))
    assert m["claims"][0]["verdict"] == "insufficient_evidence"


# d. EvidenceCard 只有摘要 → 不能作关键结论
@pytest.mark.unit
def test_d_abstract_only_not_key():
    from schemas import AbstractEvidenceCard
    card = AbstractEvidenceCard(evidence_id="PMID:1", title="t", provenance=PROV,
                                pmid="1", supporting_excerpt="", evidence_grade="初筛")
    m = _run(tool_events=[_ev("search_literature", data="x")], evidence_cards=[card],
             claim_extractor=_fake_claims({"text": "存在", "claim_type": "existence",
                                           "supporting_ids": ["PMID:1"]}))
    assert m["shadow_verifier_result"]["passed"] is False


# e. 动物证据支持临床疗效 Claim → 不成立
@pytest.mark.unit
def test_e_animal_for_clinical():
    card = FullTextEvidenceCard(evidence_id="PMID:2", title="t", provenance=PROV, pmid="2",
                                supporting_excerpt="mouse improved", evidence_direction="supports",
                                study_type="mouse model", species="mouse", source_page="p1")
    m = _run(tool_events=[_ev("search_literature", data="x")], evidence_cards=[card],
             claim_extractor=_fake_claims({"text": "抑制X对患者有效", "claim_type": "clinical_efficacy",
                                           "supporting_ids": ["PMID:2"]}))
    assert m["claims"][0]["verdict"] != "supported"


# f. 横断面相关支持强因果 Claim → 不成立
@pytest.mark.unit
def test_f_correlation_for_causal():
    card = AnalysisEvidenceCard(evidence_id="A:1", title="t", provenance=PROV, dataset="GSE1",
                                method="corr", evidence_direction="correlational", supporting_excerpt="r")
    m = _run(tool_events=[_ev("query_data_lake", data="x")], evidence_cards=[card],
             claim_extractor=_fake_claims({"text": "X 因果驱动 Y", "claim_type": "causal",
                                           "supporting_ids": ["A:1"]}))
    assert m["claims"][0]["verdict"] == "partially_supported"             # 相关≠因果


# g. 伪造 DOI → citation 层不过
@pytest.mark.unit
def test_g_fake_doi():
    card = FullTextEvidenceCard(evidence_id="e", title="t", provenance=PROV,
                                supporting_excerpt="x", doi="fake-doi", source_page="p")
    m = _run(tool_events=[_ev("search_literature", data="x")], evidence_cards=[card],
             claim_extractor=_fake_claims())
    assert m["shadow_verifier_result"]["layers"]["citation"]["passed"] is False


# h. Claim 提取失败（extractor 抛错）→ 结构化错误，不崩
@pytest.mark.unit
def test_h_claim_extraction_error():
    def boom(t, ids):
        raise ValueError("bad json")
    m = _run(tool_events=[_ev("search_literature", data="x")], claim_extractor=boom)
    assert m["claim_extraction_error"]["error"] == "claim_extraction_failed"


# i. 一个 Claim 支持、另一个不支持 → 各自裁决
@pytest.mark.unit
def test_i_mixed_claims():
    good = FullTextEvidenceCard(evidence_id="PMID:9", title="t", provenance=PROV, pmid="9",
                                supporting_excerpt="knockdown reduced fibrosis",
                                evidence_direction="supports", study_type="human knockdown",
                                species="human", source_figure_or_table="Fig1")
    m = _run(tool_events=[_ev("search_literature", data="x")], evidence_cards=[good],
             claim_extractor=_fake_claims(
                 {"text": "knockdown 降低纤维化", "claim_type": "causal", "supporting_ids": ["PMID:9"]},
                 {"text": "无证据的主张", "claim_type": "existence"}))
    verdicts = [c["verdict"] for c in m["claims"]]
    assert "supported" in verdicts and "insufficient_evidence" in verdicts


# j. 旧字符串工具经 Legacy Adapter
@pytest.mark.unit
def test_j_legacy_adapter():
    e = SH.adapt_legacy_result("read_file", {}, "一些非结构化文本")
    assert e["provenance"]["provenance_quality"] == "legacy_unstructured" and e["ok"] is True
    fail = SH.adapt_legacy_result("run_python", {}, "[工具失败:blocked] x")
    assert fail["ok"] is False


# k. 未授权工具调用被记录
@pytest.mark.unit
def test_k_unauthorized_tool():
    m = _run(tool_events=[_ev("run_python", data="x")], allowed_tools=["search_literature"],
             claim_extractor=_fake_claims())
    assert "run_python" in m["unauthorized_tool_calls"]


# l. provenance 缺失 → citation 层标记
@pytest.mark.unit
def test_l_missing_provenance():
    card = FullTextEvidenceCard(evidence_id="e", title="t", provenance=Provenance(tool_name="t"),
                                supporting_excerpt="x", source_page="p")   # provenance.source 缺失
    m = _run(tool_events=[_ev("x", data="d")], evidence_cards=[card], claim_extractor=_fake_claims())
    assert m["shadow_verifier_result"]["layers"]["citation"]["passed"] is False


# m. 新旧 Verifier 判定冲突 → 记录 divergence
@pytest.mark.unit
def test_m_old_shadow_divergence():
    m = _run(tool_events=[_ev("run_python", ok=False)], old_verify={"passed": True},
             claim_extractor=_fake_claims())
    assert m["comparison"]["old_passed"] is True and m["comparison"]["shadow_passed"] is False
    assert m["comparison"]["divergence"] is True and m["shadow_verification"] is True


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                print(f"FAIL {name}: {e}")
