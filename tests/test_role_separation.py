"""A.6.3.3：Planner / Verifier 计量身份拆分 + 动态付费客户端发现 + preflight 证据语义。
全部 fake provider / dry-run，**零真实 API**。"""
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pilot import hard_gate as HG
from pilot import paid_transport as PT
from pilot.hard_gate import BudgetExceeded, GateConfigError, GatedModel, HardBudgetGate

FAKE = "fake-model"
ROLE_CAPS = {"planner": 2, "verifier": 2, "claim_extractor": 1, "executor": 16}


@pytest.fixture(autouse=True)
def _sw(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv(HG.ENV_PAID, "1")
    monkeypatch.setenv(HG.ENV_CONFIRM, "test")


def mkgate(tmp_path, **kw):
    d = dict(stage="test", ledger_path=tmp_path / "rs.jsonl",
             max_usd_global=25.0, max_usd_stage=3.0, max_usd_task=1.5,
             max_calls_global=200, max_calls_task=21,
             max_calls_per_model={FAKE: 999}, max_calls_per_role=dict(ROLE_CAPS),
             task_timeout_s=600, max_retries=0, default_max_tokens=2000)
    d.update(kw)
    return HardBudgetGate(**d)


class FakeChat:
    def __init__(self, tag="x", script=None):
        self.tag, self.calls = tag, 0
        self.script, self._i = list(script or []), 0
        self.max_retries, self.timeout, self.max_tokens = 0, 120.0, 2000
        self.extra_body = dict(PT.THINKING_DISABLED)

    def invoke(self, *a, **k):
        self.calls += 1
        item = (self.script[min(self._i, len(self.script) - 1)] if self.script else {})
        self._i += 1
        return SimpleNamespace(content=item.get("content", "ok"),
                               tool_calls=item.get("tool_calls", []),
                               usage_metadata={"input_tokens": 100, "output_tokens": 20},
                               response_metadata={})

    def bind_tools(self, *a, **k):
        return self


def pv_pair(gate, shared_inner=False):
    """构造 planner / verifier 两个独立 wrapper（可选共享底层客户端）。"""
    inner_p = FakeChat("opus")
    inner_v = inner_p if shared_inner else FakeChat("opus")
    p = GatedModel(inner_p, gate, role="planner", model_id=FAKE, max_tokens=2000)
    v = GatedModel(inner_v, gate, role="verifier", model_id=FAKE, max_tokens=2000)
    return p, v, inner_p, inner_v


# 1 / 2 —— 两个 wrapper 不同；同底层也要独立计量
@pytest.mark.unit
def test_planner_and_verifier_are_distinct_wrappers(tmp_path):
    g = mkgate(tmp_path)
    p, v, _, _ = pv_pair(g)
    assert p is not v
    assert object.__getattribute__(p, "_role") == "planner"
    assert object.__getattribute__(v, "_role") == "verifier"
    assert object.__getattribute__(p, "_gate") is object.__getattribute__(v, "_gate") is g


@pytest.mark.unit
def test_same_underlying_client_still_meters_separately(tmp_path):
    g = mkgate(tmp_path)
    p, v, inner_p, inner_v = pv_pair(g, shared_inner=True)
    assert inner_p is inner_v                       # 底层同一个客户端
    g.start_task("T")
    p.invoke("plan"); v.invoke("verify")
    assert g.calls_by_role == {"planner": 1, "verifier": 1}


# 3 / 4 / 5 —— 上限独立 + 不可借用
@pytest.mark.unit
def test_planner_cap_independent_and_verifier_unaffected(tmp_path):
    g = mkgate(tmp_path)
    p, v, ip, iv = pv_pair(g)
    g.start_task("T")
    p.invoke("1"); p.invoke("2")
    with pytest.raises(BudgetExceeded, match=r"max_calls_per_role\[planner\]"):
        p.invoke("3")
    assert ip.calls == 2                              # 第 3 次未触达 provider
    v.invoke("1"); v.invoke("2")                      # Verifier 自己的额度不受影响
    assert iv.calls == 2
    assert g.calls_by_role == {"planner": 2, "verifier": 2}


@pytest.mark.unit
def test_verifier_cap_independent_and_planner_unaffected(tmp_path):
    g = mkgate(tmp_path)
    p, v, ip, iv = pv_pair(g)
    g.start_task("T")
    v.invoke("1"); v.invoke("2")
    with pytest.raises(BudgetExceeded, match=r"max_calls_per_role\[verifier\]"):
        v.invoke("3")
    assert iv.calls == 2
    p.invoke("1")
    assert ip.calls == 1


@pytest.mark.unit
def test_roles_cannot_borrow_each_other_quota(tmp_path):
    g = mkgate(tmp_path)
    p, v, ip, iv = pv_pair(g)
    g.start_task("T")
    for _ in range(2):
        v.invoke("x")
    with pytest.raises(BudgetExceeded, match="verifier"):
        v.invoke("x")                                  # verifier 用尽
    p.invoke("x"); p.invoke("x")                       # planner 仍有自己的 2 次
    with pytest.raises(BudgetExceeded, match="planner"):
        p.invoke("x")
    assert ip.calls == 2 and iv.calls == 2


# 6 / 7 —— 费用分别统计，但共同受单题/Stage 预算约束
@pytest.mark.unit
def test_costs_tracked_per_role_and_shared_budget(tmp_path):
    g = mkgate(tmp_path)
    p, v, _, _ = pv_pair(g)
    g.start_task("T")
    p.invoke("x"); v.invoke("x")
    ev = [e for e in g.ledger.events() if e["event"] == "reserved"]
    roles = [e["role"] for e in ev]
    assert roles == ["planner", "verifier"]
    assert all(e["price_config_version"] for e in ev)
    assert g.usd_task >= 0 and g.usd_stage >= 0        # 同一 Stage 账本共同累计
    assert g.calls_task == 2


@pytest.mark.unit
def test_shared_task_budget_still_binds_both_roles(tmp_path):
    from pilot import prices as PR
    PR.PRICES["rs-priced"] = {"provider": "test", "status": "verified",
                              "verified_on": "2026-07-20", "source": "test_only",
                              "usd_per_mtok": {"input_cache_miss": 100.0, "output": 100.0},
                              "usage_fields": ["prompt_tokens", "completion_tokens"]}
    g = mkgate(tmp_path, max_usd_task=1e-9, max_calls_per_model={"rs-priced": 99})
    ip, iv = FakeChat(), FakeChat()
    p = GatedModel(ip, g, role="planner", model_id="rs-priced", max_tokens=2000)
    v = GatedModel(iv, g, role="verifier", model_id="rs-priced", max_tokens=2000)
    g.start_task("T")
    for m in (p, v):
        with pytest.raises(BudgetExceeded, match="max_usd_task"):
            m.invoke("x" * 50000)
    assert ip.calls == 0 and iv.calls == 0
    PR.PRICES.pop("rs-priced")


# 8 / 9 / 10 —— 默认行为不变 / 注入生效 / 裁决权不变
@pytest.mark.unit
def test_default_path_is_backward_compatible():
    """未注入时 ssc_a1 必须沿用原全局对象，签名保持向后兼容。"""
    import inspect

    import ssc_a1
    sig = inspect.signature(ssc_a1.run_agent)
    assert sig.parameters["planner_model"].default is None
    assert sig.parameters["verifier_model"].default is None
    vsig = inspect.signature(ssc_a1.verify)
    assert vsig.parameters["verifier_model"].default is None
    src = Path(ssc_a1.__file__).read_text(encoding="utf-8")
    assert "planner_model or (judge_llm if judge_model" in src
    assert "verifier_model or (judge_llm if judge_model" in src


@pytest.mark.unit
def test_injection_routes_to_correct_wrapper(tmp_path, monkeypatch):
    """Planner 调用只进 planner wrapper；Verifier 调用只进 verifier wrapper。"""
    import ssc_a1
    g = mkgate(tmp_path)
    p, v, ip, iv = pv_pair(g)
    g.start_task("T")
    state = ssc_a1.AgentState(user_query="q", max_iterations=1)
    state.plan = "p"
    # 只驱动 verify 的模型调用路径（注入 verifier wrapper）
    monkeypatch.setattr(ssc_a1, "_has_citations", lambda t: True)
    ssc_a1.verify(state, "结论 PMID 12345678", verifier_model=v)
    assert iv.calls == 1 and ip.calls == 0
    assert g.calls_by_role.get("verifier") == 1 and "planner" not in g.calls_by_role


@pytest.mark.unit
def test_old_verifier_still_owns_final_answer():
    """注入只换模型对象，不改最终裁决权：final_answer 仍由旧 verify 的 passed 决定。"""
    import ssc_a1
    src = Path(ssc_a1.__file__).read_text(encoding="utf-8")
    assert 'if v.get("passed") is True:' in src
    assert "state.final_answer = output" in src
    # shadow 仍然只记录
    assert "【只记录+对比，不改最终答案】" in src


# 11 / 12 —— 动态发现未知属性名的付费客户端
@pytest.mark.unit
def test_discovers_paid_client_with_arbitrary_attr_name(tmp_path):
    import ssc_pi_agent as P
    g = mkgate(tmp_path)
    attr = "zz_some_random_llm_name_9137"
    try:
        from langchain_openai import ChatOpenAI
        setattr(P, attr, ChatOpenAI(model="deepseek-v4-flash", api_key="DUMMY-not-real",
                                    base_url="https://api.deepseek.com"))
        found = [f"{m}.{a}" for m, a, o, w, r in PT.discover_paid_clients() if a == attr]
        assert found, "未能按类型发现随机属性名的付费客户端"
        with pytest.raises(GateConfigError, match="未包装的原始付费客户端"):
            PT.assert_no_raw_paid_client_reachable()
        PT.neutralize_unused_paid_clients(g)
        assert getattr(getattr(P, attr), "_reumani_hard_gate_wrapped", False)
        with pytest.raises(BudgetExceeded, match="未为角色"):    # 无配额角色 → 拒绝
            g.start_task("T")
            getattr(P, attr).invoke("x")
    finally:
        if hasattr(P, attr):
            delattr(P, attr)


@pytest.mark.unit
def test_discovery_does_not_depend_on_fixed_names():
    src = (ROOT / "pilot" / "paid_transport.py").read_text(encoding="utf-8")
    assert "def _is_paid_client" in src
    assert "BaseChatModel" in src, "必须按类型识别，而不是按属性名"
    fn = src[src.index("def discover_paid_clients"):src.index("def neutralize_unused")]
    assert "PAID_ATTRS" not in fn, "发现逻辑不得依赖固定属性名列表"


# 13-16 —— preflight 证据语义
def _run_preflight(env=None):
    e = dict(os.environ)
    e["PYTHONIOENCODING"] = "utf-8"
    e.update(env or {})
    return subprocess.run([sys.executable, str(ROOT / "pilot" / "preflight_a1.py")],
                          cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=e, timeout=600)


@pytest.mark.unit
@pytest.mark.parametrize("drop,cid", [("REUMANI_EXPECT_PUBLIC_HEAD", 1),
                                      ("REUMANI_CI_EVIDENCE", 4),
                                      ("REUMANI_PYTEST_EVIDENCE", 5)])
def test_missing_external_evidence_refuses(drop, cid):
    env = {"REUMANI_EXPECT_PUBLIC_HEAD": "abc1234", "REUMANI_CI_EVIDENCE": "run#28",
           "REUMANI_PYTEST_EVIDENCE": "348 passed", "REUMANI_EXPECT_DEV_HEAD": "deadbee"}
    env[drop] = ""
    r = _run_preflight(env)
    assert r.returncode != 0
    assert "UNVERIF" in r.stdout, f"缺外部证据必须标 unverified_external，不能是 PASS\n{r.stdout}"


@pytest.mark.unit
def test_status_types_distinguish_auto_and_external():
    src = (ROOT / "pilot" / "preflight_a1.py").read_text(encoding="utf-8")
    for s in ("verified_automatic", "verified_external", "unverified_external",
              "failed", "skipped"):
        assert s in src
    assert "evidence_source" in src
    assert "token" not in src.lower().replace("REUMANI_", "") or "GitHub token" in src


@pytest.mark.unit
def test_preflight_never_touches_github_token():
    src = (ROOT / "pilot" / "preflight_a1.py").read_text(encoding="utf-8")
    for bad in ("GITHUB_TOKEN", "GH_TOKEN", "api.github.com", "Authorization"):
        assert bad not in src, f"preflight 不得访问 {bad}"


# 17 / 18 —— 全程零 HTTP，日志无密钥
@pytest.mark.unit
def test_zero_http_and_no_secret_in_logs():
    env = {"REUMANI_EXPECT_PUBLIC_HEAD": "abc1234", "REUMANI_CI_EVIDENCE": "run#28",
           "REUMANI_PYTEST_EVIDENCE": "348 passed",
           "REUMANI_EXPECT_DEV_HEAD": "0000000"}          # 故意不匹配 → 失败但不发请求
    r = _run_preflight(env)
    out = (r.stdout or "") + (r.stderr or "")
    for leak in ("sk-", "Bearer ", "Authorization", "Cookie", "DUMMY-"):
        assert leak not in out, f"preflight 日志泄露 {leak}"
    assert "http" not in out.lower().replace("https://api.deepseek.com", "")
