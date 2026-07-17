"""
结构化 Planner —— Planner 不再输出自由文本，而是产出经 schema 验证的 ResearchPlan。
铁律：
- 输出必须通过 ResearchPlan/PlanStep schema 验证（含每步 success_criteria 必填）。
- 每步 tool_name 必须在本任务 allowed_tools 内（LLM 不能自行扩大权限）。
- 最大重试次数由程序封顶（max_retries_cap）。
- 解析/验证失败 → 抛 PlanValidationError 停止，【绝不】自由文本降级执行。
"""

import json
import re

from schemas import ResearchPlan


class PlanValidationError(Exception):
    """计划解析或验证失败 → 必须停止，不得降级为自由文本执行。"""


def _parse_json_obj(text):
    text = (text or "").strip()
    text = re.sub(r"^```(json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        text = m.group(0)
    return json.loads(text)


def build_plan_prompt(question, constraints, selected_resources, allowed_tools, feedback=""):
    fb = f"\n\n上一轮验证未通过：\n{feedback}\n请据此修订计划。" if feedback else ""
    return (
        "你是 SSc 科研 Planner。请把研究问题拆成结构化计划，并【只】输出一个严格 JSON 对象，"
        "不要任何解释文字。JSON 形如：\n"
        "{\n"
        '  "question": "...",\n'
        '  "constraints": "...",\n'
        '  "selected_resources": ["..."],\n'
        '  "steps": [\n'
        "    {\n"
        '      "step_id": 1,\n'
        '      "objective": "这一步要达成什么",\n'
        '      "tool_name": "必须从下方允许工具里选",\n'
        '      "arguments": {"参数名": "值"},\n'
        '      "expected_output": "预期产出",\n'
        '      "success_criteria": "如何算这一步成功(必填)",\n'
        '      "risk_level": "low|medium|high",\n'
        '      "requires_human_approval": false,\n'
        '      "on_failure": "stop|retry|skip"\n'
        "    }\n"
        "  ],\n"
        '  "stop_conditions": ["..."],\n'
        '  "maximum_retries": 2\n'
        "}\n\n"
        f"【硬性约束】tool_name 只能取以下真实且已授权的工具名，禁止发明或使用其它工具：\n{sorted(allowed_tools)}\n"
        "每个步骤都必须写明 success_criteria。\n\n"
        f"研究问题：{question}\n约束：{constraints or '无'}\n"
        f"可用资源：\n{selected_resources}{fb}\n"
    )


def parse_and_validate_plan(text, allowed_tools, max_retries_cap=2):
    """解析 + 严格验证。任何失败都抛 PlanValidationError（停止，不降级）。"""
    try:
        data = _parse_json_obj(text)
    except Exception as e:
        raise PlanValidationError(f"计划 JSON 无法解析：{e}")
    try:
        plan = ResearchPlan(**data)          # schema 验证：含 success_criteria 必填、steps 非空
    except Exception as e:
        raise PlanValidationError(f"计划不符合 schema：{e}")

    allowed = set(allowed_tools)
    for s in plan.steps:
        if s.tool_name not in allowed:        # 不能用未授权工具，不能自行扩权
            raise PlanValidationError(
                f"步骤 {s.step_id} 使用了未授权工具 {s.tool_name!r}；允许的工具：{sorted(allowed)}")

    # 最大重试次数由程序封顶（LLM 给多大都不算数）
    plan.maximum_retries = max(0, min(plan.maximum_retries, max_retries_cap))
    return plan


def make_plan(llm, question, constraints, selected_resources, allowed_tools,
              max_retries_cap=2, feedback=""):
    """调用 LLM 产出结构化计划并验证。失败抛 PlanValidationError（调用方必须停止，不得自由文本执行）。"""
    prompt = build_plan_prompt(question, constraints, selected_resources, allowed_tools, feedback)
    resp = llm.invoke(prompt).content
    return parse_and_validate_plan(resp, allowed_tools, max_retries_cap)


def render_plan_text(plan):
    """把已验证的计划渲染成可读文本，交给（受权限门禁的）Executor 参考执行。"""
    lines = [f"研究问题：{plan.question}", f"约束：{plan.constraints or '无'}",
             f"最大重试：{plan.maximum_retries}", "步骤："]
    for s in plan.steps:
        appr = "（需人工批准）" if s.requires_human_approval else ""
        lines.append(
            f"  {s.step_id}. [{s.tool_name}{appr}] {s.objective}\n"
            f"     参数：{s.arguments}\n     预期：{s.expected_output}\n"
            f"     成功条件：{s.success_criteria}\n     失败时：{s.on_failure}")
    if plan.stop_conditions:
        lines.append("停止条件：" + "；".join(plan.stop_conditions))
    return "\n".join(lines)
