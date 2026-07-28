"""A.7.5 —— 人机协作控制运行时（Clarification / Approval / Pause / Resume）。

Deterministic fake：零付费模型、零外部网络、零湿实验设备、零任意代码。用一个确定性 SSc 演示
（IL-6 成纤维细胞实验，故意缺组织来源 → 澄清；模拟生成湿实验自动化执行包 → 审批）验证控制能力。

铁律：
- 等待澄清/等待审批/暂停时**不授权任何工具、不调用任何模型**（tool_calls 保持 0）；
- 审批只对完全一致的 action_hash 有效；参数/工具变化 → 旧批准失效；
- Pause 可恢复；Stop 终态不可恢复；非法状态转换 fail-closed；
- 写入幂等（idempotency_key 重复 → 原结果，不重复事件/执行/扣费）；expected_state_version 过期 → 409；
- append-only 事件可重建状态；事件不含 Prompt/患者数据/key/认证头/模型正文。
"""

from __future__ import annotations

from typing import Optional

from tool_envelope import compute_hash, now
from pilot.runtime_events import make_event
from pilot import hitl_contracts as HC

DEMO_QUESTION = "设计一项 SSc 成纤维细胞 IL-6 实验（缺少组织来源，需澄清）"
_TISSUE_OPTIONS = [("skin", "皮肤成纤维细胞"), ("lung", "肺成纤维细胞"), ("both", "两者")]


