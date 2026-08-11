"""A.8.2b.1 §2 —— 显式的 `LegacyProviderFactory`（注册零构造，resolve 才构造）。

与 `ProviderRegistry`（受控科研链）并列，但服务的是 legacy 的 5 个角色。
两者刻意不共用一个实例：受控链的预算/契约约束不适用于旧 debate/judge 路径，
混在一起会让"受控"这两个字失去意义。

结构保证：
- import / 注册阶段**不构造客户端**（`resolve_count() == 0`）；
- 客户端类（ChatOpenAI / ChatAnthropic）在 factory 函数**内部**惰性 import；
- key 由显式 `LegacyRuntimeConfig` 注入，或在 resolve 边界读环境 —— 绝不在 import 期；
- 未知 role / model / provider 一律 fail-closed，**没有默认回退模型**；
- 缺 key 在 resolve 时明确失败（占位 key 不算已配置）；
- `max_retries=0`、timeout / max_tokens 有限；
- pro / con / judge 身份独立（各自的缓存槽，互不复用对象）；
- 并发 resolve 同一角色只构造一次；
- 构造失败**不缓存半成品**，下次仍会重新尝试并再次失败。

本阶段 Factory **不接入任何现有消费者**，只用 fake 验证。
`ssc_pi_agent` 的三个单例原样保留。
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

from pilot.legacy_provider_specs import (LEGACY_ROLES, LegacyProviderSpec,
                                         LegacyProviderSpecError, all_specs, spec_for)
from pilot.legacy_runtime_config import LegacyRuntimeConfig, LegacyRuntimeConfigError


class LegacyProviderFactoryError(RuntimeError):
    """legacy factory 解析失败 → fail-closed。"""


class LegacyProviderHandle:
    """已解析的 legacy provider。**故意不是 pydantic 模型**：它持有真实客户端对象，
    不应该被 `model_dump()` 顺手序列化出去。审计只能通过 `resolved_snapshot()`。
    """

    __slots__ = ("provider_id", "role", "model_id", "provider", "temperature",
                 "max_tokens", "timeout", "gated", "_client")

    def __init__(self, *, spec: LegacyProviderSpec, client, gated: bool):
        self.provider_id = spec.provider_id
        self.role = spec.role
        self.model_id = spec.model_id
        self.provider = spec.provider
        self.temperature = spec.temperature
        self.max_tokens = spec.max_tokens
        self.timeout = spec.timeout
        self.gated = gated
        self._client = client

    @property
    def client(self):
        return self._client

    def __repr__(self) -> str:                      # noqa: D105
        return (f"LegacyProviderHandle(role={self.role!r}, "
                f"provider_id={self.provider_id!r}, gated={self.gated})")


class LegacyProviderFactory:
    """声明与构造分离的 legacy provider 工厂。线程安全、惰性、fail-closed。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._specs: dict = {}
        self._factories: dict = {}
        self._resolved: dict = {}
        self._resolve_count = 0
        self._closed = False

    # ------------------------------------------------------------ 注册（零构造）
    def register_specs(self, specs=None, *, client_factory: Optional[Callable] = None) -> int:
        """登记声明。**不构造任何客户端**，不读 key，不碰网络。

        `client_factory(spec, config) -> client` 可注入（测试用 fake）。
        不注入则使用内置的真实客户端工厂——但它同样只在 resolve 时才被调用。
        """
        with self._lock:
            self._require_open()
            for s in (all_specs() if specs is None else specs):
                s.validate_spec()
                if s.role in self._specs:
                    raise LegacyProviderFactoryError(f"角色重复注册：{s.role}")
                self._specs[s.role] = s
                self._factories[s.role] = client_factory or _default_client_factory
            return len(self._specs)

    # ------------------------------------------------------------ 解析（才构造）
    def resolve_model(self, role: str, runtime_config: LegacyRuntimeConfig):
        """按角色解析。只有这里允许构造客户端。

        并发调用同一角色只构造一次；构造失败不缓存半成品。
        """
        if role not in LEGACY_ROLES:
            raise LegacyProviderFactoryError(
                f"未知 legacy 角色 {role!r}（不提供默认回退模型）")
        if runtime_config is None:
            raise LegacyProviderFactoryError("必须显式传入 LegacyRuntimeConfig（不隐式读环境）")
        with self._lock:
            self._require_open()
            spec = self._specs.get(role)
            if spec is None:
                raise LegacyProviderFactoryError(
                    f"角色 {role!r} 未注册；请先 register_specs()（不自动补注册）")
            hit = self._resolved.get(role)
            if hit is not None:
                return hit
            spec.validate_spec()
            # key 校验放在构造之前：缺 key 必须在**构造之前**就失败。
            if not runtime_config.is_configured(spec.provider):
                raise LegacyProviderFactoryError(
                    f"{spec.provider} 的 API key 未配置 → 拒绝构造 {spec.provider_id}"
                    "（占位 key 不算已配置）")
            client = self._factories[role](spec, runtime_config)
            if client is None:
                raise LegacyProviderFactoryError(f"{spec.provider_id} 的工厂返回了 None")
            handle = LegacyProviderHandle(spec=spec, client=client,
                                          gated=_looks_gated(client))
            if spec.gated_required and not handle.gated:
                # 本阶段不接入生产，因此这里只记录、不放行到"已解析"缓存之外的地方；
                # 但仍然拒绝把一个未包 Gate 的真实客户端当成可用 handle 返回。
                raise LegacyProviderFactoryError(
                    f"{spec.provider_id} 声明 gated_required=True，但工厂返回的对象未包 Gate")
            self._resolved[role] = handle          # 只有完全成功才写缓存
            self._resolve_count += 1
            return handle

    # ------------------------------------------------------------ 只读审计
    def resolved_snapshot(self) -> tuple:
        """审计用只读快照。**不含 key、不含 Prompt、不含完整客户端配置**，
        也**不会**触发任何解析（未解析的角色不出现在快照里）。
        """
        with self._lock:
            return tuple(
                {"role": h.role, "provider_id": h.provider_id, "provider": h.provider,
                 "model_id": h.model_id, "temperature": h.temperature,
                 "max_tokens": h.max_tokens, "timeout": h.timeout, "gated": h.gated}
                for h in self._resolved.values())

    def registered_roles(self) -> tuple:
        with self._lock:
            return tuple(sorted(self._specs))

    def resolve_count(self) -> int:
        """已成功构造的客户端数量。扫描器断言用。"""
        with self._lock:
            return self._resolve_count

    def is_resolved(self, role: str) -> bool:
        with self._lock:
            return role in self._resolved

    def close(self) -> None:
        """释放已解析的客户端引用。close 之后不允许再 resolve。"""
        with self._lock:
            for h in self._resolved.values():
                closer = getattr(h.client, "close", None)
                if callable(closer):
                    try:
                        closer()
                    except Exception:               # noqa: BLE001 —— close 不得掩盖原错误
                        pass
            self._resolved.clear()
            self._closed = True

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def _require_open(self):
        if self._closed:
            raise LegacyProviderFactoryError("factory 已 close，拒绝继续使用")


