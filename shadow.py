"""
Shadow Mode（Commit A）：把新数据链(EvidenceCard → Claim → Claim Graph → 四层 verify_all)
接入 SSc-A1 的【真实运行链】，但暂不改变旧 ssc_a1.verify 的最终裁决——只记录、只对比。
铁律：
- 只从【真实 tool messages / ToolResult】收集，绝不解析最终自然语言答案来伪造工具结果。
- 只有带【真实来源】(PMID/DOI 出现在工具输出里)的结果才进 EvidenceCard；不猜测 PMID/DOI/样本量/来源。
- 旧工具返回普通字符串 → 经 LegacyToolResultAdapter，标 provenance_quality="legacy_unstructured"，
  不自动当高质量证据。
- Claim 提取失败 → 结构化错误；不把"相关"自动改写为"驱动/导致"。
- 用户可见行为不变；shadow_verification=True。
"""

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from schemas import AbstractEvidenceCard, Provenance, Claim
from claim_graph import ClaimEvidenceGraph
import verifier as V

# 真实来源提取：只认工具输出里【实际出现】的 PubMed 链接 / DOI / PMID 标注
_PMID_URL = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d{1,9})")
_PMID_TAG = re.compile(r"PMID[:\s]+(\d{1,9})")
_DOI_URL = re.compile(r"doi\.org/(10\.[^\s|)\]]+)")
_FAIL_MARKERS = ["[工具失败", "读取失败", "检索失败", "[拒绝]", "未检索到", "解析失败",
                 "失败：", "被受限沙箱", "permission_denied", "approval_required"]


def git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=5).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


# ---------- 1) 从真实 messages 抽取工具事件 ----------
def _attr(m, name, default=None):
    if isinstance(m, dict):
        return m.get(name, default)
    return getattr(m, name, default)


def extract_tool_events(messages):
    """把 LangChain messages 里的 tool_calls 与 ToolMessage 配对成结构化事件。"""
    calls = {}   # tool_call_id -> {name, args}
    for m in messages or []:
        for tc in (_attr(m, "tool_calls", None) or []):
            cid = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
            name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
            args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
            calls[cid] = {"tool_name": name, "arguments": args}
    events = []
    for m in messages or []:
        # ToolMessage：type=="tool" 或有 tool_call_id
        is_tool = (_attr(m, "type", "") == "tool") or _attr(m, "tool_call_id", None) is not None
        if not is_tool:
            continue
        cid = _attr(m, "tool_call_id", None)
        name = _attr(m, "name", "") or (calls.get(cid, {}).get("tool_name", ""))
        args = calls.get(cid, {}).get("arguments", {})
        content = _attr(m, "content", "")
        events.append(adapt_legacy_result(name, args, content if isinstance(content, str) else str(content)))
    return events


def adapt_legacy_result(tool_name, arguments, content):
    """LegacyToolResultAdapter：旧工具返回的字符串 → 结构化事件（标记 legacy_unstructured）。"""
    ok = bool(content) and not any(mk in content for mk in _FAIL_MARKERS)
    return {
        "tool_name": tool_name, "arguments": arguments,
        "ok": ok, "data": content if ok else None,
        "error": None if ok else (content[:200] if content else "空结果"),
        "provenance": {"tool_name": tool_name, "provenance_quality": "legacy_unstructured"},
        "warnings": ["legacy_unstructured: 工具返回非结构化字符串，来源可信度低"],
        "artifacts": [],
    }


# ---------- 2) 只从带真实来源的结果建 EvidenceCard ----------
def build_evidence_cards(events, max_per_event=5):
    cards, seen = [], set()
    for e in events:
        if not e["ok"] or not e["data"]:
            continue
        text = e["data"]
        pmids = _PMID_URL.findall(text) + _PMID_TAG.findall(text)
        dois = _DOI_URL.findall(text)
        for pmid in list(dict.fromkeys(pmids))[:max_per_event]:
            eid = f"PMID:{pmid}"
            if eid in seen:
                continue
            seen.add(eid)
            cards.append(_card(eid, pmid=pmid, source=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                               tool=e["tool_name"]))
        for doi in list(dict.fromkeys(dois))[:max_per_event]:
            eid = f"DOI:{doi}"
            if eid in seen:
                continue
            seen.add(eid)
            cards.append(_card(eid, doi=doi, source=f"https://doi.org/{doi}", tool=e["tool_name"]))
    return cards


def _card(eid, *, pmid=None, doi=None, source="", tool=""):
    # 摘要级、legacy 来源：不编造样本量/原文；不能作关键结论
    return AbstractEvidenceCard(
        evidence_id=eid, title="(来自工具检索结果)",
        provenance=Provenance(tool_name=tool, source=source,
                              parameters={"provenance_quality": "legacy_unstructured"}),
        pmid=str(pmid) if pmid else None, doi=str(doi) if doi else None,
        publication_status="unknown", supporting_excerpt="",
        evidence_grade="初筛", extraction_confidence=0.2, human_review_status="pending")


