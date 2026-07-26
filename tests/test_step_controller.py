"""A.7.4.4 —— 确定性 open-task Step Controller 单元测试 + 离线端到端验收。

覆盖：六种 StepStatus、合法/非法转换、literature=2 / data_lake=1 预算、未知工具 fail-closed、
预算不可借用/关键词不重置、scientific/transport/zero_hits/error 决策、no-progress 阈值、
satisfied/insufficient/failed/blocked、全终态才 synthesis、0 卡仍产生受控综合、missing_evidence 非空、
observation 幂等、terminal 不可重开、primary_failure 保留、JSON round-trip、无 LLM/网络/账本，
以及 fake B1 端到端（仅离线验收，不代表真实 B1 通过）。
"""
import pathlib

import pytest

from tool_envelope import compute_hash
from schemas import Provenance
from ids import normalize_pmid
from pilot.open_task_contracts import (LiteratureRecord, PlanStepState, OpenTaskRunState,
                                       ObservationRecord, EvidenceAccumulatorState,
                                       literature_content_level_to_provenance,
                                       TERMINAL_STEP_STATUSES)
from pilot.evidence_accumulator import accumulate
from pilot import step_controller as sc
from pilot.step_controller import (evaluate_step, apply_decision, tool_budget, default_criteria,
                                   StepCriteria, ToolOutcome, StepDecision, StepControllerError,
                                   build_synthesis_request, build_controlled_insufficient)

pytestmark = pytest.mark.unit


# ------------------------------ fixtures ------------------------------
def rec(pmid="40000001", *, study_design="cross-sectional", content_level="abstract",
        longitudinal=None, interventional=None, tag=""):
    np_ = normalize_pmid(pmid)
    ab = "IL-6 correlates with mRSS." if content_level != "metadata_only" else None
    ch = compute_hash(dict(pmid=np_, sd=study_design, cl=content_level, lo=longitudinal,
                           iv=interventional, tag=tag))
    prov = Provenance(tool_name="search_literature", source="Europe PMC", source_ids=[np_],
                      content_level=literature_content_level_to_provenance(content_level),
                      content_hash=ch, hash_algorithm="sha256")
    return LiteratureRecord(pmid=pmid, title="IL-6 in SSc", abstract=ab, content_level=content_level,
                            study_design=study_design, species="human", longitudinal=longitudinal,
                            interventional=interventional, source="Europe PMC", query="q",
                            provenance=prov, source_ids=[np_], content_hash=ch, hash_algorithm="sha256")


def lit_step(step_id=1, budget=2):
    return PlanStepState(step_id=step_id, objective="检索文献", allowed_tools=["search_literature"],
                         call_budget=budget, success_criteria="planner free text (display only)")


def dl_step(step_id=2, budget=1):
    return PlanStepState(step_id=step_id, objective="数据湖", allowed_tools=["query_data_lake"],
                         call_budget=budget, success_criteria="corpus")


def run_with(*steps, current=None):
    return OpenTaskRunState(run_id="R", question="IL-6 与 mRSS 因果证据？", route="open",
                            steps=list(steps), current_step_id=current or steps[0].step_id)


def obs(oid, step_id, tool, status="ok", eids=None):
    kw = {}
    if status in ("source_error", "parse_error", "tool_error"):
        kw["error_type"] = "e"
    return ObservationRecord(observation_id=oid, step_id=step_id, tool_name=tool,
                             tool_call_id_hash="h", status=status, structured=True,
                             evidence_ids=eids or [], provenance=Provenance(tool_name=tool), **kw)


def feed_lit(rs, oid, *, tag="", step_id=1, record=None):
    r = record or rec(tag=tag)
    ar = accumulate(rs.accumulator, r)
    rs = rs.model_copy(update={"accumulator": ar.state})
    out = ToolOutcome(observation_id=oid, step_id=step_id, tool_name="search_literature", status="ok")
    dec = evaluate_step(rs, step_id, out, ar)
    rs = apply_decision(rs, dec, obs(oid, step_id, "search_literature", eids=ar.added_evidence_ids))
    return rs, dec, ar


