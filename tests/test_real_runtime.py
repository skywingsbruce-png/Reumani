"""A.7.4.6 —— 真实确定性组件接入 OpenTaskRuntime（零付费 LLM，默认离线冻结真实公开文献）。

真实：search_literature / Exact-ID Resolver / EvidenceAccumulator / Step Controller /
EvidenceCard 构建 / Claim Graph / Shadow 结构化入口 / Lifecycle。
Fake：planner / synthesizer / verifier / claim_extractor（但产真实 Claim 对象）。
"""
from dataclasses import replace

import pytest

from pilot.event_store import InMemoryEventStore
from pilot.open_task_runtime import OpenTaskRuntime, StepSpec
from pilot.step_controller import StepCriteria
from pilot.real_runtime import (COMPONENT_STATUS, RealRunConfig, build_real_deps, run_real_demo,
                                load_frozen_real_items, VerifiedExactIdSource,
                                ZeroHitsExactIdSource, SourceErrorExactIdSource)

pytestmark = pytest.mark.unit


def _run(sources, run_id="r", planner=None):
    store = InMemoryEventStore()
    cfg = RealRunConfig(epmc_items=load_frozen_real_items(), exact_id_sources=sources)
    deps, state = build_real_deps(store.append, cfg)
    if planner:
        deps = replace(deps, planner=planner)
    res = OpenTaskRuntime(deps, run_id=run_id, question=cfg.query).run()
    return store.list(run_id), res, state


def _types(evs):
    return [e.event_type for e in evs]


# ------------------------------ real chain end to end ------------------------------
def test_real_public_record_flows_through_structured_chain():
    evs, res, state = _run(VerifiedExactIdSource(), "verified")
    t = _types(evs)
    assert t[-1] == "run_completed" and res["open_reservations"] == 0
    # LiteratureRecord -> EvidenceCard -> Accumulator produced ≥1 real evidence card
    ev_counts = [e.safe_payload.get("evidence_count") for e in evs if e.event_type == "evidence_accumulated"]
    assert max(ev_counts) >= 1
    assert res["conclusion"] is not None                       # controlled synthesis produced


def test_component_status_real_vs_fake():
    real = {k for k, v in COMPONENT_STATUS.items() if v == "real"}
    fake = {k for k, v in COMPONENT_STATUS.items() if v == "fake"}
    assert {"search_literature", "exact_id_resolver", "evidence_accumulator", "step_controller",
            "evidence_card_build", "claim_graph", "shadow_structured_entry",
            "lifecycle_reconciler"} <= real
    assert {"planner", "synthesizer", "verifier", "claim_extractor"} <= fake


def test_structured_evidence_source_and_count_visible():
    evs, _, _ = _run(VerifiedExactIdSource(), "src")
    acc = [e for e in evs if e.event_type == "evidence_accumulated"]
    assert acc and acc[-1].safe_payload.get("evidence_count") >= 1     # 数量可见
    tool_ret = [e for e in evs if e.event_type == "tool_returned" and e.step_id == 1]
    assert tool_ret and tool_ret[0].safe_payload.get("tool_name") == "search_literature"   # 来源工具可见


# ------------------------------ exact-id three states not confused ------------------------------
def test_exact_hit_zero_hits_source_error_not_confused():
    v, _, _ = _run(VerifiedExactIdSource(), "v")
    z, _, _ = _run(ZeroHitsExactIdSource(), "z")
    e, _, _ = _run(SourceErrorExactIdSource(), "e")
    s2 = lambda evs: [x.event_type for x in evs if x.step_id == 2 and x.event_type.startswith("step_") and x.event_type != "step_started"]
    assert s2(v) == ["step_satisfied"]        # exact_hit -> verified -> satisfied
    assert s2(z) == ["step_insufficient"]     # zero_hits -> insufficient (≠ no research)
    assert s2(e) == ["step_failed"]           # source_error -> failed (≠ zero_hits)


# ------------------------------ early stop + transport-only ------------------------------
def test_step_controller_early_stops_on_real_evidence():
    evs, _, _ = _run(VerifiedExactIdSource(), "early")
    step1 = [e for e in evs if e.step_id == 1]
    assert sum(1 for e in step1 if e.event_type == "tool_started") == 1   # 真实证据满足 → 第一次即早停
    assert any(e.event_type == "step_satisfied" for e in step1)


