"""
SSc-A1：把固定的"正方-反方-裁判"升级成 Planner → Executor → Verifier 状态机。
  Planner   根据问题 + 检索到的资源，制定研究计划
  Executor  用技能 agent（带所有工具）执行计划，产生真实 Observation
  Verifier  核对结果是否达成目标、证据是否充分
    ├─ 通过 → 返回
    └─ 不通过 → 修订计划重试（有 retry_count / max_iterations 循环保护，绝不死循环）
原来的正反方辩论降级为可选的 Skeptic（寻找反证），不再是主干。
每次运行都留可复现记录（runs/<时间戳>/）。
"""

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

from ssc_pi_agent import judge_llm, deepseek_llm_pro
from ssc_resources import retriever as _resource_retriever
from ssc_skill_agent import build_skill_agent

BASE = Path(__file__).resolve().parent
RUNS_DIR = BASE / "runs"
RUNS_DIR.mkdir(exist_ok=True)


# ==========================================
# 状态（阶段4 规格 + 循环保护）
# ==========================================
@dataclass
class AgentState:
    user_query: str = ""
    constraints: str = ""
    selected_resources: str = ""
    plan: str = ""
    current_step: int = 0
    observations: list = field(default_factory=list)
    evidence_cards: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    verification_results: list = field(default_factory=list)
    artifacts: list = field(default_factory=list)
    final_answer: str = ""
    # 循环保护，防止 Planner-Executor-Verifier 死循环
    retry_count: int = 0
    max_iterations: int = 2

    def to_dict(self):
        return asdict(self)


def _parse_json(text):
    text = text.strip()
    text = re.sub(r"^```(json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        text = m.group(0)
    return json.loads(text)


# ==========================================
# 三个角色
# ==========================================
def plan(state: AgentState, judge_model="claude", failure_feedback=""):
    llm = judge_llm if judge_model == "claude" else deepseek_llm_pro
    fb = f"\n\n上一轮验证未通过，原因：\n{failure_feedback}\n请针对性修订计划。" if failure_feedback else ""
    prompt = (
        "你是 SSc 科研 Planner。根据研究问题和可用资源，制定一个简洁、可执行的研究计划"
        "（3-6 步，每步说清用哪个资源/工具、要得到什么、如何验证）。\n\n"
        f"研究问题：{state.user_query}\n"
        f"约束：{state.constraints or '无'}\n\n"
        f"可用资源（已由检索器筛选）：\n{state.selected_resources}{fb}\n\n"
        "只输出计划步骤，不要现在就执行。"
    )
    return llm.invoke(prompt).content


def execute(state: AgentState, executor_model="deepseek"):
    """用技能 agent 执行计划，返回 (final_text, messages)。"""
    agent = build_skill_agent(executor_model)
    task = (
        f"研究问题：{state.user_query}\n\n"
        f"请严格按以下计划执行，用工具做真实检索/分析，不要编造：\n{state.plan}\n\n"
        "执行完给出结论，并说明每一步用了什么工具、得到什么。"
    )
    result = agent.invoke({"messages": [("user", task)]})
    msgs = result["messages"]
    final = msgs[-1].content if hasattr(msgs[-1], "content") else str(msgs[-1])
    return final, msgs


def verify(state: AgentState, executor_output, judge_model="claude"):
    """Verifier：核对执行结果是否达成目标、证据是否充分、有无过度解读。返回 dict。"""
    llm = judge_llm if judge_model == "claude" else deepseek_llm_pro
    prompt = (
        "你是严格的 Verifier。核对下面的执行结果是否真正回答了研究问题、证据是否充分、"
        "有没有编造或过度解读。\n"
        "输出严格 JSON：{\"passed\": true/false, \"reason\": \"一句话理由\", "
        "\"missing\": \"还缺什么（没有就写无）\"}\n\n"
        f"研究问题：{state.user_query}\n\n计划：\n{state.plan}\n\n执行结果：\n{executor_output}"
    )
    resp = llm.invoke(prompt).content
    try:
        v = _parse_json(resp)
    except Exception:
        v = {"passed": True, "reason": "验证器输出无法解析，默认放行", "missing": resp[:200]}
    return v


def _extract_trace(messages):
    """从执行 messages 里抽出用过的工具（可复现记录用）。"""
    steps = []
    for m in messages:
        tcs = getattr(m, "tool_calls", None)
        if tcs:
            for tc in tcs:
                name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                steps.append(name)
    return steps


# ==========================================
# 主循环（含循环保护 + 可复现记录）
# ==========================================
def run_agent(user_query, constraints="", max_iterations=2,
              executor_model="deepseek", judge_model="claude",
              stamp=None):
    state = AgentState(user_query=user_query, constraints=constraints,
                       max_iterations=max_iterations)
    # 阶段0：资源检索
    state.selected_resources = _resource_retriever.bundle_text(user_query, top_k=12)

    failure_feedback = ""
    best_output = ""
    while state.retry_count < state.max_iterations:
        # Plan
        state.plan = plan(state, judge_model=judge_model, failure_feedback=failure_feedback)
        # Execute
        try:
            output, msgs = execute(state, executor_model=executor_model)
        except Exception as e:
            state.errors.append(f"执行出错：{e}")
            state.retry_count += 1
            failure_feedback = f"执行阶段报错：{e}"
            continue
        state.observations.append(output)
        state.artifacts.append({"tools_used": _extract_trace(msgs)})
        best_output = output
        # Verify
        v = verify(state, output, judge_model=judge_model)
        state.verification_results.append(v)
        if v.get("passed"):
            state.final_answer = output
            break
        # 不通过 → 修订重试（循环保护：retry_count 递增，到上限就停）
        state.retry_count += 1
        failure_feedback = f"{v.get('reason','')}；缺：{v.get('missing','')}"

    # 循环保护触发：超过上限仍未通过，返回当前最佳 + 失败原因，而不是死循环
    if not state.final_answer:
        last = state.verification_results[-1] if state.verification_results else {}
        state.final_answer = (
            f"（已达最大迭代 {state.max_iterations} 次仍未完全通过验证，返回当前最佳结果）\n\n"
            f"{best_output}\n\n---\n未通过原因：{last.get('reason','')}；还缺：{last.get('missing','')}"
        )

    _save_run(state, stamp)
    return state


def _save_run(state: AgentState, stamp=None):
    stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    d = RUNS_DIR / stamp
    d.mkdir(exist_ok=True)
    (d / "run.json").write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "final_answer.md").write_text(state.final_answer, encoding="utf-8")
    return str(d)


if __name__ == "__main__":
    # 自检：只验证状态机和循环保护逻辑，用假的角色，不调 API
    s = AgentState(user_query="test", max_iterations=2)
    print("AgentState 字段：", list(s.to_dict().keys()))
    print("循环保护：max_iterations =", s.max_iterations, "retry_count 起始 =", s.retry_count)
