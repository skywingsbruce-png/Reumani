"""Pilot 价格配置（版本化 / 有生效日期 / 有官方来源 / 未核实即拒绝）。

规则（协议 v2 §2）：
- 价格只能来自**官方供应商文档**，不得来自记忆、第三方博客或旧报告估算；
- 无法从官方来源确认的模型或价格 → status="unverified" → `price_for()` 抛异常 → Pilot fail-closed；
- 未知模型拒绝；未知 usage 字段拒绝；**绝不静默回退为 Opus 或其它模型价格**。
"""

PRICE_TABLE_VERSION = "2026-07-20.1"
EFFECTIVE_DATE = "2026-07-20"          # 本表生效日期
QUERIED_ON = "2026-07-20"              # 官方页查询日期

ANTHROPIC_SRC = "https://platform.claude.com/docs/en/about-claude/pricing"
DEEPSEEK_SRC = "https://api-docs.deepseek.com/quick_start/pricing"
DEEPSEEK_CACHE_SRC = "https://api-docs.deepseek.com/guides/kv_cache"

# usd_per_mtok 的键在两家供应商之间不同——不做人为统一，如实保留官方口径。
PRICES = {
    "claude-opus-4-8": {
        "provider": "anthropic",
        "status": "verified",
        "verified_on": QUERIED_ON,
        "source": ANTHROPIC_SRC,
        "usd_per_mtok": {"input_base": 5.00, "cache_write_5m": 6.25,
                         "cache_write_1h": 10.00, "cache_read": 0.50, "output": 25.00},
        "usage_fields": ["input_tokens", "output_tokens",
                         "cache_creation_input_tokens", "cache_read_input_tokens"],
        "note": "官方定价页 Model pricing 表逐字核对",
    },
    "deepseek-v4-flash": {
        "provider": "deepseek",
        "status": "verified",
        "verified_on": QUERIED_ON,
        "source": DEEPSEEK_SRC,
        "usd_per_mtok": {"input_cache_miss": 0.14, "input_cache_hit": 0.0028, "output": 0.28},
        "usage_fields": ["prompt_tokens", "completion_tokens",
                         "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"],
        "note": f"cache 字段名来自 {DEEPSEEK_CACHE_SRC}；"
                "prompt_tokens/completion_tokens 为 OpenAI 兼容惯例，该页未逐字列出",
    },
    "deepseek-v4-pro": {
        "provider": "deepseek",
        "status": "verified",
        "verified_on": QUERIED_ON,
        "source": DEEPSEEK_SRC,
        "usd_per_mtok": {"input_cache_miss": 0.435, "input_cache_hit": 0.003625, "output": 0.87},
        "usage_fields": ["prompt_tokens", "completion_tokens",
                         "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"],
    },
    # ⚠️ 仓库当前默认的 DEEPSEEK_MODEL 就是它。官方定价页**已不再单列其价格**，
    # 并公告 2026/07/24 15:59 UTC 弃用（映射到 deepseek-v4-flash 非思考模式）。
    # 按 v2 §2：价格无法从官方来源直接确认 → unverified → Pilot fail-closed。
    "deepseek-chat": {
        "provider": "deepseek",
        "status": "unverified",
        "verified_on": QUERIED_ON,
        "source": DEEPSEEK_SRC,
        "usd_per_mtok": None,
        "usage_fields": None,
        "note": "官方页未单列价格；公告 2026/07/24 15:59 UTC 弃用。"
                "运行前必须把 DEEPSEEK_MODEL 显式钉到 deepseek-v4-flash 或 deepseek-v4-pro。",
    },
    "fake-model": {          # 仅测试用，零价
        "provider": "test", "status": "verified", "verified_on": QUERIED_ON,
        "source": "test_only",
        "usd_per_mtok": {"input_cache_miss": 0.0, "input_cache_hit": 0.0, "output": 0.0},
        "usage_fields": ["prompt_tokens", "completion_tokens"],
    },
}


class PriceUnverified(RuntimeError):
    """价格未经官方核实 / 模型未知 → fail-closed，禁止真实调用。"""


def price_for(model_id):
    """精确匹配（不做子串回退，避免把未知模型误配到已知价格）。"""
    e = PRICES.get(model_id)
    if e is None:
        raise PriceUnverified(f"未知模型，拒绝调用：{model_id!r}（未在版本化价格表中登记）")
    if e["status"] != "verified" or not e.get("usd_per_mtok"):
        raise PriceUnverified(
            f"模型 {model_id!r} 价格未经官方核实（status={e['status']}）→ Pilot fail-closed。"
            f" {e.get('note', '')}")
    return e


