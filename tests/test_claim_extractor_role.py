"""A.7.1.3：Claim 提取绑定到专用 gated 角色。零真实 API、零付费；使用临时账本。"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pilot import paid_transport as PT
from pilot.hard_gate import BudgetExceeded, GateConfigError, GatedModel, HardBudgetGate

PMID = "41657283"
DOI = "10.1080/03009742.2024.2302553"
A1Q = (f"请检索 PMID {PMID} 与 DOI {DOI}，分别报告标题、年份、期刊，"
       f"并说明这两条记录你是通过精确 ID 命中还是排序候选得到的。")

@pytest.fixture(autouse=True)
def _switches(monkeypatch):
    """Gate 要求两个显式开关；本文件全部使用 FakeInner + 临时账本，无任何真实调用。"""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("REUMANI_PILOT_PAID", "1")
    monkeypatch.setenv("REUMANI_PILOT_CONFIRM", "test")


LIMITS = dict(max_usd_global=25.0, max_usd_stage=3.0, max_usd_task=1.5,
              max_calls_global=200, max_calls_task=21,
              max_calls_per_model={"claude-opus-4-8": 4, PT.PINNED_DEEPSEEK: 17},
              max_calls_per_role={"planner": 2, "verifier": 2,
                                  "claim_extractor": 1, "executor": 16},
              task_timeout_s=600, max_retries=0, default_max_tokens=2000)


class FakeInner:
    """假底层客户端：不发网络请求，只计数。"""
    def __init__(self, content="[]", model_id=PT.PINNED_DEEPSEEK):
        self.calls, self.content = 0, content
        self.max_retries, self.timeout, self.max_tokens = 0, 120.0, 2000
        self.extra_body = dict(PT.THINKING_DISABLED)
        self.model_name = model_id

    def invoke(self, *a, **k):
        from langchain_core.messages import AIMessage
        self.calls += 1
        return AIMessage(content=self.content,
                         usage_metadata={"input_tokens": 10, "output_tokens": 5,
                                         "total_tokens": 15})

    def bind_tools(self, *a, **k):
        return self


def mkgate(tmp_path):
    return HardBudgetGate(stage="test", ledger_path=tmp_path / "l.jsonl", **LIMITS)


def mkrole(gate, role, inner=None, model_id=None):
    mid = model_id or (PT.PINNED_DEEPSEEK if role in PT.DEEPSEEK_ROLES else "claude-opus-4-8")
    return GatedModel(inner or FakeInner(model_id=mid), gate, role=role,
                      model_id=mid, max_tokens=2000)


CLAIM_JSON = ('[{"text": "PMID 41657283 存在", "claim_type": "existence", '
              '"causal_strength": "none", "supporting_ids": []}]')
VERIFY_JSON = '{"passed": true, "reason": "ok", "missing": "无"}'


def hit(s, m): return {"source": s, "retrieval_status": "exact_hit", "metadata": m,
                       "error_type": None, "http_status": 200, "attempts": []}
def zero(s): return {"source": s, "retrieval_status": "zero_hits", "metadata": {},
                     "error_type": None, "http_status": 200, "attempts": []}


class FakeSrc:
    def pubmed_by_pmid(self, p): return hit("pubmed", {"pmid": p, "doi": "10.1/x", "title": "T",
                                                       "journal": "J", "year": "2026"})
    def epmc_by_pmid(self, p): return hit("europepmc", {"pmid": p, "doi": "10.1/x", "year": "2026"})
    def crossref_by_doi(self, d): return zero("crossref")
    def doiorg_by_doi(self, d): return zero("doi.org")
    def epmc_by_doi(self, d): return zero("europepmc")


def run_exact_id(gate, tmp_path, claim_inner=None, verifier_inner=None):
    """走与 Pilot 相同的显式注入路径。"""
    from shadow import build_claim_extractor
    from pilot.exact_id_agent import run_routed_agent
    claim_model = mkrole(gate, "claim_extractor", claim_inner or FakeInner(CLAIM_JSON))
    verifier = mkrole(gate, "verifier", verifier_inner or FakeInner(VERIFY_JSON),
                      model_id="claude-opus-4-8")
    PT.assert_claim_extractor_ready(claim_model, gate)
    gate.start_task("A1")
    try:
        return run_routed_agent(A1Q, sources=FakeSrc(), verifier_model=verifier,
                                claim_extractor=build_claim_extractor(claim_model),
                                stamp="T1"), claim_model, verifier
    finally:
        gate.end_task()


# ---------------- 1-7,10-12 Exact-ID 路径角色计量 ----------------
@pytest.mark.unit
def test_exact_id_role_accounting(tmp_path):
    g = mkgate(tmp_path)
    state, claim_model, verifier = run_exact_id(g, tmp_path)
    cbr = dict(g.calls_by_role)
    assert cbr.get("planner", 0) == 0                       # 1
    assert cbr.get("executor", 0) == 0                      # 2 / 6 Claim 不增加 executor
    assert cbr.get("verifier", 0) >= 1                      # 3
    assert cbr.get("claim_extractor", 0) >= 1               # 4 / 7
    assert set(cbr) <= {"verifier", "claim_extractor"}      # 5 只出现实际运行的角色
    assert state.shadow.get("shadow_status") == "ok"        # 10 仍到达 Shadow
    assert len(state.evidence_cards) == 1                   # 11 EvidenceCard 不变
    assert state.evidence_cards[0]["pmid"] == PMID
    assert state.verification_results[-1]["passed"] is True  # 12 旧 Verifier 决定最终答案
    assert "未验证" not in state.final_answer


# ---------------- 8,9 Claim 上限 ----------------
@pytest.mark.unit
def test_claim_cap_one_and_second_rejected_before_provider(tmp_path):
    g = mkgate(tmp_path)
    inner = FakeInner(CLAIM_JSON)
    claim_model = mkrole(g, "claim_extractor", inner)
    g.start_task("A1")
    claim_model.invoke("first")                              # 8 第一次允许
    assert inner.calls == 1
    with pytest.raises(BudgetExceeded):                      # 9 第二次在 provider 前拒绝
        claim_model.invoke("second")
    assert inner.calls == 1                                  # provider 调用数未增加
    g.end_task()


@pytest.mark.unit
def test_roles_do_not_borrow_each_other_quota(tmp_path):
    """Executor 剩余额度不能借给 Claim；Claim 剩余额度不能借给 Executor。"""
    g = mkgate(tmp_path)
    claim_inner, exec_inner = FakeInner(CLAIM_JSON), FakeInner("x")
    claim_model = mkrole(g, "claim_extractor", claim_inner)
    exec_model = mkrole(g, "executor", exec_inner)
    g.start_task("A1")
    claim_model.invoke("c1")
    with pytest.raises(BudgetExceeded):
        claim_model.invoke("c2")                             # executor 还有 16 额度也不能借
    for i in range(3):
        exec_model.invoke(f"e{i}")                           # executor 自己的额度正常
    assert g.calls_by_role["claim_extractor"] == 1 and g.calls_by_role["executor"] == 3
    g.end_task()


# ---------------- 13 开放路径角色分离 ----------------
@pytest.mark.unit
def test_open_react_path_separates_executor_and_claim(tmp_path, monkeypatch):
    """开放 ReAct 路径：Executor 只记 ReAct 调用，Claim 单独计量。"""
    import ssc_a1
    from shadow import build_claim_extractor
    from pilot.exact_id_agent import run_routed_agent

    g = mkgate(tmp_path)
    exec_inner, claim_inner = FakeInner("done"), FakeInner(CLAIM_JSON)
    exec_model = mkrole(g, "executor", exec_inner)
    claim_model = mkrole(g, "claim_extractor", claim_inner)
    verifier = mkrole(g, "verifier", FakeInner(VERIFY_JSON), model_id="claude-opus-4-8")
    planner = mkrole(g, "planner", FakeInner("plan"), model_id="claude-opus-4-8")

    captured = {}

    def fake_run_agent(q, **kw):
        captured.update(kw)
        st = ssc_a1.AgentState(user_query=q)
        exec_model.invoke("react step")                      # 模拟 ReAct executor 调用
        kw["claim_extractor"]("final text", ["PMID:1"])       # 模拟 Shadow 里的 Claim 提取
        return st

    monkeypatch.setattr(ssc_a1, "run_agent", fake_run_agent)
    g.start_task("B")
    run_routed_agent("请综述 SSc 机制（无 ID）", planner_model=planner,
                     verifier_model=verifier,
                     claim_extractor=build_claim_extractor(claim_model))
    g.end_task()
    assert g.calls_by_role["executor"] == 1                   # 只记 ReAct
    assert g.calls_by_role["claim_extractor"] == 1            # Claim 独立计量
    assert captured.get("claim_extractor") is not None        # 显式透传到开放路径


# ---------------- 14,15 Pilot 不用全局回退 ----------------
@pytest.mark.unit
def test_pilot_does_not_use_default_claim_extractor(monkeypatch, tmp_path):
    import shadow as SH
    called = {"n": 0}
    orig = SH.default_claim_extractor
    monkeypatch.setattr(SH, "default_claim_extractor",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or orig(*a, **k))
    g = mkgate(tmp_path)
    run_exact_id(g, tmp_path)
    assert called["n"] == 0                                   # 14


@pytest.mark.unit
def test_pilot_does_not_read_global_deepseek_as_claim_model(monkeypatch, tmp_path):
    """15：Claim 模型不得来自全局 ssc_pi_agent.deepseek_llm_pro。"""
    import ssc_pi_agent as P
    sentinel = object()
    monkeypatch.setattr(P, "deepseek_llm_pro", sentinel, raising=False)
    g = mkgate(tmp_path)
    state, claim_model, _ = run_exact_id(g, tmp_path)
    assert object.__getattribute__(claim_model, "_role") == "claim_extractor"
    assert g.calls_by_role.get("claim_extractor", 0) >= 1
    assert g.calls_by_role.get("executor", 0) == 0


@pytest.mark.unit
def test_round2_runner_wires_dedicated_role():
    """Pilot 接线必须显式构造专用 Claim extractor 并传入。"""
    src = (ROOT / "pilot" / "round2_runner.py").read_text(encoding="utf-8")
    assert "assert_claim_extractor_ready" in src
    assert "build_claim_extractor(roles[\"claim_extractor\"])" in src
    assert "claim_extractor=claim_extractor" in src
    assert "default_claim_extractor" not in src               # Pilot 不用向后兼容入口


# ---------------- 16,17,18 fail-closed ----------------
@pytest.mark.unit
def test_fail_closed_missing_claim_role(tmp_path):
    g = mkgate(tmp_path)
    with pytest.raises(GateConfigError):
        PT.assert_claim_extractor_ready(None, g)              # 16


@pytest.mark.unit
def test_fail_closed_wrong_role_label(tmp_path):
    g = mkgate(tmp_path)
    wrong = mkrole(g, "executor")                             # 17 role 标签错
    with pytest.raises(GateConfigError):
        PT.assert_claim_extractor_ready(wrong, g)


@pytest.mark.unit
def test_fail_closed_unwrapped_model(tmp_path):
    g = mkgate(tmp_path)
    with pytest.raises(GateConfigError):
        PT.assert_claim_extractor_ready(FakeInner(), g)       # 18 未包装


@pytest.mark.unit
def test_fail_closed_wrong_gate_or_model_or_retries(tmp_path):
    g1, g2 = mkgate(tmp_path), mkgate(tmp_path / "b")
    other_gate_model = mkrole(g2, "claim_extractor")
    with pytest.raises(GateConfigError):                      # 账本/gate 不是当前 Stage
        PT.assert_claim_extractor_ready(other_gate_model, g1)
    bad_model = GatedModel(FakeInner(model_id="deepseek-chat"), g1,
                           role="claim_extractor", model_id="deepseek-chat", max_tokens=2000)
    with pytest.raises(GateConfigError):                      # 模型非冻结 flash
        PT.assert_claim_extractor_ready(bad_model, g1)
    inner = FakeInner(); inner.max_retries = 2
    retry_model = GatedModel(inner, g1, role="claim_extractor",
                             model_id=PT.PINNED_DEEPSEEK, max_tokens=2000)
    with pytest.raises(GateConfigError):                      # max_retries≠0
        PT.assert_claim_extractor_ready(retry_model, g1)


@pytest.mark.unit
def test_fail_closed_thinking_not_disabled(tmp_path):
    g = mkgate(tmp_path)
    inner = FakeInner(); inner.extra_body = {}                # 未声明 thinking
    m = GatedModel(inner, g, role="claim_extractor",
                   model_id=PT.PINNED_DEEPSEEK, max_tokens=2000)
    with pytest.raises(GateConfigError):
        PT.assert_claim_extractor_ready(m, g)


# ---------------- 19,20 零付费 / 真实账本不变 ----------------
@pytest.mark.unit
def test_no_real_paid_calls_and_shared_ledger_untouched(tmp_path):
    real = ROOT / "pilot" / "round2_results" / "stage1_ledger.jsonl"
    before = real.stat().st_size if real.exists() else None
    g = mkgate(tmp_path)
    state, claim_model, verifier = run_exact_id(g, tmp_path)
    # 只用了 FakeInner，没有任何真实 provider 调用
    assert object.__getattribute__(claim_model, "_inner").calls == 1
    after = real.stat().st_size if real.exists() else None
    assert before == after                                    # 20 共享账本不变
    assert (tmp_path / "l.jsonl").exists()                    # 计量写到临时账本
