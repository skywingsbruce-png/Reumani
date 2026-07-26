"""确定性开放式任务 Step Controller（A.7.4.4）。

只负责**状态转换 / 步骤预算 / 机器可判定 success criteria / scientific progress /
当前步骤完成或不足 / 全步骤终态后进入 synthesis / controlled-insufficient 触发条件**。

**不**调用 LLM、**不**执行工具、**不**解析文献、**不**构建 EvidenceCard、**不**产生模型费用、
**不**做 Verifier 最终裁决、**不**碰 UI。全部决策由确定性规则完成。

复用现有唯一权威（不复制）：
- `pilot.open_task_contracts` 的 OpenTaskRunState / PlanStepState / ObservationRecord /
  NoveltyAssessment / ControlledInsufficientConclusion / CausalEvidenceAxes（Addendum 3 冻结）；
- `pilot.evidence_accumulator` 的 AccumulationResult（证据累加已在上一阶段完成）。

纯函数边界：`evaluate_step(...)` 只读 run_state 返回 StepDecision（不原地修改）；
`apply_decision(...)` 返回**新** OpenTaskRunState。同一 observation_id 幂等；终态不可重开（fail-closed）。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field

from schemas import _Strict
from pilot.open_task_contracts import (
    OpenTaskRunState, PlanStepState, ObservationRecord, StepStatus, ObservationStatus,
    ControlledInsufficientConclusion, CausalEvidenceAxes, CausalStrength,
    TERMINAL_STEP_STATUSES,
)
from pilot.evidence_accumulator import AccumulationResult

StepAction = Literal[
    "continue_step", "complete_satisfied", "complete_insufficient",
    "complete_failed", "complete_blocked", "enter_synthesis",
]

# ------------------------- 预算策略（冻结下一次 B1 的规则） -------------------------
# 明确 policy lookup；未知工具 fail-closed（不得默认无限预算）。
_TOOL_BUDGET: dict[str, int] = {
    "search_literature": 2,     # 文献检索最多 2 次
    "query_data_lake": 1,       # 数据湖查询最多 1 次
}
OUTER_TOOL_ROUND_CAP = 8        # 外层工具总轮次（保持不变，仅作只读常量声明，本模块不修改它）

# content-level 排序（schemas ContentLevel 与 litrec 都覆盖）
_LEVEL_RANK = {"metadata_only": 0, "unknown": 0, "abstract": 1, "local_dataset": 1,
               "fulltext": 2, "full_text": 2, "computational_analysis": 2}

# 因果完备性缺口（satisfied 也保留；缺 temporal/intervention 必须体现）
_CAUSAL_GAP_AXES = [
    ("temporal_evidence", "缺少时序/纵向证据"),
    ("intervention_evidence", "缺少干预证据"),
    ("dose_response", "缺少剂量-反应证据"),
    ("confounding_addressed", "未处理混杂"),
    ("reverse_causation_addressed", "未排查反向因果"),
]


class StepControllerError(RuntimeError):
    """非法输入 / 非法状态转换 / 未知工具预算：fail-closed（不得伪装成 insufficient）。"""


def tool_budget(tool_name: str) -> int:
    """已授权工具的调用预算；未知工具 fail-closed（要求人工配置，不默认无限）。"""
    if tool_name not in _TOOL_BUDGET:
        raise StepControllerError(f"未知工具无预算策略，fail-closed：{tool_name!r}（需人工配置）")
    return _TOOL_BUDGET[tool_name]


# ------------------------- 结构化机器可判定 success criteria -------------------------
class StepCriteria(_Strict):
    """机器可判定成功标准。Planner 自由文本 success_criteria 仅供展示，不得直接控制状态。"""
    tool: str
    min_evidence_cards: int = 0
    minimum_content_level: Optional[str] = None
    required_study_designs: list[str] = Field(default_factory=list)
    required_evidence_axes: list[str] = Field(default_factory=list)
    require_counterevidence_check: bool = False
    allow_zero_hits_as_terminal: bool = False
    max_scientific_no_progress: int = 1
    error_retry_allowed: bool = True


def default_criteria(tool_name: str) -> StepCriteria:
    if tool_name == "search_literature":
        return StepCriteria(tool=tool_name, min_evidence_cards=1, minimum_content_level="abstract",
                            allow_zero_hits_as_terminal=False, max_scientific_no_progress=1)
    if tool_name == "query_data_lake":
        return StepCriteria(tool=tool_name, min_evidence_cards=0,
                            allow_zero_hits_as_terminal=True, max_scientific_no_progress=1)
    raise StepControllerError(f"无默认 success criteria，fail-closed：{tool_name!r}")


# ------------------------- 工具结果与决策 -------------------------
class ToolOutcome(_Strict):
    """一次工具尝试的**结果元数据**（不含 prompt/key/敏感参数）。执行由外层完成，本控制器只读。"""
    observation_id: str
    step_id: int
    tool_name: str
    status: ObservationStatus
    structured: bool = True
    error_type: Optional[str] = None
    result_hash: Optional[str] = None
    telemetry_conflict: bool = False


class StepDecision(_Strict):
    action: StepAction
    step_id: int
    observation_id: str
    previous_status: StepStatus
    next_status: StepStatus
    reason: str
    allow_another_tool_call: bool
    remaining_budget: int
    scientific_progress: bool
    remaining_gaps: list[str] = Field(default_factory=list)
    should_synthesize: bool
    primary_failure: Optional[str] = None
    human_review: bool = False
    attempts_after: int
    counted_attempt: bool                      # 本次是否计入 attempts（幂等重放为 False）

    @property
    def is_terminal(self) -> bool:
        return self.next_status in TERMINAL_STEP_STATUSES


# ------------------------- 内部工具 -------------------------
def _get_step(run_state: OpenTaskRunState, step_id: int) -> PlanStepState:
    for s in run_state.steps:
        if s.step_id == step_id:
            return s
    raise StepControllerError(f"step_id 不存在：{step_id}")


def _level_rank(level) -> int:
    return _LEVEL_RANK.get(level, 0)


def _all_terminal_after(run_state: OpenTaskRunState, step_id: int, next_status: StepStatus) -> bool:
    for s in run_state.steps:
        st = next_status if s.step_id == step_id else s.status
        if st not in TERMINAL_STEP_STATUSES:
            return False
    return True


def _causal_gaps(run_state: OpenTaskRunState) -> list[str]:
    axes = set(run_state.accumulator.evidence_axes)
    return [label for axis, label in _CAUSAL_GAP_AXES if axis not in axes]


def _criteria_met(criteria: StepCriteria, acc_state) -> bool:
    level_min = criteria.minimum_content_level
    wanted_designs = {" ".join(str(d).lower().split()) for d in criteria.required_study_designs}
    count = 0
    for c in acc_state.evidence_cards:
        if level_min and _level_rank(c.provenance.content_level) < _level_rank(level_min):
            continue
        if wanted_designs and " ".join(str(c.study_type).lower().split()) not in wanted_designs:
            continue
        count += 1
    if count < criteria.min_evidence_cards:
        return False
    if not set(criteria.required_evidence_axes) <= set(acc_state.evidence_axes):
        return False
    if criteria.require_counterevidence_check and \
            not any(c.evidence_direction == "refutes" for c in acc_state.evidence_cards):
        return False
    return True


def _decision(run_state, step, outcome, *, action, next_status, reason,
              allow, remaining_budget, sci, gaps, attempts_after, counted,
              human_review=False, primary_failure=None) -> StepDecision:
    should = next_status in TERMINAL_STEP_STATUSES and _all_terminal_after(run_state, step.step_id, next_status)
    # primary_failure 一旦确定不被后续错误覆盖
    pf = run_state.primary_failure or (primary_failure if next_status == "failed" else None)
    return StepDecision(
        action=action, step_id=step.step_id, observation_id=outcome.observation_id,
        previous_status=step.status, next_status=next_status, reason=reason,
        allow_another_tool_call=allow, remaining_budget=remaining_budget,
        scientific_progress=sci, remaining_gaps=gaps, should_synthesize=should,
        primary_failure=pf, human_review=human_review,
        attempts_after=attempts_after, counted_attempt=counted)


def _echo(run_state, step, outcome) -> StepDecision:
    """幂等重放：同一 observation_id 已处理 → 不再计 attempts，回显当前状态。"""
    terminal = step.status in TERMINAL_STEP_STATUSES
    action_map = {"satisfied": "complete_satisfied", "insufficient": "complete_insufficient",
                  "failed": "complete_failed", "blocked": "complete_blocked"}
    action = action_map.get(step.status, "continue_step")
    should = terminal and _all_terminal_after(run_state, step.step_id, step.status)
    return StepDecision(
        action=action, step_id=step.step_id, observation_id=outcome.observation_id,
        previous_status=step.status, next_status=step.status,
        reason="idempotent replay of already-processed observation",
        allow_another_tool_call=False, remaining_budget=max(0, tool_budget(step_tool(step)) - step.attempts)
        if step_tool(step) in _TOOL_BUDGET else 0,
        scientific_progress=False, remaining_gaps=list(step.remaining_gaps), should_synthesize=should,
        primary_failure=run_state.primary_failure, human_review=False,
        attempts_after=step.attempts, counted_attempt=False)


def step_tool(step: PlanStepState) -> Optional[str]:
    return step.allowed_tools[0] if step.allowed_tools else None


# ------------------------- 主决策（纯函数） -------------------------
def evaluate_step(run_state: OpenTaskRunState, step_id: int, tool_outcome: ToolOutcome,
                  accumulation_result: AccumulationResult,
                  criteria: Optional[StepCriteria] = None) -> StepDecision:
    """确定性评估一次工具尝试，返回 StepDecision（不修改 run_state）。"""
    if tool_outcome.step_id != step_id:
        raise StepControllerError("tool_outcome.step_id 与 step_id 不一致")
    step = _get_step(run_state, step_id)

    # 幂等：同一 observation_id 已处理 → 回显，不再计 attempts
    if tool_outcome.observation_id in set(step.observations):
        return _echo(run_state, step, tool_outcome)

    # 终态收到新 Observation → fail-closed 拒绝（不可重开）
    if step.is_terminal():
        raise StepControllerError(
            f"终态步骤 {step_id}({step.status}) 收到新 Observation {tool_outcome.observation_id}，拒绝")

    tool = tool_outcome.tool_name

    # 未授权工具 → blocked（终态）
    if tool not in step.allowed_tools:
        return _decision(run_state, step, tool_outcome, action="complete_blocked",
                         next_status="blocked", reason=f"未授权工具 {tool}（不在 allowed_tools）",
                         allow=False, remaining_budget=0, sci=False,
                         gaps=["工具未授权，需人工配置"], attempts_after=step.attempts,
                         counted=False, human_review=True)

    tool_cap = tool_budget(tool)                   # 未知工具在此 fail-closed
    criteria = criteria or default_criteria(tool)
    step_budget = min(tool_cap, step.call_budget)  # 工具策略与步骤 call_budget 取更紧者，不跨步骤借用
    effective_attempts = min(step.attempts + 1, step.call_budget)  # 预留一次；封顶不超 call_budget
    remaining_budget = max(0, step_budget - effective_attempts)
    sci = accumulation_result.scientific_progress
    no_progress = accumulation_result.scientific_no_progress_rounds
    status = tool_outcome.status

    # telemetry 冲突 → failed + human_review
    if tool_outcome.telemetry_conflict:
        return _decision(run_state, step, tool_outcome, action="complete_failed",
                         next_status="failed", reason="telemetry 冲突（计数不一致）",
                         allow=False, remaining_budget=remaining_budget, sci=sci, gaps=[],
                         attempts_after=effective_attempts, counted=True,
                         human_review=True, primary_failure="telemetry_conflict")

    # 工具/来源/解析错误 → policy：预算内一次受限重试，否则 failed
    if status in ("source_error", "parse_error", "tool_error"):
        if remaining_budget > 0 and criteria.error_retry_allowed:
            return _decision(run_state, step, tool_outcome, action="continue_step",
                             next_status="running", reason=f"{status}：预算内受限重试",
                             allow=True, remaining_budget=remaining_budget, sci=False,
                             gaps=list(step.remaining_gaps), attempts_after=effective_attempts, counted=True)
        return _decision(run_state, step, tool_outcome, action="complete_failed",
                         next_status="failed", reason=f"{status}：无重试预算",
                         allow=False, remaining_budget=remaining_budget, sci=False, gaps=[],
                         attempts_after=effective_attempts, counted=True,
                         human_review=(status != "source_error"), primary_failure=status)

    # zero_hits → 可完成为 insufficient（不得解释为“没有研究”）
    if status == "zero_hits":
        if criteria.allow_zero_hits_as_terminal or remaining_budget <= 0:
            gaps = ["zero_hits：本地/来源未命中 ≠ 该领域无研究"] + _causal_gaps(run_state)
            return _decision(run_state, step, tool_outcome, action="complete_insufficient",
                             next_status="insufficient", reason="zero_hits 终态（非“无研究”）",
                             allow=False, remaining_budget=remaining_budget, sci=False,
                             gaps=gaps, attempts_after=effective_attempts, counted=True)
        return _decision(run_state, step, tool_outcome, action="continue_step",
                         next_status="running", reason="zero_hits：预算内继续尝试",
                         allow=True, remaining_budget=remaining_budget, sci=False,
                         gaps=list(step.remaining_gaps), attempts_after=effective_attempts, counted=True)

    # ok：**先**判定机器可判定 success criteria；满足即立即终止（预算是上限，不是目标调用次数）
    criteria_met = _criteria_met(criteria, run_state.accumulator)
    if criteria_met:
        return _decision(run_state, step, tool_outcome, action="complete_satisfied",
                         next_status="satisfied", reason="成功标准已满足，立即终止（不为耗尽预算继续检索）",
                         allow=False, remaining_budget=remaining_budget, sci=sci,
                         gaps=_causal_gaps(run_state), attempts_after=effective_attempts, counted=True)
    # 未满足：有剩余预算且未达 no-progress 阈值 → 继续；否则 insufficient
    converged = no_progress >= criteria.max_scientific_no_progress
    if remaining_budget > 0 and not converged:
        return _decision(run_state, step, tool_outcome, action="continue_step",
                         next_status="running", reason="成功标准未满足，预算未耗尽且仍有进展空间，继续",
                         allow=True, remaining_budget=remaining_budget, sci=sci,
                         gaps=list(step.remaining_gaps), attempts_after=effective_attempts, counted=True)
    gaps = _causal_gaps(run_state) or ["证据不足以满足成功标准"]
    reason = "scientific no-progress 阈值达到，证据不足" if converged else "工具预算耗尽，证据不足"
    return _decision(run_state, step, tool_outcome, action="complete_insufficient",
                     next_status="insufficient", reason=reason, allow=False,
                     remaining_budget=remaining_budget, sci=sci, gaps=gaps,
                     attempts_after=effective_attempts, counted=True)


# ------------------------- 应用决策（返回新 run_state） -------------------------
def apply_decision(run_state: OpenTaskRunState, decision: StepDecision,
                   observation: Optional[ObservationRecord] = None) -> OpenTaskRunState:
    """把 StepDecision 应用为**新** OpenTaskRunState（不原地修改）。observation_id 幂等。"""
    step = _get_step(run_state, decision.step_id)
    if decision.observation_id in set(step.observations):
        return run_state                                    # 幂等：已处理

    new_steps = []
    for s in run_state.steps:
        if s.step_id != decision.step_id:
            new_steps.append(s)
            continue
        obs = list(s.observations)
        if decision.counted_attempt:
            obs.append(decision.observation_id)
        terminal = decision.next_status in TERMINAL_STEP_STATUSES
        new_steps.append(PlanStepState(
            step_id=s.step_id, objective=s.objective, allowed_tools=s.allowed_tools,
            call_budget=s.call_budget, attempts=decision.attempts_after,
            status=decision.next_status, observations=obs, evidence_ids=s.evidence_ids,
            success_criteria=s.success_criteria,
            completion_reason=(decision.reason if terminal else None),
            remaining_gaps=(decision.remaining_gaps if decision.next_status == "insufficient"
                            else (decision.remaining_gaps if terminal else [])),
        ))

    observations = list(run_state.observations)
    if observation is not None and decision.counted_attempt:
        observations.append(observation)

    # 当前步骤指针：本步终态 → 下一个非终态步骤 / None；否则保持本步
    if decision.next_status in TERMINAL_STEP_STATUSES:
        nxt = next((s.step_id for s in new_steps
                    if s.status not in TERMINAL_STEP_STATUSES), None)
    else:
        nxt = decision.step_id

    run_status = run_state.status
    primary_failure = run_state.primary_failure
    if decision.next_status == "failed" and primary_failure is None:
        run_status = "failed"
        primary_failure = decision.primary_failure or "step_failed"

    return OpenTaskRunState(
        run_id=run_state.run_id, question=run_state.question, route=run_state.route,
        steps=new_steps, current_step_id=nxt, observations=observations,
        accumulator=run_state.accumulator, causal_axes=run_state.causal_axes,
        conclusion=run_state.conclusion, status=run_status,
        primary_failure=primary_failure, human_review=run_state.human_review or decision.human_review,
    )


# ------------------------- 两阶段执行前授权 -------------------------
# 目的：工具执行前先授权并预留 attempt；被拒时工具调用计数必须保持 0、不生成假 ToolOutcome、
# 不增加 EvidenceCard。授权成功后同一 reservation 只能 settle 一次；未 settle 的 reservation 可检测；
# 不允许绕过 authorize 直接 settle。OpenTaskRunState 为冻结契约（不可加字段），故 reservation 台账
# 放在独立 ReservationLedger，并与 run_state 一起装进 ControllerSession。
class Reservation(_Strict):
    reservation_id: str
    request_id: str
    step_id: int
    tool_name: str
    attempt_number: int
    settled: bool = False


class ReservationLedger(_Strict):
    reservations: list[Reservation] = Field(default_factory=list)

    def open(self) -> list:
        return [r for r in self.reservations if not r.settled]

    def open_for_step(self, step_id: int) -> list:
        return [r for r in self.open() if r.step_id == step_id]

    def has_request(self, request_id: str) -> bool:
        return any(r.request_id == request_id for r in self.reservations)

    def find(self, reservation_id: str) -> Optional[Reservation]:
        return next((r for r in self.reservations if r.reservation_id == reservation_id), None)


class ControllerSession(_Strict):
    """把冻结的 OpenTaskRunState 与 reservation 台账绑在一起（spec 里的 run_state / reserved_state 视图）。"""
    run_state: OpenTaskRunState
    ledger: ReservationLedger = Field(default_factory=ReservationLedger)


class AttemptAuthorization(_Strict):
    authorized: bool
    reservation_id: str
    request_id: str
    step_id: int
    tool_name: str
    attempt_number: int
    remaining_budget_after_reservation: int
    denial_reason: Optional[str] = None
    human_review: bool = False


def authorize_attempt(session: ControllerSession, step_id: int, tool_name: str,
                      request_id: str) -> AttemptAuthorization:
    """执行前授权（纯检查，不执行工具、不改状态）。被拒→authorized=False + denial_reason。"""
    step = _get_step(session.run_state, step_id)          # step 不存在 → fail-closed（结构性非法）
    ledger = session.ledger
    resv_id = f"rsv-{step_id}-{request_id}"

    def deny(reason: str, hr: bool = False) -> AttemptAuthorization:
        return AttemptAuthorization(authorized=False, reservation_id=resv_id, request_id=request_id,
                                    step_id=step_id, tool_name=tool_name, attempt_number=0,
                                    remaining_budget_after_reservation=0, denial_reason=reason,
                                    human_review=hr)

    if ledger.has_request(request_id):
        return deny("duplicate_request_id")               # request_id 已处理
    if step.is_terminal():
        return deny("step_terminal")                      # 终态不再授权
    if tool_name not in step.allowed_tools:
        return deny("unauthorized_tool", hr=True)         # 不在 allowed_tools
    if tool_name not in _TOOL_BUDGET:
        return deny("no_tool_policy", hr=True)            # 无明确 policy → fail-closed（不默认无限）
    budget = min(_TOOL_BUDGET[tool_name], step.call_budget)
    used = step.attempts + len(ledger.open_for_step(step_id))   # 已结算 + 未结算预留（不跨步骤借用）
    if used >= budget:
        return deny("budget_exhausted")
    attempt_number = used + 1
    return AttemptAuthorization(authorized=True, reservation_id=resv_id, request_id=request_id,
                               step_id=step_id, tool_name=tool_name, attempt_number=attempt_number,
                               remaining_budget_after_reservation=max(0, budget - attempt_number))


def reserve(session: ControllerSession, authorization: AttemptAuthorization) -> ControllerSession:
    """把授权预留为 open reservation（工具执行前）。拒绝的授权不可预留；同一 reservation 不可重复预留。"""
    if not authorization.authorized:
        raise StepControllerError("不能预留被拒绝的授权")
    if session.ledger.find(authorization.reservation_id) is not None:
        raise StepControllerError(f"reservation 已存在：{authorization.reservation_id}")
    resv = Reservation(reservation_id=authorization.reservation_id, request_id=authorization.request_id,
                       step_id=authorization.step_id, tool_name=authorization.tool_name,
                       attempt_number=authorization.attempt_number, settled=False)
    return ControllerSession(run_state=session.run_state,
                             ledger=ReservationLedger(reservations=[*session.ledger.reservations, resv]))


def settle_attempt(reserved_session: ControllerSession, authorization: AttemptAuthorization,
                   tool_outcome: ToolOutcome, accumulation_result: AccumulationResult,
                   criteria: Optional[StepCriteria] = None) -> StepDecision:
    """结算一次已预留的 attempt（任何 outcome 都消耗它，含工具抛异常后合成的 error outcome）。
    无匹配 open reservation → 拒绝（不能绕过 authorize 直接 settle）。"""
    r = reserved_session.ledger.find(authorization.reservation_id)
    if r is None or r.settled:
        raise StepControllerError("无对应 open reservation（不得绕过 authorize 直接 settle，或已结算）")
    if tool_outcome.step_id != authorization.step_id or tool_outcome.tool_name != authorization.tool_name:
        raise StepControllerError("tool_outcome 与授权的 step/tool 不一致")
    return evaluate_step(reserved_session.run_state, authorization.step_id,
                         tool_outcome, accumulation_result, criteria)


def apply_settlement(reserved_session: ControllerSession, decision: StepDecision,
                     authorization: AttemptAuthorization,
                     observation: Optional[ObservationRecord] = None) -> ControllerSession:
    """把 StepDecision 应用为新 run_state，并把 reservation 标记 settled（同一 reservation 只结算一次）。"""
    new_run = apply_decision(reserved_session.run_state, decision, observation)
    ledger = ReservationLedger(reservations=[
        (r.model_copy(update={"settled": True}) if r.reservation_id == authorization.reservation_id else r)
        for r in reserved_session.ledger.reservations])
    return ControllerSession(run_state=new_run, ledger=ledger)


def open_reservations(session: ControllerSession) -> list:
    """未结算的 reservation（对账用；正常收尾后应为空）。"""
    return session.ledger.open()


# ------------------------- 受控综合触发 -------------------------
class SynthesisRequest(_Strict):
    question: str
    terminal_steps: list[int] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_summary: list[dict] = Field(default_factory=list)
    causal_axes: CausalEvidenceAxes = Field(default_factory=CausalEvidenceAxes)
    unsupported_claims: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    human_review: bool = False


def _causal_strength_from_axes(axes: set) -> CausalStrength:
    if "intervention_evidence" in axes:
        return "intervention_supported"
    if "temporal_evidence" in axes:
        return "temporal_association"
    if "association" in axes:
        return "association"
    return "insufficient"


def _axes_model(axes: set) -> CausalEvidenceAxes:
    # 已确认 → True；无结构化来源确认 → None（unknown），绝不写 False 冒充“已排除”
    return CausalEvidenceAxes(
        association=True if "association" in axes else None,
        temporal_evidence=True if "temporal_evidence" in axes else None,
        intervention_evidence=True if "intervention_evidence" in axes else None,
    )


def build_synthesis_request(run_state: OpenTaskRunState) -> SynthesisRequest:
    """全步骤终态后生成 SynthesisRequest；存在 running/pending 步骤 → fail-closed。"""
    if not run_state.steps or not all(s.is_terminal() for s in run_state.steps):
        raise StepControllerError("存在未终态步骤，不得进入 synthesis")
    acc = run_state.accumulator
    axes = set(acc.evidence_axes)
    summary = [{"evidence_id": c.evidence_id, "pmid": c.pmid, "doi": c.doi,
                "content_level": c.provenance.content_level, "study_type": c.study_type}
               for c in acc.evidence_cards]
    missing = _causal_gaps(run_state)
    if not acc.evidence_cards:
        missing = ["未检索到任何证据；无法评估（≠ 该领域无研究）"] + missing
    if not missing:
        missing = ["无进一步已识别缺口"]                    # 保证非空，杜绝“还缺：[]”
    unsupported = []
    if _causal_strength_from_axes(axes) not in ("causal",):
        unsupported = ["确定性因果表述（现有证据不支持升级为 causal）"]
    return SynthesisRequest(
        question=run_state.question,
        terminal_steps=[s.step_id for s in run_state.steps],
        evidence_ids=list(acc.evidence_ids), evidence_summary=summary,
        causal_axes=_axes_model(axes), unsupported_claims=unsupported,
        missing_evidence=missing,
        limitations=["证据层级与因果强度以机器可判定标准确定；未调用真实模型"],
        human_review=run_state.human_review,
    )


def build_controlled_insufficient(req: SynthesisRequest) -> ControlledInsufficientConclusion:
    """确定性地把 SynthesisRequest 映射为 ControlledInsufficientConclusion（不调用真实模型）。
    causal_strength 由已确认 axes 派生，绝不无据升为 causal；missing_evidence 保证非空。"""
    axes = set(a for a, v in req.causal_axes.model_dump().items() if v is True)
    strength = _causal_strength_from_axes(axes)
    return ControlledInsufficientConclusion(
        resolved_question=req.question,
        available_evidence=req.evidence_summary,
        unsupported_claims=req.unsupported_claims,
        causal_strength=strength,
        missing_evidence=req.missing_evidence,
        limitations=req.limitations,
        recommended_next_action="检索纵向/干预研究或做孟德尔随机化以补足因果证据",
    )


__all__ = [
    "StepAction", "StepControllerError", "tool_budget", "OUTER_TOOL_ROUND_CAP",
    "StepCriteria", "default_criteria", "ToolOutcome", "StepDecision",
    "evaluate_step", "apply_decision", "step_tool",
    "Reservation", "ReservationLedger", "ControllerSession", "AttemptAuthorization",
    "authorize_attempt", "reserve", "settle_attempt", "apply_settlement", "open_reservations",
    "SynthesisRequest", "build_synthesis_request", "build_controlled_insufficient",
]
