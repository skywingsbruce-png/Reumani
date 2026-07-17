"""
文献分层使用（全量保存 + 质量标签 + 相关性 + 按任务动态选择）。
原则：
- 论文全量保存，低等级不删除；本模块只【打标 + 动态排序】，不删数据。
- 证据等级 A–F 由【研究设计】决定，【绝不用期刊影响因子】。
- 排序透明：分别给出 研究质量 / 相关性 / 直接性 / 可重复性 / 可追溯性 / 任务契合，
  不只输出一个不透明总分。
- 按任务类型动态调整优先级（临床治疗/风险预后/机制/机制可行性/新方向探索）。
"""

import re

from schemas import LiteratureQuality

# 默认证据层级（起点，可被任务动态调整）
TIER_DESC = {
    "A": "指南、系统综述、Meta、关键 RCT",
    "B": "大型队列、纵向、多中心人体研究",
    "C": "高质量机制研究、人类组织研究",
    "D": "动物、细胞和体外研究",
    "E": "预印本、小样本、会议摘要",
    "F": "评论、叙述性综述、低信息摘要",
}
TIER_QUALITY = {"A": 1.0, "B": 0.85, "C": 0.7, "D": 0.5, "E": 0.35, "F": 0.2}


def _has(text, kws):
    t = (text or "").lower()
    return any(k in t for k in kws)


def classify_tier(q: LiteratureQuality) -> str:
    """据研究设计给 A–F（不看影响因子）。"""
    st = (q.study_type or "").lower()
    if _has(st, ["editorial", "评论", "comment", "letter", "观点", "narrative review", "叙述性综述", "社论"]):
        return "F"
    if q.retracted:
        return "F"
    if q.preprint or _has(st, ["conference", "会议", "abstract", "摘要", "poster"]) \
            or (q.sample_size is not None and q.sample_size < 20):
        return "E"
    if _has(st, ["guideline", "指南", "systematic review", "系统综述", "meta", "荟萃", "meta-analysis"]) \
            or (q.randomized and _has(st, ["rct", "randomized", "随机"])):
        return "A"
    if q.human_evidence and (q.longitudinal or q.multicenter or _has(st, ["cohort", "队列", "longitudinal", "纵向", "prospective", "前瞻"])):
        return "B"
    if q.human_evidence and _has(st, ["mechan", "机制", "tissue", "组织", "perturbation", "扰动",
                                      "knockdown", "crispr", "single-cell", "单细胞", "biopsy", "活检"]):
        return "C"
    if q.animal_evidence or q.in_vitro_evidence:
        return "D"
    if q.human_evidence:
        return "C"
    return "F"


# 任务类型 → 优先/可接受 tier + 是否允许动物体外外推临床
TASK_PROFILES = {
    "clinical_treatment": {"prefer": {"A"}, "accept": {"B"}, "allow_nonhuman_extrapolation": False,
                           "note": "临床治疗问题：指南/RCT/系统综述优先"},
    "prognosis_risk": {"prefer": {"B"}, "accept": {"A", "C"}, "allow_nonhuman_extrapolation": False,
                       "note": "风险与预后：纵向队列/多中心优先"},
    "mechanism": {"prefer": {"C"}, "accept": {"A", "B", "D"}, "allow_nonhuman_extrapolation": False,
                  "note": "机制问题：人体组织/扰动实验/独立复制优先"},
    "mechanism_feasibility": {"prefer": {"C", "D"}, "accept": {"E"}, "allow_nonhuman_extrapolation": True,
                              "note": "机制可行性：动物/细胞可用，但不能外推临床"},
    "exploration": {"prefer": {"E", "D", "C"}, "accept": {"A", "B", "F"}, "allow_nonhuman_extrapolation": True,
                    "note": "新方向探索：预印本/小样本可提供线索，不能形成强结论"},
}
DEFAULT_TASK = "mechanism"


