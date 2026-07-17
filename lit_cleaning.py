"""
四层文献清洗流程。原则：【不让一个大模型独断论文质量】——确定性检查 + 程序规则把关，
LLM 只被限制在"抽取"这一层，质量判定由程序规则和人工决定。
  第1层 确定性检查：PMID/DOI、去重、撤稿/更正、年份/期刊、研究类型、人/动物/细胞、全文可用性。
  第2层 LLM 抽取：PICO/PECO、样本量、组织细胞、主要发现、局限、原文证据片段（extractor 可注入）。
  第3层 程序规则：动物≠临床、横断面≠强因果、无样本量不编造、摘要≠全文证据、预印本必标、
                无来源定位≠关键证据。
  第4层 人工审核优先级：临床/治疗结论、新发现、与共识冲突、两模型不一致、低置信度、
                将进入 know-how/protocol/benchmark 的内容。
"""

import re

from schemas import NOT_REPORTED

_PMID = re.compile(r"^\d{1,9}$")          # PMID 为纯数字（旧文献可短）；非数字即视为伪造
_DOI = re.compile(r"^10\.\d{4,9}/\S+$")


# ---------- 第1层：确定性检查 ----------
def _evidence_class(paper):
    t = (paper.get("title", "") + " " + paper.get("abstract", "") + " " + paper.get("pub_type", "")).lower()
    if any(k in t for k in ["cell line", "in vitro", "organoid", "细胞系", "体外", "类器官"]):
        return "in_vitro"
    if any(k in t for k in ["mouse", "murine", "rat ", "in vivo", "小鼠", "大鼠", "动物模型", "knockout mice"]):
        return "animal"
    if any(k in t for k in ["patient", "patients", "human", "cohort", "clinical", "患者", "人体", "队列"]):
        return "human"
    return "unknown"


def _study_type(paper):
    t = (paper.get("title", "") + " " + paper.get("pub_type", "") + " " + paper.get("abstract", "")).lower()
    for kw, name in [("systematic review", "systematic review"), ("meta-analysis", "meta-analysis"),
                     ("randomized", "RCT"), ("randomised", "RCT"), ("cohort", "cohort"),
                     ("case-control", "case-control"), ("cross-sectional", "cross-sectional"),
                     ("横断面", "cross-sectional"), ("case report", "case report"),
                     ("editorial", "editorial"), ("review", "review")]:
        if kw in t:
            return name
    return NOT_REPORTED


def layer1_deterministic(paper):
    pmid, doi = paper.get("pmid"), paper.get("doi")
    pt = (paper.get("pub_type", "") or "").lower()
    return {
        "pmid": str(pmid) if pmid else None,
        "pmid_valid": bool(pmid and _PMID.match(str(pmid))),
        "doi": str(doi) if doi else None,
        "doi_valid": bool(doi and _DOI.match(str(doi))),
        "year": (paper.get("year") or "")[:4],
        "journal": paper.get("journal", ""),
        "retracted": ("retract" in pt),
        "corrected": ("erratum" in pt or "correction" in pt or "corrigend" in pt),
        "preprint": ("preprint" in pt
                     or any(x in (paper.get("journal", "") or "").lower() for x in ["biorxiv", "medrxiv", "preprint", "ppr"])
                     or any(x in (paper.get("link", "") or "").lower() for x in ["biorxiv", "medrxiv"])),
        "study_type": _study_type(paper),
        "evidence_class": _evidence_class(paper),
        "full_text_available": bool(paper.get("full_text_available") or paper.get("isOpenAccess") == "Y"),
    }


def dedup(papers):
    """按 PMID/DOI/标题去重（确定性）。返回 (去重后列表, 去掉数)。"""
    seen, out = set(), []
    for p in papers:
        key = str(p.get("pmid") or p.get("doi") or (p.get("title", "") or "")[:60]).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out, len(papers) - len(out)