# ---------------------------------------------------------------------------
# 真实客户端工厂。**只在 resolve_model 里被调用**，客户端类在函数内部 import。
# 本阶段没有任何生产调用者。
# ---------------------------------------------------------------------------
def _default_client_factory(spec: LegacyProviderSpec, config: LegacyRuntimeConfig):
    """构造真实客户端并立刻包 Gate。key 在这里（resolve 边界）才被取出。"""
    key = config.secret_for(spec.provider)          # 缺 key / 占位 key 在此抛错
    if spec.provider == "deepseek":
        from langchain_openai import ChatOpenAI     # 惰性 import
        raw = ChatOpenAI(model=spec.model_id, api_key=key,
                         base_url="https://api.deepseek.com",
                         temperature=spec.temperature, timeout=spec.timeout,
                         max_retries=0, max_tokens=spec.max_tokens)
    elif spec.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        raw = ChatAnthropic(model=spec.model_id, api_key=key,
                            temperature=spec.temperature, timeout=spec.timeout,
                            max_retries=0, max_tokens=spec.max_tokens)
    else:                                            # pragma: no cover - validate_spec 已挡
        raise LegacyProviderFactoryError(f"未知 provider：{spec.provider!r}")
    if not spec.gated_required:
        return raw
    from pilot.hard_gate import GatedModel
    return GatedModel(raw, _require_gate(), role=spec.role, model_id=spec.model_id,
                      max_tokens=spec.max_tokens)


def _require_gate():
    """本阶段没有生产调用者，因此这里**不构造**任何默认 Gate —— 显式拒绝。

    未来接入时必须由调用方注入 Gate；悄悄造一个没有预算的 Gate 才是真正危险的默认值。
    """
    raise LegacyProviderFactoryError(
        "gated_required=True 但未注入 HardBudgetGate："
        "本阶段 legacy factory 尚未接入生产，拒绝自造 Gate（fail-closed）")


def _looks_gated(obj) -> bool:
    """只承认真正的 GatedModel 包装。"""
    if getattr(obj, "_reumani_gated", False):
        return True
    try:
        from pilot.hard_gate import GatedModel
        return isinstance(obj, GatedModel)
    except Exception:                                # noqa: BLE001
        return False


__all__ = ["LegacyProviderFactory", "LegacyProviderFactoryError", "LegacyProviderHandle",
           "LegacyProviderSpecError", "LegacyRuntimeConfigError"]
