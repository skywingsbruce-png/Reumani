"""A.8.2a —— 惰性、可审计的 ProviderRegistry。

为什么需要它：受控科研链原先靠「启动脚本手工构造三个 GatedModel 并按顺序注入」维系安全，
依赖约定而非结构。Registry 把 provider 的**声明**（ProviderSpec）与**构造**（factory）分开：

- 构造 Registry、注册 spec 时**不碰网络、不读 key、不建客户端**；
- 只有显式 `resolve` 才调用 factory，且同一 provider_id 只构造一次（线程安全）；
- factory 必须在内部完成 Gate 包装才能返回 —— raw client 永不经公共接口外泄；
- 未知 provider/role/model、未核实价格、缺 timeout/retry/max_tokens/output mode 一律 fail-closed；
- **没有任何默认回退模型**：没注册就是没有，不会悄悄退到 Opus 或 DeepSeek。

范围诚实声明：本模块只服务**受控科研链**。`ssc_pi_agent.py` 的三个模块级付费单例
**本轮不动**，见 `pilot/provider_migration.py`（A.8.2b 处理）。
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

from pydantic import ConfigDict, Field

from schemas import _Strict


class ProviderRegistryError(RuntimeError):
    """Registry 配置/解析失败 → fail-closed，绝不回退默认模型。"""


class ProviderSpec(_Strict):
    """provider 的**声明**。只有数据，没有客户端。"""
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: str
    provider: str                     # anthropic / deepseek / fake
    model_id: str
    role: str                         # synthesizer / verifier / claim_extractor / ...
    provider_mode: str                # native_json_schema / json_object_only
    timeout: float
    max_tokens: int
    retry_policy: str                 # 本项目恒为 "no_retry"
    pricing_policy_id: str            # 关联的 BudgetPolicy
    output_contract_id: str
    capabilities: dict = Field(default_factory=dict)
    enabled: bool = True

    def validate_spec(self) -> None:
        """resolve 之前必须逐项通过；任何一项缺失都不允许构造客户端。"""
        if not self.enabled:
            raise ProviderRegistryError(f"{self.provider_id} 未启用（enabled=false）")
        for f in ("provider_id", "provider", "model_id", "role", "provider_mode",
                  "retry_policy", "pricing_policy_id", "output_contract_id"):
            if not str(getattr(self, f) or "").strip():
                raise ProviderRegistryError(f"{self.provider_id} 缺少必填字段 {f}")
        if self.retry_policy != "no_retry":
            raise ProviderRegistryError(
                f"{self.provider_id} 的 retry_policy={self.retry_policy!r} —— 本项目禁止重试")
        if self.timeout is None or self.timeout <= 0:
            raise ProviderRegistryError(f"{self.provider_id} 缺少有效 timeout")
        if not self.max_tokens or self.max_tokens <= 0:
            raise ProviderRegistryError(f"{self.provider_id} 缺少有效 max_tokens")
        # 价格必须已核实（未登记模型直接拒绝，不猜价）
        from pilot import prices
        try:
            prices.price_for(self.model_id)
        except Exception as e:                                # noqa: BLE001
            raise ProviderRegistryError(
                f"{self.provider_id} 的模型 {self.model_id!r} 价格未核实：{str(e)[:100]}") from e
        # 输出契约与 provider 能力必须存在且与本 spec 一致
        from pilot.role_contracts import contract_for
        try:
            c = contract_for(self.role)
        except KeyError as e:
            raise ProviderRegistryError(str(e)) from e
        if c.contract_id != self.output_contract_id:
            raise ProviderRegistryError(
                f"{self.provider_id} 声明契约 {self.output_contract_id}，但角色 {self.role} "
                f"的契约是 {c.contract_id}")
        if c.max_output_tokens != self.max_tokens:
            raise ProviderRegistryError(
                f"{self.provider_id} 的 max_tokens={self.max_tokens} 与契约 "
                f"{c.contract_id} 的 {c.max_output_tokens} 不一致")
        if self.provider_mode not in ("native_json_schema", "json_object_only"):
            raise ProviderRegistryError(
                f"{self.provider_id} 的 provider_mode={self.provider_mode!r} 不允许用于受控链")


class ProviderHandle(_Strict):
    """已解析的受控 provider。`gated_model` 一定是包过 Gate 的对象。"""
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    provider_id: str
    role: str
    gated_model: object
    provider_mode: str
    output_contract: object
    cost_estimator: Callable
    model_id: str
    max_tokens: int
    metadata: dict = Field(default_factory=dict)


_WRAPPED_FLAG = "_reumani_gated"


def _is_gated(obj) -> bool:
    """只承认真正的 GatedModel 包装；raw client 一律拒绝返回。"""
    from pilot.hard_gate import GatedModel
    return isinstance(obj, GatedModel) or bool(getattr(obj, _WRAPPED_FLAG, False))


class ProviderRegistry:
    """声明与构造分离的 provider 注册表。线程安全、惰性、fail-closed。"""

    def __init__(self):
        self._specs: dict = {}
        self._factories: dict = {}
        self._handles: dict = {}
        self._lock = threading.RLock()
        self._closed = False

    # ---------------------------------------------------------------- 注册（零构造）
    def register(self, spec: ProviderSpec, factory: Callable) -> None:
        """注册一个声明 + 惰性工厂。**此处绝不调用 factory**。"""
        if self._closed:
            raise ProviderRegistryError("Registry 已关闭，禁止注册")
        if not callable(factory):
            raise ProviderRegistryError(f"{spec.provider_id} 的 factory 不可调用")
        with self._lock:
            if spec.provider_id in self._specs:
                raise ProviderRegistryError(f"provider_id 重复注册：{spec.provider_id}")
            # 同一 role 不允许出现第二个隐式默认实现
            dup = [s for s in self._specs.values() if s.role == spec.role and s.enabled]
            if dup and spec.enabled:
                raise ProviderRegistryError(
                    f"角色 {spec.role} 已有实现 {dup[0].provider_id}，拒绝注册第二个隐式默认")
            self._specs[spec.provider_id] = spec
            self._factories[spec.provider_id] = factory

    # ---------------------------------------------------------------- 解析（惰性构造）
    def resolve(self, provider_id: str, role: str) -> ProviderHandle:
        if self._closed:
            raise ProviderRegistryError("Registry 已关闭，禁止 resolve")
        with self._lock:
            spec = self._specs.get(provider_id)
            if spec is None:
                raise ProviderRegistryError(f"未注册的 provider_id：{provider_id!r}")
            if spec.role != role:
                # 角色由**声明**决定，绝不按 Prompt 或调用顺序推断
                raise ProviderRegistryError(
                    f"{provider_id} 声明角色 {spec.role!r}，与请求的 {role!r} 不符")
            cached = self._handles.get(provider_id)
            if cached is not None:
                return cached
            spec.validate_spec()                       # 构造之前逐项验证
            handle = self._build(spec)
            self._handles[provider_id] = handle        # 只有成功才缓存
            return handle

    def resolve_role(self, role: str) -> ProviderHandle:
        with self._lock:
            matches = [s for s in self._specs.values() if s.role == role and s.enabled]
        if not matches:
            raise ProviderRegistryError(f"角色 {role!r} 没有已启用的 provider（fail-closed）")
        if len(matches) > 1:
            raise ProviderRegistryError(f"角色 {role!r} 有多个实现，拒绝隐式选择")
        return self.resolve(matches[0].provider_id, role)

    def _build(self, spec: ProviderSpec) -> ProviderHandle:
        from pilot.live_cost import estimate_call_cost
        from pilot.role_contracts import contract_for
        factory = self._factories[spec.provider_id]
        try:
            gated = factory(spec)                      # 唯一允许构造客户端的地方
        except Exception as e:                         # noqa: BLE001 —— 失败不得缓存半成品
            raise ProviderRegistryError(
                f"{spec.provider_id} 构造失败：{type(e).__name__}: {str(e)[:120]}") from e
        if gated is None:
            raise ProviderRegistryError(f"{spec.provider_id} 的 factory 返回了 None")
        if not _is_gated(gated):
            # raw client 绝不允许经公共接口外泄
            raise ProviderRegistryError(
                f"{spec.provider_id} 的 factory 返回了**未经 Gate 包装**的客户端 → 拒绝")
        return ProviderHandle(
            provider_id=spec.provider_id, role=spec.role, gated_model=gated,
            provider_mode=spec.provider_mode, output_contract=contract_for(spec.role),
            cost_estimator=estimate_call_cost, model_id=spec.model_id,
            max_tokens=spec.max_tokens,
            metadata={"provider": spec.provider, "pricing_policy_id": spec.pricing_policy_id,
                      "timeout": spec.timeout, "retry_policy": spec.retry_policy})

    # ---------------------------------------------------------------- 其它
    def list_specs(self) -> list:
        with self._lock:
            return [self._specs[k] for k in sorted(self._specs)]

    def validate(self) -> None:
        """全量校验所有声明；**不构造任何客户端**。"""
        for s in self.list_specs():
            if s.enabled:
                s.validate_spec()

    def resolved_count(self) -> int:
        with self._lock:
            return len(self._handles)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._handles.clear()

    @property
    def closed(self) -> bool:
        return self._closed


__all__ = ["ProviderRegistry", "ProviderSpec", "ProviderHandle", "ProviderRegistryError"]
