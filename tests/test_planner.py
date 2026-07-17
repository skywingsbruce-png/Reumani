"""结构化 Planner 测试：schema 验证 / tool_name 授权 / 重试封顶 / 解析失败即停(不降级)。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planner import parse_and_validate_plan, PlanValidationError

ALLOWED = ["search_literature", "query_data_lake"]


def _step(**over):
    s = {"step_id": 1, "objective": "找文献", "tool_name": "search_literature",
         "arguments": {"query": "ssc"}, "expected_output": "相关论文",
         "success_criteria": "至少3篇相关文献"}
    s.update(over)
    return s


def _plan(**over):
    p = {"question": "SSc 致病成纤维亚群？", "steps": [_step()], "maximum_retries": 2}
    p.update(over)
    return json.dumps(p, ensure_ascii=False)


@pytest.mark.unit
def test_valid_plan_parses():
    plan = parse_and_validate_plan(_plan(), ALLOWED, max_retries_cap=2)
    assert plan.steps[0].tool_name == "search_literature"
    assert plan.steps[0].success_criteria                       # 每步有成功条件


@pytest.mark.unit
def test_tool_name_must_be_allowed():
    bad = _plan(steps=[_step(tool_name="run_python")])          # 未授权工具
    with pytest.raises(PlanValidationError):
        parse_and_validate_plan(bad, ALLOWED)


@pytest.mark.unit
def test_llm_cannot_expand_permissions():
    # LLM 想加一个本任务没有的工具 → 必须被拒（不能自行扩权）
    bad = _plan(steps=[_step(), _step(step_id=2, tool_name="triage_hypothesis")])
    with pytest.raises(PlanValidationError):
        parse_and_validate_plan(bad, ALLOWED)


@pytest.mark.unit
def test_missing_success_criteria_rejected():
    s = _step()
    del s["success_criteria"]
    with pytest.raises(PlanValidationError):
        parse_and_validate_plan(_plan(steps=[s]), ALLOWED)


@pytest.mark.unit
def test_bad_json_stops_no_freetext():
    with pytest.raises(PlanValidationError):
        parse_and_validate_plan("这是一段自由文本计划，不是JSON", ALLOWED)


@pytest.mark.unit
def test_empty_steps_rejected():
    with pytest.raises(PlanValidationError):
        parse_and_validate_plan(_plan(steps=[]), ALLOWED)


@pytest.mark.unit
def test_max_retries_capped_by_program():
    plan = parse_and_validate_plan(_plan(maximum_retries=99), ALLOWED, max_retries_cap=2)
    assert plan.maximum_retries == 2                            # LLM 给99也被程序封到2


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                print(f"FAIL {name}: {e}")