# ------------------------------ 状态机 / 转换 ------------------------------
def test_pending_to_running_then_satisfied_and_no_third_call():
    rs = run_with(lit_step())
    rs, d1, _ = feed_lit(rs, "o1", tag="A")
    assert d1.previous_status == "pending" and d1.next_status == "running"
    rs, d2, ar2 = feed_lit(rs, "o2", tag="A")                 # 同内容 → transport-only → satisfied
    assert d2.next_status == "satisfied" and rs.steps[0].attempts == 2
    assert ar2.novelty.transport_novelty is True and d2.scientific_progress is False


def test_literature_budget_is_two():
    assert tool_budget("search_literature") == 2


def test_data_lake_budget_is_one():
    assert tool_budget("query_data_lake") == 1


def test_unknown_tool_fail_closed():
    with pytest.raises(StepControllerError):
        tool_budget("mystery_tool")
    step = PlanStepState(step_id=1, objective="x", allowed_tools=["mystery_tool"],
                         call_budget=3, success_criteria="s")
    rs = run_with(step)
    ar = accumulate(EvidenceAccumulatorState(), rec())
    with pytest.raises(StepControllerError):
        evaluate_step(rs, 1, ToolOutcome(observation_id="o", step_id=1,
                                         tool_name="mystery_tool", status="ok"), ar)


def test_budget_not_borrowed_across_steps():
    rs = run_with(lit_step(), dl_step())
    rs, _, _ = feed_lit(rs, "o1", tag="A")
    rs, _, _ = feed_lit(rs, "o2", tag="A")                    # lit 用满 2
    # data lake 仍是自己的 1，不因 lit 耗尽而变化
    ar = accumulate(rs.accumulator, {"retrieval_status": "zero_hits", "records": []})
    rs2 = rs.model_copy(update={"accumulator": ar.state})
    d = evaluate_step(rs2, 2, ToolOutcome(observation_id="o3", step_id=2,
                                          tool_name="query_data_lake", status="zero_hits"), ar)
    assert d.remaining_budget == 0 and d.next_status == "insufficient"   # 用自己的预算


def test_keyword_change_does_not_reset_budget():
    rs = run_with(lit_step())
    rs, d1, _ = feed_lit(rs, "o1", tag="query-A")
    rs, d2, _ = feed_lit(rs, "o2", tag="query-B")             # 不同关键词 → 不同内容，但预算不重置
    assert rs.steps[0].attempts == 2 and d2.remaining_budget == 0


def test_scientific_progress_continues():
    rs = run_with(lit_step(budget=3))
    rs, d1, _ = feed_lit(rs, "o1", tag="A")
    assert d1.action == "continue_step" and d1.scientific_progress is True


def test_zero_hits_is_insufficient_not_no_research():
    rs = run_with(dl_step())
    ar = accumulate(rs.accumulator, {"retrieval_status": "zero_hits", "records": []})
    rs = rs.model_copy(update={"accumulator": ar.state})
    d = evaluate_step(rs, 2, ToolOutcome(observation_id="o", step_id=2,
                                         tool_name="query_data_lake", status="zero_hits"), ar)
    assert d.next_status == "insufficient"
    assert any("无研究" in g for g in d.remaining_gaps)


def test_source_error_retries_then_fails():
    rs = run_with(lit_step(budget=2))
    ar = accumulate(rs.accumulator, {"retrieval_status": "source_error", "records": []})
    rs = rs.model_copy(update={"accumulator": ar.state})
    d1 = evaluate_step(rs, 1, ToolOutcome(observation_id="e1", step_id=1,
                       tool_name="search_literature", status="source_error", error_type="http"), ar)
    assert d1.action == "continue_step"                        # 预算内一次受限重试
    rs = apply_decision(rs, d1, obs("e1", 1, "search_literature", status="source_error"))
    d2 = evaluate_step(rs, 1, ToolOutcome(observation_id="e2", step_id=1,
                       tool_name="search_literature", status="source_error", error_type="http"), ar)
    assert d2.next_status == "failed" and d2.primary_failure == "source_error"


