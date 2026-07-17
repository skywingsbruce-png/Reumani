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

import concurrent.futures
import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

from ssc_pi_agent import judge_llm, deepseek_llm_pro
from ssc_resources import retriever as _resource_retriever
from tool_registry import select_tool_names, apply_approvals, all_tool_names
from schemas import VerificationResult   # 统一契约（P0-3）
# 注意：build_skill_agent 在 execute() 内惰性导入，避免核查/单测只想用 verify 时
# 被迫拉起整条工具链（也让无 API key 的 CI 能导入本模块）。

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
    tool_trace: list = field(default_factory=list)   # 工具选择/拒绝/调用轨迹（权限控制可审计）
    allowed_tools: list = field(default_factory=list)  # 本任务被授权的工具名
    research_plan: dict = field(default_factory=dict)  # 结构化计划（ResearchPlan.model_dump）
    # 循环保护，防止 Planner-Executor-Verifier 死循环
    retry_count: int = 0
    max_iterations: int = 2

    def to_dict(self):
        return asdict(self)


def _fail(status, reason, **kw):
    # 统一用 schemas.VerificationResult（严格校验），返回 dict 供主循环使用
    return VerificationResult(passed=False, status=status, reason=reason, **kw).model_dump()


def _aslist(v):
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()]
    s = str(v).strip()
    return [] if s in ("", "无", "none", "None", "N/A", "n/a", "—", "-") else [s]


# 可核实引用：PMID / PMC / DOI / PubMed 链接 / GSE 号
_CIT = re.compile(r"\bPMID[:\s]*\d+|\bPMC\d+|10\.\d{4,}/\S+|pubmed\.ncbi\.nlm\.nih\.gov/\d+|\bGSE\d+", re.I)


def _has_citations(text):
    return bool(_CIT.search(text or ""))


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
    allowed = state.allowed_tools or all_tool_names()
    prompt = (
        "你是 SSc 科研 Planner。根据研究问题和可用资源，制定一个简洁、可执行的研究计划"
        "（3-6 步，每步说清用哪个工具、要得到什么、如何验证）。\n\n"
        f"研究问题：{state.user_query}\n"
        f"约束：{state.constraints or '无'}\n\n"
        f"可用资源（已由检索器筛选）：\n{state.selected_resources}{fb}\n\n"
        f"【硬性】你只能规划使用以下真实存在且本任务已授权的工具名，禁止发明或使用其它工具名：\n{allowed}\n\n"
        "只输出计划步骤，不要现在就执行。"
    )
    return llm.invoke(prompt).content


def execute(state: AgentState, executor_model="deepseek"):
    """用技能 agent 执行计划，返回 (final_text, messages)。
    Executor 只拿到 state.allowed_tools —— 未授权/未知工具物理上无法被调用。"""
    from ssc_skill_agent import build_skill_agent   # 惰性导入，见文件顶部说明
    agent = build_skill_agent(executor_model, allowed_tools=state.allowed_tools or None)
    task = (
        f"研究问题：{state.user_query}\n\n"
        f"请严格按以下计划执行，用工具做真实检索/分析，不要编造：\n{state.plan}\n\n"
        "执行完给出结论，并说明每一步用了什么工具、得到什么。"
    )
    result = agent.invoke({"messages": [("user", task)]})
    msgs = result["messages"]
    final = msgs[-1].content if hasattr(msgs[-1], "content") else str(msgs[-1])
    return final, msgs


VERIFY_TIMEOUT = 120  # 秒


def _verifier_llm_call(prompt, judge_model="claude"):
    llm = judge_llm if judge_model == "claude" else deepseek_llm_pro
    return llm.invoke(prompt).content


