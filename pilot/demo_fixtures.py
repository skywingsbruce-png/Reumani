"""离线 DEMO 固件（A.7.4.5）：一个明确标为 DEMO 的 SSc 因果场景。

**不是真实 B1**，不调用任何模型/网络/账本。全部 fake/in-memory，用于打通事件链与 UI。
两步：search_literature（返回一条结构化横断面 LiteratureRecord）→ query_data_lake（zero_hits）。
早收敛：文献步骤第一次即满足 → 无第二次检索；zero_hits 不解释为“没有研究”；
受控综合 causal_strength=association，缺 temporal/intervention/confounding/reverse。
"""

from __future__ import annotations

from tool_envelope import compute_hash, now
from schemas import Provenance
from ids import normalize_pmid
from pilot.open_task_contracts import (LiteratureRecord, literature_content_level_to_provenance)
from pilot import step_controller as sc
from pilot.open_task_runtime import RuntimeDeps, ToolExecution, StepSpec, OpenTaskRuntime

DEMO_PMID = "39000001"          # 明确 FAKE 的离线固件 PMID
DEMO_QUESTION = "SSc 中血清 IL-6 与皮肤评分（mRSS）的因果证据现状？（DEMO）"


def _demo_record() -> LiteratureRecord:
    pmid = normalize_pmid(DEMO_PMID)
    ch = compute_hash({"pmid": pmid, "design": "cross-sectional", "demo": True})
    prov = Provenance(tool_name="search_literature", source="offline-demo-fixture", source_ids=[pmid],
                      content_level=literature_content_level_to_provenance("abstract"),
                      content_hash=ch, hash_algorithm="sha256")
    return LiteratureRecord(
        pmid=DEMO_PMID, title="IL-6 correlates with mRSS in SSc (DEMO fixture)", year="2024",
        journal="J Rheum (DEMO)", abstract="Serum IL-6 correlates with mRSS (offline DEMO fixture).",
        content_level="abstract", study_design="cross-sectional", species="human",
        longitudinal=False, interventional=False, source="offline-demo-fixture",
        query="SSc IL-6 skin score (DEMO)", provenance=prov, source_ids=[pmid],
        content_hash=ch, hash_algorithm="sha256")


def demo_planner(question: str) -> list:
    return [
        StepSpec(step_id=1, objective="检索 IL-6 与皮肤评分文献 (DEMO)", tool_name="search_literature",
                 call_budget=2, success_criteria="≥1 abstract EvidenceCard"),
        StepSpec(step_id=2, objective="本地数据湖交叉核验 (DEMO)", tool_name="query_data_lake",
                 call_budget=1, success_criteria="corpus hit 或 zero_hits 皆终态"),
    ]


def demo_tool_executor(step_id: int, tool_name: str, request_id: str) -> ToolExecution:
    if tool_name == "search_literature":
        rec = _demo_record()
        return ToolExecution(status="ok", accum_input=rec, result_hash=rec.content_hash[:16],
                             structured=True)
    if tool_name == "query_data_lake":
        return ToolExecution(status="zero_hits",
                             accum_input={"retrieval_status": "zero_hits", "records": []},
                             result_hash="", structured=True)
    return ToolExecution(status="tool_error", accum_input={"retrieval_status": "tool_error",
                         "records": []}, error_type="unknown_tool")


def demo_synthesizer(synthesis_request):
    return sc.build_controlled_insufficient(synthesis_request)


def demo_verifier(conclusion, cards) -> dict:
    return {"status": "insufficient_for_causal" if conclusion.causal_strength != "causal" else "passed"}


def demo_claim_extractor(conclusion, evidence_ids) -> list:
    return [{"claim_id": "c1", "text": "SSc 患者血清 IL-6 与 mRSS 正相关（DEMO）",
             "supporting_ids": list(evidence_ids)}]


def demo_claim_graph(claims, cards) -> list:
    ids = {c.evidence_id for c in cards}
    return [{"claim_id": cl["claim_id"],
             "verdict": "partially_supported" if all(s in ids for s in cl["supporting_ids"]) and
             cl["supporting_ids"] else "not_supported"} for cl in claims]


def demo_shadow(cards) -> dict:
    return {"created_new_cards": False, "n_cards": len(cards)}


def demo_artifact_producer(run_state, conclusion, claims, graph) -> list:
    n = len(run_state.accumulator.evidence_cards)
    return [
        {"artifact_id": "art-report", "name": "evidence_report.md", "kind": "md",
         "size_bytes": 4096, "hash_short": "b3a1…9e02", "provenance_status": "verified",
         "verifier_status": "insufficient_for_causal"},
        {"artifact_id": "art-records", "name": "literature_records.json", "kind": "json",
         "size_bytes": 2048, "hash_short": "0d37…4ac3", "provenance_status": "verified",
         "verifier_status": "passed"},
        {"artifact_id": "art-graph", "name": "claim_graph.json", "kind": "json",
         "size_bytes": 1024, "hash_short": "7c1a…0b55", "provenance_status": "pending",
         "verifier_status": "pending"},
        {"artifact_id": "art-trace", "name": "execution_trace.jsonl", "kind": "jsonl",
         "size_bytes": 3072, "hash_short": "8d3e…2d8c", "provenance_status": "verified",
         "verifier_status": "not_run", "note": f"{n} evidence card(s)"},
    ]


def build_demo_deps(event_sink, *, clock=now, should_stop=lambda: False) -> RuntimeDeps:
    return RuntimeDeps(
        planner=demo_planner, tool_executor=demo_tool_executor, synthesizer=demo_synthesizer,
        verifier=demo_verifier, claim_extractor=demo_claim_extractor, claim_graph=demo_claim_graph,
        shadow=demo_shadow, artifact_producer=demo_artifact_producer, event_sink=event_sink,
        clock=clock, should_stop=should_stop)


def run_demo(event_sink, *, run_id: str, clock=now, should_stop=lambda: False) -> dict:
    """执行离线 DEMO run（同步）；事件写入 event_sink。返回结果摘要。"""
    deps = build_demo_deps(event_sink, clock=clock, should_stop=should_stop)
    return OpenTaskRuntime(deps, run_id=run_id, question=DEMO_QUESTION).run()


__all__ = ["DEMO_QUESTION", "DEMO_PMID", "build_demo_deps", "run_demo", "demo_planner",
           "demo_tool_executor", "demo_synthesizer", "demo_verifier", "demo_claim_extractor",
           "demo_claim_graph", "demo_shadow", "demo_artifact_producer"]
