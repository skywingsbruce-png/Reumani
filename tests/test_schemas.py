"""核心数据契约测试（unit）：严格校验 + 失败不可伪装成功。"""
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from schemas import (Provenance, ToolResult, VerificationResult, EvidenceCard,
                     ResearchPlan, PlanStep)

P = Provenance(tool_name="t")


@pytest.mark.unit
def test_toolresult_failure_requires_error():
    with pytest.raises(ValidationError):
        ToolResult(ok=False, provenance=P)                    # 失败却没错误信息 → 拒绝


@pytest.mark.unit
def test_toolresult_success_must_not_carry_error():
    with pytest.raises(ValidationError):
        ToolResult(ok=True, error_type="x", error_message="y", provenance=P)


@pytest.mark.unit
def test_toolresult_failure_astext_marks_failure():
    r = ToolResult(ok=False, error_type="blocked", error_message="nope", provenance=P)
    assert "工具失败" in r.as_text() and "nope" in r.as_text()   # 不会被当成正常结果


@pytest.mark.unit
def test_provenance_requires_tool_name():
    with pytest.raises(ValidationError):
        Provenance()


@pytest.mark.unit
def test_extra_field_forbidden():
    with pytest.raises(ValidationError):
        Provenance(tool_name="t", bogus=1)                    # 夹带未知字段 → 拒绝


@pytest.mark.unit
def test_verification_passed_status_consistency():
    with pytest.raises(ValidationError):
        VerificationResult(passed=True, status="not_passed")
    assert VerificationResult(passed=True, status="passed").passed is True


@pytest.mark.unit
def test_evidence_card_requires_source():
    with pytest.raises(ValidationError):
        EvidenceCard(claim="c", main_finding="f")             # 缺 source → 拒绝
    assert EvidenceCard(claim="c", main_finding="f", source="PMID:1").source == "PMID:1"


@pytest.mark.unit
def test_research_plan_nested_typed():
    p = ResearchPlan(query="q", steps=[PlanStep(n=1, goal="g", expected_output="o", verification="v")])
    assert p.steps[0].n == 1


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                print(f"FAIL {name}: {e}")
