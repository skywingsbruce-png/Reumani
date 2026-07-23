"""确定性任务分流（Addendum 2 §8）。零 LLM。

规则（保守、可测试）：
- 无可提取 PMID/DOI → "open"（走原 ReAct）；
- 含可提取 PMID/DOI 且带**核验/精确**意图关键词 → "exact_id"；
- 含 ID 但无核验意图 → "open"（fail-safe：不劫持仅顺带引用 ID 的机制类任务）。

不确定时偏向 "open"（不夺走开放式任务），exact-ID 结果不会被语义搜索覆盖，
因为 exact_id 路径根本不进入 ReAct。
"""

from pilot.exact_id_resolver import extract_ids

# 核验/精确意图（出现其一即视为"目标是核验/获取这些 ID"）
_INTENT = ("精确", "命中", "核验", "查证", "resolve", "verify", "exact_hit",
           "zero_hits", "exact id", "exact-id", "标题", "年份", "期刊")


def has_verification_intent(question: str) -> bool:
    q = (question or "").lower()
    return any(k.lower() in q for k in _INTENT)


def classify(question: str) -> str:
    """返回 'exact_id' 或 'open'。确定性、无网络、无 LLM。"""
    if not extract_ids(question or ""):
        return "open"
    return "exact_id" if has_verification_intent(question) else "open"