def test_parse_error_no_budget_fails():
    rs = run_with(lit_step(budget=1))
    ar = accumulate(rs.accumulator, {"retrieval_status": "parse_error", "records": []})
    rs = rs.model_copy(update={"accumulator": ar.state})
    d = evaluate_step(rs, 1, ToolOutcome(observation_id="p1", step_id=1,
                      tool_name="search_literature", status="parse_error", error_type="bad"), ar)
    assert d.next_status == "failed"


def test_no_progress_threshold_insufficient_when_criteria_unmet():
    # criteria 要求 fulltext（永远不满足），第二次 transport-only → 收敛 → insufficient
    crit = StepCriteria(tool="search_literature", min_evidence_cards=1, minimum_content_level="full_text",
                        max_scientific_no_progress=1)
    rs = run_with(lit_step(budget=3))
    r = rec(tag="A")
    ar = accumulate(rs.accumulator, r)
    rs = rs.model_copy(update={"accumulator": ar.state})
    d1 = evaluate_step(rs, 1, ToolOutcome(observation_id="o1", step_id=1,
                       tool_name="search_literature", status="ok"), ar, criteria=crit)
    rs = apply_decision(rs, d1, obs("o1", 1, "search_literature"))
    ar2 = accumulate(rs.accumulator, rec(tag="A"))             # 同内容 → no-progress
    rs = rs.model_copy(update={"accumulator": ar2.state})
    d2 = evaluate_step(rs, 1, ToolOutcome(observation_id="o2", step_id=1,
                       tool_name="search_literature", status="ok"), ar2, criteria=crit)
    assert d2.next_status == "insufficient" and d2.remaining_gaps


def test_unauthorized_tool_blocked():
    rs = run_with(lit_step())
    ar = accumulate(rs.accumulator, rec())
    d = evaluate_step(rs, 1, ToolOutcome(observation_id="o", step_id=1,
                      tool_name="query_data_lake", status="ok"), ar)   # 不在 allowed_tools
    assert d.next_status == "blocked" and d.human_review is True


def test_telemetry_conflict_failed_human_review():
    rs = run_with(lit_step())
    ar = accumulate(rs.accumulator, rec())
    d = evaluate_step(rs, 1, ToolOutcome(observation_id="o", step_id=1, tool_name="search_literature",
                      status="ok", telemetry_conflict=True), ar)
    assert d.next_status == "failed" and d.human_review is True


def test_six_step_statuses_reachable():
    seen = {"pending"}
    seen.add(run_with(lit_step()).steps[0].status)             # pending 初始
    rs = run_with(lit_step(budget=3))
    rs, d, _ = feed_lit(rs, "o1", tag="A")
    seen.add(d.next_status)                                    # running
    rs, d2, _ = feed_lit(rs, "o2", tag="A")
    seen.add(d2.next_status)                                   # satisfied
    seen.add(evaluate_step(run_with(dl_step()).model_copy(
        update={"accumulator": accumulate(EvidenceAccumulatorState(),
                                          {"retrieval_status": "zero_hits", "records": []}).state}),
        2, ToolOutcome(observation_id="z", step_id=2, tool_name="query_data_lake", status="zero_hits"),
        accumulate(EvidenceAccumulatorState(), {"retrieval_status": "zero_hits", "records": []})
    ).next_status)                                            # insufficient
    seen.add(evaluate_step(run_with(lit_step(budget=1)).model_copy(
        update={"accumulator": accumulate(EvidenceAccumulatorState(),
                                          {"retrieval_status": "parse_error", "records": []}).state}),
        1, ToolOutcome(observation_id="p", step_id=1, tool_name="search_literature",
                       status="parse_error", error_type="x"),
        accumulate(EvidenceAccumulatorState(), {"retrieval_status": "parse_error", "records": []})
    ).next_status)                                            # failed
    seen.add(evaluate_step(run_with(lit_step()),
        1, ToolOutcome(observation_id="b", step_id=1, tool_name="query_data_lake", status="ok"),
        accumulate(EvidenceAccumulatorState(), rec())).next_status)   # blocked
    assert {"pending", "running", "satisfied", "insufficient", "failed", "blocked"} <= seen