class HitlRun:
    """单个人机协作运行的权威状态机（内存态 + append-only 事件）。"""

    def __init__(self, run_id: str, event_sink, *, clock=now):
        self.run_id = run_id
        self._sink = event_sink
        self._clock = clock
        self.state: str = "running"
        self.state_version: int = 0
        self._seq = 0
        self.pending: Optional[dict] = None          # 当前待处理的 clarification/approval（脱敏 dict）
        self._pending_obj = None                     # ClarificationRequest | ApprovalRequest
        self._resume_target: Optional[str] = None
        self._answer = None                          # 已选组织来源
        self._idem: dict[str, dict] = {}             # idempotency_key -> 结果快照
        self.tool_calls = 0
        self.artifacts: list = []
        self.lifecycle = {"requested": 0, "executed": 0, "tool_returned": 0, "observed": 0}

    # ---------- 事件 ----------
    def _emit(self, event_type, *, status=None, summary="", safe_payload=None, artifact_ids=None,
              evidence_ids=None, step_id=None):
        sp = dict(safe_payload or {})
        sp.setdefault("control_state", self.state)
        sp.setdefault("state_version", self.state_version)
        ev = make_event(run_id=self.run_id, sequence=self._seq, event_type=event_type,
                        event_id=f"{self.run_id}-{self._seq:04d}", status=status, summary=summary,
                        step_id=step_id, safe_payload=sp, artifact_ids=artifact_ids or [],
                        evidence_ids=evidence_ids or [], clock=self._clock)
        self._seq += 1
        self._sink(ev)
        return ev

    def _to(self, dst):
        HC.assert_transition(self.state, dst)        # fail-closed
        self.state = dst
        self.state_version += 1

    # ---------- 启动：跑到第一个澄清点 ----------
    def start(self):
        self._emit("run_created", summary="hitl run created", safe_payload={"note": "deterministic fake HITL"})
        self._emit("plan_ready", summary="计划已就绪（2 步）", safe_payload={"step_count": 2})
        self._emit("step_started", step_id=1, status="running", summary="设计实验（缺组织来源）",
                   safe_payload={"step_objective": "设计 SSc 成纤维细胞 IL-6 实验"})
        # 缺少会实质改变研究设计的信息（组织来源）→ 请求澄清
        qh = HC.question_hash("组织来源？", [o[0] for o in _TISSUE_OPTIONS] + ["other"],
                              "single_or_other", True)
        rid = f"clr-{self.run_id}"
        self._to("awaiting_clarification")
        req = HC.ClarificationRequest(
            request_id=rid, run_id=self.run_id, state_version=self.state_version,
            created_at=self._clock(), reason="实验缺少成纤维细胞组织来源，会实质改变研究设计",
            requesting_step_id=1, kind="single_or_other", question_hash=qh,
            allowed_options=[HC.ClarificationOption(id=o[0], label=o[1]) for o in _TISSUE_OPTIONS],
            allow_other=True)
        self._pending_obj = req
        self.pending = _clar_public(req)
        self._emit("clarification_requested", step_id=1, status="awaiting_clarification",
                   summary="需要澄清：组织来源",
                   safe_payload={"request_id": rid, "question_hash": qh, "kind": "single_or_other",
                                 "allow_other": True, "reason": req.reason,
                                 "allowed_options": [{"id": o[0], "label": o[1]} for o in _TISSUE_OPTIONS]})
        return self

    # ---------- 澄清作答 ----------
    def answer_clarification(self, ans: HC.ClarificationAnswer) -> dict:
        cached = self._check_idem(ans.idempotency_key)
        if cached is not None:
            return cached
        req = self._require_pending("clarification", ans.request_id, ans.expected_state_version)
        # 校验选项
        valid_ids = req.option_ids()
        chosen = [i for i in ans.selected_option_ids if i in valid_ids]
        if ans.other_text and req.allow_other:
            chosen = chosen or []
            answer_label = f"其他：{ans.other_text}"
            self._answer = ans.other_text
        elif chosen:
            answer_label = ",".join(chosen)
            self._answer = chosen[0]
        else:
            raise HC.ContractViolation("澄清答案无有效选项且无 other_text")
        req.status = "answered"
        self._to("running")
        self._emit("clarification_answered", step_id=1, status="running", summary="澄清已回答",
                   safe_payload={"request_id": req.request_id, "answer": answer_label[:120]})
        self._emit("step_satisfied", step_id=1, status="satisfied",
                   summary=f"实验设计已确定组织来源：{answer_label[:40]}", safe_payload={"answer": answer_label[:120]})
        self.pending = None
        self._pending_obj = None
        self._request_approval()                     # 推进到审批点
        return self._snapshot_and_cache(ans.idempotency_key)

    # ---------- 请求审批（具体动作） ----------
    def _request_approval(self):
        self._emit("step_started", step_id=2, status="running", summary="模拟生成湿实验自动化执行包",
                   safe_payload={"step_objective": "simulate wet-lab automation package (fake)"})
        args = {"tissue": self._answer, "assay": "IL-6", "mode": "simulation"}
        ah = HC.action_hash("simulate_wetlab_package", args, "high")
        rid = f"apr-{self.run_id}"
        self._to("awaiting_approval")
        req = HC.ApprovalRequest(
            request_id=rid, run_id=self.run_id, state_version=self.state_version,
            created_at=self._clock(), reason="生成会驱动湿实验的执行包（仿真），需人工批准具体动作",
            requesting_step_id=2, tool_name="simulate_wetlab_package",
            arguments_hash=HC.normalized_arguments_hash(args), risk_level="high",
            action_summary=f"模拟生成 {self._answer} 成纤维细胞 IL-6 湿实验自动化执行包",
            expected_side_effect="仅生成结构化 fake 产物；不连接真实设备、不写项目外文件、"
                                 "不执行 Shell、不操作患者数据",
            action_hash=ah, is_simulation=True)
        self._pending_obj = req
        self.pending = _apr_public(req)
        self._emit("approval_requested", step_id=2, status="awaiting_approval",
                   summary="需要审批：模拟湿实验执行包（仿真）",
                   safe_payload={"request_id": rid, "action_hash": ah, "tool_name": "simulate_wetlab_package",
                                 "arguments_hash": req.arguments_hash, "risk_level": "high",
                                 "action_summary": req.action_summary,
                                 "expected_side_effect": req.expected_side_effect, "is_simulation": True,
                                 "reason": req.reason})

    # ---------- 审批：批准 ----------
    def approve(self, dec: HC.ApprovalDecision) -> dict:
        cached = self._check_idem(dec.idempotency_key)
        if cached is not None:
            return cached
        req = self._require_pending("approval", dec.request_id, dec.expected_state_version)
        if dec.action_hash != req.action_hash:       # 仅完全一致才有效
            raise HC.ContractViolation("action_hash 不匹配（动作/参数已变化，旧批准失效，请重新申请）")
        req.status = "granted"
        self._to("running")
        self._emit("approval_granted", step_id=2, status="running", summary="动作已批准",
                   safe_payload={"request_id": req.request_id, "action_hash": req.action_hash})
        self._execute_fake_action(req)               # fake 工具只执行一次
        self.pending = None
        self._pending_obj = None
        self._to("completed")
        self._emit("run_completed", status="completed", summary="run completed",
                   artifact_ids=[a["artifact_id"] for a in self.artifacts])
        return self._snapshot_and_cache(dec.idempotency_key)

    def _execute_fake_action(self, req: HC.ApprovalRequest):
        # fake/仿真：不连接真实设备、不写项目外文件、不执行 Shell、不操作患者数据
        self.tool_calls += 1
        for k in self.lifecycle:
            self.lifecycle[k] += 1
        self._emit("tool_started", step_id=2, status="running", summary="模拟执行包生成中（fake）",
                   safe_payload={"tool_name": req.tool_name, "is_simulation": True})
        self._emit("tool_returned", step_id=2, status="ok", summary="模拟执行包已生成（fake）",
                   safe_payload={"tool_name": req.tool_name, "is_simulation": True, "structured": True})
        self._emit("observation_recorded", step_id=2, status="ok", summary="observation ok",
                   safe_payload={"retrieval_status": "ok"})
        art = {"artifact_id": "art-wetlab-sim", "name": "wetlab_simulation_package.json", "kind": "json",
               "size_bytes": 2048, "hash_short": compute_hash({"tissue": self._answer})[:8] + "…",
               "provenance_status": "verified", "verifier_status": "not_run"}
        self.artifacts.append(art)
        self._emit("artifact_created", step_id=2, summary=art["name"], artifact_ids=[art["artifact_id"]],
                   safe_payload={"artifact_name": art["name"], "artifact_kind": art["kind"],
                                 "size_bytes": art["size_bytes"], "hash_short": art["hash_short"],
                                 "provenance_status": "verified", "verifier_status": "not_run",
                                 "is_simulation": True})
        self._emit("step_satisfied", step_id=2, status="satisfied",
                   summary="模拟执行包已生成（fake）", safe_payload={"remaining_gaps": []})

    # ---------- 审批：拒绝 ----------
    def deny(self, dec: HC.ApprovalDecision) -> dict:
        cached = self._check_idem(dec.idempotency_key)
        if cached is not None:
            return cached
        req = self._require_pending("approval", dec.request_id, dec.expected_state_version)
        if dec.action_hash != req.action_hash:
            raise HC.ContractViolation("action_hash 不匹配")
        req.status = "denied"
        self._emit("approval_denied", step_id=2, status="denied", summary="动作被拒绝（不执行）",
                   safe_payload={"request_id": req.request_id, "action_hash": req.action_hash,
                                 "reason": "user_denied"})
        self.pending = None
        self._pending_obj = None
        self._to("stopped")                          # 安全终态：不执行、不产 artifact、不伪装成功
        self._emit("run_stopped", status="stopped", summary="approval denied → safe terminal",
                   safe_payload={"reason": "approval_denied"})
        return self._snapshot_and_cache(dec.idempotency_key)

    # ---------- Pause / Resume / Stop ----------
    def pause(self, idem_key: str, expected_version: int) -> dict:
        cached = self._check_idem(idem_key)
        if cached is not None:
            return cached
        self._require_version(expected_version)
        if HC.is_terminal(self.state):
            raise HC.IllegalTransition(f"终态不可暂停：{self.state}")
        self._resume_target = self.state             # 记录暂停前状态，供 resume 还原
        self._emit("pause_requested", status="pausing", summary="cooperative pause requested",
                   safe_payload={"phase": self.state})
        if self.state == "running":
            self._to("pausing")                      # 合作式：当前安全边界后进入 paused
        self._to("paused")
        self._emit("run_paused", status="paused", summary="run paused",
                   safe_payload={"phase": self._resume_target})
        return self._snapshot_and_cache(idem_key)

    def resume(self, idem_key: str, expected_version: int) -> dict:
        cached = self._check_idem(idem_key)
        if cached is not None:
            return cached
        self._require_version(expected_version)
        if self.state != "paused":
            raise HC.IllegalTransition(f"只有 paused 可 resume，当前 {self.state}")
        target = self._resume_target or "running"
        self._to(target)                             # 还原到暂停前状态（含待处理请求）
        self._resume_target = None
        self._emit("run_resumed", status=self.state, summary="run resumed",
                   safe_payload={"phase": self.state})
        return self._snapshot_and_cache(idem_key)

    def stop(self, idem_key: str, expected_version: int) -> dict:
        cached = self._check_idem(idem_key)
        if cached is not None:
            return cached
        self._require_version(expected_version)
        if HC.is_terminal(self.state):
            raise HC.IllegalTransition(f"终态不可再操作：{self.state}")   # completed/failed/stopped 不允许改
        self._to("stopped")
        self.pending = None
        self._pending_obj = None
        self._emit("run_stopped", status="stopped", summary="user stopped")
        return self._snapshot_and_cache(idem_key)

    # ---------- 校验 / 幂等 ----------
    def _require_version(self, expected):
        if int(expected) != self.state_version:
            raise HC.StaleState(f"state_version 过期：expected={expected} current={self.state_version}")

    def _require_pending(self, kind, request_id, expected_version):
        self._require_version(expected_version)
        if self._pending_obj is None or self.pending is None:
            raise HC.ContractViolation(f"当前无待处理的 {kind}")
        if self._pending_obj.request_id != request_id:
            raise HC.ContractViolation("request_id 与当前待处理请求不符")
        return self._pending_obj

    def _check_idem(self, key):
        return self._idem.get(key)

    def _snapshot_and_cache(self, key):
        snap = self.snapshot()
        self._idem[key] = snap
        return snap

    def snapshot(self) -> dict:
        return {"run_id": self.run_id, "control_state": self.state,
                "state_version": self.state_version, "pending": self.pending,
                "tool_calls": self.tool_calls, "artifact_count": len(self.artifacts),
                "lifecycle": dict(self.lifecycle)}


