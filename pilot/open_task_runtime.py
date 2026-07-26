"""有界开放任务运行时编排器（A.7.4.5）。

打通：Plan → authorize → 注入工具 → ToolResult/LiteratureRecord → EvidenceAccumulator →
settle → PlanStepState 转换 → synthesis → fake verifier/claim/graph/shadow → 版本化运行时事件。

**全部依赖注入**（planner / tool_executor / synthesizer / verifier / claim_extractor /
claim_graph / shadow / event_sink / clock / should_stop）；测试与 demo 用 fake/in-memory 实现。
导入时零副作用：不建客户端 / 网络 / 线程 / 数据库。复用现有 step_controller 两阶段授权与
EvidenceAccumulator；任何异常都结算 reservation，最终 open reservation=0。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from tool_envelope import now
from schemas import Provenance
from pilot.open_task_contracts import (OpenTaskRunState, PlanStepState, ObservationRecord)
from pilot.evidence_accumulator import accumulate
from pilot import step_controller as sc
from pilot.runtime_events import make_event

_TERMINAL_EVENT = {"satisfied": "step_satisfied", "insufficient": "step_insufficient",
                   "failed": "step_failed", "blocked": "step_blocked"}


@dataclass
class ToolExecution:
    """注入工具的一次执行结果（脱敏）。accum_input 交给 EvidenceAccumulator。"""
    status: str                                  # ObservationStatus
    accum_input: object                          # LiteratureRecord / list / ToolResult / data dict
    result_hash: Optional[str] = None
    structured: bool = True
    error_type: Optional[str] = None


@dataclass
class StepSpec:
    step_id: int
    objective: str
    tool_name: str
    call_budget: int
    success_criteria: str = ""
    criteria: object = None                       # StepCriteria | None（None → 默认策略）


@dataclass
class RuntimeDeps:
    planner: Callable[[str], list]                # question -> list[StepSpec]
    tool_executor: Callable[[int, str, str], ToolExecution]   # (step_id, tool, request_id) -> ToolExecution
    synthesizer: Callable[[object], object]       # SynthesisRequest -> ControlledInsufficientConclusion
    verifier: Callable[[object, list], dict]
    claim_extractor: Callable[[object, list], list]
    claim_graph: Callable[[list, list], list]
    shadow: Callable[[list], dict]
    artifact_producer: Callable[[OpenTaskRunState, object, list, list], list]
    event_sink: Callable[[object], None]
    clock: Callable[[], str] = now
    should_stop: Callable[[], bool] = field(default=lambda: False)


class OpenTaskRuntime:
    def __init__(self, deps: RuntimeDeps, run_id: str, question: str):
        self.deps = deps
        self.run_id = run_id
        self.question = question
        self._seq = 0

    # ---- 事件发射 ----
    def _emit(self, event_type, *, step_id=None, status=None, summary="",
              evidence_ids=None, artifact_ids=None, safe_payload=None):
        ev = make_event(run_id=self.run_id, sequence=self._seq, event_type=event_type,
                        event_id=f"{self.run_id}-{self._seq:04d}", step_id=step_id, status=status,
                        summary=summary, evidence_ids=evidence_ids, artifact_ids=artifact_ids,
                        safe_payload=safe_payload, clock=self.deps.clock)
        self._seq += 1
        self.deps.event_sink(ev)
        return ev

    def run(self) -> dict:
        d = self.deps
        specs = d.planner(self.question)
        steps = [PlanStepState(step_id=s.step_id, objective=s.objective,
                               allowed_tools=[s.tool_name], call_budget=s.call_budget,
                               success_criteria=s.success_criteria or s.objective) for s in specs]
        criteria_by_step = {s.step_id: s.criteria for s in specs}
        session = sc.ControllerSession(run_state=OpenTaskRunState(
            run_id=self.run_id, question=self.question, route="open", steps=steps,
            current_step_id=steps[0].step_id if steps else None))

        self._emit("run_created", summary="run created", safe_payload={"note": "offline demo runtime"})
        self._emit("plan_ready", summary=f"{len(steps)} 步有界计划",
                   safe_payload={"step_count": len(steps)})

        stopped = False
        try:
            for spec in specs:
                if d.should_stop():
                    stopped = True
                    break
                session = self._run_step(session, spec, criteria_by_step[spec.step_id])
                # 若停止请求在步骤内被处理（步骤未终态），退出外层
                cur = _get(session.run_state, spec.step_id)
                if not cur.is_terminal():
                    stopped = True
                    break

            if stopped:
                session = self._settle_open(session)
                self._emit("run_stopped", status="stopped", summary="cooperative stop",
                           safe_payload={"note": "no further tool authorization"})
                return self._result(session, None, stopped=True)

            # 所有步骤终态 → synthesis
            conclusion, artifacts = self._synthesize(session)
            failed = session.run_state.primary_failure is not None
            session = self._finalize(session, conclusion, failed)
            if failed:
                self._emit("run_failed", status="failed", summary="run failed",
                           safe_payload={"reason": session.run_state.primary_failure})
            else:
                self._emit("run_completed", status="finished", summary="run completed",
                           artifact_ids=[a["artifact_id"] for a in artifacts])
            assert not sc.open_reservations(session), "open reservations must be 0"
            return self._result(session, conclusion, stopped=False)
        except Exception as exc:                        # noqa: BLE001 — 任何异常都结算 reservation
            session = self._settle_open(session)
            self._emit("run_failed", status="failed", summary="runtime exception",
                       safe_payload={"reason": type(exc).__name__})
            assert not sc.open_reservations(session)
            return self._result(session, None, stopped=False, failed=True)

    # ---- 单步严格时序 ----
    def _run_step(self, session, spec, criteria):
        d = self.deps
        step_id, tool = spec.step_id, spec.tool_name
        self._emit("step_started", step_id=step_id, status="running", summary=spec.objective,
                   safe_payload={"step_objective": spec.objective, "tool_name": tool})
        attempt = 0
        while True:
            if d.should_stop():
                return session                          # 停止：不再授权新尝试
            attempt += 1
            request_id = f"{self.run_id}-s{step_id}-a{attempt}"
            auth = sc.authorize_attempt(session, step_id, tool, request_id)
            if not auth.authorized:
                self._emit("attempt_denied", step_id=step_id, status="denied",
                           summary=f"denied: {auth.denial_reason}",
                           safe_payload={"denial_reason": auth.denial_reason,
                                         "attempt_number": attempt})
                return session                          # 被拒 → 工具不执行（call_count 保持 0），退出步骤
            self._emit("attempt_authorized", step_id=step_id, status="authorized",
                       summary=f"attempt {auth.attempt_number} authorized",
                       safe_payload={"attempt_number": auth.attempt_number,
                                     "remaining_budget": auth.remaining_budget_after_reservation,
                                     "reservation_id": auth.reservation_id, "tool_name": tool})
            session = sc.reserve(session, auth)
            self._emit("tool_started", step_id=step_id, status="running",
                       summary=f"tool {tool} started", safe_payload={"tool_name": tool})
            try:
                te = d.tool_executor(step_id, tool, request_id)
            except Exception as exc:                    # provider 抛异常 → 合成 tool_error，仍 settle
                te = ToolExecution(status="tool_error", accum_input={"retrieval_status": "tool_error",
                                   "records": []}, error_type=type(exc).__name__)
            self._emit("tool_returned", step_id=step_id, status=te.status,
                       summary=f"tool {tool} returned {te.status}",
                       safe_payload={"tool_name": tool, "structured": te.structured,
                                     "result_hash": te.result_hash or "", "retrieval_status": te.status})
            ar = accumulate(session.run_state.accumulator, te.accum_input)
            session = sc.ControllerSession(
                run_state=session.run_state.model_copy(update={"accumulator": ar.state}),
                ledger=session.ledger)
            obs_id = f"{request_id}-obs"
            self._emit("observation_recorded", step_id=step_id, status=te.status,
                       summary=f"observation {te.status}",
                       safe_payload={"retrieval_status": te.status})
            self._emit("evidence_accumulated", step_id=step_id, status="ok",
                       summary=f"{len(ar.state.evidence_cards)} evidence cards",
                       evidence_ids=list(ar.state.evidence_ids),
                       safe_payload={"evidence_count": len(ar.state.evidence_cards),
                                     "identifier_count": len(ar.state.identifier_index),
                                     "new_evidence_axes": ar.novelty.new_evidence_axes})
            outcome = sc.ToolOutcome(observation_id=obs_id, step_id=step_id, tool_name=tool,
                                     status=te.status, structured=te.structured,
                                     error_type=te.error_type, result_hash=te.result_hash)
            decision = sc.settle_attempt(session, auth, outcome, ar, criteria)
            obs_record = ObservationRecord(
                observation_id=obs_id, step_id=step_id, tool_name=tool, tool_call_id_hash="h",
                status=te.status, structured=te.structured, error_type=te.error_type,
                evidence_ids=ar.added_evidence_ids, provenance=Provenance(tool_name=tool))
            session = sc.apply_settlement(session, decision, auth, obs_record)
            if decision.is_terminal:
                self._emit(_TERMINAL_EVENT[decision.next_status], step_id=step_id,
                           status=decision.next_status, summary=decision.reason,
                           safe_payload={"remaining_gaps": decision.remaining_gaps,
                                         "attempt_number": decision.attempts_after})
                return session
            # continue：进入下一 attempt

    # ---- synthesis + 下游 fake 阶段 ----
    def _synthesize(self, session):
        d = self.deps
        self._emit("synthesis_started", status="running", summary="synthesis started")
        req = sc.build_synthesis_request(session.run_state)
        conclusion = d.synthesizer(req)
        cards = session.run_state.accumulator.evidence_cards
        self._emit("synthesis_completed", status="finished", summary="controlled synthesis",
                   evidence_ids=list(session.run_state.accumulator.evidence_ids),
                   safe_payload={"causal_strength": conclusion.causal_strength,
                                 "missing_evidence": conclusion.missing_evidence})
        verdict = d.verifier(conclusion, cards)
        self._emit("verification_completed", status=verdict.get("status"),
                   summary="verification", safe_payload={"verdict_status": verdict.get("status")})
        claims = d.claim_extractor(conclusion, list(session.run_state.accumulator.evidence_ids))
        self._emit("claims_extracted", summary=f"{len(claims)} claims",
                   safe_payload={"claim_count": len(claims)})
        graph = d.claim_graph(claims, cards)
        self._emit("claim_graph_completed", summary="claim graph",
                   safe_payload={"graph_verdicts": [g.get("verdict") for g in graph]})
        shadow_res = d.shadow(cards)
        self._emit("shadow_completed", summary="shadow",
                   safe_payload={"shadow_created_new_cards": bool(shadow_res.get("created_new_cards"))})
        artifacts = d.artifact_producer(session.run_state, conclusion, claims, graph)
        for a in artifacts:
            self._emit("artifact_created", summary=a["name"], artifact_ids=[a["artifact_id"]],
                       safe_payload={"artifact_name": a["name"], "artifact_kind": a["kind"],
                                     "size_bytes": a.get("size_bytes", 0),
                                     "hash_short": a.get("hash_short", ""),
                                     "provenance_status": a.get("provenance_status", "pending"),
                                     "verifier_status": a.get("verifier_status", "not_run")})
        return conclusion, artifacts

    def _finalize(self, session, conclusion, failed):
        rs = session.run_state
        status = "failed" if failed else "finished"
        new_rs = rs.model_copy(update={"conclusion": conclusion, "status": status})
        return sc.ControllerSession(run_state=new_rs, ledger=session.ledger)

    def _settle_open(self, session):
        # 强制结算所有 open reservation（异常/停止路径）→ open=0
        resvs = [r.model_copy(update={"settled": True}) for r in session.ledger.reservations]
        return sc.ControllerSession(run_state=session.run_state,
                                    ledger=sc.ReservationLedger(reservations=resvs))

    def _result(self, session, conclusion, *, stopped=False, failed=False):
        return {"session": session, "conclusion": conclusion, "stopped": stopped,
                "failed": failed, "event_count": self._seq,
                "open_reservations": len(sc.open_reservations(session))}


def _get(run_state, step_id):
    for s in run_state.steps:
        if s.step_id == step_id:
            return s
    raise KeyError(step_id)


__all__ = ["OpenTaskRuntime", "RuntimeDeps", "ToolExecution", "StepSpec"]
