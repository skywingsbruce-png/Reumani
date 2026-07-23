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


ENV_DEEPSEEK_KEY = "DEEPSEEK_API_KEY"
ENV_ANTHROPIC_KEY = "ANTHROPIC_API_KEY"


def _secret_from_env(var_name):
    """读取密钥并包成 SecretStr。缺失/空/纯空白 → 在构造客户端**之前** fail-closed。

    铁律：异常信息、日志、repr、账本、Manifest、运行配置里都只出现**变量名**，
    绝不出现密钥值本身。不提供默认占位 key 用于真实 Pilot。
    """
    import os
    from pathlib import Path

    raw = os.environ.get(var_name)
    if raw is None or not str(raw).strip():
        # Pilot 的构造刻意早于 ssc_pi_agent 导入（导入顺序防御），所以 .env 还没被加载过。
        # 这里自己加载一次；load_dotenv 只写 os.environ，不返回也不打印任何值。
        try:
            from dotenv import load_dotenv
            load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
            raw = os.environ.get(var_name)
        except Exception:
            pass
    if raw is None or not str(raw).strip():
        raise GateConfigError(
            f"缺少 {var_name}（未设置或为空/纯空白）→ 在构造客户端前 fail-closed。"
            "Pilot 不提供默认占位 key。")
    try:
        from pydantic import SecretStr
        return SecretStr(str(raw))          # repr 为 '**********'，不会泄露
    except Exception:
        return str(raw)


def build_anthropic(model_id, *, max_tokens, timeouts=None):
    """Pilot 专用 Anthropic 实例：max_retries=0 + 有限 timeout。"""
    from langchain_anthropic import ChatAnthropic
    t = timeouts or TIMEOUTS
    price_for(model_id)                       # 未核实价格 → 直接抛，不构造
    api_key = _secret_from_env(ENV_ANTHROPIC_KEY)
    return ChatAnthropic(model=model_id, api_key=api_key, max_retries=0,
                         timeout=t["total"], max_tokens=max_tokens)


