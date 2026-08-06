"""A.8.1.1R §5/§10 —— **真实生产调用链**上的 OutputContract 接线验收。

关键点：不是测 `apply_output_contract(fake_model, contract)` 本身，而是从真实
`GatedResearchExecutor` 入口驱动，断言请求确实带上了 provider 参数。
全部离线、断网可跑，真实付费调用为 0。
"""
import json

import pytest

from pilot.event_store import InMemoryEventStore
from pilot.hard_gate import GatedModel
from pilot.frozen_evidence import FrozenEvidenceLoader
from pilot.gated_research_executor import (GatedResearchExecutor, ExecutorConfigError, STAGES,
                                           OutputTruncated)
from pilot.provider_output import ProviderRefusal, ProviderCapabilityError
from pilot.research_results import ResearchOutputError, ROLE_MAX_TOKENS
from pilot.role_contracts import ANTHROPIC_OPUS_48, DEEPSEEK_V4_FLASH, contract_for
from tests.test_gated_research_executor import (SYN, VER, CLM, make_gate, make_spec, run_chain,
                                                REPO, _ctx)

pytestmark = pytest.mark.unit

# 真实生产能力表：两个 Anthropic 角色 native schema，DeepSeek json_object
LIVE_CAPS = {"claude-opus-4-8": ANTHROPIC_OPUS_48, "deepseek-v4-flash": DEEPSEEK_V4_FLASH}


@pytest.fixture(autouse=True)
def _gate_switches(monkeypatch):
    """离线 fake 验证也必须显式开关；生产代码从不设置它们。"""
    from pilot.hard_gate import ENV_PAID, ENV_CONFIRM
    monkeypatch.setenv(ENV_PAID, "1")
    monkeypatch.setenv(ENV_CONFIRM, "A755_offline")
    monkeypatch.delenv("CI", raising=False)


class SpyChat:
    """记录**最终发出的请求参数**的 spy（不联网、不付费）。"""

    def __init__(self, payload=None, raw=None, meta=None):
        self.payload, self.raw, self.meta = payload, raw, meta or {}
        self.calls = 0
        self.structured = None       # with_structured_output(...) 收到什么
        self.bound = None            # bind(...) 收到什么

    def with_structured_output(self, schema, **kw):
        self.structured = {"schema": schema, **kw}
        return self

    def bind(self, **kw):
        self.bound = dict(kw)
        return self

    def invoke(self, prompt, **k):
        self.calls += 1
        self.last_prompt = prompt
        body = self.raw if self.raw is not None else json.dumps(self.payload, ensure_ascii=False)
        meta = self.meta

        class R:
            content = body
            usage_metadata = {"input_tokens": 100, "output_tokens": 50}
            response_metadata = meta
        return R()


def live_gate(max_usd=0.18):
    """按真实生产形状配置：两个 Anthropic 角色 + 一个 DeepSeek 角色。"""
    import os, tempfile
    from pilot.hard_gate import HardBudgetGate
    return HardBudgetGate(stage="A755_offline",
                          ledger_path=os.path.join(tempfile.mkdtemp(), "l.jsonl"),
                          max_usd_global=max_usd, max_usd_stage=max_usd, max_usd_task=max_usd,
                          max_calls_global=3, max_calls_task=3,
                          max_calls_per_model={"claude-opus-4-8": 2, "deepseek-v4-flash": 1},
                          max_calls_per_role={"synthesizer": 1, "verifier": 1,
                                              "claim_extractor": 1},
                          task_timeout_s=60.0, max_retries=0, default_max_tokens=1600,
                          allow_ci=True)


def build_live(*, syn_raw=None, syn_meta=None, ver_raw=None, clm_raw=None, gate=None):
    gate = gate or live_gate()
    spies, models = {}, {}
    for role, payload, mid, raw, meta in (
            ("synthesizer", SYN, "claude-opus-4-8", syn_raw, syn_meta),
            ("verifier", VER, "claude-opus-4-8", ver_raw, None),
            ("claim_extractor", CLM, "deepseek-v4-flash", clm_raw, None)):
        s = SpyChat(payload, raw=raw, meta=meta)
        spies[role] = s
        models[role] = GatedModel(s, gate, role=role, model_id=mid,
                                  max_tokens=ROLE_MAX_TOKENS[role])
    ex = GatedResearchExecutor(synthesizer=models["synthesizer"], verifier=models["verifier"],
                               claim_extractor=models["claim_extractor"], gate=gate,
                               evidence_loader=FrozenEvidenceLoader(REPO), capabilities=LIVE_CAPS)
    return ex, gate, spies


