"""Pilot 专用的付费传输层加固（协议 v2 §3 / §6）。

目标：禁止 SDK 在一次 GatedModel 调用内部偷偷发第二次 HTTP 请求。
- 所有 Pilot 模型实例必须 `max_retries=0` + 有限连接/读取/总时限；
- 启动前动态检查四个角色（planner / executor / verifier / claim_extractor）：
  未包装 / max_retries≠0 / 无有限 timeout / 模型名不可识别 / 价格不可识别 → **拒绝启动**；
- 只改 Pilot 专用构造路径，**不改生产入口默认行为**（生产模块源码零改动）。
"""

from pilot.hard_gate import GateConfigError, GatedModel, _WRAPPED
from pilot.prices import PriceUnverified, assert_billing_mode, price_for

ROLES = ("planner", "executor", "verifier", "claim_extractor")

# ---- A.6.3.1 §1：Pilot 的两个 DeepSeek 角色钉死到 flash ----
# 理由（写入运行配置说明，不是根据评测结果换更强模型）：
#   1. 官方声明 `deepseek-chat` 对应 `deepseek-v4-flash` 的**非思考模式**，这是兼容迁移；
#   2. Flash 足以承担工具编排与结构化抽取；
#   3. 价格低于 Pro（具体单价见 pilot/prices.py —— 本文件不复制任何价格数字）；
#   4. 明确**不用** v4-pro，也不再用即将弃用的 deepseek-chat。
PINNED_DEEPSEEK = "deepseek-v4-flash"
FORBIDDEN_DEEPSEEK = ("deepseek-chat", "deepseek-reasoner", "deepseek-v4-pro")
DEEPSEEK_ROLES = ("executor", "claim_extractor")

# §2：DeepSeek V4 默认 thinking enabled → Pilot 必须显式关闭，不依赖默认值。
THINKING_DISABLED = {"thinking": {"type": "disabled"}}

# ---- §4：Anthropic 计费模式（仅允许第一方 API + 标准速度 + global）----
ANTHROPIC_PLATFORM = "anthropic_first_party"
RESOLVED_SPEED = "standard"
RESOLVED_GEO = "global"

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
    """Pilot 专用 DeepSeek 实例：钉死 flash + max_retries=0 + 有限 timeout + **显式关闭 thinking**。"""
    from langchain_openai import ChatOpenAI
    t = timeouts or TIMEOUTS
    if model_id != PINNED_DEEPSEEK:
        raise GateConfigError(f"Pilot 只允许 DeepSeek 模型 {PINNED_DEEPSEEK!r}，收到 {model_id!r}")
    price_for(model_id)
    return ChatOpenAI(model=model_id, base_url=base_url, max_retries=0,
                      timeout=t["total"], max_tokens=max_tokens, temperature=0.3,
                      extra_body=dict(THINKING_DISABLED))      # 不依赖模型默认值


def read_extra_body(obj):
    """穿过 GatedModel 读底层客户端实际会发出的 extra_body。"""
    inner = object.__getattribute__(obj, "_inner") if getattr(obj, _WRAPPED, False) else obj
    for attr in ("extra_body", "model_kwargs"):
        v = getattr(inner, attr, None)
        if isinstance(v, dict):
            if "thinking" in v:
                return v
            if isinstance(v.get("extra_body"), dict):
                return v["extra_body"]
    return getattr(inner, "extra_body", None)


def assert_deepseek_nonthinking(role, obj, model_id):
    """§2 启动前动态检查：模型必须精确等于 flash，thinking 必须明确 disabled。"""
    if model_id != PINNED_DEEPSEEK:
        raise GateConfigError(
            f"角色 {role}：DeepSeek 模型必须精确等于 {PINNED_DEEPSEEK!r}，收到 {model_id!r}"
            + ("（deepseek-chat 即将弃用）" if model_id in FORBIDDEN_DEEPSEEK else ""))
    eb = read_extra_body(obj)
    if not isinstance(eb, dict) or "thinking" not in eb:
        raise GateConfigError(f"角色 {role}：未声明 thinking → 拒绝启动"
                              "（DeepSeek V4 默认 thinking enabled，不得依赖默认值）")
    th = eb["thinking"]
    if not (isinstance(th, dict) and th.get("type") == "disabled"):
        raise GateConfigError(f"角色 {role}：thinking 必须为 disabled，实际 {th!r} → 拒绝启动")
    for banned in ("reasoning_effort", "reasoning"):
        if banned in eb:
            raise GateConfigError(f"角色 {role}：非思考模式下不得设置 {banned!r}")
    return True


def resolve_anthropic_billing(obj, model_id):
    """§4：解析并锁定 Anthropic 计费模式；不合规直接拒绝。
    客户端没有显式 speed / inference_geo 字段时，接受"参数不存在 = 官方默认"，
    但必须验证确实没有 fast 或 US-only 参数，并把解析结果记录下来。"""
    inner = object.__getattribute__(obj, "_inner") if getattr(obj, _WRAPPED, False) else obj
    bag = {}
    for attr in ("extra_body", "model_kwargs", "default_request_timeout"):
        v = getattr(inner, attr, None)
        if isinstance(v, dict):
            bag.update(v)
    speed = bag.get("speed", getattr(inner, "speed", None))
    geo = bag.get("inference_geo", getattr(inner, "inference_geo", None))
    if speed is not None and speed != RESOLVED_SPEED:
        raise GateConfigError(f"Anthropic speed={speed!r} 不被允许（Fast Mode 单价 $10/$50）")
    if geo is not None and geo != RESOLVED_GEO:
        raise GateConfigError(f"Anthropic inference_geo={geo!r} 不被允许"
                              "（US-only 有 1.1x 乘数，预算表未应用）")
    if bag.get("betas") or bag.get("batch"):
        raise GateConfigError("Pilot 不允许 Batch API / beta 计费路径")
    base = str(getattr(inner, "anthropic_api_url", "")
               or getattr(inner, "base_url", "") or "")
    if base and "api.anthropic.com" not in base:
        raise GateConfigError(f"无法识别的 Anthropic 端点/平台 {base!r}："
                              "只允许第一方 API（Bedrock/Vertex/Foundry 计价不同）")
    assert_billing_mode(model_id, platform=ANTHROPIC_PLATFORM,
                        speed=RESOLVED_SPEED, inference_geo=RESOLVED_GEO, batch=False)
    return {"platform": ANTHROPIC_PLATFORM, "resolved_speed": RESOLVED_SPEED,
            "resolved_inference_geo": RESOLVED_GEO, "batch": False,
            "speed_param_present": speed is not None, "geo_param_present": geo is not None}


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
    for r in DEEPSEEK_ROLES:                       # §2 两个角色分别检查
        assert_deepseek_nonthinking(r, roles[r], ids[r])
    billing = {r: resolve_anthropic_billing(roles[r], ids[r])
               for r in ("planner", "verifier")}   # §4
    return roles, {"deepseek_model": deepseek_model, "thinking": "disabled",
                   "anthropic_billing": billing,
                   "price_config_version": __import__("pilot.prices", fromlist=["x"])
                   .PRICE_TABLE_VERSION}


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
