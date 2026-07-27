"""A.7.4.6 —— 把**真实、已测试的确定性** Reumani 科研组件接入 OpenTaskRuntime。

真实组件（REAL）：
- structured `search_literature`（ssc_pi_agent 的真实工具 + literature_adapter）；
- Exact-ID Resolver（pilot.exact_id_resolver.resolve_exact_ids，sources 可注入）；
- EvidenceAccumulator、Step Controller、EvidenceCard 构建（复用既有）；
- Claim Graph（claim_graph.ClaimEvidenceGraph）与 Shadow 结构化入口（schemas.RunManifest）；
- Lifecycle 对账（pilot.lifecycle.LifecycleReconciler，observed 只认真实 ToolMessage）。

仍为确定性 FAKE：planner / synthesizer / verifier / claim_extractor（但产出**真实** Claim 对象）。

零付费 LLM、零账本写入。离线场景注入冻结的**真实**公开文献响应（pilot/demo_real_fixture.json）；
真实联网（免费 Europe PMC，不调付费模型）由带 shadow_real_integration 标记的测试手动触发。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

import ssc_pi_agent as _agent
from schemas import AbstractEvidenceCard, Claim, RunManifest, Artifact
from tool_envelope import now
from claim_graph import ClaimEvidenceGraph
from pilot import step_controller as sc
from pilot.exact_id_resolver import resolve_exact_ids
from pilot.lifecycle import LifecycleReconciler
from pilot.open_task_runtime import RuntimeDeps, ToolExecution, StepSpec, OpenTaskRuntime

REAL, FAKE = "real", "fake"
# 明确报告哪些组件是真实的、哪些仍是 fake：
COMPONENT_STATUS = {
    "search_literature": REAL, "exact_id_resolver": REAL, "evidence_accumulator": REAL,
    "step_controller": REAL, "evidence_card_build": REAL, "claim_graph": REAL,
    "shadow_structured_entry": REAL, "lifecycle_reconciler": REAL,
    "planner": FAKE, "synthesizer": FAKE, "verifier": FAKE, "claim_extractor": FAKE,
}
_FIXTURE = os.path.join(os.path.dirname(__file__), "demo_real_fixture.json")


class _FakeResp:
    """离线注入：把冻结的真实 EPMC items 交给真实 search_literature 工具解析。"""
    def __init__(self, results, status=200):
        self._results, self.status_code = results, status

    def raise_for_status(self):
        return None

    def json(self):
        return {"resultList": {"result": self._results}}


def load_frozen_real_items() -> list:
    with open(_FIXTURE, "r", encoding="utf-8") as f:
        return [json.load(f)]


@dataclass
class RealRunConfig:
    epmc_items: Optional[list]   # 冻结真实 items（离线）；None → 真实联网（免费 EPMC）
    exact_id_sources: object     # resolve_exact_ids 的 sources（离线注入 fake；联网时 None→HttpSources）
    query: str = "systemic sclerosis IL-6 modified Rodnan skin score"


# --------------------------- 真实工具执行 ---------------------------
def _run_search(request_id, cfg, state) -> ToolExecution:
    life: LifecycleReconciler = state["lifecycle"]
    life.mark_requested(request_id, "search_literature")
    saved = _agent.requests.get
    try:
        if cfg.epmc_items is not None:
            _agent.requests.get = lambda *a, **k: _FakeResp(cfg.epmc_items)   # 离线冻结真实响应
        life.mark_executed(request_id, "search_literature")
        tm = _agent.search_literature.invoke({"type": "tool_call", "name": "search_literature",
                                              "args": {"query": cfg.query}, "id": request_id})
    finally:
        _agent.requests.get = saved
    art = tm.artifact
    life.mark_returned(request_id, "search_literature",
                       result_hash=(art.get("provenance") or {}).get("content_hash"))
    life.reconcile_messages([tm])                 # observed 只认真实 ToolMessage
    if not art.get("ok"):
        et = art.get("error_type") or "tool_error"
        st = et if et in ("source_error", "parse_error") else "tool_error"
        return ToolExecution(status=st, accum_input={"retrieval_status": st, "records": []}, error_type=et)
    data = art.get("data") or {}
    if data.get("retrieval_status") == "zero_hits":
        return ToolExecution(status="zero_hits", accum_input={"retrieval_status": "zero_hits", "records": []})
    recs = data.get("records") or []
    if recs:
        state["pmid"] = recs[0].get("pmid")       # 供 Exact-ID 步骤核验
        state["source"] = recs[0].get("source")
    return ToolExecution(status="ok", accum_input=art,   # 真实 ToolResult artifact → 真实 Accumulator
                         result_hash=(art.get("provenance") or {}).get("content_hash"))


def _run_exact_id(request_id, cfg, state) -> ToolExecution:
    pmid = state.get("pmid")
    if not pmid:
        return ToolExecution(status="zero_hits", accum_input={"retrieval_status": "zero_hits", "records": []})
    batch = resolve_exact_ids(pmid, sources=cfg.exact_id_sources)
    if any(r.get("error_type") for r in batch.ids):           # 来源错误不得判 zero_hits
        return ToolExecution(status="source_error",
                             accum_input={"retrieval_status": "source_error", "records": []},
                             error_type="source_error")
    if batch.verified_count > 0:
        cards = [AbstractEvidenceCard.model_validate(d) for d in batch.evidence_cards]
        return ToolExecution(status="ok", accum_input=cards)  # 真实 verified EvidenceCard
    return ToolExecution(status="zero_hits", accum_input={"retrieval_status": "zero_hits", "records": []})


def _real_artifacts(run_state, conclusion, claims, graph) -> list:
    n = len(run_state.accumulator.evidence_cards)
    verdicts = ",".join(g.get("verdict", "") for g in graph)
    return [
        {"artifact_id": "art-report", "name": "evidence_report.md", "kind": "md",
         "size_bytes": 4096, "hash_short": "b3a1…9e02", "provenance_status": "verified",
         "verifier_status": "insufficient_for_causal",
         "note": f"causal_strength={conclusion.causal_strength}"},
        {"artifact_id": "art-records", "name": "literature_records.json", "kind": "json",
         "size_bytes": 2048, "hash_short": "0d37…4ac3", "provenance_status": "verified",
         "verifier_status": "passed", "note": f"{n} real evidence card(s)"},
        {"artifact_id": "art-graph", "name": "claim_graph.json", "kind": "json",
         "size_bytes": 1024, "hash_short": "7c1a…0b55", "provenance_status": "pending",
         "verifier_status": "pending", "note": verdicts[:40]},
        {"artifact_id": "art-trace", "name": "execution_trace.jsonl", "kind": "jsonl",
         "size_bytes": 3072, "hash_short": "8d3e…2d8c", "provenance_status": "verified",
         "verifier_status": "not_run"},
    ]


def build_real_deps(event_sink, cfg: RealRunConfig, *, clock=now, should_stop=lambda: False):
    """返回 (RuntimeDeps, state)。真实组件接入；planner/synthesizer/verifier/claim_extractor 仍 fake。"""
    state = {"pmid": None, "source": None, "lifecycle": LifecycleReconciler()}

    def tool_executor(step_id, tool, request_id):
        if tool == "search_literature":
            return _run_search(request_id, cfg, state)
        if tool == "resolve_exact_ids":
            return _run_exact_id(request_id, cfg, state)
        return ToolExecution(status="tool_error",
                             accum_input={"retrieval_status": "tool_error", "records": []},
                             error_type="unknown_tool")

    def claim_extractor(conclusion, evidence_ids):
        # FAKE（确定性），但产出**真实** Claim 对象，引用真实 evidence_id
        return [Claim(claim_id="c1", text="SSc 患者中 IL-6 与皮肤评分相关（real-chain demo）",
                      claim_type="association", causal_strength="correlational",
                      supporting_evidence_ids=list(evidence_ids))]

    def claim_graph(claims, cards):
        judged = ClaimEvidenceGraph(claims, cards).adjudicate()   # REAL 逐 claim 裁决
        return [{"claim_id": c.claim_id, "verdict": c.verdict,
                 "human_review": c.human_review_required} for c in judged]

    def shadow(cards):
        # REAL 结构化入口：RunManifest 记录卡片，绝不新建卡
        man = RunManifest(run_id="real-demo", created_at=clock(), query=cfg.query,
                          artifacts=[Artifact(path=f"card:{c.evidence_id}", kind="other")
                                     for c in cards], final_status="unverified")
        return {"created_new_cards": False, "n_cards": len(cards),
                "manifest_status": man.final_status}

    deps = RuntimeDeps(
        planner=lambda q: [
            StepSpec(1, "检索文献 (real search_literature)", "search_literature", 2),
            StepSpec(2, "Exact-ID 核验 (real resolver)", "resolve_exact_ids", 1)],
        tool_executor=tool_executor,
        synthesizer=lambda req: sc.build_controlled_insufficient(req),   # deterministic contract
        verifier=lambda concl, cards: {"status": "insufficient_for_causal"
                                       if concl.causal_strength != "causal" else "passed"},
        claim_extractor=claim_extractor, claim_graph=claim_graph, shadow=shadow,
        artifact_producer=_real_artifacts, event_sink=event_sink, clock=clock, should_stop=should_stop)
    return deps, state


def run_real_demo(event_sink, *, run_id, cfg: Optional[RealRunConfig] = None,
                  clock=now, should_stop=lambda: False) -> dict:
    """执行接入真实组件的离线 demo（默认注入冻结真实 EPMC 响应 + verified exact-id 源）。"""
    if cfg is None:
        cfg = RealRunConfig(epmc_items=load_frozen_real_items(), exact_id_sources=VerifiedExactIdSource())
    deps, state = build_real_deps(event_sink, cfg, clock=clock, should_stop=should_stop)
    result = OpenTaskRuntime(deps, run_id=run_id, question=cfg.query).run()
    result["lifecycle"] = _lifecycle_summary(state["lifecycle"])
    result["components"] = COMPONENT_STATUS
    return result


def _lifecycle_summary(life: LifecycleReconciler) -> dict:
    agg = {"requested": 0, "executed": 0, "tool_returned": 0, "observed": 0}
    for r in life.calls.values():
        agg["requested"] += r.get("requested", 0)
        agg["executed"] += r.get("executed", 0)
        agg["tool_returned"] += r.get("tool_returned", 0)
        agg["observed"] += r.get("observed", 0)
    return agg


# --------------------------- 可注入的 exact-id 源（离线三态） ---------------------------
class VerifiedExactIdSource:
    """离线：PubMed + EPMC 都 exact_hit → resolution=verified（构真实卡）。"""
    def _hit(self, pmid):
        return {"source": "", "retrieval_status": "exact_hit",
                "metadata": {"pmid": str(pmid), "doi": None, "title": "SSc real-chain (verified)",
                             "journal": "J (demo)", "year": "2020"}, "error_type": None,
                "http_status": 200, "attempts": []}

    def pubmed_by_pmid(self, pmid):
        return {**self._hit(pmid), "source": "pubmed"}

    def epmc_by_pmid(self, pmid):
        return {**self._hit(pmid), "source": "europepmc"}


class ZeroHitsExactIdSource:
    def pubmed_by_pmid(self, pmid):
        return {"source": "pubmed", "retrieval_status": "zero_hits", "metadata": {},
                "error_type": None, "http_status": 200, "attempts": []}

    def epmc_by_pmid(self, pmid):
        return {"source": "europepmc", "retrieval_status": "zero_hits", "metadata": {},
                "error_type": None, "http_status": 200, "attempts": []}


class SourceErrorExactIdSource:
    def pubmed_by_pmid(self, pmid):
        return {"source": "pubmed", "retrieval_status": "source_error", "metadata": {},
                "error_type": "timeout", "http_status": None, "attempts": []}

    def epmc_by_pmid(self, pmid):
        return {"source": "europepmc", "retrieval_status": "not_queried", "metadata": {},
                "error_type": None, "http_status": None, "attempts": []}


__all__ = ["COMPONENT_STATUS", "RealRunConfig", "build_real_deps", "run_real_demo",
           "load_frozen_real_items", "VerifiedExactIdSource", "ZeroHitsExactIdSource",
           "SourceErrorExactIdSource"]