# ---------- 第2层：LLM 抽取（extractor 可注入；LLM 只做抽取，不判质量）----------
def layer2_extract(paper, extractor):
    """extractor(paper)->dict：PICO/样本量/组织细胞/主要发现/局限/原文片段。找不到写未报告，禁止编造。"""
    try:
        ext = extractor(paper) or {}
    except Exception as e:
        return {"error": str(e)[:150]}
    ext.setdefault("sample_size", NOT_REPORTED)
    ext.setdefault("supporting_excerpt", "")
    ext.setdefault("main_findings", [])
    ext.setdefault("limitations", [])
    ext.setdefault("extraction_confidence", 0.5)
    return ext


# ---------- 第3层：程序规则 ----------
def layer3_rules(det, ext):
    caveats, flags = [], []
    ec = det["evidence_class"]
    st = (det["study_type"] or "").lower()
    if ec == "animal":
        caveats.append("动物研究：不能支持临床疗效")
    if ec == "in_vitro":
        caveats.append("体外研究：不能支持患者治疗")
    if "cross-sectional" in st:
        caveats.append("横断面：不能形成强因果")
    if det["preprint"]:
        flags.append("preprint_marked")
        caveats.append("预印本：未经同行评审")
    if det["retracted"]:
        caveats.append("撤稿：不得用于正向结论")
    if det["corrected"]:
        caveats.append("更正版：须记录更正版本")

    # 无样本量不能编造：只用抽取到的，缺就 未报告
    sample_size = ext.get("sample_size") or NOT_REPORTED

    # 摘要证据不能冒充全文证据：tier 由全文可用性决定
    tier = "fulltext" if det["full_text_available"] else "abstract"
    has_locator = bool((ext.get("supporting_excerpt") or "").strip())

    # 没有来源定位不能成为关键证据；摘要级也不行
    key_evidence_eligible = bool(tier == "fulltext" and has_locator and not det["retracted"])

    return {"caveats": caveats, "flags": flags, "sample_size": sample_size,
            "tier": tier, "has_locator": has_locator,
            "key_evidence_eligible": key_evidence_eligible}


# ---------- 第4层：人工审核优先级 ----------
def layer4_review_priority(det, ext, rules, destinations=None, second_ext=None):
    """destinations: 该条将进入的去处，如 ['know-how','protocol','benchmark']。
    second_ext: 第二个模型的抽取，用于检测两模型不一致。"""
    reasons = []
    st = (det["study_type"] or "").lower()
    text = " ".join(str(x) for x in (ext.get("main_findings") or [])).lower()
    if any(k in st for k in ["rct", "cohort"]) or any(k in text for k in ["treatment", "therapy", "疗效", "治疗", "efficacy"]):
        reasons.append("临床/治疗结论")
    if ext.get("novel") or "novel" in text or "首次" in text:
        reasons.append("新发现")
    if ext.get("conflicts_consensus"):
        reasons.append("与共识冲突")
    if second_ext is not None and _extractions_disagree(ext, second_ext):
        reasons.append("两模型抽取不一致")
    if (ext.get("extraction_confidence", 1.0) or 0) < 0.5:
        reasons.append("低置信度提取")
    for d in (destinations or []):
        if d in ("know-how", "knowhow", "protocol", "benchmark"):
            reasons.append(f"将进入 {d}")
    return {"human_review_required": bool(reasons), "reasons": reasons}


def _extractions_disagree(a, b):
    if (a.get("sample_size") or NOT_REPORTED) != (b.get("sample_size") or NOT_REPORTED):
        return True
    fa = set(str(x).lower() for x in (a.get("main_findings") or []))
    fb = set(str(x).lower() for x in (b.get("main_findings") or []))
    if fa and fb and not (fa & fb):
        return True
    return False


def clean_paper(paper, extractor, destinations=None, second_extractor=None):
    """四层清洗一篇文献，返回结构化清洗结果（不含'总质量分'，各层结论透明可查）。"""
    det = layer1_deterministic(paper)
    ext = layer2_extract(paper, extractor)
    second = layer2_extract(paper, second_extractor) if second_extractor else None
    rules = layer3_rules(det, ext)
    review = layer4_review_priority(det, ext, rules, destinations=destinations, second_ext=second)
    return {"paper_id": det["pmid"] or det["doi"] or (paper.get("title", "") or "")[:40],
            "layer1_deterministic": det, "layer2_extraction": ext,
            "layer3_rules": rules, "layer4_review": review}
