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

import re
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


ERROR_SUMMARY_MAX = 200


# 机密样式（异常正文里可能夹带 key/token/凭据）→ 一律打码后才允许进入持久化事件
_SECRET_PATTERNS = (
    r"(?i)\b(?:sk|pk|rk|ghp|gho|xox[baprs])-[A-Za-z0-9_\-]{4,}",          # 常见 key 前缀
    r"(?i)\bbearer\s+[A-Za-z0-9._\-]{4,}",                                # Authorization: Bearer
    r"(?i)\b(?:api[_-]?key|apikey|token|secret|password|passwd|pwd|credential)"
    r"\s*[:=]\s*\"?'?[^\s\"']{3,}",                                       # k=v 形式
    r"\b[A-Za-z0-9+/]{32,}={0,2}\b",                                      # 长 base64 样式串
)

# A.7.5.5 §8：随 approval_requested 事件一并落盘的冻结事实（全部为脱敏元数据/稳定 hash）。
# 目的是让「人当时看到了什么」可被事后审计，而不是只存在于内存里的 HTTP 响应。
_FROZEN_FACT_EVENT_KEYS = (
    "subset_id", "subset_hash", "source_pack_hash", "protocol_hash",
    "core_evidence_count", "context_only_count", "direct_count", "indirect_count",
    "direct_human_causal_count", "causal_ceiling", "total_call_cap",
    "task_budget_usd", "worst_case_cost_usd", "preview_hash", "budget_policy_id",
    "network_allowed", "planner_allowed", "code_allowed", "device_allowed",
    "expected_artifact", "evidence_content_level",
)


def _safe_error(err: BaseException) -> str:
    """脱敏错误摘要：单行、有限长；**不含** traceback / 路径 / key / Prompt / 模型正文。"""
    msg = " ".join(str(err).split())
    # 绝对路径片段（Windows 盘符 / POSIX 家目录）
    msg = re.sub(r"[A-Za-z]:\\[^\s]*|/(?:home|Users)/[^\s]*", "<path>", msg)
    for pat in _SECRET_PATTERNS:                       # 机密样式打码
        msg = re.sub(pat, "<redacted>", msg)
    return msg[:ERROR_SUMMARY_MAX]


def request_fingerprint(kind: str, payload: dict) -> str:
    """请求指纹（稳定 hash）：把 idempotency_key 绑定到具体请求内容。

    同 key + 同 payload → 同指纹 → 幂等 replay；同 key + 不同 payload → 指纹不符 → 冲突（fail-closed）。
    """
    return compute_hash({"kind": kind, "payload": payload})