def worst_input_rate(model_id):
    """最坏输入单价：取该模型输入侧所有档位中最贵的一档（缓存写 > 基础 > 缓存读）。"""
    r = price_for(model_id)["usd_per_mtok"]
    keys = [k for k in r if k != "output"]
    return max(r[k] for k in keys)


def output_rate(model_id):
    return price_for(model_id)["usd_per_mtok"]["output"]


def worst_case_usd(model_id, est_input_tokens, max_output_tokens):
    """v2 §5 最坏费用公式：
       worst = est_in/1e6 × 最贵输入单价 + max_tokens/1e6 × 输出单价"""
    return (est_input_tokens / 1e6 * worst_input_rate(model_id)
            + max_output_tokens / 1e6 * output_rate(model_id))


def assert_usage_fields_known(model_id, usage_keys):
    """未知 usage 字段拒绝：出现价格表未登记的计费字段 → 抛错，不静默忽略。"""
    known = set(price_for(model_id)["usage_fields"] or [])
    unknown = {k for k in usage_keys if k not in known and k.endswith("_tokens")}
    if unknown:
        raise PriceUnverified(f"模型 {model_id!r} 返回未登记的 usage 字段 {sorted(unknown)}，"
                              "无法确定计价口径 → fail-closed")
    return True


def actual_usd(model_id, usage):
    """按真实 usage 结算。缓存字段存在时分档计价；不存在时用**最贵输入档**（保守）。

    绝不因为拿不到缓存明细就按最便宜档计费。
    """
    r = price_for(model_id)["usd_per_mtok"]
    u = dict(usage or {})
    out = u.get("output_tokens") or u.get("completion_tokens") or 0
    cost = out / 1e6 * r["output"]

    hit = u.get("prompt_cache_hit_tokens")
    miss = u.get("prompt_cache_miss_tokens")
    if hit is not None or miss is not None:                     # DeepSeek 口径
        cost += (hit or 0) / 1e6 * r.get("input_cache_hit", worst_input_rate(model_id))
        cost += (miss or 0) / 1e6 * r.get("input_cache_miss", worst_input_rate(model_id))
        return cost

    cw = u.get("cache_creation_input_tokens")
    cr = u.get("cache_read_input_tokens")
    base = u.get("input_tokens") or u.get("prompt_tokens") or 0
    if cw is not None or cr is not None:                        # Anthropic 口径
        cost += (base or 0) / 1e6 * r.get("input_base", worst_input_rate(model_id))
        cost += (cw or 0) / 1e6 * r.get("cache_write_1h", worst_input_rate(model_id))
        cost += (cr or 0) / 1e6 * r.get("cache_read", 0.0)
        return cost

    cost += base / 1e6 * worst_input_rate(model_id)             # 无缓存明细 → 最贵档
    return cost


# ---- 不支持的计费模式：直接拒绝，不静默按标准价计 ----
ALLOWED_PLATFORM = "anthropic_first_party"
ALLOWED_SPEED = "standard"
ALLOWED_GEO = "global"


def assert_billing_mode(model_id, *, platform, speed, inference_geo, batch=False):
    e = price_for(model_id)
    if e["provider"] == "anthropic":
        if platform != ALLOWED_PLATFORM:
            raise PriceUnverified(f"不支持的计费平台 {platform!r}：价格表只覆盖 "
                                  f"{ALLOWED_PLATFORM}（Bedrock/Vertex/Foundry 计价不同）")
        if speed != ALLOWED_SPEED:
            raise PriceUnverified(f"speed={speed!r} 不被允许（Fast Mode 单价不同：$10/$50）")
        if inference_geo != ALLOWED_GEO:
            raise PriceUnverified(f"inference_geo={inference_geo!r} 不被允许"
                                  "（US-only 有 1.1x 乘数，价格表未应用）")
        if batch:
            raise PriceUnverified("Batch API 有 50% 折扣，价格表未覆盖 → 拒绝")
    return True


def table_meta():
    return {"price_config_version": PRICE_TABLE_VERSION, "effective_date": EFFECTIVE_DATE,
            "queried_on": QUERIED_ON,
            "models": {k: {"status": v["status"], "source": v["source"]}
                       for k, v in PRICES.items()}}
