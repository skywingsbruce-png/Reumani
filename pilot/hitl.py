"""A.7.5 / A.7.5.1 —— 人机协作控制运行时（Clarification / Approval / Pause / Resume）。

Deterministic fake：零付费模型、零外部网络、零湿实验设备、零任意代码。用一个确定性 SSc 演示
（IL-6 成纤维细胞实验，故意缺组织来源 → 澄清；模拟生成湿实验自动化执行包 → 审批）验证控制能力。

A.7.5.1 加固（只修控制层，不新增科研功能）：
- **真正原子并发**：每个 run 一把 RLock；idempotency 检查 + expected_state_version 检查 + 状态转换
  + 事件追加 + 结果缓存全部位于同一临界区。事件先缓冲、在临界区末尾以 append_batch 原子落盘；
  落盘失败则回滚内存快照——内存状态永不领先于持久化日志（不会"状态已变但事件未记录"）。
- **持久化重建**：仅凭 append-only 事件即可用**新进程/新 RunManager** 完整恢复：state / version /
  typed pending / answer / action_hash / arguments_hash / resume_target / 已用 idempotency_key 的
  稳定 hash 与响应摘要 / tool_calls / lifecycle / artifacts / 已完成步骤 / 终态。日志损坏/缺事件/
  hash 不一致 → fail-closed（要求人工审查）。事件只存 idempotency_key 的 hash，不存原值。
- **执行中协作式 Pause**：可控 blocking fake 阶段；Pause 先进入 pausing，不杀死已开始调用，
  当前调用返回后进入 paused；Resume 从下一未完成阶段继续；已完成阶段不重复；reservations 归 0。
  Stop 若在 pausing 到达 → 终态 stopped，不被随后完成的调用改回。

**边界**：仅单进程、单 worker。多 worker 场景不声称跨进程原子（RunManager 显式拒绝）。
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Optional

from tool_envelope import compute_hash, now
from pilot.runtime_events import make_event
from pilot import hitl_contracts as HC

DEMO_QUESTION = "设计一项 SSc 成纤维细胞 IL-6 实验（缺少组织来源，需澄清）"
_TISSUE_OPTIONS = [("skin", "皮肤成纤维细胞"), ("lung", "肺成纤维细胞"), ("both", "两者")]

# 审批后 fake 动作的阶段游标：0=工具调用（含在途 block）1=观察 2=产物；>=3 → 完成
_STAGE_TOOL, _STAGE_OBSERVE, _STAGE_ARTIFACT, _STAGE_DONE = 0, 1, 2, 3


class RecoveryError(RuntimeError):
    """持久化日志损坏 / 缺事件 / hash 不一致 → fail-closed，要求人工审查。"""


def idem_hash(key: str) -> str:
    """idempotency_key 的稳定 hash（事件只存此 hash，不存原始 key）。"""
    return compute_hash({"idem": str(key)})


def request_fingerprint(kind: str, payload: dict) -> str:
    """请求指纹（稳定 hash）：把 idempotency_key 绑定到具体请求内容。

    同 key + 同 payload → 同指纹 → 幂等 replay；同 key + 不同 payload → 指纹不符 → 冲突（fail-closed）。
    """
    return compute_hash({"kind": kind, "payload": payload})


class HitlRun:
    """单个人机协作运行的权威状态机（内存态投影 + append-only 事件为单一真相）。"""

    def __init__(self, run_id: str, event_store, *, clock=now,
                 exec_delay_ms: int = 0, exec_gate: Optional[threading.Event] = None):
        self.run_id = run_id
        self._store = event_store                    # 需提供 append_batch(list) 与 list(run_id)
        self._clock = clock
        self._exec_delay_ms = max(0, int(exec_delay_ms or 0))
        self._exec_gate = exec_gate                  # 注入的可控 blocking（测试/演示）
        self._lock = threading.RLock()

        self.state: str = "running"
        self.state_version: int = 0
        self._seq = 0
        self.pending: Optional[dict] = None          # 当前待处理请求（脱敏 dict，供 UI）
        self._pending_obj = None                     # ClarificationRequest | ApprovalRequest（typed）
        self._resume_target: Optional[str] = None
        self._answer = None                          # 已选组织来源（id）
        self.tool_calls = 0
        self.artifacts: list = []
        self.lifecycle = {"requested": 0, "executed": 0, "tool_returned": 0, "observed": 0}
        self.completed_steps: set = set()

        self._idem: dict[str, dict] = {}             # idem_hash -> 响应快照（幂等）
        self._idem_fp: dict[str, str] = {}           # idem_hash -> 请求指纹（同 key 不同 payload → 冲突）
        self._buffer: Optional[list] = None          # 当前事务的事件缓冲
        self._cur_idem: Optional[str] = None         # 当前事务的 idem_hash（打进事件）
        self._cur_fp: Optional[str] = None           # 当前事务的 request_fingerprint（打进事件）
        self.needs_human_review = False              # 崩溃于 pausing 等不确定态 → 需人工审查

        # 执行中 Pause 相关
        self._exec_cursor = _STAGE_DONE              # 无审批时不执行
        self._pause_requested = False
        self._exec_active = False
        self._open_reservations = 0
        self._worker: Optional[threading.Thread] = None

    # ============================ 事务 / 事件 ============================
    def _emit(self, event_type, *, status=None, summary="", safe_payload=None, artifact_ids=None,
              evidence_ids=None, step_id=None):
        assert self._buffer is not None, "事件必须在事务内产生"
        sp = dict(safe_payload or {})
        sp.setdefault("control_state", self.state)
        sp.setdefault("state_version", self.state_version)
        if self._cur_idem is not None:
            sp.setdefault("idempotency_hash", self._cur_idem)
        if self._cur_fp is not None:
            sp.setdefault("request_fingerprint", self._cur_fp)
        ev = make_event(run_id=self.run_id, sequence=self._seq, event_type=event_type,
                        event_id=f"{self.run_id}-{self._seq:04d}", status=status, summary=summary,
                        step_id=step_id, safe_payload=sp, artifact_ids=artifact_ids or [],
                        evidence_ids=evidence_ids or [], clock=self._clock)
        self._seq += 1
        self._buffer.append(ev)
        return ev

    def _to(self, dst):
        HC.assert_transition(self.state, dst)        # fail-closed
        self.state = dst
        self.state_version += 1

    _MUTABLE = ("state", "state_version", "_seq", "pending", "_pending_obj", "_resume_target",
                "_answer", "tool_calls", "_exec_cursor", "_pause_requested", "_exec_active",
                "_open_reservations")

    def _snapshot_mutable(self) -> dict:
        snap = {k: getattr(self, k) for k in self._MUTABLE}
        snap["artifacts"] = list(self.artifacts)
        snap["lifecycle"] = dict(self.lifecycle)
        snap["completed_steps"] = set(self.completed_steps)
        return snap

    def _restore_mutable(self, snap: dict) -> None:
        for k in self._MUTABLE:
            setattr(self, k, snap[k])
        self.artifacts = list(snap["artifacts"])
        self.lifecycle = dict(snap["lifecycle"])
        self.completed_steps = set(snap["completed_steps"])

    @contextmanager
    def _txn(self, idem_h: Optional[str] = None, fp: Optional[str] = None):
        """原子事务：缓冲事件 → append_batch 落盘；任何异常（含落盘失败）回滚内存快照。"""
        with self._lock:
            before = self._snapshot_mutable()
            self._buffer = []
            self._cur_idem = idem_h
            self._cur_fp = fp
            try:
                yield
                self._store.append_batch(self._buffer)   # 全有或全无
            except BaseException:
                self._restore_mutable(before)            # 内存回滚：不领先于持久化日志
                raise
            finally:
                self._buffer = None
                self._cur_idem = None
                self._cur_fp = None

    # ============================ 启动 ============================
    def start(self):
        with self._txn(None):
            self._emit("run_created", summary="hitl run created",
                       safe_payload={"note": "deterministic fake HITL"})
            self._emit("plan_ready", summary="计划已就绪（2 步）", safe_payload={"step_count": 2})
            self._emit("step_started", step_id=1, status="running", summary="设计实验（缺组织来源）",
                       safe_payload={"step_objective": "设计 SSc 成纤维细胞 IL-6 实验"})
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

    # ============================ 澄清作答 ============================
    def answer_clarification(self, ans: HC.ClarificationAnswer) -> dict:
        with self._lock:
            h = idem_hash(ans.idempotency_key)
            fp = request_fingerprint("answer", {"selected": sorted(ans.selected_option_ids),
                                                "other": ans.other_text or ""})
            cached = self._idem_get(h, fp)
            if cached is not None:
                return cached
            req = self._require_pending("clarification", ans.request_id, ans.expected_state_version)
            valid_ids = req.option_ids()
            chosen = [i for i in ans.selected_option_ids if i in valid_ids]
            if chosen:
                answer_label = ",".join(chosen)
                answer_id = chosen[0]
            elif ans.other_text and req.allow_other:
                answer_label = f"其他：{ans.other_text}"
                answer_id = ans.other_text
            else:
                raise HC.ContractViolation("澄清答案无有效选项且无 other_text")
            with self._txn(h):
                req.status = "answered"
                self._answer = answer_id
                self._to("running")
                self._emit("clarification_answered", step_id=1, status="running", summary="澄清已回答",
                           safe_payload={"request_id": req.request_id, "answer": answer_label[:120]})
                self._emit("step_satisfied", step_id=1, status="satisfied",
                           summary=f"实验设计已确定组织来源：{answer_label[:40]}",
                           safe_payload={"answer": answer_label[:120], "remaining_gaps": []})
                self.completed_steps.add(1)
                self.pending = None
                self._pending_obj = None
                self._request_approval()                 # 推进到审批点（同一事务原子）
            return self._cache(h, fp)

    # ---------- 请求审批（具体动作），在澄清事务内调用 ----------
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

    # ============================ 审批：批准 ============================
    def approve(self, dec: HC.ApprovalDecision) -> dict:
        with self._lock:
            h = idem_hash(dec.idempotency_key)
            fp = request_fingerprint("approve", {"action_hash": dec.action_hash})
            cached = self._idem_get(h, fp)
            if cached is not None:
                return cached
            req = self._require_pending("approval", dec.request_id, dec.expected_state_version)
            if dec.action_hash != req.action_hash:       # 仅完全一致才有效
                raise HC.ContractViolation("action_hash 不匹配（动作/参数已变化，旧批准失效，请重新申请）")
            async_mode = self._async_exec()
            with self._txn(h, fp):
                req.status = "granted"
                self._to("running")
                self._emit("approval_granted", step_id=2, status="running", summary="动作已批准",
                           safe_payload={"request_id": req.request_id, "action_hash": req.action_hash})
                self.pending = None
                self._pending_obj = None
                self._exec_cursor = _STAGE_TOOL
                if not async_mode:
                    self._run_stages_inline()            # 同步：所有阶段 → completed（同一原子批）
            if async_mode:
                self._exec_active = True                 # 后台 worker 驱动，approve 立即返回 running
                self._start_worker()
            return self._cache(h, fp)

    def _async_exec(self) -> bool:
        return self._exec_gate is not None or self._exec_delay_ms > 0

    # ---------- fake 动作阶段（每段只发生一次；游标推进保证不重复） ----------
    def _emit_tool_events(self):
        self.tool_calls += 1
        self.lifecycle["requested"] += 1
        self.lifecycle["executed"] += 1
        self._emit("tool_started", step_id=2, status="running", summary="模拟执行包生成中（fake）",
                   safe_payload={"tool_name": "simulate_wetlab_package", "is_simulation": True})
        self.lifecycle["tool_returned"] += 1
        self._emit("tool_returned", step_id=2, status="ok", summary="模拟执行包已生成（fake）",
                   safe_payload={"tool_name": "simulate_wetlab_package", "is_simulation": True,
                                 "structured": True})

    def _emit_observe_events(self):
        self.lifecycle["observed"] += 1
        self._emit("observation_recorded", step_id=2, status="ok", summary="observation ok",
                   safe_payload={"retrieval_status": "ok"})

    def _emit_artifact_events(self):
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
        self.completed_steps.add(2)

    def _emit_complete(self):
        self._to("completed")
        self._emit("run_completed", status="completed", summary="run completed",
                   artifact_ids=[a["artifact_id"] for a in self.artifacts])

    def _run_stages_inline(self):
        """同步执行剩余阶段（默认路径；零 blocking，确定性）。事件进入当前事务。"""
        if self._exec_cursor <= _STAGE_TOOL:
            self._emit_tool_events()
        if self._exec_cursor <= _STAGE_OBSERVE:
            self._emit_observe_events()
        if self._exec_cursor <= _STAGE_ARTIFACT:
            self._emit_artifact_events()
        self._exec_cursor = _STAGE_DONE
        self._emit_complete()

    # ---------- 后台 worker：执行中协作式 pause ----------
    def _start_worker(self):
        self._worker = threading.Thread(target=self._worker_loop, name=f"hitl-{self.run_id}", daemon=True)
        self._worker.start()

    def _block_inflight(self):
        """模拟"已开始的调用"在途阻塞（fake / 仿真；零付费）。在锁外执行，pause 可并发到达。"""
        if self._exec_gate is not None:
            self._exec_gate.wait(timeout=30)
        elif self._exec_delay_ms > 0:
            threading.Event().wait(self._exec_delay_ms / 1000.0)

    def _worker_loop(self):
        while True:
            # ---- 安全边界（持锁）：检查终态 / pause / 完成 ----
            with self._lock:
                if HC.is_terminal(self.state):
                    self._exec_active = False
                    return
                if self._pause_requested and self.state in ("running", "pausing"):
                    self._commit_paused_from_exec()
                    self._exec_active = False
                    return
                cursor = self._exec_cursor
                if cursor >= _STAGE_DONE:
                    with self._txn(None):
                        self._emit_complete()
                    self._exec_active = False
                    return
            # ---- 工具阶段：先在锁外跑"在途调用"，返回后再原子记录 ----
            if cursor == _STAGE_TOOL:
                with self._lock:
                    self._open_reservations = 1              # 预留（内存记账）
                self._block_inflight()                       # 不被 pause 强杀
                with self._lock:
                    if HC.is_terminal(self.state):           # stop 在途到达 → 丢弃，reservations 归 0
                        self._open_reservations = 0
                        self._exec_active = False
                        return
                    with self._txn(None):
                        self._emit_tool_events()
                        self._exec_cursor = _STAGE_OBSERVE
                    self._open_reservations = 0
            elif cursor == _STAGE_OBSERVE:
                with self._lock:
                    if HC.is_terminal(self.state):
                        self._exec_active = False
                        return
                    with self._txn(None):
                        self._emit_observe_events()
                        self._exec_cursor = _STAGE_ARTIFACT
            elif cursor == _STAGE_ARTIFACT:
                with self._lock:
                    if HC.is_terminal(self.state):
                        self._exec_active = False
                        return
                    with self._txn(None):
                        self._emit_artifact_events()
                        self._exec_cursor = _STAGE_DONE

    def _commit_paused_from_exec(self):
        """在执行边界进入 paused（保留 resume 游标，供从下一未完成阶段继续）。持锁调用。"""
        with self._txn(None):
            self._resume_target = "running"
            self._to("paused")
            self._emit("run_paused", status="paused", summary="run paused (in-flight boundary)",
                       safe_payload={"phase": "running", "resume_cursor": self._exec_cursor})
        self._pause_requested = False

    def join_worker(self, timeout: float = 30):
        w = self._worker
        if w is not None:
            w.join(timeout)

    # ============================ 审批：拒绝 ============================
    def deny(self, dec: HC.ApprovalDecision) -> dict:
        with self._lock:
            h = idem_hash(dec.idempotency_key)
            fp = request_fingerprint("deny", {"action_hash": dec.action_hash})
            cached = self._idem_get(h, fp)
            if cached is not None:
                return cached
            req = self._require_pending("approval", dec.request_id, dec.expected_state_version)
            if dec.action_hash != req.action_hash:
                raise HC.ContractViolation("action_hash 不匹配")
            with self._txn(h, fp):
                req.status = "denied"
                self._emit("approval_denied", step_id=2, status="denied", summary="动作被拒绝（不执行）",
                           safe_payload={"request_id": req.request_id, "action_hash": req.action_hash,
                                         "reason": "user_denied"})
                self.pending = None
                self._pending_obj = None
                self._to("stopped")                          # 安全终态：不执行、不产 artifact
                self._emit("run_stopped", status="stopped", summary="approval denied → safe terminal",
                           safe_payload={"reason": "approval_denied"})
            return self._cache(h, fp)

    # ============================ Pause / Resume / Stop ============================
    def pause(self, idem_key: str, expected_version: int) -> dict:
        with self._lock:
            h = idem_hash(idem_key)
            fp = request_fingerprint("pause", {})
            cached = self._idem_get(h, fp)
            if cached is not None:
                return cached
            self._require_version(expected_version)
            if HC.is_terminal(self.state):
                raise HC.IllegalTransition(f"终态不可暂停：{self.state}")
            if self._exec_active and self.state == "running":
                # 执行中：先进入 pausing，不杀在途调用；worker 在边界完成后再进入 paused。
                # 关键：先 _to("pausing") 再 emit，使持久化事件携带 control_state=pausing——
                # 否则崩溃后恢复会误判为 running（看似在跑实则无 worker）。
                with self._txn(h, fp):
                    self._resume_target = "running"
                    self._to("pausing")
                    self._emit("pause_requested", status="pausing",
                               summary="cooperative pause requested (in-flight)",
                               safe_payload={"phase": "running"})
                    self._pause_requested = True
                return self._cache(h, fp)
            # 等待态 / 无在途执行：合作式进入 paused
            pre = self.state
            with self._txn(h, fp):
                self._resume_target = pre
                self._emit("pause_requested", status="pausing", summary="cooperative pause requested",
                           safe_payload={"phase": pre})
                if self.state == "running":
                    self._to("pausing")
                self._to("paused")
                self._emit("run_paused", status="paused", summary="run paused",
                           safe_payload={"phase": self._resume_target})
            return self._cache(h, fp)

    def resume(self, idem_key: str, expected_version: int) -> dict:
        with self._lock:
            h = idem_hash(idem_key)
            fp = request_fingerprint("resume", {})
            cached = self._idem_get(h, fp)
            if cached is not None:
                return cached
            self._require_version(expected_version)
            if self.state != "paused":
                raise HC.IllegalTransition(f"只有 paused 可 resume，当前 {self.state}")
            target = self._resume_target or "running"
            resumes_exec = (target == "running" and self._exec_cursor < _STAGE_DONE)
            with self._txn(h, fp):
                self._to(target)                             # 还原到暂停前状态（含待处理请求）
                self._resume_target = None
                self._emit("run_resumed", status=self.state, summary="run resumed",
                           safe_payload={"phase": self.state, "resume_cursor": self._exec_cursor})
                if resumes_exec and not self._async_exec():
                    self._run_stages_inline()                # 恢复后（无 gate）同步跑完剩余阶段
            if resumes_exec and self._async_exec():
                self._exec_active = True
                self._start_worker()                         # 从 self._exec_cursor 继续，不重复已完成阶段
            return self._cache(h, fp)

    def stop(self, idem_key: str, expected_version: int) -> dict:
        with self._lock:
            h = idem_hash(idem_key)
            fp = request_fingerprint("stop", {})
            cached = self._idem_get(h, fp)
            if cached is not None:
                return cached
            self._require_version(expected_version)
            if HC.is_terminal(self.state):
                raise HC.IllegalTransition(f"终态不可再操作：{self.state}")
            with self._txn(h, fp):
                self._to("stopped")
                self.pending = None
                self._pending_obj = None
                self._pause_requested = False
                self._emit("run_stopped", status="stopped", summary="user stopped")
            return self._cache(h, fp)

    # ============================ 校验 / 幂等 ============================
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

    def _idem_get(self, idem_h, fp):
        """幂等查表：命中且指纹一致 → 返回缓存；命中但指纹不符 → 冲突（同 key 不同 payload）。"""
        if idem_h in self._idem:
            if self._idem_fp.get(idem_h) != fp:
                raise HC.ContractViolation("idempotency_key 复用于不同请求（payload 不一致）→ 冲突")
            return self._idem[idem_h]
        return None

    def _cache(self, idem_h, fp=None):
        snap = self.snapshot()
        self._idem[idem_h] = snap
        self._idem_fp[idem_h] = fp
        return snap

    def snapshot(self) -> dict:
        return {"run_id": self.run_id, "control_state": self.state,
                "state_version": self.state_version, "pending": self.pending,
                "tool_calls": self.tool_calls, "artifact_count": len(self.artifacts),
                "open_reservations": self._open_reservations, "lifecycle": dict(self.lifecycle),
                "needs_human_review": self.needs_human_review}

    # ============================ 从持久化事件恢复（§2） ============================
    @classmethod
    def recover(cls, run_id: str, event_store, *, clock=now,
                exec_delay_ms: int = 0, exec_gate: Optional[threading.Event] = None) -> "HitlRun":
        """用**新对象**仅凭 append-only 事件重建完整控制状态（新进程/新 RunManager 可用）。

        任何损坏都 fail-closed（RecoveryError）：截断 JSON、content_hash 不一致、未知 schema、
        sequence 断裂/重复、state_version 回退、终态后追加事件、恢复期非法转换。不自动重放、
        不自动 resume、不启动 worker、不执行工具、不改写原日志。
        """
        from json import JSONDecodeError
        from pydantic import ValidationError
        try:
            events = event_store.list(run_id)        # 读取即校验 content_hash / schema_version
        except (ValidationError, ValueError, JSONDecodeError) as e:
            raise RecoveryError(f"事件日志损坏/截断/未知 schema（human_review）：{str(e)[:120]}") from e
        if not events:
            raise RecoveryError(f"无事件可恢复：{run_id}")
        r = cls(run_id, event_store, clock=clock, exec_delay_ms=exec_delay_ms, exec_gate=exec_gate)
        expected_seq = 0
        prev_version = -1
        terminal_seen = False
        idem_last: dict[str, int] = {}               # idem_hash -> 该组最后一个事件的 index
        idem_fp: dict[str, str] = {}                 # idem_hash -> request_fingerprint（重启后冲突检测）
        for i, e in enumerate(events):
            if e.sequence != expected_seq:           # 缺事件 / 重复 / 乱序 → fail-closed
                raise RecoveryError(f"事件 sequence 断裂：得到 {e.sequence}，应为 {expected_seq}")
            expected_seq += 1
            if terminal_seen:                        # 终态后仍有事件 → 篡改/损坏
                raise RecoveryError(f"终态之后仍有事件（human_review）：seq={e.sequence} {e.event_type}")
            sv = e.safe_payload.get("state_version")
            if sv is not None:
                if int(sv) < prev_version:           # state_version 单调不减
                    raise RecoveryError(f"state_version 回退（human_review）：{sv} < {prev_version}")
                prev_version = int(sv)
            try:                                     # content_hash 已在 RuntimeEvent 校验；此处兜底转换
                r._apply_recovered(e)
            except HC.IllegalTransition as ex:
                raise RecoveryError(f"恢复期非法状态转换（human_review）：{ex}") from ex
            if e.event_type in ("run_completed", "run_failed", "run_stopped"):
                terminal_seen = True
            ih = e.safe_payload.get("idempotency_hash")
            if ih:
                idem_last[ih] = i
                fpv = e.safe_payload.get("request_fingerprint")
                if fpv is not None:
                    idem_fp[ih] = fpv                # 重建请求指纹 → 重启后同 key 不同 payload 仍冲突
        r._seq = expected_seq
        if r.state == "pausing":                     # 崩溃于 pausing：在途调用外部副作用未知 → 人工审查
            r.needs_human_review = True              # 不自动 resume 成 running（resume 从 pausing 本就非法）
        # 用每个 idem 组"最后一个事件后的投影"作为原响应摘要（hash + 摘要都可复现）
        for ih, _idx in idem_last.items():
            r._idem[ih] = {**r.snapshot(), "recovered": True,
                           "result_digest": compute_hash({"idem": ih, "state": r.state,
                                                           "version": r.state_version})}
            r._idem_fp[ih] = idem_fp.get(ih)
        # 若恢复时停在执行中暂停点 → 重建可继续的阶段游标（stages 确定性，无需持久化 stage 体）
        return r

    def _apply_recovered(self, e):
        sp = e.safe_payload
        et = e.event_type
        if sp.get("control_state"):
            self.state = sp["control_state"]
        if sp.get("state_version") is not None:
            self.state_version = int(sp["state_version"])
        if et == "clarification_requested":
            opts = [HC.ClarificationOption(id=o["id"], label=o["label"])
                    for o in sp.get("allowed_options", [])]
            req = HC.ClarificationRequest(
                request_id=sp.get("request_id"), run_id=self.run_id,
                state_version=int(sp.get("state_version", self.state_version)),
                created_at=e.timestamp, reason=sp.get("reason", ""),
                requesting_step_id=e.step_id or 1, kind=sp.get("kind", "single_or_other"),
                question_hash=sp.get("question_hash", ""), allowed_options=opts,
                allow_other=bool(sp.get("allow_other", False)))
            self._pending_obj = req
            self.pending = _clar_public(req)
        elif et == "clarification_answered":
            ans = str(sp.get("answer", ""))
            self._answer = ans.split(",")[0] if ans and not ans.startswith("其他：") else \
                ans.replace("其他：", "") or self._answer
            self.pending = None
            self._pending_obj = None
        elif et == "approval_requested":
            req = HC.ApprovalRequest(
                request_id=sp.get("request_id"), run_id=self.run_id,
                state_version=int(sp.get("state_version", self.state_version)),
                created_at=e.timestamp, reason=sp.get("reason", ""),
                requesting_step_id=e.step_id or 2, tool_name=sp.get("tool_name", ""),
                arguments_hash=sp.get("arguments_hash", ""), risk_level=sp.get("risk_level", "high"),
                action_summary=sp.get("action_summary", ""),
                expected_side_effect=sp.get("expected_side_effect", ""),
                action_hash=sp.get("action_hash", ""), is_simulation=bool(sp.get("is_simulation", True)))
            self._pending_obj = req
            self.pending = _apr_public(req)
        elif et in ("approval_granted", "approval_denied"):
            if et == "approval_granted":
                self._exec_cursor = _STAGE_TOOL
            self.pending = None
            self._pending_obj = None
        elif et == "tool_started":
            self.tool_calls += 1
            self.lifecycle["requested"] += 1
            self.lifecycle["executed"] += 1
        elif et == "tool_returned":
            self.lifecycle["tool_returned"] += 1
            self._exec_cursor = max(self._exec_cursor, _STAGE_OBSERVE)
        elif et == "observation_recorded":
            self.lifecycle["observed"] += 1
            self._exec_cursor = max(self._exec_cursor, _STAGE_ARTIFACT)
        elif et == "artifact_created":
            aid = (e.artifact_ids or ["art-wetlab-sim"])[0]
            self.artifacts.append({"artifact_id": aid, "name": sp.get("artifact_name"),
                                   "kind": sp.get("artifact_kind"), "size_bytes": sp.get("size_bytes"),
                                   "hash_short": sp.get("hash_short"),
                                   "provenance_status": sp.get("provenance_status"),
                                   "verifier_status": sp.get("verifier_status")})
            self._exec_cursor = _STAGE_DONE
        elif et == "step_satisfied":
            if e.step_id is not None:
                self.completed_steps.add(e.step_id)
        elif et == "run_paused":
            self._resume_target = sp.get("phase") or self._resume_target
            if sp.get("resume_cursor") is not None:
                self._exec_cursor = int(sp["resume_cursor"])
        elif et == "run_resumed":
            self._resume_target = None
        elif et in ("run_completed", "run_stopped", "run_failed"):
            self.pending = None
            self._pending_obj = None


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


# ---------- 从 append-only 事件重建控制状态（轻量公开视图；完整恢复见 HitlRun.recover） ----------
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


__all__ = ["HitlRun", "DEMO_QUESTION", "rebuild_state_from_events", "RecoveryError",
           "idem_hash", "request_fingerprint"]
