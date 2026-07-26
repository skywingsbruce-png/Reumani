"""A.7.4.4 (+ A.7.4.4.1) —— 确定性 open-task Step Controller 单元测试 + 离线端到端验收。

覆盖：六种 StepStatus、合法/非法转换、literature=2 / data_lake=1 预算、未知工具 fail-closed、
预算不可借用/关键词不重置、**满足标准即刻终止（early convergence）**、no-progress 阈值、
satisfied/insufficient/failed/blocked、全终态才 synthesis、0 卡仍产生受控综合、missing_evidence 非空、
observation 幂等、terminal 不可重开、primary_failure 保留、JSON round-trip、无 LLM/网络/账本，
**两阶段执行前授权（authorize/reserve/settle）**、**reload-safe 严格重验证**，
以及两阶段 fake B1 端到端（仅离线验收，不代表真实 B1 通过）。
"""
import importlib
import pathlib

import pytest

from tool_envelope import compute_hash
from schemas import Provenance
from ids import normalize_pmid
import pilot.open_task_contracts as otc
from pilot.open_task_contracts import (LiteratureRecord, PlanStepState, OpenTaskRunState,
                                       ObservationRecord, EvidenceAccumulatorState,
                                       literature_content_level_to_provenance)
from pilot.evidence_accumulator import accumulate, AccumulatorInputError
from evidence_build import evidence_card_from_literature_record
from pilot import step_controller as scmod
from pilot.step_controller import (evaluate_step, apply_decision, tool_budget, default_criteria,
                                   StepCriteria, ToolOutcome, StepDecision, StepControllerError,
                                   build_synthesis_request, build_controlled_insufficient,
                                   ControllerSession, authorize_attempt, reserve, settle_attempt,
                                   apply_settlement, open_reservations)

pytestmark = pytest.mark.unit

# criteria 强于默认（第一次横断面 abstract 不满足）→ 用于制造“需要第二次”的场景
STRICT2 = StepCriteria(tool="search_literature", min_evidence_cards=2, minimum_content_level="abstract",
                       max_scientific_no_progress=1)


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


def obsrec(oid, step_id, tool, status="ok", eids=None):
    kw = {}
    if status in ("source_error", "parse_error", "tool_error"):
        kw["error_type"] = "e"
    return ObservationRecord(observation_id=oid, step_id=step_id, tool_name=tool,
                             tool_call_id_hash="h", status=status, structured=True,
                             evidence_ids=eids or [], provenance=Provenance(tool_name=tool), **kw)


def feed_lit(rs, oid, *, tag="", step_id=1, record=None, criteria=None):
    r = record or rec(tag=tag)
    ar = accumulate(rs.accumulator, r)
    rs = rs.model_copy(update={"accumulator": ar.state})
    out = ToolOutcome(observation_id=oid, step_id=step_id, tool_name="search_literature", status="ok")
    dec = evaluate_step(rs, step_id, out, ar, criteria)
    rs = apply_decision(rs, dec, obsrec(oid, step_id, "search_literature", eids=ar.added_evidence_ids))
    return rs, dec, ar


# ------------------------------ early convergence（Fix 1） ------------------------------
def test_first_search_satisfies_immediately_no_second():
    rs = run_with(lit_step())
    rs, d1, _ = feed_lit(rs, "o1", tag="A")                   # 默认 criteria：≥1 abstract 卡
    assert d1.action == "complete_satisfied" and d1.next_status == "satisfied"
    assert rs.steps[0].attempts == 1                          # attempts=1 后 satisfied
    assert d1.allow_another_tool_call is False                # 不为耗尽预算继续检索


def test_second_search_only_when_criteria_unmet():
    rs = run_with(lit_step(budget=2))
    rs, d1, _ = feed_lit(rs, "o1", tag="A", criteria=STRICT2)  # 需 2 卡 → 第一次不满足
    assert d1.action == "continue_step"
    rs, d2, ar2 = feed_lit(rs, "o2", tag="A", criteria=STRICT2)  # 同证据、仍 1 卡 → 无新增
    assert ar2.novelty.transport_novelty is True
    assert d2.next_status == "insufficient"                   # 不虚假 satisfied
    assert d2.remaining_gaps


def test_keyword_change_cannot_force_continue():
    # 默认 criteria 第一次即满足 → 即便换关键词也不得强迫第二次
    rs = run_with(lit_step())
    rs, d1, _ = feed_lit(rs, "o1", tag="query-A")
    assert d1.next_status == "satisfied" and rs.steps[0].attempts == 1


