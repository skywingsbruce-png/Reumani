"""A.8.2b.2b.1 §3 —— 无状态 LLM 消费者的**最小模型注入桥**。

这不是第二套 ProviderRegistry，也不是第二套 Gate、模型工厂或全局模型容器：
- 它**不构造**任何客户端（`legacy_chat_model` 只是把 legacy 里已经存在的对象取出来）；
- 它**不缓存**任何模型对象 —— 每次都现取。这一点是必须的：
  `pilot/preflight_a1.py` 与 `pilot/round2_runner.py` 会在运行期把
  `ssc_pi_agent.judge_llm` / `deepseek_llm_pro` 重绑为 GatedModel。若在这里缓存，
  就会绕过 Gate；现取才能保证消费者永远拿到当前那个（可能已包 Gate 的）对象。
- 它**不读** .env、不读 key、不联网。

import 本模块零副作用：`ssc_pi_agent` 只在 `legacy_chat_model()` 真正被调用时
才惰性 import —— 这正是 A.8.2b.2b.1 的目标：把 legacy 依赖从 import 期挪到调用期。
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable


class ModelInjectionError(RuntimeError):
    """无法确定角色或拿不到模型 → fail-closed，绝不静默回退到某个默认模型。"""


@runtime_checkable
class ChatModelProtocol(Protocol):
    """消费者只需要这么多。与 LangChain `BaseChatModel` 兼容，故真实模型可直接注入。"""

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any: ...


# 两个通用角色。与 pilot/legacy_provider_specs.LEGACY_ROLES 中的同名角色对应，
# 但这里**不**引入 spec/factory —— 本轮只做注入边界，不改变模型来源。
ROLE_GENERAL_CLAUDE = "general_claude"
ROLE_GENERAL_DEEPSEEK = "general_deepseek"

# legacy 里承载这两个角色的属性名。角色映射是**代码事实**，不是按 Prompt 文本猜的：
# 三个消费者原本都写 `llm = judge_llm if model == "claude" else deepseek_llm_pro`。
_ROLE_TO_LEGACY_ATTR = {
    ROLE_GENERAL_CLAUDE: "judge_llm",
    ROLE_GENERAL_DEEPSEEK: "deepseek_llm_pro",
}

# 公开函数里 `model=` 参数的取值 → 角色。保持与迁移前完全一致的语义：
# 只有恰好等于 "claude" 才走 Claude，其余一律 DeepSeek。
_CHOICE_TO_ROLE = {
    "claude": ROLE_GENERAL_CLAUDE,
    "deepseek": ROLE_GENERAL_DEEPSEEK,
}


def role_for_model_choice(model: str) -> str:
    """把公开参数 `model=` 映射到角色。

    迁移前的写法是 `judge_llm if model == "claude" else deepseek_llm_pro`，
    即任何非 "claude" 的值都落到 DeepSeek。这里**保持该语义**（不收紧、不放宽），
    否则会改变现有科研业务行为。
    """
    return _CHOICE_TO_ROLE.get(str(model), ROLE_GENERAL_DEEPSEEK)


def legacy_chat_model(role: str) -> ChatModelProtocol:
    """从 legacy 取出该角色当前绑定的模型对象。**惰性 import、绝不缓存**。

    这是一条**具名的、被测试覆盖的**兼容路径，不是静默回退：调用方没有注入模型时，
    行为与迁移前逐字一致。拿不到就抛，绝不返回 None 或换一个模型顶替。
    """
    attr = _ROLE_TO_LEGACY_ATTR.get(role)
    if attr is None:
        raise ModelInjectionError(
            f"未知角色 {role!r}；本桥只服务 {tuple(_ROLE_TO_LEGACY_ATTR)}（不猜、不回退）")
    try:
        import ssc_pi_agent as _legacy          # 惰性：import 期不触发
    except Exception as e:                      # noqa: BLE001
        raise ModelInjectionError(
            f"无法加载 legacy 模型来源以解析角色 {role!r}：{type(e).__name__}") from e
    # 现取而非缓存 —— preflight / round2_runner 会把这些属性重绑为 GatedModel。
    model = getattr(_legacy, attr, None)
    if model is None:
        raise ModelInjectionError(f"legacy 中不存在角色 {role!r} 对应的 {attr}")
    return model


def resolve_chat_model(role: str, injected: Optional[ChatModelProtocol] = None
                       ) -> ChatModelProtocol:
    """注入优先；未注入才走具名的 legacy 兼容路径。两条路都 fail-closed。"""
    if injected is not None:
        if not hasattr(injected, "invoke"):
            raise ModelInjectionError(
                f"注入的模型不满足 ChatModelProtocol（缺 invoke）：{type(injected).__name__}")
        return injected
    if role not in _ROLE_TO_LEGACY_ATTR:
        raise ModelInjectionError(f"未知角色 {role!r}（不猜、不回退）")
    return legacy_chat_model(role)


def resolve_for_choice(model: str, injected: Optional[ChatModelProtocol] = None
                       ) -> ChatModelProtocol:
    """消费者常用的组合：把 `model=` 参数解析成角色，再解析出模型。"""
    return resolve_chat_model(role_for_model_choice(model), injected)


def legacy_tool(name: str):
    """取 legacy 里定义的工具（如 `search_literature`）。同样惰性、不缓存、fail-closed。

    工具不是模型，不消耗预算；但它定义在 `ssc_pi_agent` 里，因此消费者若在模块顶层
    import 它，legacy 仍会在 import 期被拉起。这里提供调用期取用的入口。
    """
    try:
        import ssc_pi_agent as _legacy
    except Exception as e:                      # noqa: BLE001
        raise ModelInjectionError(
            f"无法加载 legacy 工具来源以解析 {name!r}：{type(e).__name__}") from e
    t = getattr(_legacy, name, None)
    if t is None:
        raise ModelInjectionError(f"legacy 中不存在工具 {name!r}")
    return t


__all__ = ["ChatModelProtocol", "ModelInjectionError", "ROLE_GENERAL_CLAUDE",
           "ROLE_GENERAL_DEEPSEEK", "role_for_model_choice", "legacy_chat_model",
           "resolve_chat_model", "resolve_for_choice", "legacy_tool"]