# ------------------------------ synthesis 触发 ------------------------------
def test_synthesis_only_after_all_terminal():
    rs = run_with(lit_step(), dl_step())
    with pytest.raises(StepControllerError):
        build_synthesis_request(rs)                           # 有 pending → fail-closed


def test_synthesis_request_with_zero_cards_is_nonempty():
    # 单步、直接 insufficient（0 卡）→ 仍产生受控综合请求，missing_evidence 非空
    rs = run_with(dl_step(step_id=1))
    ar = accumulate(rs.accumulator, {"retrieval_status": "zero_hits", "records": []})
    rs = rs.model_copy(update={"accumulator": ar.state})
    d = evaluate_step(rs, 1, ToolOutcome(observation_id="z", step_id=1,
                      tool_name="query_data_lake", status="zero_hits"), ar)
    rs = apply_decision(rs, d, obs("z", 1, "query_data_lake", status="zero_hits"))
    assert d.should_synthesize is True
    req = build_synthesis_request(rs)
    assert req.evidence_ids == [] and req.missing_evidence
    concl = build_controlled_insufficient(req)
    assert concl.missing_evidence and "还缺：[]" not in str(concl.model_dump())


# ------------------------------ 幂等 / 不可重开 / 保留 ------------------------------
def test_observation_idempotent_apply_twice():
    rs = run_with(lit_step(budget=3))
    r = rec(tag="A")
    ar = accumulate(rs.accumulator, r)
    rs = rs.model_copy(update={"accumulator": ar.state})
    d = evaluate_step(rs, 1, ToolOutcome(observation_id="o1", step_id=1,
                      tool_name="search_literature", status="ok"), ar)
    rs1 = apply_decision(rs, d, obs("o1", 1, "search_literature"))
    rs2 = apply_decision(rs1, d, obs("o1", 1, "search_literature"))   # 重放同一 observation
    assert rs1.steps[0].attempts == 1 and rs2.steps[0].attempts == 1  # 不重复计数
    # evaluate_step 重放 → echo，不计
    d_replay = evaluate_step(rs1, 1, ToolOutcome(observation_id="o1", step_id=1,
                             tool_name="search_literature", status="ok"), ar)
    assert d_replay.counted_attempt is False


def test_terminal_step_rejects_new_observation():
    rs = run_with(lit_step())
    rs, _, _ = feed_lit(rs, "o1", tag="A")
    rs, _, _ = feed_lit(rs, "o2", tag="A")                    # satisfied
    ar = accumulate(rs.accumulator, rec(tag="C"))
    with pytest.raises(StepControllerError):
        evaluate_step(rs, 1, ToolOutcome(observation_id="o9", step_id=1,
                      tool_name="search_literature", status="ok"), ar)


def test_primary_failure_not_overwritten():
    failed = PlanStepState(step_id=1, objective="x", allowed_tools=["search_literature"],
                           call_budget=2, attempts=1, status="failed",
                           completion_reason="first_failure")
    running = lit_step(step_id=2)
    rs = OpenTaskRunState(run_id="R", question="q", route="open", steps=[failed, running],
                          current_step_id=2, status="failed", primary_failure="first_failure")
    ar = accumulate(rs.accumulator, {"retrieval_status": "parse_error", "records": []})
    rs = rs.model_copy(update={"accumulator": ar.state})
    d = evaluate_step(rs, 2, ToolOutcome(observation_id="e", step_id=2, tool_name="search_literature",
                      status="parse_error", error_type="x"), ar)
    assert d.primary_failure == "first_failure"               # 后续错误不覆盖