def test_keyword_change_does_not_reset_budget_when_unmet():
    rs = run_with(lit_step(budget=2))
    rs, _, _ = feed_lit(rs, "o1", tag="query-A", criteria=STRICT2)
    rs, d2, _ = feed_lit(rs, "o2", tag="query-B", criteria=STRICT2)   # 不同关键词，预算不重置
    assert rs.steps[0].attempts == 2 and d2.remaining_budget == 0


def test_counterevidence_must_be_declared_via_criteria():
    crit = StepCriteria(tool="search_literature", min_evidence_cards=1, minimum_content_level="abstract",
                        require_counterevidence_check=True, max_scientific_no_progress=1)
    rs = run_with(lit_step())
    rs, d1, _ = feed_lit(rs, "o1", tag="A", criteria=crit)    # 无反证 → 未满足 → 不 satisfied
    assert d1.action != "complete_satisfied"


# ------------------------------ 预算 / 未知工具 ------------------------------
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
    rs, _, _ = feed_lit(rs, "o1", tag="A")                    # lit satisfied (attempts=1)
    ar = accumulate(rs.accumulator, {"retrieval_status": "zero_hits", "records": []})
    rs2 = rs.model_copy(update={"accumulator": ar.state})
    d = evaluate_step(rs2, 2, ToolOutcome(observation_id="o3", step_id=2,
                                          tool_name="query_data_lake", status="zero_hits"), ar)
    assert d.remaining_budget == 0 and d.next_status == "insufficient"


def test_scientific_progress_continues_when_criteria_unmet():
    rs = run_with(lit_step(budget=3))
    rs, d1, _ = feed_lit(rs, "o1", tag="A", criteria=STRICT2)
    assert d1.action == "continue_step" and d1.scientific_progress is True


# ------------------------------ zero_hits / errors / blocked / telemetry ------------------------------
def test_zero_hits_is_insufficient_not_no_research():
    rs = run_with(dl_step())
    ar = accumulate(rs.accumulator, {"retrieval_status": "zero_hits", "records": []})
    rs = rs.model_copy(update={"accumulator": ar.state})
    d = evaluate_step(rs, 2, ToolOutcome(observation_id="o", step_id=2,
                                         tool_name="query_data_lake", status="zero_hits"), ar)
    assert d.next_status == "insufficient" and any("无研究" in g for g in d.remaining_gaps)


def test_source_error_retries_then_fails():
    rs = run_with(lit_step(budget=2))
    ar = accumulate(rs.accumulator, {"retrieval_status": "source_error", "records": []})
    rs = rs.model_copy(update={"accumulator": ar.state})
    d1 = evaluate_step(rs, 1, ToolOutcome(observation_id="e1", step_id=1,
                       tool_name="search_literature", status="source_error", error_type="http"), ar)
    assert d1.action == "continue_step"
    rs = apply_decision(rs, d1, obsrec("e1", 1, "search_literature", status="source_error"))
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
    crit = StepCriteria(tool="search_literature", min_evidence_cards=1, minimum_content_level="full_text",
                        max_scientific_no_progress=1)
    rs = run_with(lit_step(budget=3))
    rs, d1, _ = feed_lit(rs, "o1", tag="A", criteria=crit)    # 无 fulltext 卡 → 未满足 → continue
    rs, d2, _ = feed_lit(rs, "o2", tag="A", criteria=crit)    # 同内容 → no-progress → insufficient
    assert d1.action == "continue_step" and d2.next_status == "insufficient" and d2.remaining_gaps


def test_unauthorized_tool_blocked():
    rs = run_with(lit_step())
    ar = accumulate(rs.accumulator, rec())
    d = evaluate_step(rs, 1, ToolOutcome(observation_id="o", step_id=1,
                      tool_name="query_data_lake", status="ok"), ar)
    assert d.next_status == "blocked" and d.human_review is True


def test_telemetry_conflict_failed_human_review():
    rs = run_with(lit_step())
    ar = accumulate(rs.accumulator, rec())
    d = evaluate_step(rs, 1, ToolOutcome(observation_id="o", step_id=1, tool_name="search_literature",
                      status="ok", telemetry_conflict=True), ar)
    assert d.next_status == "failed" and d.human_review is True