def test_second_transport_only_search_not_scientific_progress():
    # 强 criteria（需 2 卡）→ 第二次同一真实记录仅 transport 变化 → 无科学进展 → insufficient
    strict = StepCriteria(tool="search_literature", min_evidence_cards=2,
                          minimum_content_level="abstract", max_scientific_no_progress=1)
    planner = lambda q: [StepSpec(1, "检索文献", "search_literature", 2, criteria=strict)]
    evs, res, _ = _run(VerifiedExactIdSource(), "transport", planner=planner)
    t = _types(evs)
    assert t.count("tool_started") == 2                        # 真的搜了两次
    assert "step_insufficient" in t and "step_satisfied" not in [x for x in t if x.startswith("step_")]
    assert res["open_reservations"] == 0


# ------------------------------ lifecycle consistency ------------------------------
def test_lifecycle_selected_requested_executed_returned_observed_consistent():
    evs, _, state = _run(VerifiedExactIdSource(), "life")
    life = state["lifecycle"]
    agg = {k: sum(r.get(k, 0) for r in life.calls.values())
           for k in ("requested", "executed", "tool_returned", "observed")}
    assert agg["requested"] == agg["executed"] == agg["tool_returned"] == agg["observed"] == 1
    # runtime event lifecycle (selected=attempt_authorized ... observed=observation_recorded) consistent
    t = _types(evs)
    assert t.count("attempt_authorized") == t.count("tool_started") == t.count("tool_returned") \
        == t.count("observation_recorded")


# ------------------------------ reservation zero on all exits ------------------------------
def test_reservation_zero_on_completion_and_failure_and_stop():
    _, res_ok, _ = _run(VerifiedExactIdSource(), "ok")
    _, res_fail, _ = _run(SourceErrorExactIdSource(), "fail")
    assert res_ok["open_reservations"] == 0 and res_fail["open_reservations"] == 0
    # stop
    store = InMemoryEventStore()
    res_stop = run_real_demo(store.append, run_id="stop", should_stop=lambda: True)
    assert res_stop["open_reservations"] == 0 and res_stop["stopped"] is True


# ------------------------------ claim graph real adjudication ------------------------------
def test_real_claim_graph_adjudicates():
    evs, _, _ = _run(VerifiedExactIdSource(), "cg")
    cg = [e for e in evs if e.event_type == "claim_graph_completed"]
    assert cg and isinstance(cg[0].safe_payload.get("graph_verdicts"), list)
    assert len(cg[0].safe_payload["graph_verdicts"]) >= 1      # 至少一个 claim 被裁决


# ------------------------------ zero paid LLM / offline ------------------------------
def test_zero_paid_llm_calls(monkeypatch):
    from langchain_anthropic import ChatAnthropic
    from langchain_openai import ChatOpenAI
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise AssertionError("no paid LLM allowed")
    monkeypatch.setattr(ChatAnthropic, "invoke", boom)
    monkeypatch.setattr(ChatOpenAI, "invoke", boom)
    run_real_demo(InMemoryEventStore().append, run_id="nollm")
    assert calls["n"] == 0


def test_default_run_is_offline_frozen_real_record():
    # 默认 run_real_demo 用冻结的真实公开记录（离线、确定），产生真实证据卡
    store = InMemoryEventStore()
    res = run_real_demo(store.append, run_id="off")
    assert res["components"] == COMPONENT_STATUS
    assert res["lifecycle"]["observed"] == 1
    assert max(e.safe_payload.get("evidence_count", 0) for e in store.list("off")
               if e.event_type == "evidence_accumulated") >= 1


# ------------------------------ live real source (manual only; CI-skipped) ------------------------------
@pytest.mark.shadow_real_integration
def test_live_europepmc_real_source_enters_runtime():
    # 手动真实试运行：免费 Europe PMC（不调付费模型）。CI 默认跳过。
    store = InMemoryEventStore()
    cfg = RealRunConfig(epmc_items=None, exact_id_sources=None)   # None → live free sources
    res = run_real_demo(store.append, run_id="live", cfg=cfg)
    assert res["open_reservations"] == 0
    assert max(e.safe_payload.get("evidence_count", 0) for e in store.list("live")
               if e.event_type == "evidence_accumulated") >= 1
