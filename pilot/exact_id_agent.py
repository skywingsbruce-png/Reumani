"""Exact-ID 任务的确定性执行流 + 分流（Addendum 2 §6/§8）。

exact_id 任务：query classifier → resolve_exact_ids（程序、零 LLM）→ 结构化 observations
→ 旧 Verifier / Claim extractor / Shadow / 科研 Manifest，**不进入开放式 ReAct Executor**
（Executor 模型调用为 0，search_literature 调用为 0）。

open 任务：原样委托 ssc_a1.run_agent（行为完全不变）。
"""

from pilot.exact_id_resolver import resolve_exact_ids
from pilot.query_classifier import classify


def _render_answer(batch) -> str:
    lines = []
    for r in batch.ids:
        st, nid = r["resolution_status"], r["normalized_id"]
        if st == "verified":
            tag = (f"PMID:{r['canonical_pmid']}" if r["id_type"] == "pmid"
                   else f"DOI:{r['canonical_doi']}")
            lines.append(f"- {nid} → 精确核验通过（{tag}）："
                         f"{r.get('canonical_title') or ''} | {r.get('journal') or ''} | "
                         f"{r.get('year') or ''}")
        elif st == "not_found":
            lines.append(f"- {nid} → 多来源确认无该记录（not_found），不构证据卡；"
                         f"这不等于该领域没有研究。")
        elif st == "mismatch":
            lines.append(f"- {nid} → 来源元数据冲突（mismatch），需人工复核，不构卡。")
        else:
            lines.append(f"- {nid} → 无法确认（{st}），需人工复核，不构卡。")
    return "Exact-ID 精确核验结果（确定性执行，未使用开放式检索循环）：\n" + "\n".join(lines)


def _batch_to_tool_events(batch) -> list:
    """把每个 ID 的解析结果转成 shadow 可读的结构化事件（工具本身成功 → ok=True）。"""
    from tool_envelope import make_toolresult
    events = []
    for r in batch.ids:
        data = {"normalized_id": r["normalized_id"], "id_type": r["id_type"],
                "resolution_status": r["resolution_status"],
                "retrieval_status_by_source": r["retrieval_status_by_source"],
                "canonical_pmid": r.get("canonical_pmid"),
                "canonical_doi": r.get("canonical_doi")}
        art = make_toolresult("resolve_exact_ids", True, data, content_level="metadata_only",
                              source="PubMed/Crossref/doi.org/EuropePMC exact-id",
                              source_ids=[x for x in (r.get("canonical_pmid"),
                                                      r.get("canonical_doi")) if x],
                              parameters={"normalized_id": r["normalized_id"]},
                              warnings=r.get("warnings", []))
        events.append({"tool_name": "resolve_exact_ids",
                       "arguments": {"id": r["normalized_id"]},
                       "ok": True, "data": str(data), "structured": art,
                       "error": None, "provenance": art.get("provenance", {}),
                       "warnings": r.get("warnings", []), "artifacts": []})
    return events


def run_exact_id_flow(question, *, sources=None, verifier_model=None, claim_extractor=None,
                      model_id="deepseek", shadow=True, stamp=None, constraints="",
                      **_ignored):
    """确定性 exact-ID 执行流。返回 ssc_a1.AgentState（形状与 run_agent 一致）。
    不调用 Planner/Executor；Verifier/Claim/Shadow 照常运行。"""
    import ssc_a1
    from schemas import AbstractEvidenceCard

    stamp = stamp or ssc_a1._gen_run_id()                     # 唯一 run_id，避免并发覆盖
    state = ssc_a1.AgentState(user_query=question, constraints=constraints, max_iterations=1)
    state.allowed_tools = ["resolve_exact_ids"]
    state.tool_trace.append({"event": "route", "tool": "exact_id", "detail": "deterministic"})

    batch = resolve_exact_ids(question, sources=sources)      # 程序、零 LLM
    cards = [AbstractEvidenceCard.model_validate(c) for c in batch.evidence_cards]
    state.evidence_cards = batch.evidence_cards
    state.research_plan = {"mode": "exact_id_deterministic",
                           "ids": [{"id": r["normalized_id"], "status": r["resolution_status"]}
                                   for r in batch.ids],
                           "all_terminal": batch.all_terminal}
    answer = _render_answer(batch)
    state.observations.append(answer)
    events = _batch_to_tool_events(batch)

    # 旧 Verifier 决定最终答案（裁决权不变）
    v = ssc_a1.verify(state, answer, verifier_model=verifier_model,
                      evidence_cards=cards, tool_failed=False)
    state.verification_results.append(v)
    if v.get("passed") is True:
        state.final_answer = answer
    else:
        state.final_answer = (f"⚠️ 未验证 / 证据不足（{v.get('status', 'no_verification')}）："
                              f"{v.get('reason', '')}\n\n{answer}")

    # Claim extractor + Shadow（结构化 observations 注入；不重建卡）
    if shadow:
        try:
            from shadow import run_shadow, default_claim_extractor
            state.shadow = run_shadow(
                question=question, plan=state.research_plan,
                allowed_tools=state.allowed_tools, selected_tools=["resolve_exact_ids"],
                final_text=answer, tool_events=events, messages=None, old_verify=v,
                claim_extractor=claim_extractor or default_claim_extractor(model_id),
                model_id=model_id, stamp=stamp or "exactid", evidence_cards=cards)
        except Exception as e:
            state.shadow = {"shadow_status": "failed", "shadow_error_type": type(e).__name__,
                            "shadow_error_message": str(e)[:300]}
    return state


def run_routed_agent(question, *, sources=None, **kwargs):
    """分流入口：exact_id → 确定性流；open → 原 ssc_a1.run_agent（行为不变）。
    返回 AgentState。kwargs 透传给下游（constraints/max_iterations/shadow/planner_model/
    verifier_model/claim_extractor/...）。"""
    route = classify(question)
    if route == "exact_id":
        return run_exact_id_flow(
            question, sources=sources,
            verifier_model=kwargs.get("verifier_model"),
            claim_extractor=kwargs.get("claim_extractor"),
            model_id=kwargs.get("executor_model", "deepseek"),
            shadow=kwargs.get("shadow", True),
            stamp=kwargs.get("stamp"),
            constraints=kwargs.get("constraints", ""))
    import ssc_a1
    return ssc_a1.run_agent(question, **kwargs)
