"""A.8.2a.3 —— DeferredRegistryResearchExecutor：批准之后才 resolve 付费 provider。

HitlRun 在**批准之前**就要调用 `approval_facts()` / `execution_preview()` 来生成审批卡，
而 `GatedResearchExecutor.from_registry()` 一旦调用就会立即 resolve 三个角色。
两者直接相接必然导致「人还没批准，付费客户端已经建好」。

本类把这两件事分开：

  批准前：approval_facts / execution_preview / CostEstimate / spec 元数据
          —— 全部只依赖 ProviderSpec 与冻结证据，**不 resolve、不读 key、不建客户端**。
  批准后：authorize(grant) 重新校验全部绑定字段；真正的 resolve 推迟到**第一次 run_stage**。

deny / cancel / stop / pause 都不会触达 authorize，因此也不会 resolve。
"""

from __future__ import annotations

import threading

from pilot.approval_grant import (ApprovalGrant, ApprovalGrantError, PendingApprovalBinding,
                                  compare_binding)
from pilot.gated_research_executor import (EXECUTOR_ID, STAGES, GatedResearchExecutor,
                                           ExecutorConfigError)
from pilot.research_results import ROLE_MAX_TOKENS
from pilot.role_contracts import contract_for


class DeferredRegistryResearchExecutor:
    """实现 ResearchExecutor Protocol；把 provider 解析推迟到批准之后。"""

    executor_id = EXECUTOR_ID
    stages = STAGES
    has_blocking_stages = True

    def __init__(self, *, registry, gate, evidence_loader,
                 budget_policy_id: str = "research-budget-policy-v2",
                 capabilities=None, approval_event_lookup=None):
        self._registry = registry
        self._gate = gate
        self._loader = evidence_loader
        self._budget_policy_id = budget_policy_id
        self._capabilities = capabilities
        # 只读的 approval_granted 事件查询口：用于核实 Grant 指向的事件真实存在。
        # 不得允许写 EventStore，也不得来自 HTTP。
        self._event_lookup = approval_event_lookup
        self._binding: PendingApprovalBinding | None = None
        self._grant: ApprovalGrant | None = None
        self._revoked = False                  # deny / cancel / stop 之后绑定与授权失效
        self._inner: GatedResearchExecutor | None = None
        self._lock = threading.RLock()
        self.resolved_at_stage = None          # 记录首次 resolve 发生在哪个阶段（可审计）

    # ------------------------------------------------------------ 审批请求阶段：冻结绑定
    def bind_pending_approval(self, binding: PendingApprovalBinding) -> None:
        """由 HitlRun 在创建 pending approval request 后调用。只允许绑定一次。"""
        if not isinstance(binding, PendingApprovalBinding):
            raise ApprovalGrantError("bind_pending_approval 需要 PendingApprovalBinding")
        with self._lock:
            if self._revoked:
                raise ApprovalGrantError("运行已 deny/cancel/stop，拒绝绑定新的审批请求")
            if self._binding is not None:
                if self._binding.binding() != binding.binding():
                    raise ApprovalGrantError("已存在不同的审批绑定，拒绝覆盖（fail-closed）")
                return                          # 完全相同 → 幂等
            self._binding = binding

    def revoke(self, reason: str = "denied_or_stopped") -> None:
        """deny / cancel / stop 之后使绑定与授权失效。"""
        with self._lock:
            self._revoked = True
            self._binding = None
            self._grant = None

    @property
    def pending_binding(self):
        return self._binding

    # ------------------------------------------------------------ 批准前：零 resolve
    def _preview_only(self):
        """用**声明**构造一个只做预览的 executor：三个角色用 spec 的占位描述，不 resolve。"""
        from pilot.provider_registry import ProviderRegistryError

        class _SpecStub:
            """只暴露 model_id / role / max_tokens —— 预览与费用估算需要的全部信息。"""

            def __init__(self, spec):
                object.__setattr__(self, "_role", spec.role)
                object.__setattr__(self, "_model_id", spec.model_id)
                object.__setattr__(self, "_max_tokens", spec.max_tokens)

            def invoke(self, *a, **k):        # 预览路径永远不会走到这里
                raise ExecutorConfigError("预览阶段不得调用 provider（未授权）")

        stubs = {}
        for spec in self._registry.list_specs():
            if spec.role in ("synthesizer", "verifier", "claim_extractor") and spec.enabled:
                stubs[spec.role] = _SpecStub(spec)
        missing = {"synthesizer", "verifier", "claim_extractor"} - set(stubs)
        if missing:
            raise ProviderRegistryError(f"Registry 缺少角色声明：{sorted(missing)}")
        return GatedResearchExecutor(
            synthesizer=stubs["synthesizer"], verifier=stubs["verifier"],
            claim_extractor=stubs["claim_extractor"], gate=self._gate,
            evidence_loader=self._loader, budget_policy_id=self._budget_policy_id,
            capabilities=self._capabilities)

    def approval_facts(self) -> dict:
        return self.execution_preview().model_dump(mode="json")

    def execution_preview(self):
        with self._lock:
            if self._inner is not None:
                return self._inner.execution_preview()
        return self._preview_only().execution_preview()     # 仍然零 resolve

    # ------------------------------------------------------------ 授权
    def authorize(self, grant: ApprovalGrant) -> None:
        """**第一次授权也走完整校验**（A.8.2a.3 只比 3 项，是被驳回的缺陷）。

        比对 Grant ↔ 已冻结 binding 的全部 7 个字段，再核实 approval_granted 事件
        真实存在且内容一致，最后确认当前预览未漂移。任一不符 → fail-closed。
        """
        if not isinstance(grant, ApprovalGrant):
            raise ApprovalGrantError("authorize() 需要 ApprovalGrant（fail-closed）")
        with self._lock:
            if self._revoked:
                raise ApprovalGrantError("运行已 deny/cancel/stop，拒绝授权")
            binding = self._binding
            existing = self._grant
        # 1) 逐项比对审批**请求**时冻结的绑定（含 run_id / request_id / action_hash /
        #    preview_hash / request_state_version / executor_id / policy_id）
        compare_binding(grant, binding)
        # 2) 授权本身的取值合法性
        if grant.granted_event_sequence < 0:
            raise ApprovalGrantError("granted_event_sequence < 0 → 拒绝授权")
        if grant.granted_state_version < grant.request_state_version:
            raise ApprovalGrantError("granted_state_version 早于 request_state_version → 拒绝")
        if grant.executor_id != self.executor_id:
            raise ApprovalGrantError("executor_id 与当前 executor 不符 → 拒绝授权")
        if grant.policy_id != self._budget_policy_id:
            raise ApprovalGrantError("policy_id 与当前预算策略不符 → 拒绝授权")
        # 3) 核实 approval_granted 事件真实存在且内容一致（查询失败即拒绝，绝不放行）
        self._verify_granted_event(grant)
        # 4) 当前预览必须仍与授权一致（零 resolve）
        if self.execution_preview().preview_hash != grant.preview_hash:
            raise ApprovalGrantError("当前审批预览已漂移 → 拒绝执行（provider 调用为 0）")
        with self._lock:
            if existing is not None and existing.identity() != grant.identity():
                raise ApprovalGrantError("已存在不同的授权，拒绝覆盖（fail-closed）")
            self._grant = grant

    def _verify_granted_event(self, grant: ApprovalGrant) -> None:
        """用只读查询口核实事件。**没有查询口 = 无法核实 = 拒绝授权**（不是放行）。"""
        lookup = self._event_lookup
        if not callable(lookup):
            raise ApprovalGrantError(
                "缺少 approval_granted 事件查询口，无法核实授权 → 拒绝（fail-closed）")
        try:
            ev = lookup(grant.run_id, grant.granted_event_sequence)
        except Exception as e:                              # noqa: BLE001
            raise ApprovalGrantError(
                f"读取 approval_granted 事件失败 → 拒绝授权：{type(e).__name__}") from e
        if ev is None:
            raise ApprovalGrantError(
                f"sequence={grant.granted_event_sequence} 上不存在事件 → 拒绝授权")
        if getattr(ev, "event_type", None) != "approval_granted":
            raise ApprovalGrantError(
                f"sequence={grant.granted_event_sequence} 指向的不是 approval_granted → 拒绝")
        if getattr(ev, "run_id", None) != grant.run_id:
            raise ApprovalGrantError("事件 run_id 与授权不符 → 拒绝授权")
        sp = getattr(ev, "safe_payload", None) or {}
        if sp.get("request_id") != grant.request_id:
            raise ApprovalGrantError("事件 request_id 与授权不符 → 拒绝授权")
        if sp.get("action_hash") != grant.action_hash:
            raise ApprovalGrantError("事件 action_hash 与授权不符 → 拒绝授权")

    @property
    def authorized(self) -> bool:
        return self._grant is not None

    # ------------------------------------------------------------ 批准后：首次 run_stage 才 resolve
    def _ensure_resolved(self, stage):
        with self._lock:
            if self._inner is not None:
                return self._inner
            if self._grant is None:
                raise ApprovalGrantError(
                    "未经授权不得执行 run_stage（provider 调用为 0，fail-closed）")
            inner = GatedResearchExecutor.from_registry(
                registry=self._registry, gate=self._gate, evidence_loader=self._loader,
                budget_policy_id=self._budget_policy_id)
            if self._capabilities is not None:
                inner._capabilities = dict(self._capabilities)
            # 授权绑定的预览必须仍然成立（resolve 之后再核一次，防止中途漂移）
            if inner.execution_preview().preview_hash != self._grant.preview_hash:
                raise ApprovalGrantError("resolve 后预览发生漂移 → 拒绝执行")
            self._inner = inner
            self.resolved_at_stage = stage
            return inner

    # ------------------------------------------------------------ Protocol
    def run_stage(self, *, stage, ctx, state, emit=None):
        inner = self._ensure_resolved(stage)
        return inner.run_stage(stage=stage, ctx=ctx, state=state, emit=emit)

    def build_artifact(self, *, ctx, state):
        if self._inner is None:
            raise ApprovalGrantError("未授权/未执行，不得生成 Artifact")
        return self._inner.build_artifact(ctx=ctx, state=state)

    # ---- 供审计/测试读取（不构造任何东西） ----
    @property
    def role_calls(self):
        return self._inner.role_calls if self._inner else {
            "synthesizer": 0, "verifier": 0, "claim_extractor": 0}

    def model_call_count(self) -> int:
        return self._inner.model_call_count() if self._inner else 0

    @property
    def provider_handles(self):
        return self._inner.provider_handles if self._inner else {}

    @property
    def cost_estimates(self):
        return self._inner.cost_estimates if self._inner else {}

    @property
    def enforcement(self):
        return self._inner.enforcement if self._inner else {}

    @property
    def artifacts_built(self):
        return self._inner.artifacts_built if self._inner else 0


__all__ = ["DeferredRegistryResearchExecutor"]