def _run_stages(ex, upto):
    ctx, state = _ctx(ex), {}
    for s in STAGES:
        state.update(ex.run_stage(stage=s, ctx=ctx, state=state) or {})
        if s == upto:
            break
    return state


# ---------------------------------------------------------------- 15-18 真实链上的请求形状
def test_live_chain_sends_native_schema_for_both_anthropic_roles():
    """从真实 executor 入口驱动；两个 Anthropic 角色的请求必须带原生 JSON Schema。"""
    ex, _, spies = build_live()
    _run_stages(ex, "claim_extractor")
    for role in ("synthesizer", "verifier"):
        st = spies[role].structured
        assert st is not None, f"{role} 未发送 structured output 参数"
        assert st["method"] == "json_schema"
        assert st["schema"] == contract_for(role).json_schema()
        assert st["schema"]["additionalProperties"] is False
        assert spies[role].bound is None          # 走的是 native，不是 json_object
        assert ex.enforcement[role]["mode"] == "native_json_schema"
        assert ex.enforcement[role]["contract_id"] == contract_for(role).contract_id


def test_live_chain_sends_json_object_for_deepseek_role():
    ex, _, spies = build_live()
    _run_stages(ex, "claim_extractor")
    s = spies["claim_extractor"]
    assert s.bound == {"response_format": {"type": "json_object"}}
    assert s.structured is None                   # DeepSeek 不冒充 native schema
    assert ex.enforcement["claim_extractor"]["mode"] == "json_object_only"
    assert ex.enforcement["claim_extractor"]["guarantees"]["field_structure_by_provider"] is False


def test_each_role_uses_its_own_contract_on_the_live_path():
    ex, _, _ = build_live()
    _run_stages(ex, "claim_extractor")
    assert {r: e["contract_id"] for r, e in ex.enforcement.items()} == {
        "synthesizer": "synthesis-result-v2", "verifier": "verifier-result-v2",
        "claim_extractor": "claim-extraction-result-v2"}
    assert ex.role_calls == {"synthesizer": 1, "verifier": 1, "claim_extractor": 1}


def test_adapter_is_not_test_only():
    """provider adapter 必须被生产模块 import，而不只是被测试使用。"""
    import pathlib
    src = (pathlib.Path(REPO) / "pilot" / "gated_research_executor.py").read_text(encoding="utf-8")
    assert "from pilot.provider_output import" in src
    assert "apply_output_contract(model, contract, capability)" in src


# ---------------------------------------------------------------- 20-21 调用前拒绝
def test_unregistered_model_capability_refuses_before_provider():
    ex, _, spies = build_live()
    ex._capabilities = {"claude-opus-4-8": ANTHROPIC_OPUS_48}      # 缺 deepseek
    with pytest.raises(ExecutorConfigError):
        _run_stages(ex, "claim_extractor")
    assert spies["claim_extractor"].calls == 0


def test_capability_model_mismatch_refuses_before_provider():
    ex, _, spies = build_live()
    ex._capabilities = {"claude-opus-4-8": DEEPSEEK_V4_FLASH,      # 记录与实际 model 不符
                        "deepseek-v4-flash": DEEPSEEK_V4_FLASH}
    with pytest.raises(ExecutorConfigError):
        _run_stages(ex, "synthesizer")
    assert spies["synthesizer"].calls == 0