# ---------- 脱敏公开视图（供 UI 渲染卡片） ----------
def _clar_public(req: HC.ClarificationRequest) -> dict:
    return {"type": "clarification", "request_id": req.request_id, "state_version": req.state_version,
            "kind": req.kind, "reason": req.reason, "requesting_step_id": req.requesting_step_id,
            "allow_other": req.allow_other, "status": req.status,
            "allowed_options": [{"id": o.id, "label": o.label} for o in req.allowed_options]}


def _apr_public(req: HC.ApprovalRequest) -> dict:
    return {"type": "approval", "request_id": req.request_id, "state_version": req.state_version,
            "tool_name": req.tool_name, "risk_level": req.risk_level, "action_summary": req.action_summary,
            "expected_side_effect": req.expected_side_effect, "action_hash": req.action_hash,
            "is_simulation": req.is_simulation, "reason": req.reason, "status": req.status}


# ---------- 从 append-only 事件重建控制状态 ----------
def rebuild_state_from_events(events) -> dict:
    """仅凭事件流恢复当前控制状态 + 待处理请求（刷新/新进程可用）。"""
    state, version, pending = "running", 0, None
    for e in events:
        sp = e.safe_payload
        cs = sp.get("control_state")
        if cs:
            state = cs
        if sp.get("state_version") is not None:
            version = sp["state_version"]
        et = e.event_type
        if et == "clarification_requested":
            pending = {"type": "clarification", "request_id": sp.get("request_id"),
                       "allowed_options": sp.get("allowed_options", []), "kind": sp.get("kind"),
                       "allow_other": sp.get("allow_other", False), "reason": sp.get("reason"),
                       "state_version": sp.get("state_version"), "status": "open"}
        elif et == "approval_requested":
            pending = {"type": "approval", "request_id": sp.get("request_id"),
                       "action_hash": sp.get("action_hash"), "tool_name": sp.get("tool_name"),
                       "risk_level": sp.get("risk_level"), "action_summary": sp.get("action_summary"),
                       "expected_side_effect": sp.get("expected_side_effect"),
                       "is_simulation": sp.get("is_simulation"), "reason": sp.get("reason"),
                       "state_version": sp.get("state_version"), "status": "open"}
        elif et in ("clarification_answered", "approval_granted", "approval_denied"):
            pending = None
    return {"control_state": state, "state_version": version, "pending": pending}


__all__ = ["HitlRun", "DEMO_QUESTION", "rebuild_state_from_events"]