class HitlRun:
    """单个人机协作运行的权威状态机（内存态投影 + append-only 事件为单一真相）。"""

    def __init__(self, run_id: str, event_store, *, clock=now,
                 exec_delay_ms: int = 0, exec_gate: Optional[threading.Event] = None,
                 spec=None, executor=None):
        """spec/executor 同时给出 → research run（参数化）；都不给 → 原有 demo run（行为不变）。

        `executor` 只能是服务端 registry 注入的 ResearchExecutor 实现；HitlRun 不 import
        任何模型客户端，也不会在事件里持久化执行器对象。
        """
        self.run_id = run_id
        self._store = event_store                    # 需提供 append_batch(list) 与 list(run_id)
        self._clock = clock
        self._exec_delay_ms = max(0, int(exec_delay_ms or 0))
        self._exec_gate = exec_gate                  # 注入的可控 blocking（测试/演示）
        self._lock = threading.RLock()

        # ---- research run 接线（demo run 时全部为 None/空，语义与 A.7.5 完全一致） ----
        if (spec is None) != (executor is None):
            raise HC.ContractViolation("research run 必须同时提供 spec 与 executor（不做任何回退）")
        self._spec = spec
        self._executor = executor
        self.run_type = "research" if spec is not None else "demo"
        if spec is not None:
            spec.execution_policy.assert_zero_paid_stage()      # 本阶段权限位必须全 False
            if getattr(executor, "executor_id", None) != spec.executor_id:
                raise HC.ContractViolation("executor_id 与 spec 不一致（fail-closed）")
        self._research_stages = tuple(getattr(executor, "stages", ()) or ())
        self._research_state: dict = {}
        self._stages_done: list = []
        self._frozen_plan: Optional[dict] = None      # Approval 时冻结的执行计划
        self._approval_facts: Optional[dict] = None   # §8 审批卡上展示过的冻结证据事实
        self.approval_grant = None                    # A.8.2a.3 签发的执行授权（仅进程内）
        self._approval_request_state_version = 0      # 创建 pending approval 时的状态版本
        self._spec_missing = False                    # 恢复出的 research run 缺 spec/executor → 不得执行
        self._recovered_plan: Optional[dict] = None   # 恢复期从 approval_requested 还原的冻结计划
        self._worker_generation = 0                   # 服务端产生的 worker 代次（客户端不可指定）
        self.primary_failure: Optional[dict] = None   # 首个失败（脱敏），不被次级失败覆盖
        self.secondary_failure: Optional[dict] = None # 记录失败但绝不覆盖 primary
        self.failure_manifest: Optional[dict] = None  # research-failure-v1 诊断产物（claims=[]）
        self._inflight_stage: Optional[str] = None    # 恢复期：已 started 未收敛的阶段
        self.interrupted_stage: Optional[str] = None  # 恢复出的"不确定在途阶段"（需人工审查）

        self.state: str = "running"
        self.state_version: int = 0
        self._seq = 0
        self.pending: Optional[dict] = None          # 当前待处理请求（脱敏 dict，供 UI）
        self._pending_obj = None                     # ClarificationRequest | ApprovalRequest（typed）
        self._resume_target: Optional[str] = None
        self._answer = None                          # 澄清答案（demo：组织来源；research：证据标准）
        self._answer_hash: Optional[str] = None
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
                "_answer", "_answer_hash", "tool_calls", "_exec_cursor", "_pause_requested",
                "_exec_active", "_open_reservations", "_frozen_plan")

    def _snapshot_mutable(self) -> dict:
        snap = {k: getattr(self, k) for k in self._MUTABLE}
        snap["artifacts"] = list(self.artifacts)
        snap["lifecycle"] = dict(self.lifecycle)
        snap["completed_steps"] = set(self.completed_steps)
        snap["_research_state"] = dict(self._research_state)
        snap["_stages_done"] = list(self._stages_done)
        return snap

    def _restore_mutable(self, snap: dict) -> None:
        for k in self._MUTABLE:
            setattr(self, k, snap[k])
        self.artifacts = list(snap["artifacts"])
        self.lifecycle = dict(snap["lifecycle"])
        self.completed_steps = set(snap["completed_steps"])
        self._research_state = dict(snap["_research_state"])
        self._stages_done = list(snap["_stages_done"])

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
        if self.run_type == "research":
            return self._start_research()
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
            research = self.run_type == "research"
            with self._txn(h):
                req.status = "answered"
                self._answer = answer_id
                self._answer_hash = compute_hash({"answer": answer_label})
                self._to("running")
                self._emit("clarification_answered", step_id=1, status="running", summary="澄清已回答",
                           safe_payload={"request_id": req.request_id, "answer": answer_label[:120],
                                         "answer_hash": self._answer_hash})
                self._emit("step_satisfied", step_id=1, status="satisfied",
                           summary=(f"证据标准已确定：{answer_label[:40]}" if research
                                    else f"实验设计已确定组织来源：{answer_label[:40]}"),
                           safe_payload={"answer": answer_label[:120], "remaining_gaps": []})
                self.completed_steps.add(1)
                self.pending = None
                self._pending_obj = None
                # 推进到审批点（同一事务原子）
                self._request_research_approval() if research else self._request_approval()
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

    # ==================== research run：参数化启动 / 审批 / 分阶段执行 ====================
    def _start_research(self):
        """research run 启动：问题、澄清、选项全部来自 ResearchRunSpec（不再写死）。"""
        spec = self._spec
        pub = spec.public_view()
        with self._txn(None):
            self._emit("run_created", summary="research run created",
                       safe_payload={"run_type": "research", "executor_id": spec.executor_id,
                                     "question_hash": pub["question_hash"],
                                     "evidence_count": pub["evidence_count"],
                                     "policy_hash": pub["policy_hash"], "fixture": pub["fixture_evidence"],
                                     "note": spec.question[:200]})
            self._emit("plan_ready", summary="研究计划已就绪（澄清 → 审批 → 执行）",
                       safe_payload={"step_count": 2, "stage_count": len(self._research_stages)})
            self._emit("step_started", step_id=1, status="running", summary="确认证据标准",
                       safe_payload={"step_objective": spec.question[:200]})
            c = spec.clarification
            qh = c.question_hash()
            rid = f"clr-{self.run_id}"
            self._to("awaiting_clarification")
            req = HC.ClarificationRequest(
                request_id=rid, run_id=self.run_id, state_version=self.state_version,
                created_at=self._clock(), reason=c.reason, requesting_step_id=1, kind=c.kind,
                question_hash=qh, allow_other=c.allow_other,
                allowed_options=[HC.ClarificationOption(id=o.id, label=o.label) for o in c.options])
            self._pending_obj = req
            self.pending = {**_clar_public(req), "prompt": c.question[:400],
                            "recommended": next((o.id for o in c.options if o.recommended), None)}
            self._emit("clarification_requested", step_id=1, status="awaiting_clarification",
                       summary=f"需要澄清：{c.question[:60]}",
                       safe_payload={"request_id": rid, "question_hash": qh, "kind": c.kind,
                                     "allow_other": c.allow_other, "reason": c.reason,
                                     "note": c.question[:200],      # 澄清提问本体（供刷新/恢复后渲染）
                                     "allowed_options": [{"id": o.id, "label": o.label} for o in c.options]})
        return self

    def _request_research_approval(self):
        """审批卡内容来自 spec；批准时**冻结执行计划**（question/evidence/policy/executor）。"""
        spec = self._spec
        pub = spec.public_view()
        a = spec.approval
        self._emit("step_started", step_id=2, status="running", summary=a.action_summary[:120],
                   safe_payload={"step_objective": "run fake research chain",
                                 "stage_count": len(self._research_stages)})
        # §8：审批前向人展示 executor 自报的**冻结事实**（证据子集 hash / 计数 / 真实上限）。
        # 确定性、零模型调用；若冻结证据已漂移，这里就抛错 → 根本不会出现审批卡。
        facts = self._executor_approval_facts()
        self._approval_facts = facts              # 人在卡上看到的那一份，批准时被冻结
        # action_hash 绑定完整执行计划 + 冻结事实：任一项改变 → 旧批准失效
        args = {"plan_hash": spec.plan_hash(), "answer_hash": self._answer_hash}
        if facts:
            args["preview_hash"] = facts["preview_hash"]
        ah = HC.action_hash(spec.executor_id, args, a.risk_level)
        rid = f"apr-{self.run_id}"
        self._to("awaiting_approval")
        req = HC.ApprovalRequest(
            request_id=rid, run_id=self.run_id, state_version=self.state_version,
            created_at=self._clock(), reason=a.reason, requesting_step_id=2,
            tool_name=spec.executor_id, arguments_hash=HC.normalized_arguments_hash(args),
            risk_level=a.risk_level, action_summary=a.action_summary,
            expected_side_effect=a.expected_side_effect, action_hash=ah,
            is_simulation=a.is_simulation)
        self._pending_obj = req
        self.pending = {**_apr_public(req), "run_type": "research",
                        "question": pub["question"], "clarification_answer": self._answer,
                        "evidence_count": pub["evidence_count"], "evidence_ids": pub["evidence_ids"],
                        "executor_id": pub["executor_id"], "policy": pub["policy"],
                        "policy_hash": pub["policy_hash"], "evidence_hash": pub["evidence_hash"],
                        "plan_hash": pub["plan_hash"], "stages": list(self._research_stages),
                        "expected_outputs": pub["expected_outputs"], "fixture": pub["fixture_evidence"],
                        **({"frozen_facts": facts} if facts else {})}
        self._emit("approval_requested", step_id=2, status="awaiting_approval",
                   summary=f"需要审批：{a.action_summary[:60]}",
                   safe_payload={"request_id": rid, "action_hash": ah, "tool_name": spec.executor_id,
                                 "arguments_hash": req.arguments_hash, "risk_level": a.risk_level,
                                 "action_summary": a.action_summary,
                                 "expected_side_effect": a.expected_side_effect,
                                 "is_simulation": a.is_simulation, "reason": a.reason,
                                 "executor_id": spec.executor_id, "policy_hash": pub["policy_hash"],
                                 "evidence_count": pub["evidence_count"],
                                 "question_hash": pub["question_hash"], "run_type": "research",
                                 "stage_count": len(self._research_stages),
                                 "fixture": pub["fixture_evidence"],
                                 **({k: facts[k] for k in _FROZEN_FACT_EVENT_KEYS if k in facts}
                                    if facts else {})})
        # A.8.2a.4a §3：pending request 建立后立刻冻结绑定，交给支持该接口的 executor。
        # binding 只来自当前真实 pending request，不接受 HTTP 传入。
        self._approval_request_state_version = int(self.state_version)
        bind = getattr(self._executor, "bind_pending_approval", None)
        if callable(bind) and facts:
            from pilot.approval_grant import issue_binding, _ISSUER
            bind(issue_binding(
                _ISSUER, run_id=self.run_id, request_id=rid, action_hash=ah,
                preview_hash=str(facts.get("preview_hash") or ""),
                request_state_version=int(self.state_version),
                executor_id=spec.executor_id,
                policy_id=str(facts.get("budget_policy_id") or "")))

    def _executor_approval_facts(self):
        """向 executor 索取审批冻结事实（可选能力；fake executor 没有则返回 None）。"""
        fn = getattr(self._executor, "approval_facts", None)
        if not callable(fn):
            return None
        facts = fn()
        if not isinstance(facts, dict) or not facts.get("preview_hash"):
            raise HC.ContractViolation("executor 返回的审批冻结事实无效 → 拒绝进入审批")
        return facts

    def _freeze_plan(self, *, use_card_facts=False):
        """批准瞬间冻结执行计划；执行前逐项复核，任何漂移 → fail-closed。"""
        spec = self._spec
        plan = {"question_hash": spec.question_hash(), "evidence_hash": spec.evidence_hash(),
                "policy_hash": spec.policy_hash(), "executor_id": spec.executor_id,
                "plan_hash": spec.plan_hash(), "answer_hash": self._answer_hash}
        # §8：冻结的必须是**人在审批卡上实际看到的那份事实**；比对时再重新求值。
        # 若两者不同 → 批准后证据/上限发生了漂移 → 旧批准失效。
        if use_card_facts:
            if self._approval_facts:
                plan["preview_hash"] = self._approval_facts["preview_hash"]
        else:
            facts = self._executor_approval_facts()
            if facts:
                plan["preview_hash"] = facts["preview_hash"]
        return plan

    def _assert_plan_unchanged(self):
        if self._frozen_plan is None:
            raise HC.ContractViolation("执行计划未冻结（未经批准不得执行）")
        cur = self._freeze_plan()
        for k, v in self._frozen_plan.items():
            if cur.get(k) != v:
                raise HC.ContractViolation(
                    f"批准后执行计划被修改（{k}）→ 旧批准失效，拒绝执行")
        if getattr(self._executor, "executor_id", None) != self._frozen_plan["executor_id"]:
            raise HC.ContractViolation("executor 身份与批准时不一致 → 拒绝执行")

    def _research_ctx(self):
        from pilot.research_contracts import ResearchRunContext
        spec = self._spec
        return ResearchRunContext(
            run_id=self.run_id, question=spec.question, question_hash=spec.question_hash(),
            clarification_answer=self._answer, answer_hash=self._answer_hash,
            evidence_refs=list(spec.evidence_refs), policy=spec.execution_policy)

    # ---------- 统一阶段边界：锁内校验+落 started → 锁外执行 → 锁内收敛 ----------
    def _run_research_stage(self, idx: int, generation: int):
        """执行第 idx 个阶段。返回 'ok' / 'discarded' / 'paused' / 'failed'。

        锁内先持久化 `research_stage_started`（供恢复期识别"不确定的在途阶段"），
        阶段本体在锁外执行（Pause/Stop 可并发到达），返回后再在锁内收敛。
        """
        stage = self._research_stages[idx]
        with self._lock:
            if not self._worker_valid(generation) or self._exec_cursor != idx:
                return "discarded"
            self._assert_plan_unchanged()
            ctx = self._research_ctx()
            snapshot_state = dict(self._research_state)
            with self._txn(None):
                self._emit("research_stage_started", step_id=2, status="running",
                           summary=f"stage: {stage}",
                           safe_payload={"stage": stage, "stage_index": idx,
                                         "stage_count": len(self._research_stages),
                                         "worker_generation": generation})
        # ---------- 锁外执行阶段本体；异常不得逃逸成未处理线程异常 ----------
        try:
            delta = self._executor.run_stage(stage=stage, ctx=ctx, state=snapshot_state, emit=None)
            err = None
        except BaseException as e:                      # noqa: BLE001 — 一律收敛为 fail-closed
            delta, err = None, e
        with self._lock:
            # stale worker / 终态防护：迟到结果不得改写已提交的终态
            if not self._worker_valid(generation) or self._exec_cursor != idx:
                return "discarded"
            if err is not None:
                self._commit_stage_failure(stage, idx, err, generation)
                return "failed"
            self._assert_plan_unchanged()               # 冻结计划在记录前再复核一次
            self._commit_stage_success(stage, idx, delta, generation)
            if self._pause_requested and self.state in ("running", "pausing"):
                self._commit_paused_from_exec()         # 当前阶段已安全提交后才进入 paused
                return "paused"
        return "ok"

    def _commit_stage_success(self, stage, idx, delta, generation):
        """持锁：原子记录阶段成功并推进游标（每阶段只发生一次）。"""
        with self._txn(None):
            self._research_state.update(delta or {})
            self._stages_done.append(stage)
            if stage in ("synthesizer", "verifier", "claim_extractor"):
                self.tool_calls += 1                       # fake 角色调用计数（零付费）
                self.lifecycle["requested"] += 1
                self.lifecycle["executed"] += 1
                self.lifecycle["tool_returned"] += 1
            sp = {"stage": stage, "stage_index": idx, "stage_count": len(self._research_stages),
                  "worker_generation": generation}
            if stage == "evidence_accumulator":
                sp["evidence_count"] = self._research_state.get("evidence_count", 0)
                self.lifecycle["observed"] += 1
            if stage == "verifier":
                sp["verifier_verdict"] = self._research_state.get("verifier_verdict")
                sp["causal_tier"] = self._research_state.get("causal_tier")
            if stage == "claim_extractor":
                sp["claim_count"] = len(self._research_state.get("claims", []))
            if stage == "shadow":
                sp["shadow_verdict"] = self._research_state.get("shadow_verdict")
            self._emit("research_stage_completed", step_id=2, status="ok",
                       summary=f"stage done: {stage}", safe_payload=sp)
            self._exec_cursor = idx + 1

    def _commit_stage_failure(self, stage, idx, err, generation):
        """持锁：阶段异常 → research_stage_failed + run_failed（fail-closed，不产成功产物）。"""
        # §7：输出截断有专门的诊断字段（只取元数据，绝不取被截断的输出正文）
        trunc = {}
        getter = getattr(err, "manifest_fields", None)
        if callable(getter):
            try:
                trunc = {k: v for k, v in dict(getter()).items()
                         if k in ("role", "finish_reason", "output_tokens",
                                  "configured_max_tokens", "output_truncated")}
            except Exception:                            # noqa: BLE001
                trunc = {}
        self.primary_failure = {"failed_stage": stage, "stage_index": idx,
                                "error_type": type(err).__name__,
                                "error_summary": _safe_error(err),
                                "worker_generation": generation,
                                "completed_stages": list(self._stages_done), **trunc}
        # 事件层用不含 "token" 子串的键名：SAFE_PAYLOAD 的敏感子串检查会拦下 *_tokens
        # （那条规则是为了挡 auth token，不能为了本功能放宽）。
        ev_trunc = {}
        if trunc:
            ev_trunc = {"output_truncated": bool(trunc.get("output_truncated")),
                        "truncated_role": trunc.get("role"),
                        "finish_reason": trunc.get("finish_reason"),
                        "output_size": trunc.get("output_tokens"),
                        "configured_output_limit": trunc.get("configured_max_tokens")}
            ev_trunc = {k: v for k, v in ev_trunc.items() if v is not None}
        base_sp = {"stage": stage, "stage_index": idx, "stage_count": len(self._research_stages),
                   "failed_stage": stage, "error_type": type(err).__name__,
                   "error_summary": _safe_error(err), "worker_generation": generation,
                   "completed_stage_count": len(self._stages_done), "human_review": True}
        sp = {**base_sp, **ev_trunc}
        try:
            with self._txn(None):
                self._emit("research_stage_failed", step_id=2, status="failed",
                           summary=f"stage failed: {stage}", safe_payload=sp)
                self.needs_human_review = True
                self.pending = None
                self._pending_obj = None
                self._to("failed")
                self._emit("run_failed", status="failed", summary="research run failed",
                           safe_payload={"failed_stage": stage, "error_type": type(err).__name__,
                                         "error_summary": _safe_error(err), "human_review": True,
                                         "completed_stage_count": len(self._stages_done)})
        except BaseException as sink_err:                # noqa: BLE001 — 次级失败不得覆盖 primary
            self.secondary_failure = {"where": "persist_failure_events",
                                      "error_type": type(sink_err).__name__,
                                      "error_summary": _safe_error(sink_err)}
            self.needs_human_review = True
            # 富诊断写不进去（例如 payload 被安全校验拒绝）时，**必须**退回最小载荷再收敛一次：
            # 否则事务回滚会把 run 永远留在 running（fail-open），这与 fail-closed 相悖。
            try:
                with self._txn(None):
                    self._emit("research_stage_failed", step_id=2, status="failed",
                               summary=f"stage failed: {stage}", safe_payload=dict(base_sp))
                    self.needs_human_review = True
                    self.pending = None
                    self._pending_obj = None
                    self._to("failed")
                    self._emit("run_failed", status="failed", summary="research run failed",
                               safe_payload={"failed_stage": stage,
                                             "error_type": type(err).__name__,
                                             "error_summary": _safe_error(err),
                                             "human_review": True,
                                             "completed_stage_count": len(self._stages_done)})
            except BaseException:                        # noqa: BLE001 — 已尽最大努力
                pass
        self._build_failure_manifest()                   # 诊断产物；写失败只作为 secondary

    def _build_failure_manifest(self):
        """失败诊断 Manifest（claims=[]，不新增证据，绝不冒充科研成功产物）。"""
        try:
            from pilot.research_contracts import ResearchFailureManifest
            pf = self.primary_failure or {}
            m = ResearchFailureManifest(
                run_id=self.run_id, failed_stage=pf.get("failed_stage", "unknown"),
                error_type=pf.get("error_type", "unknown"),
                error_summary=pf.get("error_summary", ""),
                completed_stages=list(pf.get("completed_stages", [])),
                evidence_count=len(self._spec.evidence_refs) if self._spec else 0,
                worker_generation=int(pf.get("worker_generation", 0)),
                # §7 截断诊断（只记录元数据，绝不记录被截断的输出正文）
                output_truncated=bool(pf.get("output_truncated")),
                truncated_role=pf.get("role"), finish_reason=pf.get("finish_reason"),
                output_tokens=pf.get("output_tokens"),
                configured_max_tokens=pf.get("configured_max_tokens")).finalize()
            self.failure_manifest = m.model_dump(mode="json")
        except BaseException as e:                       # noqa: BLE001
            self.secondary_failure = self.secondary_failure or {
                "where": "failure_manifest", "error_type": type(e).__name__,
                "error_summary": _safe_error(e)}

    def _finish_research(self):
        """所有阶段完成 → 生成唯一 Artifact + completed（持锁、原子）。"""
        self._assert_plan_unchanged()
        art = self._executor.build_artifact(ctx=self._research_ctx(), state=self._research_state)
        with self._txn(None):
            rec = {"artifact_id": f"art-{self.run_id}", "name": "research_artifact.json",
                   "kind": "json", "size_bytes": len(art.model_dump_json()),
                   "hash_short": art.content_hash[:8] + "…", "provenance_status": "verified",
                   "verifier_status": art.verifier_verdict, "fixture": art.fixture}
            self.artifacts.append(rec)
            self._emit("artifact_created", step_id=2, summary=rec["name"],
                       artifact_ids=[rec["artifact_id"]],
                       safe_payload={"artifact_name": rec["name"], "artifact_kind": "json",
                                     "size_bytes": rec["size_bytes"], "hash_short": rec["hash_short"],
                                     "provenance_status": "verified",
                                     "verifier_status": art.verifier_verdict,
                                     "shadow_verdict": art.shadow_verdict,
                                     "causal_tier": art.causal_tier,
                                     "claim_count": len(art.claims), "fixture": art.fixture,
                                     "is_simulation": True})
            self._emit("step_satisfied", step_id=2, status="satisfied", summary="研究链执行完成",
                       safe_payload={"remaining_gaps": []})
            self.completed_steps.add(2)
            self._to("completed")
            self._emit("run_completed", status="completed", summary="research run completed",
                       artifact_ids=[rec["artifact_id"]],
                       safe_payload={"verifier_verdict": art.verifier_verdict,
                                     "shadow_verdict": art.shadow_verdict,
                                     "causal_tier": art.causal_tier, "fixture": art.fixture})

    def _worker_valid(self, generation: int) -> bool:
        """持锁调用：该 worker 是否仍是唯一有效的执行者（generation 未被取代且未进入终态）。"""
        return generation == self._worker_generation and not HC.is_terminal(self.state)

    def _research_worker(self, generation: int):
        """逐阶段推进；每阶段之间是 pause/stop 的安全边界。异常一律收敛，不逃逸出线程。"""
        try:
            while True:
                with self._lock:
                    if not self._worker_valid(generation):
                        return
                    if self._pause_requested and self.state in ("running", "pausing"):
                        self._commit_paused_from_exec()
                        return
                    idx = self._exec_cursor
                    if idx >= len(self._research_stages):
                        try:
                            self._finish_research()
                        except BaseException as e:       # noqa: BLE001 — 收尾失败同样 fail-closed
                            self._commit_stage_failure("artifact_builder",
                                                       len(self._research_stages) - 1, e, generation)
                        return
                    self._open_reservations = 1
                try:
                    outcome = self._run_research_stage(idx, generation)
                finally:
                    with self._lock:
                        self._open_reservations = 0
                if outcome in ("failed", "paused", "discarded"):
                    return
        except BaseException as e:                       # noqa: BLE001 — 最后兜底，绝不产生未处理线程异常
            try:
                with self._lock:
                    if self._worker_valid(generation):
                        self._commit_stage_failure("worker", self._exec_cursor, e, generation)
            except BaseException:                        # noqa: BLE001
                pass
        finally:
            with self._lock:
                if generation == self._worker_generation:
                    self._exec_active = False
                    self._worker = None                  # 清理引用，不留悬挂 worker

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
            research = self.run_type == "research"
            if research and (self._spec_missing or self._spec is None or self._executor is None):
                # 恢复出的 research run 缺 spec/executor → fail-closed（不回退 demo，不回退真实模型）
                raise HC.ContractViolation("research run 缺少 spec/executor，拒绝执行（需重新注入后再批准）")
            async_mode = self._async_exec()
            with self._txn(h, fp):
                req.status = "granted"
                self._to("running")
                self._emit("approval_granted", step_id=2, status="running", summary="动作已批准",
                           safe_payload={"request_id": req.request_id, "action_hash": req.action_hash})
                self.pending = None
                self._pending_obj = None
                self._exec_cursor = 0 if research else _STAGE_TOOL
                if research:
                    # 批准瞬间冻结执行计划（冻结审批卡上展示过的冻结事实）
                    self._frozen_plan = self._freeze_plan(use_card_facts=True)
                if not research and not async_mode:
                    self._run_stages_inline()            # 同步：所有阶段 → completed（同一原子批）
            if research:
                # A.8.2a.3：approval_granted 已成功落盘之后，才签发绑定的执行授权。
                # 授权只在进程内交给 executor，绝不出现在 HTTP 请求/响应里。
                self._issue_approval_grant(req)
                # A.7.5.3.1：research run **始终异步**——与 gate / delay / executor 声明无关。
                # approve() 在此立即返回 running，8 个阶段由后台 worker 顺序推进。
                self._start_research_worker()
            elif async_mode:
                self._exec_active = True                 # 后台 worker 驱动，approve 立即返回 running
                self._start_worker()
            return self._cache(h, fp)

    def _issue_approval_grant(self, req):
        """在 approval_granted 落盘后签发授权，并交给支持 authorize() 的 executor。

        executor 会用全部绑定字段重新校验（凭证本身在同进程内并非不可伪造，
        真正的防线是那次比对）。executor 不支持 authorize 时静默跳过 —— 旧的
        fake executor 仍按原语义工作。
        """
        auth = getattr(self._executor, "authorize", None)
        if not callable(auth):
            return
        from pilot.approval_grant import issue_grant, ApprovalGrantError, _ISSUER
        facts = self._approval_facts or {}
        # A.8.2a.4a：必须**读到**刚落盘的 approval_granted 事件并取其真实 sequence。
        # 读取失败或找不到 → 不签发、不授权、不启动 worker（此前用 seq=-1 继续，是 fail-open）。
        try:
            evs = [e for e in self._store.list(self.run_id)
                   if e.event_type == "approval_granted"
                   and (e.safe_payload or {}).get("request_id") == req.request_id]
        except Exception as e:                               # noqa: BLE001
            raise ApprovalGrantError(
                f"无法读取 approval_granted 事件 → 拒绝授权：{type(e).__name__}") from e
        if not evs:
            raise ApprovalGrantError(
                "approval_granted 事件未持久化成功 → 拒绝授权（fail-closed）")
        seq = max(e.sequence for e in evs)
        grant = issue_grant(
            _ISSUER, run_id=self.run_id, request_id=req.request_id,
            action_hash=req.action_hash,
            preview_hash=str(facts.get("preview_hash") or ""),
            request_state_version=int(self._approval_request_state_version),
            granted_state_version=int(self.state_version),
            executor_id=getattr(self._executor, "executor_id", ""),
            policy_id=str(facts.get("budget_policy_id") or ""),
            granted_event_sequence=int(seq))
        auth(grant)                                          # 不符即抛 → 不会进入执行
        self.approval_grant = grant

    def _async_exec(self) -> bool:
        """是否需要后台 worker：本身有门控/延迟，或 executor 声明了阻塞阶段（research）。"""
        return (self._exec_gate is not None or self._exec_delay_ms > 0
                or bool(getattr(self._executor, "has_blocking_stages", False)))

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
    def _start_worker(self, research: bool = False):
        if research:
            return self._start_research_worker()
        self._worker = threading.Thread(target=self._worker_loop, name=f"hitl-{self.run_id}", daemon=True)
        self._worker.start()

    def _start_research_worker(self):
        """持锁调用：启动**唯一**后台 research worker（generation 由服务端产生，客户端不可指定）。

        终态不启动；每次启动都递增 generation，因此旧 worker 的迟到结果一律被判定为 stale。
        """
        if HC.is_terminal(self.state):
            return None
        self._worker_generation += 1
        gen = self._worker_generation
        self._exec_active = True
        frag = "".join(ch for ch in self.run_id if ch.isalnum() or ch in "-_")
        for pre in ("hitl-research-", "hitl-"):        # 避免与线程名前缀重复
            if frag.startswith(pre):
                frag = frag[len(pre):]
                break
        t = threading.Thread(target=self._research_worker, args=(gen,),
                             name=f"hitl-research-{frag[-24:]}-g{gen}", daemon=True)
        self._worker = t
        t.start()
        return gen

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
            research = self.run_type == "research"
            limit = len(self._research_stages) if research else _STAGE_DONE
            resumes_exec = (target == "running" and self._exec_cursor < limit)
            with self._txn(h, fp):
                self._to(target)                             # 还原到暂停前状态（含待处理请求）
                self._resume_target = None
                self._pause_requested = False                # resume 只恢复一次
                self._emit("run_resumed", status=self.state, summary="run resumed",
                           safe_payload={"phase": self.state, "resume_cursor": self._exec_cursor})
                if resumes_exec and not research and not self._async_exec():
                    self._run_stages_inline()                # 恢复后（无 gate）同步跑完剩余阶段
            if resumes_exec and research:
                self._start_research_worker()                # 始终异步；从游标继续，不重复已完成阶段
            elif resumes_exec and self._async_exec():
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
        snap = {"run_id": self.run_id, "control_state": self.state,
                "state_version": self.state_version, "pending": self.pending,
                "tool_calls": self.tool_calls, "artifact_count": len(self.artifacts),
                "open_reservations": self._open_reservations, "lifecycle": dict(self.lifecycle),
                "needs_human_review": self.needs_human_review, "run_type": self.run_type}
        if self.run_type == "research":
            stages = list(self._research_stages)
            idx = int(self._exec_cursor)
            snap["research"] = {
                "executor_id": (self._frozen_plan or {}).get("executor_id")
                               or (self._spec.executor_id if self._spec else None),
                "stages": stages, "stages_done": list(self._stages_done),
                "current_stage": stages[idx] if 0 <= idx < len(stages) else None,
                "stage_index": idx, "stage_count": len(stages),
                "question_hash": (self._frozen_plan or {}).get("question_hash"),
                "policy_hash": (self._frozen_plan or {}).get("policy_hash"),
                "plan_frozen": self._frozen_plan is not None,
                "answer": self._answer, "answer_hash": self._answer_hash,
                "verifier_verdict": self._research_state.get("verifier_verdict"),
                "shadow_verdict": self._research_state.get("shadow_verdict"),
                "causal_tier": self._research_state.get("causal_tier"),
                "claim_count": len(self._research_state.get("claims", [])),
                "fixture": True,
                "worker_generation": self._worker_generation,
                "worker_active": self._exec_active,
                "interrupted_stage": self.interrupted_stage,
                "failed_stage": (self.primary_failure or {}).get("failed_stage"),
                "error_type": (self.primary_failure or {}).get("error_type"),
                "error_summary": (self.primary_failure or {}).get("error_summary"),
                "secondary_failure": self.secondary_failure,
                "failure_manifest": self.failure_manifest}
        return snap

    # ============================ 从持久化事件恢复（§2） ============================
    @classmethod
    def recover(cls, run_id: str, event_store, *, clock=now,
                exec_delay_ms: int = 0, exec_gate: Optional[threading.Event] = None,
                spec=None, executor=None) -> "HitlRun":
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
        r = cls(run_id, event_store, clock=clock, exec_delay_ms=exec_delay_ms, exec_gate=exec_gate,
                spec=spec, executor=executor)
        # 旧事件没有 run_type → 按既有 demo/legacy 安全处理（绝不擅自升级为 research）
        first_sp = events[0].safe_payload if events else {}
        if first_sp.get("run_type") == "research":
            r.run_type = "research"
            if executor is not None:
                r._research_stages = tuple(getattr(executor, "stages", ()) or ())
            r._spec_missing = spec is None or executor is None   # 缺 spec → 只恢复状态，不得继续执行
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
        if r._inflight_stage is not None:
            # stage_started 之后没有 completed/failed → 该阶段执行结果不确定（可能已产生外部副作用）。
            # 不自动重放、不自动推进、不产成功产物；要求人工审查。
            r.needs_human_review = True
            r.interrupted_stage = r._inflight_stage
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
            if sp.get("note"):                       # research：恢复澄清提问本体
                self.pending["prompt"] = sp["note"]
        elif et == "clarification_answered":
            ans = str(sp.get("answer", ""))
            self._answer = ans.split(",")[0] if ans and not ans.startswith("其他：") else \
                ans.replace("其他：", "") or self._answer
            self._answer_hash = sp.get("answer_hash") or self._answer_hash
            self.pending = None
            self._pending_obj = None
        elif et == "approval_requested":
            if sp.get("executor_id"):        # research：记录批准时的冻结计划，供恢复后复核
                self._recovered_plan = {"executor_id": sp.get("executor_id"),
                                        "policy_hash": sp.get("policy_hash"),
                                        "question_hash": sp.get("question_hash"),
                                        "answer_hash": self._answer_hash}
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
                # research 从阶段 0 开始；demo 保持原 _STAGE_TOOL 语义
                self._exec_cursor = 0 if self.run_type == "research" else _STAGE_TOOL
                if self.run_type == "research" and self._frozen_plan is None:
                    self._frozen_plan = dict(self._recovered_plan or {})   # 恢复冻结计划
            self.pending = None
            self._pending_obj = None
        elif et == "research_stage_started":
            # 记录"已开始但尚未收敛"的阶段；若日志到此为止 → 不确定执行，恢复后需人工审查
            self._inflight_stage = sp.get("stage")
        elif et == "research_stage_failed":
            self._inflight_stage = None
            self.primary_failure = {"failed_stage": sp.get("failed_stage") or sp.get("stage"),
                                    "stage_index": sp.get("stage_index"),
                                    "error_type": sp.get("error_type"),
                                    "error_summary": sp.get("error_summary"),
                                    "worker_generation": sp.get("worker_generation"),
                                    "completed_stages": list(self._stages_done)}
            self.needs_human_review = True
        elif et == "research_stage_completed":
            self._inflight_stage = None
            stage = sp.get("stage")
            if stage:
                self._stages_done.append(stage)
            if sp.get("stage_index") is not None:
                self._exec_cursor = int(sp["stage_index"]) + 1
            for k in ("verifier_verdict", "causal_tier", "shadow_verdict", "evidence_count"):
                if sp.get(k) is not None:
                    self._research_state[k] = sp[k]
            if sp.get("claim_count") is not None:
                self._research_state["claim_count"] = sp["claim_count"]
            if stage in ("synthesizer", "verifier", "claim_extractor"):
                self.tool_calls += 1
                self.lifecycle["requested"] += 1
                self.lifecycle["executed"] += 1
                self.lifecycle["tool_returned"] += 1
            if stage == "evidence_accumulator":
                self.lifecycle["observed"] += 1
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