def test_six_step_statuses_reachable():
    seen = {run_with(lit_step()).steps[0].status}             # pending
    rs = run_with(lit_step(budget=3))
    rs, d1, _ = feed_lit(rs, "o1", tag="A", criteria=STRICT2)
    seen.add(d1.next_status)                                  # running
    seen.add(feed_lit(run_with(lit_step()), "s1", tag="A")[1].next_status)   # satisfied
    zar = accumulate(EvidenceAccumulatorState(), {"retrieval_status": "zero_hits", "records": []})
    seen.add(evaluate_step(run_with(dl_step()).model_copy(update={"accumulator": zar.state}),
             2, ToolOutcome(observation_id="z", step_id=2, tool_name="query_data_lake",
                            status="zero_hits"), zar).next_status)            # insufficient
    par = accumulate(EvidenceAccumulatorState(), {"retrieval_status": "parse_error", "records": []})
    seen.add(evaluate_step(run_with(lit_step(budget=1)).model_copy(update={"accumulator": par.state}),
             1, ToolOutcome(observation_id="p", step_id=1, tool_name="search_literature",
                            status="parse_error", error_type="x"), par).next_status)   # failed
    seen.add(evaluate_step(run_with(lit_step()), 1, ToolOutcome(observation_id="b", step_id=1,
             tool_name="query_data_lake", status="ok"),
             accumulate(EvidenceAccumulatorState(), rec())).next_status)     # blocked
    assert {"pending", "running", "satisfied", "insufficient", "failed", "blocked"} <= seen


# ------------------------------ synthesis 触发 ------------------------------
def test_synthesis_only_after_all_terminal():
    rs = run_with(lit_step(), dl_step())
    with pytest.raises(StepControllerError):
        build_synthesis_request(rs)


def test_synthesis_request_with_zero_cards_is_nonempty():
    rs = run_with(dl_step(step_id=1))
    ar = accumulate(rs.accumulator, {"retrieval_status": "zero_hits", "records": []})
    rs = rs.model_copy(update={"accumulator": ar.state})
    d = evaluate_step(rs, 1, ToolOutcome(observation_id="z", step_id=1,
                      tool_name="query_data_lake", status="zero_hits"), ar)
    rs = apply_decision(rs, d, obsrec("z", 1, "query_data_lake", status="zero_hits"))
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
                      tool_name="search_literature", status="ok"), ar, STRICT2)
    rs1 = apply_decision(rs, d, obsrec("o1", 1, "search_literature"))
    rs2 = apply_decision(rs1, d, obsrec("o1", 1, "search_literature"))
    assert rs1.steps[0].attempts == 1 and rs2.steps[0].attempts == 1
    d_replay = evaluate_step(rs1, 1, ToolOutcome(observation_id="o1", step_id=1,
                             tool_name="search_literature", status="ok"), ar, STRICT2)
    assert d_replay.counted_attempt is False


def test_terminal_step_rejects_new_observation():
    rs = run_with(lit_step())
    rs, _, _ = feed_lit(rs, "o1", tag="A")                    # satisfied on first
    ar = accumulate(rs.accumulator, rec(tag="C"))
    with pytest.raises(StepControllerError):
        evaluate_step(rs, 1, ToolOutcome(observation_id="o9", step_id=1,
                      tool_name="search_literature", status="ok"), ar)


def test_primary_failure_not_overwritten():
    failed = PlanStepState(step_id=1, objective="x", allowed_tools=["search_literature"],
                           call_budget=2, attempts=1, status="failed", completion_reason="first_failure")
    rs = OpenTaskRunState(run_id="R", question="q", route="open", steps=[failed, lit_step(step_id=2)],
                          current_step_id=2, status="failed", primary_failure="first_failure")
    ar = accumulate(rs.accumulator, {"retrieval_status": "parse_error", "records": []})
    rs = rs.model_copy(update={"accumulator": ar.state})
    d = evaluate_step(rs, 2, ToolOutcome(observation_id="e", step_id=2, tool_name="search_literature",
                      status="parse_error", error_type="x"), ar)
    assert d.primary_failure == "first_failure"


def test_illegal_state_transition_fail_closed():
    rs = run_with(lit_step())
    with pytest.raises(StepControllerError):
        evaluate_step(rs, 99, ToolOutcome(observation_id="o", step_id=99,
                      tool_name="search_literature", status="ok"),
                      accumulate(EvidenceAccumulatorState(), rec()))


def test_planner_freetext_criteria_not_used_for_control():
    step = PlanStepState(step_id=1, objective="x", allowed_tools=["search_literature"],
                         call_budget=3, success_criteria="ALWAYS SUCCEED IMMEDIATELY")
    rs = run_with(step)
    rs, d, _ = feed_lit(rs, "o1", tag="A", criteria=STRICT2)  # 结构化未满足 → 自由文本不生效
    assert d.action == "continue_step"