def test_illegal_state_transition_fail_closed():
    rs = run_with(lit_step())
    with pytest.raises(StepControllerError):
        evaluate_step(rs, 99, ToolOutcome(observation_id="o", step_id=99,
                      tool_name="search_literature", status="ok"),
                      accumulate(EvidenceAccumulatorState(), rec()))


def test_planner_freetext_criteria_not_used_for_control():
    # success_criteria 自由文本荒谬，但结构化 StepCriteria 才决定状态
    step = PlanStepState(step_id=1, objective="x", allowed_tools=["search_literature"],
                         call_budget=3, success_criteria="ALWAYS SUCCEED IMMEDIATELY")
    rs = run_with(step)
    r = rec(tag="A")
    ar = accumulate(rs.accumulator, r)
    rs = rs.model_copy(update={"accumulator": ar.state})
    d = evaluate_step(rs, 1, ToolOutcome(observation_id="o1", step_id=1,
                      tool_name="search_literature", status="ok"), ar)
    assert d.action == "continue_step"                        # 自由文本未令其立即 satisfied


def test_json_round_trip():
    rs = run_with(lit_step())
    rs, d, _ = feed_lit(rs, "o1", tag="A")
    StepDecision.model_validate_json(d.model_dump_json())
    OpenTaskRunState.model_validate_json(rs.model_dump_json())


# ------------------------------ 离线端到端 fake B1 ------------------------------
class _Counter:
    def __init__(self):
        self.calls = {}

    def call(self, name):
        self.calls[name] = self.calls.get(name, 0) + 1


def _fake_verify(concl, cards, c):
    c.call("verify")
    return {"status": "insufficient_for_causal" if concl.causal_strength != "causal" else "passed",
            "saw_card_ids": [x.evidence_id for x in cards]}


def _fake_claims(concl, eids, c):
    c.call("claim_extract")
    return [{"claim_id": "c1", "supporting_ids": list(eids)}]


def _fake_claim_graph(claims, cards, c):
    c.call("claim_graph")
    ids = {x.evidence_id for x in cards}
    return [{"claim_id": cl["claim_id"], "verdict": "partially_supported"
             if all(s in ids for s in cl["supporting_ids"]) else "not_supported"} for cl in claims]


def _fake_shadow(cards, c):
    c.call("shadow")
    return {"created_new_cards": False, "n_cards": len(cards)}


def replay_b1():
    """离线可执行 B1 重放（真实 accumulator + 真实 controller + fake 工具/阶段）。
    NOT a real B1 run；不调用任何模型/网络/账本；固件明确 FAKE。"""
    counter = _Counter()
    lifecycle = {"tool_calls": 0}
    controller_decisions = 0
    step_novelty = {}
    rs = run_with(lit_step(), dl_step())

    # 文献步骤：第一次横断面 abstract，第二次同证据（transport-only）
    for oid, tag in (("o1", "A"), ("o2", "A")):
        lifecycle["tool_calls"] += 1
        r = rec(pmid="40000001", study_design="cross-sectional", tag=tag)
        ar = accumulate(rs.accumulator, r)
        rs = rs.model_copy(update={"accumulator": ar.state})
        d = evaluate_step(rs, 1, ToolOutcome(observation_id=oid, step_id=1,
                          tool_name="search_literature", status="ok"), ar)
        controller_decisions += 1 if d.counted_attempt else 0
        step_novelty[oid] = ar.novelty
        rs = apply_decision(rs, d, obs(oid, 1, "search_literature", eids=ar.added_evidence_ids))

    # 数据湖：zero_hits
    lifecycle["tool_calls"] += 1
    ar = accumulate(rs.accumulator, {"retrieval_status": "zero_hits", "records": []})
    rs = rs.model_copy(update={"accumulator": ar.state})
    d_dl = evaluate_step(rs, 2, ToolOutcome(observation_id="o3", step_id=2,
                         tool_name="query_data_lake", status="zero_hits"), ar)
    controller_decisions += 1 if d_dl.counted_attempt else 0
    rs = apply_decision(rs, d_dl, obs("o3", 2, "query_data_lake", status="zero_hits"))

    assert all(s.is_terminal() for s in rs.steps) and d_dl.should_synthesize
    req = build_synthesis_request(rs)
    concl = build_controlled_insufficient(req)

    verdict = _fake_verify(concl, rs.accumulator.evidence_cards, counter)
    claims = _fake_claims(concl, req.evidence_ids, counter)
    judged = _fake_claim_graph(claims, rs.accumulator.evidence_cards, counter)
    shadow = _fake_shadow(rs.accumulator.evidence_cards, counter)

    run_metrics = {"tool_calls": lifecycle["tool_calls"],
                   "evidence_cards": len(rs.accumulator.evidence_cards),
                   "controller_decisions": controller_decisions,
                   "stage_calls": dict(counter.calls)}
    return {"rs": rs, "conclusion": concl, "verdict": verdict, "claims": claims,
            "claim_graph": judged, "shadow": shadow, "run_metrics": run_metrics,
            "step_novelty": step_novelty, "lit_attempts": rs.steps[0].attempts,
            "lit_observations": rs.steps[0].observations}


