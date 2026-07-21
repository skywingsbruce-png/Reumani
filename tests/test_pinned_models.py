"""Commit A.6.3.1：钉住模型 + 非思考模式 + 价格单一来源 + Anthropic 计费模式 + A1 预检。
全部 fake provider / dry-run，**零真实 API 调用**。"""
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pilot import hard_gate as HG
from pilot import paid_transport as PT
from pilot import prices as PR
from pilot.hard_gate import GateConfigError, GatedModel, HardBudgetGate
from pilot.prices import PriceUnverified

FLASH = "deepseek-v4-flash"
OPUS = "claude-opus-4-8"


@pytest.fixture(autouse=True)
def _sw(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv(HG.ENV_PAID, "1")
    monkeypatch.setenv(HG.ENV_CONFIRM, "test")


def mkgate(tmp_path):
    return HardBudgetGate(stage="test", ledger_path=tmp_path / "p.jsonl",
                          max_usd_global=25.0, max_usd_stage=3.0, max_usd_task=1.5,
                          max_calls_global=200, max_calls_task=21,
                          max_calls_per_model={FLASH: 99, OPUS: 99},
                          max_calls_per_role=dict(PT.MAX_TOKENS),
                          task_timeout_s=600, max_retries=0, default_max_tokens=2000)


class FakeDS:
    """假 DeepSeek 客户端：extra_body 就是实际会发给供应商的请求配置。"""

    def __init__(self, extra_body=None, max_retries=0, timeout=120.0, max_tokens=3000):
        self.extra_body = extra_body
        self.max_retries, self.timeout, self.max_tokens = max_retries, timeout, max_tokens
        self.calls, self.seen_bodies = 0, []

    def invoke(self, *a, **k):
        self.calls += 1
        self.seen_bodies.append(self.extra_body)
        return SimpleNamespace(content="ok",
                               usage_metadata={"input_tokens": 10, "output_tokens": 5},
                               response_metadata={}, tool_calls=[])

    def bind_tools(self, *a, **k):
        return self


class FakeAnthropic:
    def __init__(self, **kw):
        self.max_retries, self.timeout, self.max_tokens = 0, 120.0, 2000
        self.extra_body = kw.get("extra_body")
        self.anthropic_api_url = kw.get("url", "https://api.anthropic.com")
        self.calls = 0

    def invoke(self, *a, **k):
        self.calls += 1
        return SimpleNamespace(content="ok",
                               usage_metadata={"input_tokens": 10, "output_tokens": 5},
                               response_metadata={})


def ds_model(gate, role, extra_body=PT.THINKING_DISABLED, model_id=FLASH, **kw):
    p = FakeDS(extra_body=dict(extra_body) if extra_body is not None else None, **kw)
    return GatedModel(p, gate, role=role, model_id=model_id,
                      max_tokens=PT.MAX_TOKENS[role]), p


# ---------- §2：非思考模式，8 条 ----------
@pytest.mark.unit
@pytest.mark.parametrize("role", ["executor", "claim_extractor"])   # 6 分别检查
def test_flash_with_thinking_disabled_is_allowed(tmp_path, role):
    g = mkgate(tmp_path)
    m, _ = ds_model(g, role)
    assert PT.assert_deepseek_nonthinking(role, m, FLASH) is True


@pytest.mark.unit
@pytest.mark.parametrize("role", ["executor", "claim_extractor"])
def test_flash_without_declared_thinking_is_refused(tmp_path, role):
    g = mkgate(tmp_path)
    m, _ = ds_model(g, role, extra_body=None)
    with pytest.raises(GateConfigError, match="未声明 thinking"):
        PT.assert_deepseek_nonthinking(role, m, FLASH)
    m2, _ = ds_model(g, role, extra_body={"temperature": 0.3})       # 有 extra_body 但无 thinking
    with pytest.raises(GateConfigError, match="未声明 thinking"):
        PT.assert_deepseek_nonthinking(role, m2, FLASH)


@pytest.mark.unit
@pytest.mark.parametrize("role", ["executor", "claim_extractor"])
@pytest.mark.parametrize("th", [{"type": "enabled"}, {"type": "auto"}, True, "disabled"])
def test_thinking_enabled_or_malformed_is_refused(tmp_path, role, th):
    g = mkgate(tmp_path)
    m, _ = ds_model(g, role, extra_body={"thinking": th})
    with pytest.raises(GateConfigError, match="thinking 必须为 disabled"):
        PT.assert_deepseek_nonthinking(role, m, FLASH)


@pytest.mark.unit
@pytest.mark.parametrize("role", ["executor", "claim_extractor"])
def test_deepseek_chat_is_refused(tmp_path, role):
    g = mkgate(tmp_path)
    m, _ = ds_model(g, role, model_id="deepseek-chat")
    with pytest.raises(GateConfigError, match="deepseek-chat 即将弃用"):
        PT.assert_deepseek_nonthinking(role, m, "deepseek-chat")
    with pytest.raises(GateConfigError, match="只允许 DeepSeek 模型"):
        PT.build_deepseek("deepseek-chat", max_tokens=3000)


@pytest.mark.unit
@pytest.mark.parametrize("role", ["executor", "claim_extractor"])
def test_deepseek_v4_pro_is_refused(tmp_path, role):
    g = mkgate(tmp_path)
    m, _ = ds_model(g, role, model_id="deepseek-v4-pro")
    with pytest.raises(GateConfigError, match="必须精确等于"):
        PT.assert_deepseek_nonthinking(role, m, "deepseek-v4-pro")
    with pytest.raises(GateConfigError, match="只允许 DeepSeek 模型"):
        PT.build_deepseek("deepseek-v4-pro", max_tokens=3000)


@pytest.mark.unit
def test_no_reasoning_effort_allowed(tmp_path):
    g = mkgate(tmp_path)
    m, _ = ds_model(g, "executor",
                    extra_body={"thinking": {"type": "disabled"}, "reasoning_effort": "low"})
    with pytest.raises(GateConfigError, match="不得设置"):
        PT.assert_deepseek_nonthinking("executor", m, FLASH)


@pytest.mark.unit
def test_request_config_actually_carries_disabled(tmp_path):
    """7：实际传给底层客户端的请求配置里必须带 thinking disabled。"""
    g = mkgate(tmp_path)
    m, inner = ds_model(g, "executor")
    g.start_task("T")
    m.invoke("x")
    assert inner.seen_bodies == [{"thinking": {"type": "disabled"}}]
    assert PT.read_extra_body(m) == {"thinking": {"type": "disabled"}}


@pytest.mark.unit
def test_fake_tool_loop_completes_in_nonthinking_mode(tmp_path):
    """8：非思考模式下 fake 工具循环仍能跑完，且不产生/依赖 reasoning_content。"""
    g = mkgate(tmp_path)
    m, inner = ds_model(g, "executor")
    g.start_task("T")
    for _ in range(5):                       # 4 轮工具 + 1 轮收尾
        r = m.invoke("react step")
        assert not hasattr(r, "reasoning_content")
    assert inner.calls == 5 and g.calls_by_role["executor"] == 5
    assert all(b == {"thinking": {"type": "disabled"}} for b in inner.seen_bodies)


# ---------- §3：价格单一来源 ----------
PROD_PILOT_FILES = [p for p in (ROOT / "pilot").glob("*.py")
                    if p.name not in ("prices.py", "__init__.py")]
# 只取**价格专有**的字面量。注意 "5.00"/"25.00" 不能用：
# 它们同时是 round2_runner 里的预算上限（$5.00 单题 / $25.00 全局），与单价无关。
PRICE_LITERALS = ["0.14", "0.28", "0.435", "0.87", "6.25", "0.0028", "0.003625", "0.50"]


@pytest.mark.unit
def test_price_numbers_live_in_exactly_one_production_file():
    offenders = {}
    for f in PROD_PILOT_FILES:
        s = f.read_text(encoding="utf-8")
        hits = [lit for lit in PRICE_LITERALS if lit in s]
        if hits:
            offenders[f.name] = hits
    assert not offenders, f"价格数字必须只存在于 pilot/prices.py，发现重复：{offenders}"


@pytest.mark.unit
def test_no_second_price_table_structure_anywhere():
    """结构性检查：除 prices.py 外，任何生产文件都不得出现单价表结构。

    注意匹配必须是**价格形状**（"input"/"output" 映射到数值），
    不能是任意含 "input" 键的字典 —— 否则会把工具回调之类的正常代码误报为价格表。
    """
    offenders = {}
    for f in PROD_PILOT_FILES:
        s = f.read_text(encoding="utf-8")
        bad = [k for k in ("usd_per_mtok", "PRICES_PER_MTOK", "per_mtok") if k in s]
        if re.search(r'"(input|output)"\s*:\s*[0-9]+\.?[0-9]*\s*[,}]', s):
            bad.append("price-shaped dict")
        if bad:
            offenders[f.name] = bad
    assert not offenders, f"发现第二份价格表结构：{offenders}"


@pytest.mark.unit
def test_hard_gate_holds_no_price_constants():
    s = (ROOT / "pilot" / "hard_gate.py").read_text(encoding="utf-8")
    assert "PRICES = {" not in s, "hard_gate 不得保留任何单价表"
    assert "usd_per_mtok" not in s
    assert "_prices." in s, "hard_gate 只能通过公开接口查询价格"


@pytest.mark.unit
def test_price_config_version_in_ledger(tmp_path):
    g = mkgate(tmp_path)
    m, _ = ds_model(g, "executor")
    g.start_task("T")
    m.invoke("x")
    ev = g.ledger.events()
    assert all(e.get("price_config_version") == PR.PRICE_TABLE_VERSION
               for e in ev if e["event"] in ("reserved", "reconciled"))


@pytest.mark.unit
def test_unknown_model_cache_region_speed_all_refused():
    with pytest.raises(PriceUnverified, match="未知模型"):
        PR.price_for("mystery-model")
    with pytest.raises(PriceUnverified, match="计费平台"):
        PR.assert_billing_mode(OPUS, platform="bedrock", speed="standard",
                               inference_geo="global")
    with pytest.raises(PriceUnverified, match="speed"):
        PR.assert_billing_mode(OPUS, platform=PR.ALLOWED_PLATFORM, speed="fast",
                               inference_geo="global")
    with pytest.raises(PriceUnverified, match="inference_geo"):
        PR.assert_billing_mode(OPUS, platform=PR.ALLOWED_PLATFORM, speed="standard",
                               inference_geo="us")
    with pytest.raises(PriceUnverified, match="Batch"):
        PR.assert_billing_mode(OPUS, platform=PR.ALLOWED_PLATFORM, speed="standard",
                               inference_geo="global", batch=True)


# ---------- §4：Anthropic 计费模式 ----------
@pytest.mark.unit
def test_anthropic_resolved_standard_global(tmp_path):
    """客户端无显式 speed/geo 字段 → 接受"参数不存在=官方默认"，但必须记录 resolved 值。"""
    g = mkgate(tmp_path)
    m = GatedModel(FakeAnthropic(), g, role="planner", model_id=OPUS, max_tokens=2000)
    r = PT.resolve_anthropic_billing(m, OPUS)
    assert r["resolved_speed"] == "standard"
    assert r["resolved_inference_geo"] == "global"
    assert r["platform"] == "anthropic_first_party" and r["batch"] is False
    assert r["speed_param_present"] is False and r["geo_param_present"] is False


@pytest.mark.unit
@pytest.mark.parametrize("body,pat", [
    ({"speed": "fast"}, "speed"),
    ({"inference_geo": "us"}, "inference_geo"),
    ({"betas": ["x"]}, "Batch API / beta"),
])
def test_anthropic_nonstandard_billing_refused(tmp_path, body, pat):
    g = mkgate(tmp_path)
    m = GatedModel(FakeAnthropic(extra_body=body), g, role="planner",
                   model_id=OPUS, max_tokens=2000)
    with pytest.raises(GateConfigError, match=pat):
        PT.resolve_anthropic_billing(m, OPUS)


@pytest.mark.unit
@pytest.mark.parametrize("url", ["https://bedrock-runtime.us-east-1.amazonaws.com",
                                 "https://us-central1-aiplatform.googleapis.com",
                                 "https://my-foundry.azure.com"])
def test_third_party_platform_refused(tmp_path, url):
    g = mkgate(tmp_path)
    m = GatedModel(FakeAnthropic(url=url), g, role="planner", model_id=OPUS, max_tokens=2000)
    with pytest.raises(GateConfigError, match="只允许第一方 API"):
        PT.resolve_anthropic_billing(m, OPUS)


# ---------- §5：A1 预算预检 ----------
@pytest.mark.unit
def test_a1_precheck_is_dry_run_and_within_caps():
    from pilot.budget_precheck import precheck
    r = precheck("A1")
    assert r["dry_run"] is True and r["real_api_calls"] == 0
    assert r["models"]["deepseek"] == FLASH and r["models"]["anthropic"] == OPUS
    assert r["price_config_version"] == PR.PRICE_TABLE_VERSION
    # 必须包含系统提示/工具 schema/历史/工具结果上界，不能只用问题文本
    assert r["n_tools_in_schema"] >= 10
    assert all(v > 0 for v in r["explicit_bounds_for_unknown_lengths"].values())
    # Executor 三个阶段分别报告
    execs = [x["call"] for x in r["rows"] if x["role"] == "executor"]
    assert execs == ["first", "typical_middle", "last_allowed"]
    assert r["within_task_cap"] and r["within_stage1_cap"]
    assert r["task_worst_usd"] <= r["task_cap_usd"]
    assert r["stage1_worst_usd"] <= r["stage1_cap_usd"]


@pytest.mark.unit
def test_precheck_does_not_silently_zero_unknown_lengths():
    from pilot.budget_precheck import BOUNDS
    assert BOUNDS["tool_result_tokens_per_call"] > 0
    assert BOUNDS["history_growth_per_step"] > 0
    assert BOUNDS["resource_bundle_tokens"] > 0


# ---------- §7：可执行配置中 deepseek-chat 出现 0 次 ----------
@pytest.mark.unit
def test_no_deepseek_chat_in_executable_pilot_config():
    """价格表里作为"已弃用/拒绝"条目登记是允许的；可执行配置里必须 0 次。"""
    for f in PROD_PILOT_FILES:
        s = f.read_text(encoding="utf-8")
        code = "\n".join(ln for ln in s.splitlines()
                         if not ln.strip().startswith("#"))
        for m in re.finditer(r'"deepseek-chat"', code):
            ctx = code[max(0, m.start() - 200):m.end() + 80]
            assert "FORBIDDEN" in ctx, f"{f.name} 在可执行配置中引用了 deepseek-chat"
    assert PT.PINNED_DEEPSEEK == FLASH
    assert "deepseek-chat" in PT.FORBIDDEN_DEEPSEEK