def test_json_round_trip():
    rs = run_with(lit_step())
    rs, d, _ = feed_lit(rs, "o1", tag="A")
    StepDecision.model_validate_json(d.model_dump_json())
    OpenTaskRunState.model_validate_json(rs.model_dump_json())


# ------------------------------ 两阶段授权（Fix 2） ------------------------------
def _session():
    return ControllerSession(run_state=run_with(lit_step(), dl_step()))


def test_authorize_success_reserve_settle_and_no_open():
    s = _session()
    auth = authorize_attempt(s, 1, "search_literature", "req-1")
    assert auth.authorized and auth.attempt_number == 1
    s = reserve(s, auth)
    assert len(open_reservations(s)) == 1                     # 未 settle 可检测
    ar = accumulate(s.run_state.accumulator, rec())
    s = ControllerSession(run_state=s.run_state.model_copy(update={"accumulator": ar.state}), ledger=s.ledger)
    d = settle_attempt(s, auth, ToolOutcome(observation_id="o1", step_id=1,
                       tool_name="search_literature", status="ok"), ar)
    s = apply_settlement(s, d, auth, obsrec("o1", 1, "search_literature"))
    assert d.action == "complete_satisfied" and open_reservations(s) == []


def test_authorize_denials():
    s = _session()
    # unauthorized tool
    assert authorize_attempt(s, 1, "query_data_lake", "r").denial_reason == "unauthorized_tool"
    # no tool policy
    assert authorize_attempt(s, 1, "search_literature", "r").authorized  # baseline authorized
    step = PlanStepState(step_id=1, objective="x", allowed_tools=["mystery"], call_budget=2, success_criteria="s")
    s2 = ControllerSession(run_state=run_with(step))
    assert authorize_attempt(s2, 1, "mystery", "r").denial_reason == "no_tool_policy"
    # duplicate request_id
    s3 = reserve(s, authorize_attempt(s, 1, "search_literature", "dup"))
    assert authorize_attempt(s3, 1, "search_literature", "dup").denial_reason == "duplicate_request_id"


def test_authorize_budget_exhausted_denies_without_tool_call():
    s = _session()
    a1 = authorize_attempt(s, 2, "query_data_lake", "d1")     # data_lake budget 1
    s = reserve(s, a1)
    a2 = authorize_attempt(s, 2, "query_data_lake", "d2")     # 已有 1 open reservation → 用满
    assert a2.authorized is False and a2.denial_reason == "budget_exhausted"


def test_reserve_denied_authorization_raises():
    s = _session()
    denied = authorize_attempt(s, 1, "query_data_lake", "r")
    with pytest.raises(StepControllerError):
        reserve(s, denied)


def test_settle_without_reservation_is_rejected():
    s = _session()
    auth = authorize_attempt(s, 1, "search_literature", "r")  # 未 reserve
    ar = accumulate(s.run_state.accumulator, rec())
    with pytest.raises(StepControllerError):
        settle_attempt(s, auth, ToolOutcome(observation_id="o", step_id=1,
                       tool_name="search_literature", status="ok"), ar)


def test_reservation_settled_once():
    s = _session()
    auth = authorize_attempt(s, 1, "search_literature", "r")
    s = reserve(s, auth)
    ar = accumulate(s.run_state.accumulator, rec())
    s = ControllerSession(run_state=s.run_state.model_copy(update={"accumulator": ar.state}), ledger=s.ledger)
    d = settle_attempt(s, auth, ToolOutcome(observation_id="o", step_id=1,
                       tool_name="search_literature", status="ok"), ar)
    s = apply_settlement(s, d, auth, obsrec("o", 1, "search_literature"))
    with pytest.raises(StepControllerError):                  # 二次 settle 被拒
        settle_attempt(s, auth, ToolOutcome(observation_id="o", step_id=1,
                       tool_name="search_literature", status="ok"), ar)


def test_settle_succeeds_even_if_tool_raised():
    # provider/tool 抛异常 → 调用方合成 tool_error outcome → 仍可 settle 为失败
    s = ControllerSession(run_state=run_with(lit_step(budget=1)))
    auth = authorize_attempt(s, 1, "search_literature", "r")
    s = reserve(s, auth)
    ar = accumulate(s.run_state.accumulator, {"retrieval_status": "tool_error", "records": []})
    s = ControllerSession(run_state=s.run_state.model_copy(update={"accumulator": ar.state}), ledger=s.ledger)
    d = settle_attempt(s, auth, ToolOutcome(observation_id="x", step_id=1,
                       tool_name="search_literature", status="tool_error", error_type="boom"), ar)
    s = apply_settlement(s, d, auth, obsrec("x", 1, "search_literature", status="tool_error"))
    assert d.next_status == "failed" and open_reservations(s) == []


