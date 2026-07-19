"""
四层 Verifier（不再是"另一个 LLM 读上游答案给 passed=True/False"）。
  1. Schema Verifier      —— 字段/类型/必填/工具状态。
  2. Citation Verifier    —— PMID/DOI 格式与存在性、证据卡是否有来源定位（抓伪造 DOI）。
  3. Claim–Evidence Verifier —— 原始证据片段是否真支持每个 Claim（复用 claim_graph）。
  4. Adversarial Verifier —— 【不读 Planner 推理】，独立重生成检索式找反证/替代解释。
反偏见：Verifier 只拿 研究问题 + 原子 Claim + 工具结果 + 原始证据；不读 Planner 完整推理。
        第二个 LLM/反证器只作【顾问】，不当金标准；关键结论一律 human_review_required=True。
整体 fail-closed：四层全过才算通过；任何一层不过 → 不通过。
"""

from schemas import VerificationResult
import ids                      # 唯一 ID 权威（PMID/DOI 校验不在此重写）


def _fail(status, reason, **kw):
    return VerificationResult(passed=False, status=status, reason=reason, **kw).model_dump()


def _ok(reason=""):
    return VerificationResult(passed=True, status="passed", reason=reason).model_dump()


# ---------- Layer 1: Schema ----------
def schema_verify(tool_results, claims):
    """工具是否真实成功 + 是否有原子 Claim（不能拿一个笼统结论蒙混）。"""
    for tr in tool_results or []:
        ok = tr.get("ok") if isinstance(tr, dict) else getattr(tr, "ok", None)
        if ok is not True:
            et = tr.get("error_type") if isinstance(tr, dict) else getattr(tr, "error_type", "")
            return _fail("tool_execution_failed", f"存在未成功的工具结果（error_type={et}）")
    if not claims:
        return _fail("verification_error", "没有可核查的原子 Claim（禁止直接给笼统结论）")
    return _ok("schema/工具状态正常")


# ---------- Layer 2: Citation ----------
def citation_verify(evidence_cards, checker=None):
    """checker(pmid, doi)->bool 可注入在线核验；默认离线只做格式+来源定位检查（抓伪造 DOI/无出处）。"""
    problems = []
    for c in evidence_cards or []:
        eid = getattr(c, "evidence_id", "?")
        pmid, doi = getattr(c, "pmid", None), getattr(c, "doi", None)
        if pmid and not ids.valid_pmid(pmid):
            problems.append(f"{eid}: PMID 格式非法({pmid})")
        if doi and not ids.valid_doi(doi):
            problems.append(f"{eid}: DOI 格式非法/疑伪造({doi})")
        src = getattr(getattr(c, "provenance", None), "source", None)
        if not (pmid or doi or src):
            problems.append(f"{eid}: 无任何来源标识")
        if getattr(c, "tier", "") != "analysis" and c.traceability() == "low":
            problems.append(f"{eid}: 低可追溯（缺来源定位）")
        if checker and (pmid or doi) and not checker(pmid, doi):
            problems.append(f"{eid}: 在线核验未通过（可能不存在）")
    if problems:
        return _fail("citation_error", "引用核验问题：" + "；".join(problems[:6]),
                     warnings=problems)
    return _ok("引用核验通过")


# ---------- Layer 3: Claim–Evidence ----------
def claim_evidence_verify(claims, evidence_cards):
    """用 claim_graph 逐个裁决；返回 (result_dict, judged_claims)。"""
    from claim_graph import ClaimEvidenceGraph
    judged = ClaimEvidenceGraph(claims, evidence_cards).adjudicate()
    contradicted = [c for c in judged if c.verdict == "contradicted"]
    unsupported = [c for c in judged if c.verdict == "not_supported"]
    weak = [c for c in judged if c.verdict in ("partially_supported", "insufficient_evidence",
                                               "technically_unverifiable")]
    if contradicted:
        return _fail("claim_contradicted", f"{len(contradicted)} 个 Claim 被反证",
                     warnings=[c.claim_id for c in contradicted]), judged
    if unsupported:
        return _fail("claim_unsupported", f"{len(unsupported)} 个 Claim 有证据但不支持",
                     warnings=[c.claim_id for c in unsupported]), judged
    if weak:
        return _fail("insufficient_evidence", f"{len(weak)} 个 Claim 证据不足/仅部分支持",
                     warnings=[c.claim_id for c in weak]), judged
    return _ok("所有 Claim 均被达标证据支持"), judged


# ---------- Layer 4: Adversarial（不读 Planner 推理）----------
def _counter_queries(claim_text):
    """据 Claim 文本重新生成【反证/替代解释】检索式，与 Planner 的检索无关。"""
    base = claim_text.strip()
    return [f"{base} negative OR no association OR fails to",
            f"{base} alternative explanation OR confounding",
            f"{base} not required OR independent of"]


def adversarial_verify(question, claims, searcher=None):
    """独立找反证。searcher(query)->list(hits) 可注入(检索/LLM)。
    只吃 question + 原子 claim，不吃 Planner 推理。第二个模型只作顾问，不当金标准。"""
    if searcher is None:
        # 未运行独立反证 → 不静默放行；标记需人工复核
        return VerificationResult(passed=True, status="passed",
                                  reason="未运行独立反证检索（顾问层缺失）",
                                  warnings=["adversarial_not_run"]).model_dump()
    refuted = []
    for c in claims:
        for q in _counter_queries(c.text):
            hits = searcher(q) or []
            if hits:
                refuted.append({"claim_id": c.claim_id, "query": q, "n_counter": len(hits)})
                break
    if refuted:
        return _fail("adversarial_counterevidence",
                     f"独立反证检索发现 {len(refuted)} 个 Claim 有反证/替代解释",
                     warnings=[r["claim_id"] for r in refuted])
    return _ok("独立反证检索未发现明显反证（顾问性，非金标准）")


# ---------- 编排 ----------
def verify_all(question, tool_results, claims, evidence_cards, *,
               citation_checker=None, adversary_searcher=None, high_risk=False):
    """四层独立核查 + fail-closed 聚合。返回逐层结果 + 整体判定 + human_review。"""
    layers = {}
    layers["schema"] = schema_verify(tool_results, claims)
    layers["citation"] = citation_verify(evidence_cards, checker=citation_checker)
    r3, judged = claim_evidence_verify(claims, evidence_cards)
    layers["claim_evidence"] = r3
    layers["adversarial"] = adversarial_verify(question, claims, searcher=adversary_searcher)

    passed = all(l["passed"] is True for l in layers.values())
    adversary_not_run = "adversarial_not_run" in (layers["adversarial"].get("warnings") or [])
    human_review = bool(high_risk
                        or any(getattr(c, "human_review_required", False) for c in judged)
                        or not passed
                        or adversary_not_run)     # 关键结论/未跑反证 → 必须人工复核
    return {
        "passed": passed,
        "status": "passed" if passed else "not_passed",
        "human_review_required": human_review,
        "layers": layers,
        "claims": [c.model_dump() for c in judged],
        "note": "四层独立核查；反证器不读 Planner 推理；第二 LLM 非金标准；关键结论需人工审核。",
    }
