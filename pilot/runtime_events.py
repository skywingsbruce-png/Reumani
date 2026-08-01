"""版本化的运行时机器事件契约（reumani-event-v1，A.7.4.5 / UI-1）。

严格 Pydantic 契约：sequence 单调、event_id 唯一、content_hash=SHA-256、safe_payload 白名单。
**不**保存完整 Prompt / API key / 认证头 / Cookie / .env / 原始患者数据 / 模型正文。
本模块为纯数据契约：无网络 / LLM / 账本 / 线程 / 客户端（导入时零副作用）。
TS 端通过导出的 JSON Schema 做一致性校验，避免两套事件枚举漂移。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field, field_validator, model_validator

from schemas import _Strict
from tool_envelope import compute_hash, now

EVENT_SCHEMA = "reumani-event-v1"

EventType = Literal[
    "run_created", "plan_ready", "step_started", "attempt_authorized", "attempt_denied",
    "tool_started", "tool_returned", "observation_recorded", "evidence_accumulated",
    "step_satisfied", "step_insufficient", "step_failed", "step_blocked",
    "synthesis_started", "synthesis_completed",
    "verification_started", "verification_completed",
    "claim_extraction_started", "claims_extracted",
    "shadow_started", "shadow_completed",
    "claim_graph_completed", "artifact_created",
    # A.7.5 human-in-the-loop control
    "clarification_requested", "clarification_answered",
    "approval_requested", "approval_granted", "approval_denied",
    "pause_requested", "run_paused", "run_resumed",
    # A.7.5.3 parameterized research-run stages
    "research_stage_started", "research_stage_completed",
    # A.7.5.3.1 fail-closed stage errors
    "research_stage_failed",
    "run_completed", "run_failed", "run_stopped",
]
EVENT_TYPES: frozenset = frozenset(EventType.__args__)  # type: ignore[attr-defined]

# safe_payload 允许键白名单（只放脱敏的执行元数据，绝不放敏感字段）
SAFE_PAYLOAD_KEYS: frozenset = frozenset({
    "query", "retrieval_status", "record_count", "tool_name", "structured",
    "result_hash", "evidence_count", "duration_ms", "attempt_number",
    "remaining_budget", "reservation_id", "denial_reason", "causal_strength",
    "missing_evidence", "remaining_gaps", "verdict_status", "claim_count",
    "graph_verdicts", "artifact_kind", "artifact_name", "size_bytes", "hash_short",
    "provenance_status", "verifier_status", "step_objective", "content_level",
    "reason", "phase", "elapsed_ms", "new_evidence_axes", "identifier_count",
    "shadow_created_new_cards", "step_count", "terminal_steps", "note",
    # A.7.5 human-in-the-loop control (desensitized only)
    "request_id", "state_version", "question_hash", "action_hash", "arguments_hash",
    "allowed_options", "risk_level", "action_summary", "expected_side_effect",
    "control_state", "is_simulation", "kind", "answer", "allow_other",
    # A.7.5.1 durability / concurrency (stable hashes + recovery cursors only)
    "idempotency_hash", "resume_cursor",
    # A.7.5.1.1 idempotency payload binding (stable request fingerprint hash only)
    "request_fingerprint",
    # A.7.5.3 research-run bridge (desensitized metadata + stable hashes only)
    "run_type", "stage", "stage_index", "stage_count", "executor_id", "policy_hash",
    "evidence_count", "answer_hash", "causal_tier", "verifier_verdict", "shadow_verdict",
    "claim_count", "fixture",
    # A.7.5.3.1 async worker + fail-closed stage errors (desensitized diagnostics only;
    # never traceback / prompt / key / path / model body)
    "worker_generation", "failed_stage", "error_type", "error_summary",
    "completed_stage_count", "human_review",
})
# 明令禁止出现的敏感键子串（即便被误放进白名单外，也在校验层再兜底）
_FORBIDDEN_SUBSTRINGS = ("prompt", "api_key", "apikey", "authorization", "auth_header",
                         "cookie", "secret", "token", "password", "env", "patient",
                         "raw_response", "model_body", "private_key", "credential")


class RuntimeEvent(_Strict):
    schema_version: Literal["reumani-event-v1"] = EVENT_SCHEMA
    event_id: str
    run_id: str
    sequence: int
    timestamp: str
    event_type: EventType
    step_id: Optional[int] = None
    status: Optional[str] = None
    summary: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    safe_payload: dict = Field(default_factory=dict)
    content_hash: str

    @field_validator("event_id", "run_id")
    @classmethod
    def _nonempty(cls, v):
        if not v or not str(v).strip():
            raise ValueError("event_id / run_id 不能为空")
        return v

    @field_validator("sequence")
    @classmethod
    def _seq(cls, v):
        if v < 0:
            raise ValueError("sequence 必须 ≥ 0")
        return v

    @field_validator("safe_payload")
    @classmethod
    def _safe(cls, v):
        for key in (v or {}):
            k = str(key)
            if k not in SAFE_PAYLOAD_KEYS:
                raise ValueError(f"safe_payload 键不在白名单：{k!r}")
            low = k.lower()
            if any(bad in low for bad in _FORBIDDEN_SUBSTRINGS):
                raise ValueError(f"safe_payload 含敏感键：{k!r}")
        return v

    @model_validator(mode="after")
    def _hash(self):
        expected = event_content_hash(self)
        if self.content_hash != expected:
            raise ValueError("content_hash 与内容不一致（SHA-256）")
        return self


def _canonical(schema_version, run_id, sequence, event_type, step_id, status, summary,
               evidence_ids, artifact_ids, safe_payload) -> dict:
    # content_hash 的规范化 payload：不含 event_id/timestamp（易变、非科学内容）
    return {"schema_version": schema_version, "run_id": run_id, "sequence": sequence,
            "event_type": event_type, "step_id": step_id, "status": status,
            "summary": summary, "evidence_ids": sorted(evidence_ids),
            "artifact_ids": sorted(artifact_ids), "safe_payload": safe_payload}


def event_content_hash(ev: "RuntimeEvent") -> str:
    return compute_hash(_canonical(ev.schema_version, ev.run_id, ev.sequence, ev.event_type,
                                   ev.step_id, ev.status, ev.summary, ev.evidence_ids,
                                   ev.artifact_ids, ev.safe_payload))


def make_event(*, run_id: str, sequence: int, event_type: str, event_id: str,
               step_id: Optional[int] = None, status: Optional[str] = None, summary: str = "",
               evidence_ids: Optional[list] = None, artifact_ids: Optional[list] = None,
               safe_payload: Optional[dict] = None, clock=now) -> RuntimeEvent:
    """构造一个已校验的 RuntimeEvent（集中计算 content_hash + safe_payload 白名单校验）。"""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"未知 event_type：{event_type!r}")
    evidence_ids = list(evidence_ids or [])
    artifact_ids = list(artifact_ids or [])
    safe_payload = dict(safe_payload or {})
    ch = compute_hash(_canonical(EVENT_SCHEMA, run_id, sequence, event_type, step_id, status,
                                 summary, evidence_ids, artifact_ids, safe_payload))
    return RuntimeEvent(event_id=event_id, run_id=run_id, sequence=sequence, timestamp=clock(),
                        event_type=event_type, step_id=step_id, status=status, summary=summary,
                        evidence_ids=evidence_ids, artifact_ids=artifact_ids,
                        safe_payload=safe_payload, content_hash=ch)


def event_json_schema() -> dict:
    """导出 JSON Schema（供 TS 端一致性校验，防止两套枚举漂移）。"""
    return RuntimeEvent.model_json_schema()


__all__ = ["EVENT_SCHEMA", "EventType", "EVENT_TYPES", "SAFE_PAYLOAD_KEYS",
           "RuntimeEvent", "make_event", "event_content_hash", "event_json_schema"]