# ------------------------------ reload-safe 严格重验证（Fix 3） ------------------------------
def test_record_created_before_reload_still_builds():
    r = rec("40000010")
    c1 = evidence_card_from_literature_record(r)
    importlib.reload(otc)
    c2 = evidence_card_from_literature_record(r)              # reload 后旧记录仍可重验证构卡
    assert c1.model_dump() == c2.model_dump()


def test_same_named_impostor_rejected():
    class LiteratureRecord:                                   # 同名伪类，字段不符
        pmid = "40000011"
        doi = None
    with pytest.raises(TypeError):
        evidence_card_from_literature_record(LiteratureRecord())


def test_missing_fields_object_rejected():
    with pytest.raises(TypeError):
        evidence_card_from_literature_record({"pmid": "40000012"})


def test_illegal_hash_provenance_id_rejected():
    good = rec("40000013").model_dump()
    for mutate in ({"content_hash": "nothex"}, {"pmid": "not-a-pmid", "doi": None},
                   {"provenance": None}):
        bad = {**good, **mutate}
        with pytest.raises(TypeError):
            evidence_card_from_literature_record(bad)


def test_plain_dict_cannot_bypass_accumulator():
    with pytest.raises(AccumulatorInputError):
        accumulate(EvidenceAccumulatorState(),
                   {"pmid": "40000014", "content_level": "abstract"})   # 非结构化 dict


def test_valid_record_round_trip_stable():
    r = rec("40000015")
    assert evidence_card_from_literature_record(r).model_dump() == \
           evidence_card_from_literature_record(r).model_dump()


# ------------------------------ 两阶段离线端到端 fake B1 ------------------------------
class _Counter:
    def __init__(self):
        self.calls = {}

    def call(self, name):
        self.calls[name] = self.calls.get(name, 0) + 1


def replay_b1_two_phase():
    """两阶段 fake B1：authorize → tool → accumulate → settle，早满足即停。仅离线，不代表真实 B1 通过。"""
    counter = _Counter()
    order = []
    tool_calls = {"search_literature": 0, "query_data_lake": 0}
    lifecycle = 0
    decisions = 0
    session = ControllerSession(run_state=run_with(lit_step(), dl_step()))

    def attempt(session, step_id, tool, request_id, make):
        nonlocal lifecycle, decisions
        order.append(f"authorize:{request_id}")
        auth = authorize_attempt(session, step_id, tool, request_id)
        if not auth.authorized:
            order.append(f"denied:{request_id}:{auth.denial_reason}")
            return session, None, auth                        # 未授权 → 不调用工具
        session = reserve(session, auth)
        order.append(f"tool:{tool}")
        tool_calls[tool] += 1
        lifecycle += 1
        outcome, obsr = make()
        ar = accumulate(session.run_state.accumulator, outcome["accum_input"])
        order.append("accumulate")
        session = ControllerSession(run_state=session.run_state.model_copy(
            update={"accumulator": ar.state}), ledger=session.ledger)
        order.append(f"settle:{request_id}")
        d = settle_attempt(session, auth, outcome["tool_outcome"], ar)
        decisions += 1
        session = apply_settlement(session, d, auth, obsr(ar))
        return session, d, auth

    # 文献：第一次横断面 abstract → early satisfied
    session, d_lit, _ = attempt(session, 1, "search_literature", "lit-1", lambda: (
        {"accum_input": rec(study_design="cross-sectional", tag="A"),
         "tool_outcome": ToolOutcome(observation_id="o1", step_id=1, tool_name="search_literature", status="ok")},
        lambda ar: obsrec("o1", 1, "search_literature", eids=ar.added_evidence_ids)))
    # 尝试第二次文献 → 步骤已终态 → 授权被拒，工具不被调用
    session, d_lit2, auth_lit2 = attempt(session, 1, "search_literature", "lit-2", lambda: (
        {"accum_input": rec(tag="B"),
         "tool_outcome": ToolOutcome(observation_id="o2", step_id=1, tool_name="search_literature", status="ok")},
        lambda ar: obsrec("o2", 1, "search_literature")))
    # 数据湖：zero_hits → insufficient
    session, d_dl, _ = attempt(session, 2, "query_data_lake", "dl-1", lambda: (
        {"accum_input": {"retrieval_status": "zero_hits", "records": []},
         "tool_outcome": ToolOutcome(observation_id="o3", step_id=2, tool_name="query_data_lake", status="zero_hits")},
        lambda ar: obsrec("o3", 2, "query_data_lake", status="zero_hits")))

    req = build_synthesis_request(session.run_state)
    concl = build_controlled_insufficient(req)

    def _v(concl, cards):
        counter.call("verify")
        return {"status": "insufficient_for_causal" if concl.causal_strength != "causal" else "passed"}
    verdict = _v(concl, session.run_state.accumulator.evidence_cards)
    counter.call("claim_extract"); counter.call("claim_graph"); counter.call("shadow")

    run_metrics = {"tool_calls": lifecycle, "evidence_cards": len(session.run_state.accumulator.evidence_cards),
                   "controller_decisions": decisions, "reservations_settled": len(session.ledger.reservations),
                   "open_reservations": len(open_reservations(session)), "stage_calls": dict(counter.calls)}
    return {"session": session, "order": order, "tool_calls": tool_calls, "conclusion": concl,
            "verdict": verdict, "run_metrics": run_metrics, "lit_decision": d_lit,
            "lit2_auth": auth_lit2, "dl_decision": d_dl}