def test_binding_failure_refuses_before_provider():
    class NoSchema(SpyChat):
        def with_structured_output(self, *a, **k):
            raise RuntimeError("adapter stripped the schema")
    ex, gate, spies = build_live()
    broken = NoSchema(SYN)
    ex._models["synthesizer"] = GatedModel(broken, gate, role="synthesizer",
                                           model_id="claude-opus-4-8",
                                           max_tokens=ROLE_MAX_TOKENS["synthesizer"])
    with pytest.raises(ProviderCapabilityError):
        _run_stages(ex, "synthesizer")
    assert broken.calls == 0


# ---------------------------------------------------------------- 22-27 严格解析
def test_gated_path_does_not_use_regex_json_extraction():
    """gated 路径必须整体解析 response body；前置/后置 prose 一律拒绝。"""
    for bad in ('Here is the JSON:\n' + json.dumps(SYN),          # 前置 prose
                json.dumps(SYN) + '\nHope this helps!',           # 后置 prose
                '```json\n' + json.dumps(SYN) + '\n```',          # markdown fence
                json.dumps(SYN)[:-3]):                            # 不完整 JSON
        ex, _, spies = build_live(syn_raw=bad)
        with pytest.raises(ResearchOutputError):
            _run_stages(ex, "synthesizer")
        assert spies["verifier"].calls == 0                       # 后续角色未被调用


def test_complete_json_body_is_accepted():
    ex, _, _ = build_live(syn_raw=json.dumps(SYN))
    st = _run_stages(ex, "synthesizer")
    assert st["synthesis"].causal_assessment == "preclinical_perturbation_support"


def test_empty_output_is_rejected():
    ex, _, _ = build_live(syn_raw="")
    with pytest.raises(ResearchOutputError):
        _run_stages(ex, "synthesizer")


# ---------------------------------------------------------------- 28-29 分类顺序
def test_provider_refusal_is_classified_separately():
    ex, _, spies = build_live(syn_raw="I cannot help with that.",
                              syn_meta={"stop_reason": "refusal"})
    with pytest.raises(ProviderRefusal):
        _run_stages(ex, "synthesizer")
    assert spies["verifier"].calls == 0 and spies["claim_extractor"].calls == 0


def test_truncation_takes_priority_over_parse_error():
    ex, _, _ = build_live(syn_raw=json.dumps(SYN)[:120],
                          syn_meta={"stop_reason": "max_tokens"})
    with pytest.raises(OutputTruncated):
        _run_stages(ex, "synthesizer")


def test_refusal_takes_priority_over_truncation():
    """两个信号同时出现时，refusal 排第一顺位。"""
    ex, _, _ = build_live(syn_raw=json.dumps(SYN)[:120],
                          syn_meta={"stop_reason": "refusal"})
    with pytest.raises(ProviderRefusal):
        _run_stages(ex, "synthesizer")


# ---------------------------------------------------------------- 30-38 全链与产物
def test_live_success_chain_produces_exactly_one_artifact():
    ex, gate, spies = build_live()
    store, r, _ = run_chain(ex, rid="hitl-live-ok")
    assert r.state == "completed"
    assert [s.calls for s in spies.values()] == [1, 1, 1]
    arts = [a for a in r.artifacts if a.get("kind") == "json"]
    assert len(arts) == 1
    assert not gate.ledger.open_reservations()
    assert gate.retries == 0


def test_live_failure_chain_stops_and_makes_no_artifact():
    ex, gate, spies = build_live(syn_raw="not json at all")
    store, r, _ = run_chain(ex, rid="hitl-live-fail")
    assert r.state == "failed" and r.needs_human_review
    assert spies["synthesizer"].calls == 1
    assert spies["verifier"].calls == 0 and spies["claim_extractor"].calls == 0
    assert not [a for a in r.artifacts if a.get("kind") == "json"]
    assert not gate.ledger.open_reservations()
    types = [e.event_type for e in store.list("hitl-live-fail")]
    assert "run_failed" in types and "artifact_created" not in types


def test_no_real_paid_client_is_constructed():
    import sys
    for mod in ("pilot.gated_research_executor", "pilot.provider_output", "pilot.output_contract"):
        src = open(sys.modules[mod].__file__, encoding="utf-8").read()
        for bad in ("ChatAnthropic(", "ChatOpenAI(", "anthropic.Anthropic("):
            assert bad not in src