@pytest.fixture(scope="module")
def B1():
    return replay_b1()


def test_e2e_literature_first_cross_sectional_second_transport_only(B1):
    assert B1["step_novelty"]["o1"].scientific_progress is True           # 1: 构卡
    assert B1["step_novelty"]["o2"].transport_novelty is True             # 2: transport-only
    assert B1["step_novelty"]["o2"].scientific_progress is False


def test_e2e_literature_terminal_after_second_no_third(B1):
    assert B1["rs"].steps[0].status == "satisfied"                        # 3
    assert B1["lit_attempts"] == 2 and len(B1["lit_observations"]) == 2   # 6: 不发生第三次


def test_e2e_datalake_zero_hits_terminal(B1):
    assert B1["rs"].steps[1].status == "insufficient"                     # 4,5


def test_e2e_all_terminal_then_synthesis_nonempty(B1):
    assert all(s.is_terminal() for s in B1["rs"].steps)                   # 7
    assert B1["conclusion"].missing_evidence                              # 8: 非空 insufficient


def test_e2e_fake_stages_each_called_once(B1):
    for st in ("verify", "claim_extract", "claim_graph", "shadow"):       # 9-12
        assert B1["run_metrics"]["stage_calls"].get(st) == 1
    assert B1["shadow"]["created_new_cards"] is False


def test_e2e_causal_strength_association_with_gaps(B1):
    assert B1["conclusion"].causal_strength == "association"              # 13
    gaps = " ".join(B1["conclusion"].missing_evidence)
    for kw in ("时序", "干预", "混杂", "反向"):                            # 14
        assert kw in gaps


def test_e2e_metrics_consistent_across_sources(B1):
    m = B1["run_metrics"]                                                 # 15
    assert m["tool_calls"] == 3                                           # Lifecycle
    assert m["evidence_cards"] == 1                                       # Accumulator
    assert m["controller_decisions"] == 3                                 # Controller
    assert m["stage_calls"] == {"verify": 1, "claim_extract": 1, "claim_graph": 1, "shadow": 1}


def test_e2e_is_offline_only(B1):
    # 明确：这是离线验收产物，不代表真实 B1 通过
    assert B1["verdict"]["status"] == "insufficient_for_causal"


# ------------------------------ 边界 ------------------------------
_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_no_llm_network_or_ledger_imports():
    src = (_ROOT / "pilot" / "step_controller.py").read_text(encoding="utf-8")
    for bad in ("import requests", "import httpx", "import urllib", "import socket",
                "anthropic", "openai", "ledger_integrity", "loop_guard"):
        assert bad not in src, bad


def test_controller_not_wired_into_production_chain():
    for name in ("ssc_a1.py", "shadow.py", "pilot/exec_wiring.py", "pilot/tool_middleware.py",
                 "pilot/round2_runner.py", "pilot/literature_adapter.py", "pilot/loop_guard.py"):
        p = _ROOT / name
        if p.exists():
            assert "step_controller" not in p.read_text(encoding="utf-8"), name
