"""A.8.2b.2b.1.1 —— 具名、显式、可审计的 **legacy 兼容适配器**。

这是本仓库里唯一一处允许在业务调用期取用 `ssc_pi_agent` 裸客户端的地方，
而且**只有应用入口可以主动选用它**。核心科研路径
（`pilot.legacy_model_bridge`）不会、也不能到达这里。

诚实标注（不得弱化）：
- 这条通道拿到的模型**未经** per-run ProviderRegistry / HardBudgetGate / HITL
  批准与审计。它只是把 `ssc_pi_agent` 里已经存在的对象取出来，好让旧页面在
  legacy 尚未拆除期间继续工作；
- 因此每个入口点必须**显式**写出 `legacy_chat_model_for_preference(...)`，
  让"我在用未受控的旧模型"这件事出现在调用点，而不是藏在 resolver 默认值里；
- 适配器**不构造**任何客户端，也**不缓存** —— `preflight_a1` /
  `round2_runner` 会把 legacy 属性重绑为 GatedModel，缓存会绕过 Gate；
- `"claude"` / `"deepseek"` 是 **provider 偏好**，不是科学职责。职责由调用方
  按 `pilot.legacy_model_bridge` 的角色常量自行决定，与这里的偏好正交。

import 本模块零副作用：`ssc_pi_agent` 只在函数真正被调用时才惰性 import。
"""

from __future__ import annotations

from typing import Any

from pilot.legacy_model_bridge import ChatModelProtocol, ModelDependencyMissing


class LegacyCompatUnavailable(ModelDependencyMissing):
    """兼容通道也拿不到东西 → 依然 fail-closed，绝不返回 None 或换一个顶替。"""


#: 这条通道是**未受控**的。测试直接读它断言，防止日后被悄悄描述成"已受控"。
COMPAT_IS_CONTROLLED = False
COMPAT_PASSES_THROUGH_REGISTRY = False
COMPAT_PASSES_THROUGH_GATE = False
COMPAT_PASSES_THROUGH_HITL = False

# provider 偏好 → legacy 属性名。这**不是**角色表：它只回答"用哪家的模型"。
_PREFERENCE_TO_LEGACY_ATTR = {
    "claude": "judge_llm",
    "deepseek": "deepseek_llm_pro",
}
DEFAULT_PREFERENCE = "deepseek"


def _legacy_module():
    try:
        import ssc_pi_agent as _legacy       # 惰性：import 本模块不触发
    except Exception as e:                   # noqa: BLE001
        raise LegacyCompatUnavailable(
            f"无法加载 legacy 模块：{type(e).__name__}") from e
    return _legacy


def legacy_chat_model_for_preference(preference: str = DEFAULT_PREFERENCE
                                     ) -> ChatModelProtocol:
    """取 legacy 里该 provider 偏好当前绑定的模型对象。**现取、不缓存。**

    保持迁移前的取值语义：只有恰好 `"claude"` 走 Claude，其余一律 DeepSeek。
    这个语义属于**兼容层**，核心路径不再有它。
    """
    attr = _PREFERENCE_TO_LEGACY_ATTR.get(str(preference),
                                          _PREFERENCE_TO_LEGACY_ATTR[DEFAULT_PREFERENCE])
    legacy = _legacy_module()
    model = getattr(legacy, attr, None)      # 现取 —— 让 Gate 重绑仍然生效
    if model is None:
        raise LegacyCompatUnavailable(f"legacy 中不存在 {attr}")
    if not callable(getattr(model, "invoke", None)):
        raise LegacyCompatUnavailable(f"legacy 的 {attr} 不满足 ChatModelProtocol")
    return model


def legacy_search_tool(tool_name: str = "search_literature") -> Any:
    """取 legacy 里定义的检索工具。工具不消耗模型预算，但同样只在此处显式取用。"""
    legacy = _legacy_module()
    tool = getattr(legacy, tool_name, None)
    if tool is None:
        raise LegacyCompatUnavailable(f"legacy 中不存在工具 {tool_name!r}")
    if not callable(getattr(tool, "invoke", None)):
        raise LegacyCompatUnavailable(f"legacy 的工具 {tool_name!r} 没有可调用的 invoke")
    return tool


def compat_disclosure() -> dict:
    """供 UI / 日志如实展示这条通道的受控状态。不含 key、不含模型对象。"""
    return {"channel": "legacy_compat_adapter",
            "controlled": COMPAT_IS_CONTROLLED,
            "through_registry": COMPAT_PASSES_THROUGH_REGISTRY,
            "through_gate": COMPAT_PASSES_THROUGH_GATE,
            "through_hitl": COMPAT_PASSES_THROUGH_HITL,
            "note": "legacy 裸客户端；未经 per-run Registry/Gate/HITL 授权与审计"}


__all__ = ["legacy_chat_model_for_preference", "legacy_search_tool",
           "LegacyCompatUnavailable", "compat_disclosure", "DEFAULT_PREFERENCE",
           "COMPAT_IS_CONTROLLED", "COMPAT_PASSES_THROUGH_REGISTRY",
           "COMPAT_PASSES_THROUGH_GATE", "COMPAT_PASSES_THROUGH_HITL"]
