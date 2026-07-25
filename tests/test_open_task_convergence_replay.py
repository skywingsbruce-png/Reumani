"""A.7.3.1 —— 开放任务有界收敛执行契约的**可执行离线重放** + 自动化测试。

这是**设计/验证产物，不是生产代码**（不导入生产执行链，不调用任何模型/网络）。
它用真实可运行的 fake 结构化工具结果 + fake 五阶段（带调用计数）演示 OpenTaskExecutionContract
的确定性收敛，并把遥测统一到权威计数对象。修复 A.7.3 离线重放的三项缺陷：
  (1) 之前是预生成 JSON，非可执行 replay；
  (2) 之前手写 stages_invoked，无真实调用证据；
  (3) 之前遥测数字手填、字段名含义相反。

运行 `pytest tests/test_open_task_convergence_replay.py`；或直接执行本文件生成脱敏产物 JSON。
"""
import hashlib
import json
import pathlib
import sys

import pytest


def _h(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


# ============================ 离线遥测权威对象 ============================
class LifecycleCounter:
    """工具执行遥测的唯一权威（模拟生产 LifecycleReconciler 的角色）。"""
    def __init__(self):
        self.requested = self.executed = self.tool_returned = self.observed = self.failed = 0

    def record_execution(self):
        self.requested += 1; self.executed += 1
        self.tool_returned += 1; self.observed += 1

    def counts(self):
        return {"requested": self.requested, "executed": self.executed,
                "tool_returned": self.tool_returned, "observed": self.observed,
                "failed": self.failed}


class CallCounter:
    """综合/核查阶段的真实调用计数 + 顺序（不手写 stages_invoked）。"""
    def __init__(self):
        self.counts = {}
        self.order = []

    def call(self, name):
        self.counts[name] = self.counts.get(name, 0) + 1
        self.order.append(name)


# ============================ 严格文献结果适配器（fail-closed）============================
class LiteratureParseError(RuntimeError):
    pass


def literature_adapter(record):
    """把结构化文献记录解析成规范字段；无法可靠解析 → fail-closed（不猜）。"""
    if not isinstance(record, dict) or record.get("schema_version") != "litrec-v1":
        raise LiteratureParseError("legacy/unstructured literature result → fail-closed（不得猜测字段）")
    pid = record.get("pmid") or record.get("doi")
    if not pid:
        raise LiteratureParseError("无 PMID/DOI → fail-closed")
    return {
        "pmid": record.get("pmid"), "doi": record.get("doi"),
        "title": record.get("title", "unknown"), "year": record.get("year", "unknown"),
        "journal": record.get("journal", "unknown"),
        "content_level": record.get("content_level", "unknown"),
        "study_design": record.get("study_design", "unknown"),
        "species": record.get("species", "unknown"),
        "longitudinal": record.get("longitudinal", "unknown"),
        "interventional": record.get("interventional", "unknown"),
        "source": record.get("source", "unknown"), "query": record.get("query", "unknown"),
        "provenance": record.get("provenance", {}), "content_hash": _h(record),
        "schema_version": "litrec-v1",
    }


# ============================ EvidenceAccumulator（四级新颖度）============================
CAUSAL_TIERS = ["none", "association", "temporal_association", "intervention_supported", "causal"]


class EvidenceAccumulator:
    def __init__(self):
        self.transport_hashes = set()
        self.identifiers = set()
        self.evidence_axes = set()         # (design, longitudinal, interventional, species)
        self.causal_tier = "none"
        self.cards = []

    def _tier_for(self, rec):
        if rec.get("interventional") is True:
            return "intervention_supported"
        if rec.get("longitudinal") is True:
            return "temporal_association"
        if rec.get("study_design"):
            return "association"
        return "none"

    def ingest(self, records):
        nov = {"transport": False, "identifier": False, "evidence": False, "decision": False}
        for rec in records:
            th = rec["content_hash"]
            if th not in self.transport_hashes:
                self.transport_hashes.add(th); nov["transport"] = True
            pid = rec.get("pmid") or rec.get("doi")
            if pid and pid not in self.identifiers:
                self.identifiers.add(pid); nov["identifier"] = True
                self.cards.append({"pmid": rec.get("pmid"), "doi": rec.get("doi"),
                                   "study_design": rec.get("study_design"),
                                   "content_level": rec.get("content_level"),
                                   "content_hash": th, "hash_algorithm": "sha256",
                                   "provenance_source": rec.get("source")})
            axis = (rec.get("study_design"), rec.get("longitudinal"),
                    rec.get("interventional"), rec.get("species"))
            if axis not in self.evidence_axes:
                self.evidence_axes.add(axis); nov["evidence"] = True
            new_tier = self._tier_for(rec)
            if CAUSAL_TIERS.index(new_tier) > CAUSAL_TIERS.index(self.causal_tier):
                self.causal_tier = new_tier; nov["decision"] = True
        # 科研进展只认 identifier/evidence/decision；transport 仅审计
        nov["scientific_progress"] = nov["identifier"] or nov["evidence"] or nov["decision"]
        return nov


# ============================ PlanStep 状态机 + Step Controller ============================
def run_literature_step(step, tool_results, acc, life, timeline):
    """确定性微循环：解析→记录→更新累加器→查 success→决定终态；步骤级预算封顶。"""
    for raw in tool_results:
        if step["attempts"] >= step["call_budget"]:
            break
        step["attempts"] += 1
        life.record_execution()
        try:
            rec = literature_adapter(raw)
            parsed = [rec]
        except LiteratureParseError:
            life.failed += 1
            timeline.append({"step": step["step_id"], "attempt": step["attempts"],
                             "tool": "search_literature", "parse": "fail_closed"})
            step["status"] = "failed"; step["completion_reason"] = "literature_parse_fail_closed"
            return step
        nov = acc.ingest(parsed)
        step["evidence_ids"] = sorted(acc.identifiers)
        timeline.append({"step": step["step_id"], "attempt": step["attempts"],
                         "tool": "search_literature",
                         "transport_novelty": nov["transport"],
                         "identifier_novelty": nov["identifier"],
                         "evidence_novelty": nov["evidence"],
                         "decision_novelty": nov["decision"],
                         "scientific_progress": nov["scientific_progress"]})
    # success criteria：拿到 ≥1 张带研究设计标注的卡
    if any(c.get("study_design") for c in acc.cards):
        step["status"] = "satisfied"
        step["completion_reason"] = "success_criteria_met_no_new_scientific_progress_on_last_attempt"
    else:
        step["status"] = "insufficient"; step["completion_reason"] = "no_design_annotated_card"
    return step


def run_datalake_step(step, retrieval_status, life, timeline):
    step["attempts"] += 1
    life.record_execution()
    timeline.append({"step": step["step_id"], "attempt": 1, "tool": "query_data_lake",
                     "retrieval_status": retrieval_status})
    if retrieval_status in ("zero_hits", "zero_candidates"):
        step["status"] = "insufficient"
        step["completion_reason"] = "zero_hits (本地无该精确记录，≠该领域无研究)"
        step["remaining_gaps"] = ["本地 corpus 未命中，不能推断领域无研究"]
    else:
        step["status"] = "satisfied"; step["completion_reason"] = "corpus_hit"
    return step


# ============================ fake 五阶段（真实被调用 + 计数）============================
def synthesize(cards, acc, counter):
    counter.call("synthesize")
    tier = acc.causal_tier
    return {
        "input_card_ids": [c["pmid"] or c["doi"] for c in cards],
        "resolved_question": "SSc 中血清 IL-6 与皮肤评分（mRSS）关系及证据层级",
        "available_evidence": [{"pmid": c["pmid"], "study_design": c["study_design"],
                                "content_level": c["content_level"]} for c in cards],
        "unsupported_claims": ["IL-6 升高导致皮肤纤维化加重（确定性因果）"],
        "causal_strength": tier if tier != "none" else "insufficient",
        "missing_evidence": ["纵向/时序证据", "干预（RCT/敲除回补）证据", "剂量-反应",
                             "混杂控制", "反向因果排查"],
        "limitations": ["证据为横断面相关，无法确立时间先后/因果",
                        "本地 corpus 未命中≠无研究", "摘要级证据，未核全文"],
        "recommended_next_action": "检索纵向队列/干预研究或 Mendelian randomization",
        "schema": "OpenTaskConclusion-v1",
    }


def verify(conclusion, cards, counter):
    counter.call("verify")
    saw_cards = [c["pmid"] or c["doi"] for c in cards]     # Verifier 能看到 EvidenceCard
    passed = conclusion["causal_strength"] in ("causal",)  # 仅横断面 → 不通过因果
    return {"input_card_ids": saw_cards, "passed": passed,
            "status": "passed" if passed else "insufficient_for_causal",
            "reason": "仅关联/横断面证据，不支持确定性因果；结论已正确降级",
            "schema": "VerificationResult-v1"}


def extract_claims(conclusion, evidence_ids, counter):
    counter.call("claim_extract")
    # Claim 只能引用已有 evidence_id
    return [{"claim_id": "c1", "text": "SSc 患者血清 IL-6 与 mRSS 正相关",
             "claim_type": "association", "causal_strength": "correlational",
             "supporting_ids": [e for e in ["FAKE-PMID-38000001"] if e in evidence_ids],
             "schema": "Claim-v1"}]


def build_claim_graph(claims, cards, counter):
    counter.call("claim_graph")
    card_ids = {c["pmid"] or c["doi"] for c in cards}
    judged = []
    for cl in claims:
        supported = all(s in card_ids for s in cl["supporting_ids"]) and cl["supporting_ids"]
        judged.append({"claim_id": cl["claim_id"],
                       "verdict": "partially_supported" if supported else "not_supported",
                       "note": "关联被横断面证据支持；因果不被支持"})
    return judged


def run_shadow(cards, claims, judged, counter):
    counter.call("shadow")
    return {"schema": "RunManifest-v1", "shadow_status": "ok",
            "evidence_cards": [dict(c) for c in cards],   # 只记录，不新建卡
            "claims": judged, "created_new_cards": False}


# ============================ B1 场景重放（可执行）============================
FAKE_PMID = "FAKE-PMID-38000001"


def _litrec(order_tag):
    """结构化文献 fixture（litrec-v1），含**明确标为 fake** 的 PMID。order_tag 改变 transport 字节。"""
    return {"schema_version": "litrec-v1", "pmid": FAKE_PMID, "doi": None,
            "title": "IL-6 correlates with mRSS in SSc (FAKE fixture)", "year": "2023",
            "journal": "J Rheum (FAKE)", "content_level": "abstract",
            "study_design": "cross-sectional", "species": "human",
            "longitudinal": False, "interventional": False,
            "source": "offline-fixture", "query": f"SSc IL-6 skin score #{order_tag}",
            "provenance": {"tool": "search_literature", "note": "FAKE offline fixture"}}


def replay_b1():
    life = LifecycleCounter()
    counter = CallCounter()
    acc = EvidenceAccumulator()
    timeline = []

    step_lit = {"step_id": 1, "objective": "检索 IL-6 与皮肤评分关系文献",
                "allowed_tools": ["search_literature"], "call_budget": 2, "attempts": 0,
                "status": "pending", "observations": [], "evidence_ids": [],
                "success_criteria": "获得 ≥1 张带研究设计标注的 EvidenceCard",
                "completion_reason": None, "remaining_gaps": []}
    step_dl = {"step_id": 2, "objective": "本地 corpus 交叉核验",
               "allowed_tools": ["query_data_lake"], "call_budget": 1, "attempts": 0,
               "status": "pending", "observations": [], "evidence_ids": [],
               "success_criteria": "获得 corpus 检索终态", "completion_reason": None,
               "remaining_gaps": []}

    # 第一次结构化命中 + 第二次同证据但 transport 排序不同（内容 hash 因 query_tag 不同）
    run_literature_step(step_lit, [_litrec("A"), _litrec("B")], acc, life, timeline)
    # data lake zero_hits
    run_datalake_step(step_dl, "zero_hits", life, timeline)

    all_terminal = all(s["status"] in ("satisfied", "insufficient", "failed", "blocked")
                       for s in (step_lit, step_dl))
    # 全终态 → synthesize → verify → claim → claim_graph → shadow
    conclusion = synthesize(acc.cards, acc, counter)
    verdict = verify(conclusion, acc.cards, counter)
    claims = extract_claims(conclusion, sorted(acc.identifiers), counter)
    judged = build_claim_graph(claims, acc.cards, counter)
    manifest = run_shadow(acc.cards, claims, judged, counter)

    run_metrics = {"tool_calls": life.executed,                 # 来自 Lifecycle
                   "evidence_cards": len(acc.cards),             # 来自 Accumulator
                   "stage_calls": dict(counter.counts),          # 来自 CallCounter
                   "timeline_len": len(timeline),
                   "source": "aggregated from LifecycleCounter + EvidenceAccumulator + CallCounter"}
    return {"steps": [step_lit, step_dl], "all_terminal": all_terminal, "timeline": timeline,
            "evidence_cards": acc.cards, "causal_tier": acc.causal_tier,
            "conclusion": conclusion, "verifier": verdict, "claims": claims,
            "claim_graph": judged, "shadow": manifest,
            "lifecycle_counts": life.counts(), "stage_order": counter.order,
            "stage_counts": dict(counter.counts), "run_metrics": run_metrics}


# ============================ 自动化测试 ============================
@pytest.fixture(scope="module")
def R():
    return replay_b1()


@pytest.mark.unit
def test_two_literature_searches_second_is_transport_only(R):
    lit = [t for t in R["timeline"] if t.get("tool") == "search_literature"]
    assert len(lit) == 2                                        # §1.6 两次文献检索
    assert R["steps"][0]["attempts"] == 2                       # §1.5 attempts=2
    a2 = lit[1]                                                 # 第二次
    assert a2["transport_novelty"] is True                     # §1.3
    assert a2["identifier_novelty"] is False
    assert a2["evidence_novelty"] is False
    assert a2["decision_novelty"] is False
    assert a2["scientific_progress"] is False


@pytest.mark.unit
def test_no_third_literature_search_and_positive_field_name(R):
    assert R["steps"][0]["status"] in ("satisfied",)           # §1.4 终态
    assert R["steps"][0]["attempts"] <= R["steps"][0]["call_budget"] == 2   # 不允许第三次
    lit = [t for t in R["timeline"] if t.get("tool") == "search_literature"]
    # §1.8 正向字段名：transport-only 不重置科研进展
    prop = {"transport_only_does_not_reset_scientific_progress":
            (lit[1]["transport_novelty"] and not lit[1]["scientific_progress"])}
    assert prop["transport_only_does_not_reset_scientific_progress"] is True


@pytest.mark.unit
def test_four_tier_novelty_semantics():
    acc = EvidenceAccumulator()
    r1 = literature_adapter(_litrec("A"))
    n1 = acc.ingest([r1])
    assert n1["identifier"] and n1["evidence"] and n1["scientific_progress"]     # 新 PMID→identifier
    n2 = acc.ingest([literature_adapter(_litrec("B"))])                           # 同 PMID 不同 tag
    assert n2["transport"] and not n2["identifier"] and not n2["evidence"] and not n2["scientific_progress"]
    # 同 PMID 新增纵向设计 → evidence novelty
    longi = dict(_litrec("A"), longitudinal=True, study_design="longitudinal cohort")
    n3 = acc.ingest([literature_adapter(longi)])
    assert n3["evidence"] is True                                                 # 新 axis
    # 新增干预证据 → decision novelty（因果层级改变）
    interv = dict(_litrec("A"), interventional=True, study_design="RCT")
    n4 = acc.ingest([literature_adapter(interv)])
    assert n4["decision"] is True and acc.causal_tier == "intervention_supported"


@pytest.mark.unit
def test_legacy_unparseable_fail_closed():
    with pytest.raises(LiteratureParseError):
        literature_adapter("- IL-6 correlates | J Rheum | PMID:38000001")        # legacy 字符串
    with pytest.raises(LiteratureParseError):
        literature_adapter({"schema_version": "litrec-v1"})                      # 无 PMID/DOI


@pytest.mark.unit
def test_zero_hits_not_no_research(R):
    dl = R["steps"][1]
    assert dl["status"] == "insufficient" and "zero_hits" in dl["completion_reason"]
    assert "≠该领域无研究" in dl["completion_reason"] or "不能推断领域无研究" in str(dl["remaining_gaps"])


@pytest.mark.unit
def test_controlled_insufficient_conclusion_non_empty(R):
    c = R["conclusion"]
    assert c["resolved_question"] and c["causal_strength"]
    assert c["missing_evidence"] and len(c["missing_evidence"]) >= 1              # missing 非空
    assert c["unsupported_claims"]
    # 不出现 "还缺：[]"
    assert "还缺：[]" not in json.dumps(c, ensure_ascii=False)


@pytest.mark.unit
def test_five_fake_stages_actually_invoked_in_order(R):
    for st in ("synthesize", "verify", "claim_extract", "claim_graph", "shadow"):
        assert R["stage_counts"].get(st) == 1                  # 每阶段 call_count=1
    assert R["stage_order"] == ["synthesize", "verify", "claim_extract", "claim_graph", "shadow"]


@pytest.mark.unit
def test_stage_contracts(R):
    # Verifier 看到 EvidenceCard；判 insufficient_for_causal
    assert R["verifier"]["input_card_ids"] == [FAKE_PMID]
    assert R["verifier"]["status"] == "insufficient_for_causal" and R["verifier"]["passed"] is False
    # Claim 只引用已有 evidence_id
    for cl in R["claims"]:
        assert all(s in R["steps"][0]["evidence_ids"] for s in cl["supporting_ids"])
    # Shadow 不新建 EvidenceCard
    assert R["shadow"]["created_new_cards"] is False
    assert len(R["shadow"]["evidence_cards"]) == len(R["evidence_cards"])


@pytest.mark.unit
def test_telemetry_consistency(R):
    lc = R["lifecycle_counts"]
    assert lc["requested"] == lc["executed"] == lc["tool_returned"] == lc["observed"] == 3
    assert lc["failed"] == 0
    assert R["run_metrics"]["tool_calls"] == lc["executed"]                       # 来自 Lifecycle
    assert R["run_metrics"]["evidence_cards"] == len(R["evidence_cards"])         # 来自 Accumulator
    assert R["run_metrics"]["stage_calls"] == R["stage_counts"]                   # 来自 CallCounter
    assert R["run_metrics"]["timeline_len"] == len(R["timeline"]) == 3            # timeline 与 metrics 一致


def _emit_product():
    r = replay_b1()
    r = {"note": "OFFLINE executable simulation of OpenTaskExecutionContract "
                 "(tests/test_open_task_convergence_replay.py). NOT a real B1 run; no model/network; "
                 "does NOT claim B1 passed. All fixtures explicitly FAKE.", **r}
    p = pathlib.Path(__file__).resolve().parent.parent / "pilot" / "round2_results" / "B1_state_machine_replay.json"
    p.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return p


@pytest.mark.unit
def test_emit_desensitized_product(tmp_path):
    r = replay_b1()
    blob = json.dumps(r, ensure_ascii=False, default=str)
    for bad in ("F:" + chr(92) + "SSC", "sk" + "-", "ANTHROPIC_" + "API_" + "KEY"):
        assert bad not in blob                                 # 脱敏


if __name__ == "__main__":
    print("emitting product ->", _emit_product())
    sys.exit(0)
