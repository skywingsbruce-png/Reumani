"""A.6.3.2：**真实 LangChain 客户端"只构造、不调用"**冒烟测试。

这一层是 A.6.3.1 的盲区——那时 34 项测试全用 fake provider，真实构造路径一次都没跑过，
结果漏了 api_key，直到 A1 preflight 才暴露。

铁律：本文件实例化真实客户端类，但**绝不**调用 invoke/ainvoke/stream/batch，
绝不发 HTTP；只用 dummy key 或 monkeypatch，**永不使用真实密钥**。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pilot import paid_transport as PT
from pilot.hard_gate import GateConfigError

# 故意不做成真实 key 的形状（不以 sk- 开头），避免绊倒密钥扫描器；
# 它们只是"某个非空字符串"，足以走通构造路径。
DUMMY_DS = "DUMMY-deepseek-test-value-not-a-real-key"
DUMMY_AN = "DUMMY-anthropic-test-value-not-a-real-key"
FLASH = "deepseek-v4-flash"
OPUS = "claude-opus-4-8"


@pytest.fixture
def dummy_keys(monkeypatch):
    monkeypatch.setenv(PT.ENV_DEEPSEEK_KEY, DUMMY_DS)
    monkeypatch.setenv(PT.ENV_ANTHROPIC_KEY, DUMMY_AN)
    yield


@pytest.fixture(autouse=True)
def no_http(monkeypatch):
    """把真实 HTTP 出口打死：只替换**发送方法**，不替换类
    （替换 httpx.Client 类会破坏 langchain_openai 的 isinstance 校验）。"""
    import httpx

    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise AssertionError("构造期不得发起任何 HTTP 请求")

    monkeypatch.setattr(httpx.Client, "send", boom, raising=False)
    monkeypatch.setattr(httpx.Client, "request", boom, raising=False)
    monkeypatch.setattr(httpx.AsyncClient, "send", boom, raising=False)
    monkeypatch.setattr(httpx.AsyncClient, "request", boom, raising=False)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", boom, raising=False)
    yield calls
    assert calls["n"] == 0, "构造期发生了 HTTP 请求"


# 1 / 10 / 11 —— 三类客户端都能只构造
@pytest.mark.unit
def test_deepseek_client_constructs_with_dummy_key(dummy_keys):
    llm = PT.build_deepseek(FLASH, max_tokens=3000)
    assert type(llm).__name__ == "ChatOpenAI"


@pytest.mark.unit
@pytest.mark.parametrize("role", ["executor", "claim_extractor"])
def test_both_deepseek_roles_construct(dummy_keys, role):
    llm = PT.build_deepseek(FLASH, max_tokens=PT.MAX_TOKENS[role])
    assert llm.max_tokens == PT.MAX_TOKENS[role]


@pytest.mark.unit
@pytest.mark.parametrize("role", ["planner", "verifier"])
def test_anthropic_planner_and_verifier_construct(dummy_keys, role):
    llm = PT.build_anthropic(OPUS, max_tokens=PT.MAX_TOKENS[role])
    assert type(llm).__name__ == "ChatAnthropic"
    assert llm.max_tokens == PT.MAX_TOKENS[role]


@pytest.mark.unit
@pytest.mark.parametrize("role,model,build", [
    ("planner", OPUS, "anthropic"), ("verifier", OPUS, "anthropic"),
    ("executor", FLASH, "deepseek"), ("claim_extractor", FLASH, "deepseek"),
])
def test_real_client_passes_hardening_assertions(dummy_keys, role, model, build):
    """真实客户端必须能通过启动前加固检查——A.6.3.1 漏掉的正是这一层。
    （只构造 + 只读检查，仍然不发任何请求。）"""
    from pilot.hard_gate import GatedModel, HardBudgetGate

    llm = (PT.build_anthropic(model, max_tokens=PT.MAX_TOKENS[role]) if build == "anthropic"
           else PT.build_deepseek(model, max_tokens=PT.MAX_TOKENS[role]))
    gate = HardBudgetGate(stage="t", ledger_path=Path("/tmp/x.jsonl"),
                          max_usd_global=1, max_usd_stage=1, max_usd_task=1,
                          max_calls_global=1, max_calls_task=1,
                          max_calls_per_model={model: 1}, task_timeout_s=1,
                          max_retries=0, default_max_tokens=1)
    g = GatedModel(llm, gate, role=role, model_id=model, max_tokens=PT.MAX_TOKENS[role])
    assert PT.assert_role_hardened(role, g, model) is True
    t = PT.inspect_transport(g)
    assert t["max_retries"] == 0
    assert isinstance(t["timeout"], (int, float)) and 0 < t["timeout"] < float("inf")
    if build == "deepseek":
        assert PT.assert_deepseek_nonthinking(role, g, model) is True
    else:
        r = PT.resolve_anthropic_billing(g, model)
        assert r["resolved_speed"] == "standard" and r["resolved_inference_geo"] == "global"


@pytest.fixture
def no_dotenv(monkeypatch):
    """构造路径在环境变量缺失时会回退去加载 .env（生产也是这样拿 key 的）。
    测"缺 key fail-closed"必须把这条回退也断掉，否则真实 .env 会把 key 补上。"""
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    yield


# 2 / 3 —— 缺 key / 空 key fail-closed（且早于客户端构造）
@pytest.mark.unit
def test_missing_key_is_fail_closed(monkeypatch, no_dotenv):
    monkeypatch.delenv(PT.ENV_DEEPSEEK_KEY, raising=False)
    with pytest.raises(GateConfigError, match=PT.ENV_DEEPSEEK_KEY):
        PT.build_deepseek(FLASH, max_tokens=3000)


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["", "   ", "\t\n"])
def test_empty_or_whitespace_key_is_fail_closed(monkeypatch, no_dotenv, bad):
    monkeypatch.setenv(PT.ENV_DEEPSEEK_KEY, bad)
    with pytest.raises(GateConfigError, match="未设置或为空"):
        PT.build_deepseek(FLASH, max_tokens=3000)


@pytest.mark.unit
def test_missing_anthropic_key_is_fail_closed(monkeypatch, no_dotenv):
    monkeypatch.delenv(PT.ENV_ANTHROPIC_KEY, raising=False)
    with pytest.raises(GateConfigError, match=PT.ENV_ANTHROPIC_KEY):
        PT.build_anthropic(OPUS, max_tokens=2000)


@pytest.mark.unit
def test_no_default_placeholder_key_for_real_pilot(monkeypatch, no_dotenv):
    """不得为真实 Pilot 提供任何默认占位 key。"""
    monkeypatch.delenv(PT.ENV_DEEPSEEK_KEY, raising=False)
    src = (ROOT / "pilot" / "paid_transport.py").read_text(encoding="utf-8")
    assert "sk-" not in src, "源码中不得内置任何 key 字面量"
    with pytest.raises(GateConfigError):
        PT.build_deepseek(FLASH, max_tokens=3000)


# 4-8 —— 冻结配置逐项锁定
@pytest.mark.unit
def test_constructed_client_locks_frozen_settings(dummy_keys):
    llm = PT.build_deepseek(FLASH, max_tokens=3000)
    assert llm.model_name == FLASH                       # 5 model 精确
    assert llm.max_retries == 0                          # 6
    assert 0 < float(llm.request_timeout or llm.timeout or 0) < float("inf")   # 7
    assert llm.max_tokens == 3000                        # 8
    eb = PT.read_extra_body(llm)                         # 4 thinking disabled
    assert eb == {"thinking": {"type": "disabled"}}


# 9 —— repr / 异常 / 配置快照都不含 dummy key
@pytest.mark.unit
def test_key_never_leaks_into_repr_or_snapshot(dummy_keys):
    import json

    llm = PT.build_deepseek(FLASH, max_tokens=3000)
    surfaces = [repr(llm), str(llm), json.dumps(PT.inspect_transport(llm), default=str),
                json.dumps(PT.read_extra_body(llm), default=str)]
    try:
        surfaces.append(json.dumps(llm.dict(), default=str))
    except Exception:
        pass
    for s in surfaces:
        assert DUMMY_DS not in s, "dummy key 泄露到 repr/快照"
        assert "dummy-deepseek" not in s
    # SecretStr 包装后 repr 应是掩码
    key_obj = getattr(llm, "openai_api_key", None) or getattr(llm, "api_key", None)
    if key_obj is not None:
        assert DUMMY_DS not in repr(key_obj)


@pytest.mark.unit
def test_exception_message_never_contains_key(monkeypatch):
    monkeypatch.setenv(PT.ENV_DEEPSEEK_KEY, DUMMY_DS)
    try:
        PT.build_deepseek("deepseek-v4-pro", max_tokens=3000)   # 触发拒绝
    except GateConfigError as e:
        assert DUMMY_DS not in str(e) and "dummy" not in str(e).lower()
    else:
        pytest.fail("应当拒绝非 flash 模型")


# 12 —— 构造期 provider HTTP 调用计数为 0
@pytest.mark.unit
def test_zero_http_during_construction(dummy_keys, no_http):
    PT.build_deepseek(FLASH, max_tokens=3000)
    PT.build_anthropic(OPUS, max_tokens=2000)
    assert no_http["n"] == 0
