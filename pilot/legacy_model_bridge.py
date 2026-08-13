"""A.8.2b.2b.1.1 —— 科研消费者的**显式注入**边界（核心路径 fail-closed）。

上一轮（A.8.2b.2b.1）这里有一个 `resolve_chat_model(role, injected=None)`：
注入为空就自动去 import `ssc_pi_agent` 取裸客户端。那是**隐式回退**——
即使它被叫作"具名兼容路径"，默认路径依然可能用上一个没有经过 per-run
Registry / Gate / HITL 的裸客户端。本模块现在不再提供任何自动回退。

规则：
- 核心解析只接受**显式注入**；缺模型/缺工具一律抛错，绝不 import legacy；
- 角色是**科学职责**（起草 / 校验 / 抽取 / 核验），不是 provider 名称。
  `"claude"` / `"deepseek"` 是 provider 偏好，只有显式兼容适配器才认识它；
- **工具不是模型角色**：检索工具走独立的 `require_injected_tool`；
- 本模块 import 零副作用，且在任何路径上都**不会** import ssc_pi_agent
  （由测试对源码与运行时双重断言）。

确需旧行为的应用入口，必须主动选用 `pilot.legacy_compat_adapter` ——
那是一条具名、显式、可审计、且明确标注"未受控"的通道。
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable


class ModelInjectionError(RuntimeError):
    """注入的对象不满足契约。"""


class ModelDependencyMissing(ModelInjectionError):
    """核心路径缺少显式模型/工具 → fail-closed，不猜、不回退、不 import legacy。"""


@runtime_checkable
class ChatModelProtocol(Protocol):
    """消费者只需要这么多。与 LangChain `BaseChatModel` 兼容。"""

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any: ...


# ---------------------------------------------------------------------------
# 科学职责角色。刻意**不**用 provider 名称：一个角色描述"这一步在科研上负什么责"，
# 与用哪家模型无关。provider 偏好属于兼容适配器的输入，不属于这里。
# ---------------------------------------------------------------------------
ROLE_LITERATURE_DRAFTING = "literature_drafting"     # 基于检索结果起草综述/引言
ROLE_LITERATURE_REVISION = "literature_revision"     # 在已有草稿上按要求修订
ROLE_PROTOCOL_DRAFTING = "protocol_drafting"         # 起草可执行实验方案
ROLE_EVIDENCE_EXTRACTION = "evidence_extraction"     # 从摘要抽取结构化证据卡片
ROLE_CLAIM_VERIFICATION = "claim_verification"       # 用证据核验一条论断

SCIENTIFIC_ROLES: frozenset = frozenset({
    ROLE_LITERATURE_DRAFTING, ROLE_LITERATURE_REVISION, ROLE_PROTOCOL_DRAFTING,
    ROLE_EVIDENCE_EXTRACTION, ROLE_CLAIM_VERIFICATION,
})


def validate_model(candidate: Any) -> ChatModelProtocol:
    """只检查契约，不构造、不替换。"""
    if candidate is None:
        raise ModelDependencyMissing("模型为 None")
    if not callable(getattr(candidate, "invoke", None)):
        raise ModelInjectionError(
            f"注入的对象不满足 ChatModelProtocol（缺可调用的 invoke）："
            f"{type(candidate).__name__}")
    return candidate


def require_injected_model(role: str, injected: Optional[ChatModelProtocol] = None
                           ) -> ChatModelProtocol:
    """核心路径的唯一入口。**没有注入就失败**，绝不去 legacy 里找一个顶上。"""
    if role not in SCIENTIFIC_ROLES:
        raise ModelDependencyMissing(
            f"未知科学职责 role={role!r}；已声明的只有 {sorted(SCIENTIFIC_ROLES)}")
    if injected is None:
        raise ModelDependencyMissing(
            f"explicit model required for role={role}；"
            "核心路径不提供默认模型，也不会自动加载 legacy。"
            "应用入口如需旧行为，请显式使用 pilot.legacy_compat_adapter。")
    return validate_model(injected)


def require_injected_tool(tool_name: str, injected: Any = None) -> Any:
    """工具**不是**模型角色，单独一条通道。同样 fail-closed。"""
    if not str(tool_name or "").strip():
        raise ModelDependencyMissing("工具名不能为空")
    if injected is None:
        raise ModelDependencyMissing(
            f"explicit tool required for tool={tool_name}；"
            "核心路径不会自动加载 legacy 工具。"
            "应用入口如需旧行为，请显式使用 pilot.legacy_compat_adapter。")
    if not callable(getattr(injected, "invoke", None)):
        raise ModelInjectionError(
            f"注入的工具缺可调用的 invoke：{type(injected).__name__}")
    return injected


__all__ = ["ChatModelProtocol", "ModelInjectionError", "ModelDependencyMissing",
           "validate_model", "require_injected_model", "require_injected_tool",
           "SCIENTIFIC_ROLES", "ROLE_LITERATURE_DRAFTING", "ROLE_LITERATURE_REVISION",
           "ROLE_PROTOCOL_DRAFTING", "ROLE_EVIDENCE_EXTRACTION",
           "ROLE_CLAIM_VERIFICATION"]