# ---------- 3) 从 Executor 输出提取原子 Claim（extractor 可注入；不改写相关为因果）----------
def default_claim_extractor(model="deepseek"):
    """生产用：LLM 把最终答案拆成原子 Claim。返回 (final_text, card_ids)->list[dict]。"""
    from ssc_pi_agent import deepseek_llm_pro, judge_llm
    llm = judge_llm if model == "claude" else deepseek_llm_pro

    def _extract(final_text, card_ids):
        prompt = (
            "把下面的科研结论拆成【原子 Claim】（每个只表达一个可检验命题）。"
            "严禁把'相关/关联'改写成'驱动/导致/因果'。输出严格 JSON 数组，每项："
            "{\"text\":\"...\",\"claim_type\":\"existence|association|causal|mechanistic|clinical_efficacy|other\","
            "\"causal_strength\":\"none|correlational|associative|mechanistic|causal|unknown\","
            "\"supporting_ids\":[\"PMID:...\"]}。supporting_ids 只能取自这些已知证据ID："
            f"{card_ids}。找不到就留空。\n\n结论：\n{final_text}")
        return json.loads(re.search(r"\[.*\]", llm.invoke(prompt).content, re.DOTALL).group(0))

    return _extract


def extract_claims(final_text, cards, extractor):
    """返回 (claims, error)。extractor 失败 → 结构化错误，不崩。"""
    card_ids = [c.evidence_id for c in cards]
    try:
        raw = extractor(final_text, card_ids) or []
    except Exception as e:
        return [], {"error": "claim_extraction_failed", "detail": str(e)[:200]}
    claims = []
    for i, r in enumerate(raw):
        try:
            sup = [x for x in (r.get("supporting_ids") or []) if x in card_ids]
            unresolved = [x for x in (r.get("supporting_ids") or []) if x not in card_ids]
            claims.append(Claim(
                claim_id=f"claim_{i+1}", text=r.get("text", ""),
                claim_type=r.get("claim_type", "other"),
                causal_strength=r.get("causal_strength", "unknown"),
                supporting_evidence_ids=sup, unresolved_evidence_ids=unresolved))
        except Exception:
            continue
    return claims, None


# ---------- 4) Shadow 编排 ----------
def run_shadow(question, *, plan=None, allowed_tools=None, selected_tools=None,
               final_text="", tool_events=None, messages=None, old_verify=None,
               claim_extractor=None, model_id="", stamp=None, evidence_cards=None):
    events = tool_events if tool_events is not None else extract_tool_events(messages)
    allowed = set(allowed_tools or [])
    # 未授权工具调用（真实链上出现但不在 allowed_tools）
    unauthorized = sorted({e["tool_name"] for e in events if allowed and e["tool_name"] not in allowed})

    # 默认只从【工具真实结果】构建证据卡；evidence_cards 可注入(将来全文工具/测试)
    cards = evidence_cards if evidence_cards is not None else build_evidence_cards(events)
    claim_err = None
    if claim_extractor is not None:
        claims, claim_err = extract_claims(final_text, cards, claim_extractor)
    else:
        claims = []
        claim_err = {"error": "no_claim_extractor", "detail": "未提供 claim_extractor"}

    judged = ClaimEvidenceGraph(claims, cards).adjudicate() if claims else []
    tool_failed = any(e["ok"] is False for e in events)
    shadow_v = V.verify_all(question, events, claims, cards, adversary_searcher=None, high_risk=True)

    old_passed = bool(old_verify.get("passed")) if isinstance(old_verify, dict) else None
    shadow_passed = bool(shadow_v.get("passed"))
    comparison = {"old_passed": old_passed, "shadow_passed": shadow_passed,
                  "agree": (old_passed == shadow_passed) if old_passed is not None else None,
                  "divergence": (old_passed is not None and old_passed != shadow_passed)}

    manifest = {
        "shadow_verification": True,
        "run_id": stamp or datetime.now().strftime("%Y%m%d_%H%M%S"),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "git_commit": git_commit(), "model_id": model_id,
        "question": question,
        "research_plan": plan,
        "selected_tools": selected_tools, "allowed_tools": sorted(allowed),
        "unauthorized_tool_calls": unauthorized,
        "tool_events": events,
        "evidence_cards": [c.model_dump() for c in cards],
        "claims": [c.model_dump() for c in judged],
        "claim_extraction_error": claim_err,
        "any_tool_failed": tool_failed,
        "old_verifier_result": old_verify,
        "shadow_verifier_result": shadow_v,
        "comparison": comparison,
        "note": "Shadow：新链只记录+对比，最终裁决仍由旧 ssc_a1.verify 决定；用户可见行为不变。",
    }
    return manifest
