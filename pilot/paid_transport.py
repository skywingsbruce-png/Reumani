"""Pilot 专用的付费传输层加固（协议 v2 §3 / §6）。

目标：禁止 SDK 在一次 GatedModel 调用内部偷偷发第二次 HTTP 请求。
- 所有 Pilot 模型实例必须 `max_retries=0` + 有限连接/读取/总时限；
- 启动前动态检查四个角色（planner / executor / verifier / claim_extractor）：
  未包装 / max_retries≠0 / 无有限 timeout / 模型名不可识别 / 价格不可识别 → **拒绝启动**；
- 只改 Pilot 专用构造路径，**不改生产入口默认行为**（生产模块源码零改动）。
"""

from pilot.hard_gate import GateConfigError, GatedModel, _WRAPPED
from pilot.prices import PriceUnverified, price_for

ROLES = ("planner", "executor", "verifier", "claim_extractor")

# v2 §5 分角色输出 token 上限
MAX_TOKENS = {"planner": 2000, "executor": 3000, "verifier": 2000, "claim_extractor": 2000}

# v2 §3 有限超时（秒）
TIMEOUTS = {"connect": 10.0, "read": 90.0, "total": 120.0}


def _finite(x):
    return isinstance(x, (int, float)) and x > 0 and x != float("inf")


def build_anthropic(model_id, *, max_tokens, timeouts=None):
    """Pilot 专用 Anthropic 实例：max_retries=0 + 有限 timeout。"""
    from langchain_anthropic import ChatAnthropic
    t = timeouts or TIMEOUTS
    price_for(model_id)                       # 未核实价格 → 直接抛，不构造
    return ChatAnthropic(model=model_id, max_retries=0, timeout=t["total"],
                         max_tokens=max_tokens)


def build_deepseek(model_id, *, max_tokens, base_url="https://api.deepseek.com", timeouts=None):
    """Pilot 专用 DeepSeek(OpenAI 兼容) 实例：max_retries=0 + 有限 timeout。"""
    from langchain_openai import ChatOpenAI
    t = timeouts or TIMEOUTS
    price_for(model_id)
    return ChatOpenAI(model=model_id, base_url=base_url, max_retries=0,
                      timeout=t["total"], max_tokens=max_tokens, temperature=0.3)


def inspect_transport(obj):
    """读出底层实例的 max_retries / timeout（穿过 GatedModel 包装）。"""
    inner = object.__getattribute__(obj, "_inner") if getattr(obj, _WRAPPED, False) else obj
    return {"max_retries": getattr(inner, "max_retries", None),
            "timeout": getattr(inner, "timeout", None),
            "request_timeout": getattr(inner, "request_timeout", None),
            "max_tokens": getattr(inner, "max_tokens", None)}


def assert_role_hardened(role, obj, model_id):
    """五项启动前检查，任一不过 → GateConfigError（拒绝启动，不降级）。"""
    if not getattr(obj, _WRAPPED, False):
        raise GateConfigError(f"角色 {role} 未经 GatedModel 包装 → 拒绝启动")
    t = inspect_transport(obj)
    if t["max_retries"] != 0:
        raise GateConfigError(f"角色 {role} 的 max_retries={t['max_retries']!r} ≠ 0 → 拒绝启动"
                              "（SDK 可能在一次调用内偷偷重发 HTTP 请求）")
    to = t["timeout"] if t["timeout"] is not None else t["request_timeout"]
    if not _finite(to):
        raise GateConfigError(f"角色 {role} 没有有限 timeout（timeout={to!r}）→ 拒绝启动")
    if not _finite(t["max_tokens"]):
        raise GateConfigError(f"角色 {role} 没有有限 max_tokens（{t['max_tokens']!r}）→ 拒绝启动")
    try:
        price_for(model_id)
    except PriceUnverified as e:
        raise GateConfigError(f"角色 {role} 的模型/价格不可识别 → 拒绝启动：{e}")
    return True


def build_pilot_roles(gate, *, anthropic_model, deepseek_model, timeouts=None):
    """构造四个角色的 Pilot 专用模型（已加固 + 已包装），并逐个通过启动前检查。"""
    roles = {}
    a = build_anthropic(anthropic_model, max_tokens=MAX_TOKENS["planner"], timeouts=timeouts)
    roles["planner"] = GatedModel(a, gate, role="planner", model_id=anthropic_model,
                                  max_tokens=MAX_TOKENS["planner"])
    v = build_anthropic(anthropic_model, max_tokens=MAX_TOKENS["verifier"], timeouts=timeouts)
    roles["verifier"] = GatedModel(v, gate, role="verifier", model_id=anthropic_model,
                                   max_tokens=MAX_TOKENS["verifier"])
    e = build_deepseek(deepseek_model, max_tokens=MAX_TOKENS["executor"], timeouts=timeouts)
    roles["executor"] = GatedModel(e, gate, role="executor", model_id=deepseek_model,
                                   max_tokens=MAX_TOKENS["executor"])
    c = build_deepseek(deepseek_model, max_tokens=MAX_TOKENS["claim_extractor"], timeouts=timeouts)
    roles["claim_extractor"] = GatedModel(c, gate, role="claim_extractor",
                                          model_id=deepseek_model,
                                          max_tokens=MAX_TOKENS["claim_extractor"])
    ids = {"planner": anthropic_model, "verifier": anthropic_model,
           "executor": deepseek_model, "claim_extractor": deepseek_model}
    for r in ROLES:
        assert_role_hardened(r, roles[r], ids[r])
    return roles


def classify_failure(exc):
    """v2 §6 超时与取消语义。绝不把 provider timeout 写成"确认未计费"。"""
    name = type(exc).__name__.lower()
    text = f"{name} {exc}".lower()
    if "budgetexceeded" in name or "gateconfigerror" in name:
        return "rejected_before_request"       # 1 调用前已超时/越限：没发请求
    if "timeout" in text or "timedout" in text:
        return "provider_may_have_billed"      # 2 provider 超时：可能已计费
    if "cancel" in text or "keyboardinterrupt" in name:
        return "user_cancelled"                # 3 用户取消
    if "connection" in text or "dns" in text or "resolve" in text:
        return "network_error_no_auto_retry"   # 5 网络错误：不自动重试
    return "provider_may_have_billed"          # 保守默认：不假设未计费