def build_deepseek(model_id, *, max_tokens, base_url="https://api.deepseek.com", timeouts=None):
    """Pilot 专用 DeepSeek 实例：钉死 flash + max_retries=0 + 有限 timeout + **显式关闭 thinking**。"""
    from langchain_openai import ChatOpenAI
    t = timeouts or TIMEOUTS
    if model_id != PINNED_DEEPSEEK:
        raise GateConfigError(f"Pilot 只允许 DeepSeek 模型 {PINNED_DEEPSEEK!r}，收到 {model_id!r}")
    price_for(model_id)
    api_key = _secret_from_env(ENV_DEEPSEEK_KEY)       # 缺失即抛，早于客户端构造
    return ChatOpenAI(model=model_id, api_key=api_key, base_url=base_url, max_retries=0,
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


def assert_claim_extractor_ready(model, gate, *, expected_role="claim_extractor",
                                 expected_model_id=None):
    """§4 禁止静默回退：专用 Claim 角色必须**完全就位**，否则拒绝启动。

    逐项检查：角色对象存在 / 已被 Gate 包装 / role 标签正确 / 绑定的是当前 Stage 的同一
    gate（即同一账本）/ 模型钉死冻结的 DeepSeek Flash / thinking disabled / max_retries=0。
    任一不满足直接抛 GateConfigError —— 绝不静默退回 deepseek_llm_pro，也绝不把 Claim
    记成 Executor 以继续运行。
    """
    expected_model_id = expected_model_id or PINNED_DEEPSEEK
    if model is None:
        raise GateConfigError("缺少专用 claim_extractor 角色 → 拒绝启动（不得回退到 executor）")
    if not getattr(model, _WRAPPED, False):
        raise GateConfigError("claim_extractor 未被 Budget Gate 包装 → 拒绝启动")
    role = object.__getattribute__(model, "_role")
    if role != expected_role:
        raise GateConfigError(
            f"claim_extractor 角色标签错误：期望 {expected_role!r}，实际 {role!r} → 拒绝启动")
    bound_gate = object.__getattribute__(model, "_gate")
    if bound_gate is not gate:
        raise GateConfigError("claim_extractor 绑定的不是当前 Stage 的 Budget Gate/账本 → 拒绝启动")
    model_id = object.__getattribute__(model, "_model_id")
    if model_id != expected_model_id:
        raise GateConfigError(
            f"claim_extractor 模型必须为 {expected_model_id!r}，实际 {model_id!r} → 拒绝启动")
    assert_deepseek_nonthinking(expected_role, model, model_id)      # thinking 必须 disabled
    t = inspect_transport(model)
    if t.get("max_retries") != 0:
        raise GateConfigError(
            f"claim_extractor max_retries 必须为 0，实际 {t.get('max_retries')!r} → 拒绝启动")
    return {"role": role, "model_id": model_id, "max_retries": t.get("max_retries"),
            "wrapped": True, "same_gate": True}


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
    # 各客户端的超时字段名不同：ChatOpenAI 用 request_timeout/timeout，
    # ChatAnthropic 用 default_request_timeout（别名 timeout）。三个都读。
    timeout = None
    for attr in ("timeout", "request_timeout", "default_request_timeout"):
        v = getattr(inner, attr, None)
        if isinstance(v, (int, float)) and v > 0:
            timeout = v
            break
    return {"max_retries": getattr(inner, "max_retries", None),
            "timeout": timeout,
            "request_timeout": getattr(inner, "request_timeout", None),
            "default_request_timeout": getattr(inner, "default_request_timeout", None),
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
    # Planner 与 Verifier 各自一个**独立的** GatedModel（同一 Stage 账本、各自角色计数、
    # 额度互不借用）。底层各构造一个客户端，配置完全相同。
    a = build_anthropic(anthropic_model, max_tokens=MAX_TOKENS["planner"], timeouts=timeouts)
    roles["planner"] = GatedModel(a, gate, role="planner", model_id=anthropic_model,
                                  max_tokens=MAX_TOKENS["planner"])
    v = build_anthropic(anthropic_model, max_tokens=MAX_TOKENS["verifier"], timeouts=timeouts)
    roles["verifier"] = GatedModel(v, gate, role="verifier", model_id=anthropic_model,
                                   max_tokens=MAX_TOKENS["verifier"])
    if roles["planner"] is roles["verifier"]:
        raise GateConfigError("Planner 与 Verifier 必须是两个独立 wrapper")
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


# ---------- §4：导入顺序防御 ----------
GUARDED_MODULES = ("ssc_a1", "ssc_skill_agent")
PAID_ATTRS = ("judge_llm", "deepseek_llm_pro", "deepseek_llm_con")


def assert_import_order_clean():
    """安装 GatedModel 之前必须调用：这些模块一旦已导入，就已经把**未包装**的
    付费对象复制进了自己的命名空间，事后替换 ssc_pi_agent 属性无法覆盖它们。
    不 reload、不遍历改写全局变量 —— 明确报错并终止。"""
    import sys

    already = [m for m in GUARDED_MODULES if m in sys.modules]
    if already:
        raise GateConfigError(
            f"这些模块已在包装前被导入：{already}。它们持有未包装的付费模型绑定，"
            "事后替换无法覆盖 → 拒绝启动（不 reload、不静默改写）。"
            "正确顺序：验证配置 → 构造客户端 → 包装 → 替换入口 → 首次导入 ssc_a1/ssc_skill_agent。")
    leaked = []
    for name, mod in list(sys.modules.items()):
        if name.startswith(("pilot", "ssc_pi_agent")) or mod is None:
            continue
        for attr in PAID_ATTRS:
            obj = getattr(mod, attr, None)
            if obj is not None and not getattr(obj, _WRAPPED, False):
                leaked.append(f"{name}.{attr}")
    if leaked:
        raise GateConfigError(f"以下模块已复制未包装的付费模型对象：{leaked} → 拒绝启动")
    return True


UNUSED_ROLE = "unused_not_in_pilot"       # 故意不配调用上限 → 任何调用都 fail-closed
SCAN_MODULES = ("ssc_pi_agent", "ssc_a1", "ssc_skill_agent", "shadow",
                "pilot.round2_runner")


def _is_paid_client(obj):
    """按**类型**识别付费模型客户端，不依赖属性名。"""
    if obj is None or isinstance(obj, (str, int, float, bool, dict, list, tuple, set)):
        return False
    if getattr(obj, _WRAPPED, False):
        return True                        # 已包装的也算"付费客户端"，供归属判断
    try:
        from langchain_core.language_models.chat_models import BaseChatModel
        if isinstance(obj, BaseChatModel):
            return True
    except Exception:
        pass
    name = type(obj).__name__
    return name in ("ChatAnthropic", "ChatOpenAI", "GatedModel") or name.startswith("Chat")


def discover_paid_clients():
    """动态扫描 Pilot 执行路径上的所有付费客户端（按类型，不按固定属性名）。
    返回 [(module_name, attr_name, obj, wrapped, role)]。"""
    import sys

    found = []
    for mname in SCAN_MODULES:
        mod = sys.modules.get(mname)
        if mod is None:
            continue
        for attr in dir(mod):
            if attr.startswith("__"):
                continue
            try:
                obj = getattr(mod, attr)
            except Exception:
                continue
            if not _is_paid_client(obj):
                continue
            wrapped = bool(getattr(obj, _WRAPPED, False))
            role = object.__getattribute__(obj, "_role") if wrapped else None
            found.append((mname, attr, obj, wrapped, role))
    return found


def neutralize_unused_paid_clients(gate, approved=None):
    """把**动态发现的**、不属于四个批准角色的原始付费客户端包到一个没有配额的角色上：
    既不留原始客户端可达，一旦被调用也在网络请求前被拒。
    发现未知付费客户端时默认 fail-closed（不猜它属于哪个角色）。"""
    import sys

    approved_ids = {id(o) for o in (approved or {}).values()}
    neutralized = []
    for mname, attr, obj, wrapped, role in discover_paid_clients():
        if wrapped or id(obj) in approved_ids:
            continue
        mod = sys.modules[mname]
        setattr(mod, attr, GatedModel(obj, gate, role=UNUSED_ROLE,
                                      model_id=PINNED_DEEPSEEK, max_tokens=1))
        neutralized.append(f"{mname}.{attr}")
    return neutralized


def assert_no_raw_paid_client_reachable(approved=None):
    """动态证明：Pilot 执行路径上不存在任何**可直接调用的原始付费客户端**。"""
    approved_ids = {id(o) for o in (approved or {}).values()}
    raw = [f"{m}.{a}" for m, a, o, w, _ in discover_paid_clients()
           if not w and id(o) not in approved_ids]
    if raw:
        raise GateConfigError(f"发现未包装的原始付费客户端（fail-closed）：{raw}")
    return True


def assert_bindings_after_import(roles, gate):
    """首次导入 ssc_a1 / ssc_skill_agent 之后逐项断言六个绑定的**身份**。
    任一失败必须在任何网络请求之前终止。"""
    import ssc_a1
    import ssc_pi_agent as P
    import ssc_skill_agent

    expect_judge = roles["planner"]
    expect_exec = roles["executor"]
    checks = {
        "ssc_pi_agent.judge_llm": (P.judge_llm, expect_judge),
        "ssc_pi_agent.deepseek_llm_pro": (P.deepseek_llm_pro, expect_exec),
        "ssc_a1.judge_llm": (getattr(ssc_a1, "judge_llm", None), expect_judge),
        "ssc_a1.deepseek_llm_pro": (getattr(ssc_a1, "deepseek_llm_pro", None), expect_exec),
        "ssc_skill_agent.judge_llm": (getattr(ssc_skill_agent, "judge_llm", None), expect_judge),
        "ssc_skill_agent.deepseek_llm_pro":
            (getattr(ssc_skill_agent, "deepseek_llm_pro", None), expect_exec),
    }
    bad = []
    for name, (actual, expect) in checks.items():
        if actual is not expect:
            bad.append(f"{name}（身份不符：{'未包装' if not getattr(actual, _WRAPPED, False) else '包装但非本轮对象'}）")
    if bad:
        raise GateConfigError(f"绑定身份断言失败：{bad} → 在任何网络请求前终止")

    # Shadow 的 claim extractor 是函数内 import，运行时取 —— 验证它拿到的就是包装对象
    import shadow  # noqa: F401
    if P.deepseek_llm_pro is not expect_exec:
        raise GateConfigError("Shadow claim extractor 运行时取到的不是包装对象")

    # 四个角色的 role 标签与账本必须一致
    for r in ROLES:
        m = roles[r]
        if object.__getattribute__(m, "_role") != r:
            raise GateConfigError(f"角色标签错误：{r}")
        if object.__getattribute__(m, "_gate") is not gate:
            raise GateConfigError(f"角色 {r} 未接到同一个 Stage 预算账本")

    # 原始未包装客户端不得从 Pilot 执行路径直接访问
    for mod in (P, ssc_a1, ssc_skill_agent):
        for attr in PAID_ATTRS:
            obj = getattr(mod, attr, None)
            if obj is not None and not getattr(obj, _WRAPPED, False):
                raise GateConfigError(
                    f"{mod.__name__}.{attr} 仍是未包装的原始客户端 → 终止")
    return list(checks)


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