@pytest.fixture(scope="module")
def B1():
    return replay_b1_two_phase()


def test_e2e_authorize_before_tool_ordering(B1):
    order = B1["order"]
    assert order.index("authorize:lit-1") < order.index("tool:search_literature")
    assert order.index("authorize:dl-1") < order.index("tool:query_data_lake")
    # 固定顺序 authorize→tool→accumulate→settle（以 lit-1 为例）
    seg = order[order.index("authorize:lit-1"):order.index("settle:lit-1") + 1]
    assert seg == ["authorize:lit-1", "tool:search_literature", "accumulate", "settle:lit-1"]


def test_e2e_first_satisfies_and_second_denied_no_tool_call(B1):
    assert B1["lit_decision"].action == "complete_satisfied"          # 第一次即满足
    assert B1["session"].run_state.steps[0].attempts == 1
    assert B1["lit2_auth"].authorized is False                        # 第二次授权被拒
    assert B1["lit2_auth"].denial_reason == "step_terminal"
    assert B1["tool_calls"]["search_literature"] == 1                 # 工具只被调用一次


def test_e2e_datalake_zero_hits_terminal_and_synthesis(B1):
    assert B1["dl_decision"].next_status == "insufficient"
    assert B1["dl_decision"].should_synthesize is True
    assert B1["conclusion"].missing_evidence


def test_e2e_causal_not_upgraded_by_early_satisfied(B1):
    assert B1["conclusion"].causal_strength == "association"
    gaps = " ".join(B1["conclusion"].missing_evidence)
    for kw in ("时序", "干预", "混杂", "反向"):
        assert kw in gaps


def test_e2e_fake_stages_each_once(B1):
    for st in ("verify", "claim_extract", "claim_graph", "shadow"):
        assert B1["run_metrics"]["stage_calls"].get(st) == 1


def test_e2e_reservation_and_metrics_consistent_no_open(B1):
    m = B1["run_metrics"]
    assert m["tool_calls"] == 2                               # lifecycle：lit 1 + dl 1（被拒的不算）
    assert m["evidence_cards"] == 1
    assert m["controller_decisions"] == 2
    assert m["reservations_settled"] == 2                     # 两个已授权 attempt
    assert m["open_reservations"] == 0                        # 无未结算 reservation


# ------------------------------ 边界 ------------------------------
_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_no_llm_network_or_ledger_imports():
    src = (_ROOT / "pilot" / "step_controller.py").read_text(encoding="utf-8")
    for bad in ("import requests", "import httpx", "import urllib", "import socket",
                "anthropic", "openai", "ledger_integrity", "loop_guard"):
        assert bad not in src, bad


def test_controller_not_wired_into_production_chain():
    for name in ("ssc_a1.py", "shadow.py", "pilot/exec_wiring.py", "pilot/tool_middleware.py",
                 "pilot/round2_runner.py", "pilot/literature_adapter.py", "pilot/loop_guard.py",
                 "search_literature.py"):
        p = _ROOT / name
        if p.exists():
            assert "step_controller" not in p.read_text(encoding="utf-8"), name
