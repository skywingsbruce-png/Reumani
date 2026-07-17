"""小型端到端 mock：权限选工具 → 结构化计划 → 文献清洗 → 证据卡 → Claim裁决 → 四层Verifier。
全用假依赖，不调 LLM/API/网络/大数据。验证各环节接线一致。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tool_registry import select_tool_names, apply_approvals, resolve
from planner import parse_and_validate_plan
import lit_cleaning as LC
from evidence_build import abstract_card_from_extraction
from schemas import Claim
import verifier as V


@pytest.mark.unit
def test_end_to_end_mock_pipeline():
    q = "cGAS-STING 与系统性硬化症纤维化的关联证据"

    # 1) 权限：确定性选工具 → 高风险未批准物理排除 → resolve 校验
    allowed = apply_approvals(select_tool_names(q))
    resolve(allowed)                                    # 全是注册表真实工具，否则抛错
    assert "search_literature" in allowed and "run_python" not in allowed   # 高风险默认排除

    # 2) 结构化计划：只用授权工具，schema 校验通过
    plan_json = json.dumps({"question": q, "steps": [
        {"step_id": 1, "objective": "查文献", "tool_name": "search_literature",
         "expected_output": "相关论文", "success_criteria": "≥3篇相关"}], "maximum_retries": 5})
    plan = parse_and_validate_plan(plan_json, allowed, max_retries_cap=2)
    assert plan.maximum_retries == 2                    # 程序封顶

    # 3) 文献清洗（LLM 只做抽取，这里注入假抽取）
    paper = {"title": "cGAS-STING correlates with fibrosis in SSc skin", "pmid": "12345678",
             "full_text_available": True, "journal": "bioRxiv"}
    cleaned = LC.clean_paper(paper, lambda p: {"supporting_excerpt": "", "sample_size": "n=40",
                                               "main_findings": ["correlation r=0.35"]})
    assert cleaned["layer1_deterministic"]["preprint"] is True   # 预印本被标记

    # 4) 证据卡（保留 provenance；预印本 + 无逐字原文 → 不能作关键结论）
    card = abstract_card_from_extraction(
        {"study_type": "cross-sectional", "main_findings": ["correlation"]}, paper)
    assert card.usable_for_key_conclusion()[0] is False

    # 5) Claim：因果主张只有相关性证据 → 单独裁决为 partially_supported
    causal = Claim(claim_id="c", text="cGAS-STING 因果驱动 SSc 纤维化", claim_type="causal",
                   supporting_evidence_ids=[card.evidence_id])
    r3, judged = V.claim_evidence_verify([causal], [card])
    assert r3["passed"] is False                         # 相关≠因果

    # 6) 四层 Verifier：整体 fail-closed + 需人工复核
    from schemas import ToolResult, Provenance
    ok_tr = ToolResult(ok=True, data="x", provenance=Provenance(tool_name="search_literature"))
    out = V.verify_all(q, [ok_tr], [causal], [card], adversary_searcher=lambda x: [])
    assert out["passed"] is False and out["human_review_required"] is True


if __name__ == "__main__":
    test_end_to_end_mock_pipeline()
    print("PASS test_end_to_end_mock_pipeline")
