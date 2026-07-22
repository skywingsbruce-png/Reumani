"""从**真实 ToolMessage 的 artifact** 构建 EvidenceCard（A.6.6.2 §4）。

铁律：
- EvidenceCard 只能从 `ToolMessage.artifact` 的结构化 provenance 构建；
- PMID/DOI 只能来自 artifact，**绝不**从自然语言 content 里正则猜 ID；
- artifact 缺失 / schema 不兼容 → fail-closed（跳过，不伪造）；
- 错误 ToolMessage（status=error）→ 不构卡；
- zero_hits → 不构造虚假 EvidenceCard。
"""

from ids import valid_doi, valid_pmid

SUPPORTED_SCHEMA = "toolresult-v1"


class EvidenceSourceError(RuntimeError):
    """artifact 缺失/不兼容/被 NL 污染时 fail-closed。"""


def _tool_message_artifact(msg):
    """只认真实 ToolMessage 的 artifact；其它一律 None。"""
    if type(msg).__name__ != "ToolMessage":
        return None, None
    status = str(getattr(msg, "status", "") or "").lower()
    if status == "error":
        return None, "error_tool_message"        # 错误观察不构卡
    art = getattr(msg, "artifact", None)
    if art is None:
        return None, "no_artifact"
    if not isinstance(art, dict):
        return None, "artifact_not_dict"
    if art.get("schema_version") != SUPPORTED_SCHEMA:
        return None, f"incompatible_schema:{art.get('schema_version')}"
    return art, None


def cards_from_messages(messages, *, strict=True):
    """扫描 Agent 消息状态里的 ToolMessage，从**兼容 artifact** 构建 EvidenceCard。

    返回 (cards, skipped)。strict=True 时 artifact 存在但 ID 来源不合法则抛错。
    """
    import evidence_build as EB

    cards, skipped = [], []
    for m in _iter(messages):
        art, why = _tool_message_artifact(m)
        if art is None:
            if why and why not in ("no_artifact",):     # 非结构化工具正常没有 artifact
                skipped.append({"reason": why,
                                "tool": getattr(m, "name", None)})
            continue
        data = art.get("data") if isinstance(art.get("data"), dict) else {}
        rstatus = data.get("retrieval_status")
        if rstatus in ("zero_hits", "zero_candidates"):
            skipped.append({"reason": f"no_card_for_{rstatus}",
                            "tool": getattr(m, "name", None)})
            continue
        papers = data.get("papers") or ([data] if data.get("pmid") or data.get("doi")
                                        else [])
        for p in papers:
            pmid, doi = p.get("pmid"), p.get("doi")
            # ID 只能来自 artifact，且必须通过同一权威校验
            if pmid and not valid_pmid(str(pmid)):
                if strict:
                    raise EvidenceSourceError(f"artifact 里的 PMID 非法：{pmid!r}")
                skipped.append({"reason": "invalid_pmid_in_artifact"})
                continue
            if doi and not valid_doi(str(doi)):
                if strict:
                    raise EvidenceSourceError(f"artifact 里的 DOI 非法：{doi!r}")
                skipped.append({"reason": "invalid_doi_in_artifact"})
                continue
            if not (pmid or doi):
                skipped.append({"reason": "no_id_in_artifact_provenance"})
                continue
            cards.append(EB.abstract_card_from_paper(
                p, tool_name=getattr(m, "name", "search_evidence"),
                query=data.get("query", "")))
    return cards, skipped


def _iter(messages):
    if isinstance(messages, dict):
        messages = messages.get("messages", [])
    return messages if isinstance(messages, (list, tuple)) else []
