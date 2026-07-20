"""协议 v2 测试（Commit A.6.3）：全部 fake provider，**零真实 API**。
覆盖 v2 §7 的 25 条 + 真实编排 fake 全链。"""
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pilot import hard_gate as HG
from pilot import paid_transport as PT
from pilot import prices as PR
from pilot.hard_gate import BudgetExceeded, GateConfigError, GatedModel, HardBudgetGate
from pilot.prices import PriceUnverified

FAKE = "fake-model"
V1_SHA = "5d166bce159de665c4df677aef6765803575a48827afdc5d061cb49ff54f0f22"
ROLE_CAPS = {"planner": 2, "verifier": 2, "claim_extractor": 1, "executor": 16}


@pytest.fixture(autouse=True)
def _sw(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv(HG.ENV_PAID, "1")
    monkeypatch.setenv(HG.ENV_CONFIRM, "test")


def mkgate(tmp_path, **kw):
    d = dict(stage="test", ledger_path=tmp_path / "v2.jsonl",
             max_usd_global=25.0, max_usd_stage=3.0, max_usd_task=1.5,
             max_calls_global=200, max_calls_task=21,
             max_calls_per_model={FAKE: 999}, max_calls_per_role=dict(ROLE_CAPS),
             task_timeout_s=600, max_retries=0, default_max_tokens=2000)
    d.update(kw)
    return HardBudgetGate(**d)


class FakeProvider:
    """带 max_retries / timeout 属性的假 provider，用于加固检查与"没有自动重试"的证明。"""

    def __init__(self, *, max_retries=0, timeout=120.0, max_tokens=2000,
                 usage=True, raise_exc=None):
        self.calls = 0
        self.max_retries, self.timeout, self.max_tokens = max_retries, timeout, max_tokens
        self.usage, self.raise_exc = usage, raise_exc

    def invoke(self, *a, **k):
        self.calls += 1                      # 每次真实 HTTP 请求 +1
        if self.raise_exc:
            raise self.raise_exc
        um = {"input_tokens": 50, "output_tokens": 20} if self.usage else None
        return SimpleNamespace(content="ok", usage_metadata=um, response_metadata={})

    async def ainvoke(self, *a, **k):
        return self.invoke(*a, **k)

    def stream(self, *a, **k):
        return self.invoke(*a, **k)

    def bind_tools(self, *a, **k):
        return self


def role_model(gate, role, provider=None, model_id=FAKE):
    p = provider or FakeProvider(max_tokens=PT.MAX_TOKENS.get(role, 2000))
    return GatedModel(p, gate, role=role, model_id=model_id,
                      max_tokens=PT.MAX_TOKENS.get(role, 2000)), p


def all_roles(gate):
    return {r: role_model(gate, r) for r in PT.ROLES}


# 1 / 2 —— 四角色包装 + 未包装拒绝
@pytest.mark.unit
def test_all_four_roles_are_wrapped(tmp_path):
    g = mkgate(tmp_path)
    for r, (m, _) in all_roles(g).items():
        assert PT.assert_role_hardened(r, m, FAKE) is True


@pytest.mark.unit
def test_unwrapped_role_refuses_to_start(tmp_path):
    with pytest.raises(GateConfigError, match="未经 GatedModel 包装"):
        PT.assert_role_hardened("planner", FakeProvider(), FAKE)


# 3 / 4 —— max_retries>0 拒绝；无 timeout 拒绝
@pytest.mark.unit
def test_nonzero_max_retries_refuses(tmp_path):
    g = mkgate(tmp_path)
    m, _ = role_model(g, "planner", FakeProvider(max_retries=2))
    with pytest.raises(GateConfigError, match="max_retries"):
        PT.assert_role_hardened("planner", m, FAKE)


@pytest.mark.unit
@pytest.mark.parametrize("bad", [None, float("inf"), 0])
def test_missing_or_infinite_timeout_refuses(tmp_path, bad):
    g = mkgate(tmp_path)
    m, _ = role_model(g, "planner", FakeProvider(timeout=bad))
    with pytest.raises(GateConfigError, match="timeout"):
        PT.assert_role_hardened("planner", m, FAKE)


# 5 / 6 —— 未知模型 / 未核实价格拒绝
@pytest.mark.unit
def test_unknown_model_refused_by_price_table():
    with pytest.raises(PriceUnverified, match="未知模型"):
        PR.price_for("gpt-9-turbo")


@pytest.mark.unit
def test_unverified_price_is_fail_closed():
    """deepseek-chat：官方页已不单列价格且将弃用 → 必须拒绝，不得回退 Opus 价。"""
    assert PR.PRICES["deepseek-chat"]["status"] == "unverified"
    with pytest.raises(PriceUnverified, match="未经官方核实"):
        PR.price_for("deepseek-chat")
    assert "deepseek-chat" not in HG.PRICES, "未核实模型不得进入运行价格表"


@pytest.mark.unit
def test_no_silent_fallback_to_opus():
    """未知模型绝不能被静默按 Opus 计价（A.6.2 的真实缺陷）。"""
    with pytest.raises(PriceUnverified):
        PR.worst_case_usd("some-unlisted-model", 1000, 1000)


# 7-10 —— 分角色上限，全部在 provider 之前拒绝
@pytest.mark.unit
@pytest.mark.parametrize("role,cap", [("planner", 2), ("verifier", 2),
                                      ("claim_extractor", 1), ("executor", 16)])
def test_per_role_cap_rejects_before_provider(tmp_path, role, cap):
    g = mkgate(tmp_path)
    m, p = role_model(g, role)
    g.start_task("T")
    for _ in range(cap):
        m.invoke("x")
    assert p.calls == cap
    with pytest.raises(BudgetExceeded, match=f"max_calls_per_role\\[{role}\\]"):
        m.invoke("x")
    assert p.calls == cap, f"{role} 第 {cap + 1} 次必须在 provider 之前被拒"


# 11 —— 总调用第 22 次拒绝
@pytest.mark.unit
def test_total_21_then_22nd_rejected(tmp_path):
    g = mkgate(tmp_path)
    roles = all_roles(g)
    g.start_task("T")
    n = 0
    for r, cap in (("planner", 2), ("verifier", 2), ("claim_extractor", 1), ("executor", 16)):
        for _ in range(cap):
            roles[r][0].invoke("x")
            n += 1
    assert n == 21 and g.calls_task == 21
    with pytest.raises(BudgetExceeded):
        roles["executor"][0].invoke("x")
    assert sum(p.calls for _, p in roles.values()) == 21


# 12 —— 角色之间不能借用额度
@pytest.mark.unit
def test_roles_cannot_borrow_quota(tmp_path):
    g = mkgate(tmp_path)
    roles = all_roles(g)
    g.start_task("T")
    for _ in range(16):                       # Executor 用满
        roles["executor"][0].invoke("x")
    with pytest.raises(BudgetExceeded, match="executor"):
        roles["executor"][0].invoke("x")
    roles["planner"][0].invoke("x")           # Planner 自己的额度不受影响
    assert roles["planner"][1].calls == 1
    with pytest.raises(BudgetExceeded, match="planner"):   # 也不能借 executor 的
        roles["planner"][0].invoke("x")
        roles["planner"][0].invoke("x")


@pytest.mark.unit
def test_unconfigured_role_is_refused(tmp_path):
    g = mkgate(tmp_path)
    m, p = role_model(g, "some_new_role")
    g.start_task("T")
    with pytest.raises(BudgetExceeded, match="未为角色"):
        m.invoke("x")
    assert p.calls == 0


# 13 —— max_tokens 确实受限并传到请求配置
@pytest.mark.unit
def test_max_tokens_is_bounded_per_role(tmp_path):
    assert PT.MAX_TOKENS == {"planner": 2000, "executor": 3000,
                             "verifier": 2000, "claim_extractor": 2000}
    g = mkgate(tmp_path)
    for r, expect in PT.MAX_TOKENS.items():
        m, _ = role_model(g, r)
        assert PT.inspect_transport(m)["max_tokens"] == expect
        assert object.__getattribute__(m, "_max_tokens") == expect


# 14 / 15 / 16 —— 三级预算越界都不碰 provider
@pytest.mark.unit
@pytest.mark.parametrize("kw,pat", [
    (dict(max_usd_task=1e-9), "max_usd_task"),
    (dict(max_usd_stage=1e-9, max_usd_task=1e6), "max_usd_stage"),
    (dict(max_usd_global=1e-9, max_usd_task=1e6, max_usd_stage=1e6), "max_usd_global"),
])
def test_budget_ceilings_reject_before_provider(tmp_path, kw, pat):
    HG.PRICES["priced-v2"] = {"input": 10.0, "output": 10.0, "source": "test"}
    g = mkgate(tmp_path, max_calls_per_model={"priced-v2": 99}, **kw)
    p = FakeProvider()
    m = GatedModel(p, g, role="planner", model_id="priced-v2", max_tokens=2000)
    g.start_task("T")
    with pytest.raises(BudgetExceeded, match=pat):
        m.invoke("x" * 200000)
    assert p.calls == 0
    HG.PRICES.pop("priced-v2")


# 17 —— 请求超时保留 reservation，且不得判为未计费
@pytest.mark.unit
def test_request_timeout_holds_reservation_and_may_be_billed(tmp_path):
    g = mkgate(tmp_path)
    exc = TimeoutError("read timeout")
    m, p = role_model(g, "planner", FakeProvider(raise_exc=exc))
    g.start_task("T")
    with pytest.raises(TimeoutError):
        m.invoke("x")
    ev = [e for e in g.ledger.events() if e["event"] == "failed_maybe_billed"]
    assert ev and ev[0]["billing_state"] == "provider_may_have_billed"
    assert ev[0]["held_usd"] >= 0
    assert PT.classify_failure(exc) == "provider_may_have_billed"


# 18 —— usage 未知保留最坏费用
@pytest.mark.unit
def test_unknown_usage_holds_worst_case(tmp_path):
    HG.PRICES["nou-v2"] = {"input": 10.0, "output": 10.0, "source": "test"}
    g = mkgate(tmp_path, max_calls_per_model={"nou-v2": 9})
    p = FakeProvider(usage=False)
    m = GatedModel(p, g, role="planner", model_id="nou-v2", max_tokens=2000)
    g.start_task("T")
    m.invoke("x")
    ev = [e for e in g.ledger.events() if e["event"] == "usage_unknown"]
    assert ev and ev[0]["held_usd"] > 0
    HG.PRICES.pop("nou-v2")


# 19 / 22 —— 网络错误不自动重试；provider 调用计数证明没有 SDK 重试
@pytest.mark.unit
def test_network_error_is_not_auto_retried(tmp_path):
    g = mkgate(tmp_path)
    m, p = role_model(g, "planner", FakeProvider(raise_exc=ConnectionError("dns fail")))
    g.start_task("T")
    with pytest.raises(ConnectionError):
        m.invoke("x")
    assert p.calls == 1, "一次逻辑调用只能产生一次 HTTP 请求，不得自动重试"
    assert PT.classify_failure(ConnectionError("dns fail")) == "network_error_no_auto_retry"


@pytest.mark.unit
def test_one_logical_call_equals_one_http_request(tmp_path):
    g = mkgate(tmp_path)
    m, p = role_model(g, "executor")
    g.start_task("T")
    for _ in range(5):
        m.invoke("x")
    assert p.calls == 5 and g.calls_by_role["executor"] == 5


# 20 / 21 —— 任务超时 / 用户取消后所有角色拒绝
@pytest.mark.unit
def test_task_timeout_blocks_every_role(tmp_path):
    g = mkgate(tmp_path)
    roles = all_roles(g)
    g.start_task("T")
    g.stop_task("task_timeout")
    for r, (m, p) in roles.items():
        with pytest.raises(BudgetExceeded, match="task_stopped"):
            m.invoke("x")
        assert p.calls == 0


@pytest.mark.unit
def test_user_cancel_blocks_every_role(tmp_path):
    g = mkgate(tmp_path)
    roles = all_roles(g)
    g.start_task("T")
    g.cancel()
    for r, (m, p) in roles.items():
        with pytest.raises(BudgetExceeded, match="user_cancelled"):
            m.invoke("x")
        assert p.calls == 0


def frozen_sha256(path):
    """冻结 hash 定义为**对 LF 规范化字节**求 SHA-256。
    否则 Git 在 Windows 检出时把 LF 转成 CRLF，同一份文件在两个平台上 hash 不同
    （CI #25 windows-latest 就是这样红的）。`.gitattributes` 已用 -text 兜底，
    这里再做一次规范化，保证 hash 与平台无关。"""
    raw = Path(path).read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(raw).hexdigest()


# 23 —— v1 文件与 hash 保持不变
@pytest.mark.unit
def test_v1_protocol_and_hash_unchanged():
    assert frozen_sha256(ROOT / "SHADOW_PILOT_ROUND2_PROTOCOL.md") == V1_SHA
    rec = (ROOT / "SHADOW_PILOT_ROUND2_PROTOCOL.sha256").read_text(encoding="utf-8")
    assert V1_SHA in rec


@pytest.mark.unit
def test_v2_hash_is_recorded_and_platform_stable():
    v2 = frozen_sha256(ROOT / "SHADOW_PILOT_ROUND2_PROTOCOL_V2.md")
    rec = (ROOT / "SHADOW_PILOT_ROUND2_PROTOCOL_V2.sha256").read_text(encoding="utf-8")
    assert v2 in rec, "v2 正文与记录的 hash 不一致"


# 24 —— 12 道题与评分规则与 v1 逐字一致
@pytest.mark.unit
def test_tasks_and_scoring_identical_to_v1():
    from pilot.round2_tasks import PROTOCOL_SHA256, TASKS
    assert PROTOCOL_SHA256 == V1_SHA
    assert len(TASKS) == 12
    v1_text = (ROOT / "SHADOW_PILOT_ROUND2_PROTOCOL.md").read_text(encoding="utf-8")
    for tid, t in TASKS.items():
        assert f"**{tid} —" in v1_text or f"**{tid} " in v1_text, f"{tid} 不在 v1 正文中"
        for bad in ("expected_multiplicity", "hard_fail"):
            assert bad in t
    v2_text = (ROOT / "SHADOW_PILOT_ROUND2_PROTOCOL_V2.md").read_text(encoding="utf-8")
    assert "与 v1 逐字一致" in v2_text
    # v2 不得包含任何题目正文（题目只存在于 v1）
    assert "ZZQX7 基因与皮肤纤维化的关系" not in v2_text


# 25 —— CI 不能进入付费路径
@pytest.mark.unit
def test_ci_cannot_enter_paid_path(tmp_path, monkeypatch):
    g = mkgate(tmp_path)
    m, p = role_model(g, "planner")
    g.start_task("T")
    monkeypatch.setenv("CI", "true")
    with pytest.raises(GateConfigError, match="CI 环境禁止"):
        m.invoke("x")
    assert p.calls == 0


# ---------- 真实编排 + fake models 全链 ----------
@pytest.mark.unit
def test_fake_full_chain_typical_worst_and_22nd_rejected(tmp_path):
    g = mkgate(tmp_path)
    roles = all_roles(g)
    g.start_task("A1_fake_v2")
    graph = []

    def call(role):
        roles[role][0].invoke("payload")
        graph.append({"call_index": len(graph) + 1, "role": role})

    # 典型路径：2 轮 × (1 planner + 4 executor + 1 verifier) + 1 claim = 13
    for _ in range(2):
        call("planner")
        for _ in range(4):
            call("executor")
        call("verifier")
    call("claim_extractor")
    assert len(graph) == 13, "典型路径应为 13 次"

    # 继续跑到最坏 21（Executor 还剩 16-8=8 次）
    for _ in range(8):
        call("executor")
    assert len(graph) == 21 and g.calls_task == 21

    # 第 22 次：所有角色都已用尽，provider 不得被调用
    before = {r: p.calls for r, (_, p) in roles.items()}
    for r in PT.ROLES:
        with pytest.raises(BudgetExceeded):
            roles[r][0].invoke("payload")
    assert {r: p.calls for r, (_, p) in roles.items()} == before

    # 账本：reservation 与 reconciliation 均完成
    ev = g.ledger.events()
    reserved = [e for e in ev if e["event"] == "reserved"]
    reconciled = [e for e in ev if e["event"] == "reconciled"]
    assert len(reserved) == 21 and len(reconciled) == 21
    assert g.reserved_usd == pytest.approx(0.0, abs=1e-9)   # 未用额度全部释放

    # 生成完整 fake Manifest 并做脱敏审计
    import manifest_safety as MS
    manifest = MS.sanitize_manifest({
        "run_id": "fake_v2_run", "question": "fake question", "shadow_status": "ok",
        "selected_tools": ["search_evidence"], "allowed_tools": ["search_evidence"],
        "tool_events": [{"tool_name": "search_evidence", "ok": True}],
        "evidence_cards": [], "claims": [], "comparison": {"agree": True},
        "note": json.dumps(graph, ensure_ascii=False)})
    assert manifest["manifest_schema_version"] == "runmanifest-v1"
    blob = json.dumps(manifest, ensure_ascii=False) + json.dumps(ev, ensure_ascii=False)
    for leak in ("sk-", "Authorization", "Cookie", "Bearer ", "ANTHROPIC_API_KEY",
                 "DEEPSEEK_API_KEY", str(ROOT)):
        assert leak not in blob, f"账本/Manifest 泄露了 {leak!r}"
    # 账本只存元数据，不存完整 Prompt
    assert all("payload" not in json.dumps(e, ensure_ascii=False) for e in reserved)


@pytest.mark.unit
def test_price_table_is_versioned_dated_and_sourced():
    meta = PR.table_meta()
    assert meta["price_table_version"] == "2026-07-20.1"
    assert meta["effective_date"] == "2026-07-20" and meta["queried_on"] == "2026-07-20"
    opus = PR.price_for("claude-opus-4-8")
    assert opus["usd_per_mtok"]["input_base"] == 5.00
    assert opus["usd_per_mtok"]["output"] == 25.00
    assert opus["source"].startswith("https://platform.claude.com")
    ds = PR.price_for("deepseek-v4-flash")
    assert ds["usd_per_mtok"]["input_cache_miss"] == 0.14
    assert ds["usd_per_mtok"]["output"] == 0.28
    assert "prompt_cache_hit_tokens" in ds["usage_fields"]
    # 最坏输入单价取最贵一档（Opus = 1h 缓存写 $10）
    assert PR.worst_input_rate("claude-opus-4-8") == 10.00


@pytest.mark.unit
def test_unknown_usage_field_is_refused():
    with pytest.raises(PriceUnverified, match="未登记的 usage 字段"):
        PR.assert_usage_fields_known("claude-opus-4-8", ["input_tokens", "mystery_tokens"])
    assert PR.assert_usage_fields_known("claude-opus-4-8",
                                        ["input_tokens", "output_tokens"]) is True