def _relevance(paper, query):
    """简单透明的相关性：query 词在 标题+摘要 的覆盖比例（0..1）。可由检索层覆盖传入更好的值。"""
    qtok = set(re.findall(r"[a-z0-9]{3,}", (query or "").lower())) | set(re.findall(r"[一-鿿]{2,}", query or ""))
    if not qtok:
        return 0.0
    blob = (paper.get("title", "") + " " + paper.get("abstract", "")).lower()
    hit = sum(1 for t in qtok if t in blob)
    return round(hit / len(qtok), 3)


def _directness(q: LiteratureQuality, task):
    prof = TASK_PROFILES.get(task, TASK_PROFILES[DEFAULT_TASK])
    if q.human_evidence:
        return 1.0
    if q.animal_evidence or q.in_vitro_evidence:
        return 0.7 if prof["allow_nonhuman_extrapolation"] else 0.25
    return 0.5


def _reproducibility(q: LiteratureQuality):
    if q.retracted:
        return 0.0
    s = 0.4
    if q.independent_replication:
        s += 0.3
    if q.multicenter:
        s += 0.15
    if q.preregistered:
        s += 0.1
    if q.adjusted_analysis:
        s += 0.05
    return round(min(s, 1.0), 3)


def _traceability(q: LiteratureQuality):
    return 1.0 if q.full_text_available else 0.4


def _task_fit(tier, task):
    prof = TASK_PROFILES.get(task, TASK_PROFILES[DEFAULT_TASK])
    if tier in prof["prefer"]:
        return 1.0
    if tier in prof["accept"]:
        return 0.6
    return 0.3


def score_factors(paper, quality: LiteratureQuality, query, task=DEFAULT_TASK, relevance=None):
    """返回【透明的多因子分解】+ 组合分（组合分不掩盖各因子）。"""
    tier = classify_tier(quality)
    f = {
        "tier": tier,
        "research_quality": TIER_QUALITY[tier],
        "relevance": _relevance(paper, query) if relevance is None else round(float(relevance), 3),
        "directness": _directness(quality, task),
        "reproducibility": _reproducibility(quality),
        "traceability": _traceability(quality),
        "task_fit": _task_fit(tier, task),
    }
    # 组合分 = 五因子乘积 × 任务契合（透明，可自行改权重）
    f["combined"] = round(f["research_quality"] * f["relevance"] * f["directness"]
                          * f["reproducibility"] * f["traceability"] * f["task_fit"], 4)
    f["flags"] = _flags(quality, tier, task)
    return f


def _flags(q: LiteratureQuality, tier, task):
    prof = TASK_PROFILES.get(task, TASK_PROFILES[DEFAULT_TASK])
    flags = []
    if q.retracted:
        flags.append("撤稿：不得用于正向结论")
    if q.preprint:
        flags.append("预印本：仅线索，不能形成强结论")
    if (q.animal_evidence or q.in_vitro_evidence) and not prof["allow_nonhuman_extrapolation"]:
        flags.append("非人体研究：不能直接外推到该临床/预后问题")
    if q.sample_size is not None and q.sample_size < 20:
        flags.append("小样本")
    return flags


def rank_literature(items, query, task=DEFAULT_TASK, top_k=None, drop_retracted=True):
    """items: [{"paper": dict, "quality": LiteratureQuality}]。
    返回按 combined 降序的列表（每条带完整因子分解）。全量保留：撤稿默认移出【结论用】排序，
    但作为 excluded 一并返回，不从数据湖删除。"""
    ranked, excluded = [], []
    for it in items:
        f = score_factors(it["paper"], it["quality"], query, task=task)
        row = {"paper": it["paper"], "factors": f, "tier": f["tier"], "flags": f["flags"]}
        if drop_retracted and it["quality"].retracted:
            excluded.append(row)
        else:
            ranked.append(row)
    ranked.sort(key=lambda r: -r["factors"]["combined"])
    if top_k:
        ranked = ranked[:top_k]
    return {"task": task, "task_note": TASK_PROFILES.get(task, {}).get("note", ""),
            "ranked": ranked, "excluded_retracted": excluded,
            "note": "全量保存：低等级论文仍在数据湖，此处仅按任务动态排序，未删除任何论文。"}
