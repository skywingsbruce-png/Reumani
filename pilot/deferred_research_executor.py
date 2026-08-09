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

from pilot.approval_grant import ApprovalGrant, ApprovalGrantError
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
                 capabilities=None):
        self._registry = registry
        self._gate = gate
        self._loader = evidence_loader
        self._budget_policy_id = budget_policy_id
        self._capabilities = capabilities
        self._grant: ApprovalGrant | None = None
        self._inner: GatedResearchExecutor | None = None
        self._lock = threading.RLock()
        self.resolved_at_stage = None          # 记录首次 resolve 发生在哪个阶段（可审计）

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
        """重新校验**全部**绑定字段。这才是真正的安全防线（凭证本身可被同进程伪造）。"""
        if not isinstance(grant, ApprovalGrant):
            raise ApprovalGrantError("authorize() 需要 ApprovalGrant（fail-closed）")
        pv = self.execution_preview()                       # 当前真实预览（零 resolve）
        expected = {"preview_hash": pv.preview_hash, "executor_id": self.executor_id,
                    "policy_id": self._budget_policy_id}
        actual = {"preview_hash": grant.preview_hash, "executor_id": grant.executor_id,
                  "policy_id": grant.policy_id}
        for k, v in expected.items():
            if actual[k] != v:
                raise ApprovalGrantError(
                    f"授权与当前审批事实不符（{k}）→ 拒绝执行（provider 调用为 0）")
        with self._lock:
            if self._grant is not None and self._grant.binding() != grant.binding():
                raise ApprovalGrantError("已存在不同的授权，拒绝覆盖（fail-closed）")
            self._grant = grant

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