def verify(state: AgentState, executor_output, judge_model="claude", *,
           verifier_call=None, timeout=VERIFY_TIMEOUT, tool_failed=False,
           require_evidence=True, evidence_cards=None):
    """【Fail-closed 核查】任何核查异常/无法核实/证据不足，一律判【未通过】，绝不默认放行。
    返回 VerificationResult.to_dict()。verifier_call 可注入以便测试（签名 (prompt, judge_model)）。"""
    # 0) 工具执行失败 → 未通过（即使 LLM 生成了看似正常的答案）
    if tool_failed:
        return _fail("tool_execution_failed", "工具执行失败，结论不可信")

    # 1) 证据不足：无证据卡且结论没有任何可核实引用 → 未通过，标记未验证/证据不足
    if require_evidence:
        cards = evidence_cards if evidence_cards is not None else state.evidence_cards
        if not cards and not _has_citations(executor_output):
            return _fail("insufficient_evidence",
                         "无证据卡且结论无任何可核实引用（PMID/DOI/GSE），标记为未验证/证据不足",
                         warnings=["结论缺乏来源支撑，不能视为已验证"])

    # 2) 调用 Verifier（带超时；调用异常/超时都 fail-closed）
    prompt = (
        "你是严格的 Verifier。核对下面的执行结果是否真正回答了研究问题、证据是否充分、"
        "有没有编造或过度解读。\n"
        "输出严格 JSON：{\"passed\": true/false, \"reason\": \"一句话理由\", "
        "\"missing\": \"还缺什么（没有就写无）\"}\n\n"
        f"研究问题：{state.user_query}\n\n计划：\n{state.plan}\n\n执行结果：\n{executor_output}"
    )
    call = verifier_call or _verifier_llm_call
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            resp = ex.submit(call, prompt, judge_model).result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        return _fail("verifier_timeout", "Verifier 超时")
    except Exception as e:
        return _fail("verifier_unavailable", f"Verifier 无法调用：{e}")

    # 3) 空输出 → 未通过
    if resp is None or not str(resp).strip():
        return _fail("verification_error", "Verifier 返回空")

    # 4) JSON 无法解析 → 未通过（原来的 bug 在这里默认放行，已修）
    try:
        raw = _parse_json(resp)
    except Exception:
        return _fail("verification_error", "Verifier output could not be parsed")
    if not isinstance(raw, dict):
        return _fail("verification_error", "Verifier 输出不是 JSON 对象")

    # 5) 缺 passed 字段 → 未通过
    if "passed" not in raw:
        return _fail("verification_error", "缺少必要字段 passed")

    # 6) passed 必须是【布尔 True】；字符串 "true"/1/None 等一律不放行
    if raw["passed"] is not True:
        if raw["passed"] is False:
            return _fail("not_passed", str(raw.get("reason", "未通过")),
                         missing=_aslist(raw.get("missing")),
                         unsupported_claims=_aslist(raw.get("unsupported_claims")))
        return _fail("verification_error",
                     f"passed 非布尔 True（收到 {raw['passed']!r}），fail-closed")

    # 7) 真正通过
    return VerificationResult(passed=True, status="passed", reason=str(raw.get("reason", "")),
                              missing=_aslist(raw.get("missing"))).model_dump()


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
              stamp=None, approved_tools=None):
    state = AgentState(user_query=user_query, constraints=constraints,
                       max_iterations=max_iterations)
    # 阶段0：资源检索
    state.selected_resources = _resource_retriever.bundle_text(user_query, top_k=12)
    # 阶段0.5：工具权限控制 —— 确定性选工具 → 高风险未批准的物理排除 → 记入 trace
    selected = select_tool_names(user_query)
    for n in selected:
        state.tool_trace.append({"event": "selected", "tool": n, "detail": ""})
    state.allowed_tools = apply_approvals(selected, approved_tools, trace=state.tool_trace)

    from planner import make_plan, render_plan_text, PlanValidationError
    plan_llm = judge_llm if judge_model == "claude" else deepseek_llm_pro

    failure_feedback = ""
    best_output = ""
    while state.retry_count < state.max_iterations:
        # Plan：结构化 + schema 验证；tool_name 必须在 allowed_tools 内；
        # 解析/验证失败 → 立即停止，【绝不】自由文本降级执行
        try:
            rplan = make_plan(plan_llm, state.user_query, state.constraints,
                              state.selected_resources, state.allowed_tools,
                              max_retries_cap=state.max_iterations, feedback=failure_feedback)
        except PlanValidationError as e:
            state.errors.append(f"计划非法：{e}")
            state.tool_trace.append({"event": "plan_rejected", "tool": "", "detail": str(e)[:200]})
            state.final_answer = (
                f"⚠️ 计划非法，已停止（fail-closed，不做自由文本降级执行）：\n{e}")
            _save_run(state, stamp)
            return state
        state.research_plan = rplan.model_dump()
        state.plan = render_plan_text(rplan)
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
        # Verify（fail-closed：工具失败时也判未通过）
        v = verify(state, output, judge_model=judge_model,
                   tool_failed=bool(state.errors))
        state.verification_results.append(v)
        if v.get("passed") is True:          # 必须是布尔 True 才算通过
            state.final_answer = output
            break
        # 不通过 → 修订重试（循环保护：retry_count 递增，到上限就停）
        state.retry_count += 1
        failure_feedback = f"[{v.get('status','')}] {v.get('reason','')}；缺：{v.get('missing','')}"

    # 循环保护触发：超过上限仍未通过，返回当前最佳 + 失败原因，而不是死循环
    # 关键：明确标记【未验证/证据不足】，绝不把未通过伪装成通过
    if not state.final_answer:
        last = state.verification_results[-1] if state.verification_results else {}
        state.final_answer = (
            f"⚠️ 未验证 / 证据不足（{last.get('status','no_verification')}）：{last.get('reason','')}\n\n"
            f"（已达最大迭代 {state.max_iterations} 次仍未通过独立核查，以下为当前最佳结果，"
            f"仅供参考、未经核实，请勿直接作为科研结论）\n\n{best_output}\n\n---\n"
            f"还缺：{last.get('missing', [])}"
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
