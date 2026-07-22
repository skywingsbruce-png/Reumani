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


def _sanitized_paper_or_skip(p, *, strict, skipped):
    """逐 ID 独立校验（ID 只来自 artifact provenance）。

    - PMID/DOI 存在但非法：strict → 抛 EvidenceSourceError；否则丢弃该 ID 并记 skip；
    - 清理后仍无任何合法 ID → 返回 None（跳过该 paper，不构卡）；
    - 有 ≥1 合法 ID → 返回**副本**（非法 ID 已置空），保留真实合法 ID 的证据。

    不因为一个畸形 DOI 就丢掉带合法 PMID 的真实证据。
    """
    pmid, doi = p.get("pmid"), p.get("doi")
    clean = dict(p)
    if pmid and not valid_pmid(str(pmid)):
        if strict:
            raise EvidenceSourceError(f"artifact 里的 PMID 非法：{pmid!r}")
        skipped.append({"reason": "invalid_pmid_in_artifact"})
        clean["pmid"] = None
    if doi and not valid_doi(str(doi)):
        if strict:
            raise EvidenceSourceError(f"artifact 里的 DOI 非法：{doi!r}")
        skipped.append({"reason": "invalid_doi_in_artifact"})
        clean["doi"] = None
    if not (clean.get("pmid") or clean.get("doi")):
        skipped.append({"reason": "no_id_in_artifact_provenance"})
        return None
    return clean


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
            clean = _sanitized_paper_or_skip(p, strict=strict, skipped=skipped)
            if clean is None:
                continue
            cards.append(EB.abstract_card_from_paper(
                clean, tool_name=getattr(m, "name", "search_evidence"),
                query=data.get("query", "")))
    return cards, skipped


def _iter(messages):
    if isinstance(messages, dict):
        messages = messages.get("messages", [])
    return messages if isinstance(messages, (list, tuple)) else []


def cards_from_shadow_event(event, *, strict=False):
    """从**shadow tool_event**（含 e["structured"] = 结构化 artifact）构建 EvidenceCard，
    施加与 cards_from_messages 相同的守卫。供 shadow.build_evidence_cards 调用
    —— 即真实 Runner → run_agent → shadow 链上的非测试调用方。

    返回 (cards, skipped)。
    """
    import evidence_build as EB
    from ids import valid_doi, valid_pmid

    cards, skipped = [], []
    if not event.get("ok"):
        skipped.append({"reason": "tool_failed", "tool": event.get("tool_name")})
        return cards, skipped
    art = event.get("structured")
    if not isinstance(art, dict):
        return cards, skipped                       # 非结构化 → 交给 legacy 路径
    if art.get("schema_version") != SUPPORTED_SCHEMA:
        skipped.append({"reason": f"incompatible_schema:{art.get('schema_version')}"})
        return cards, skipped
    prov = art.get("provenance") or {}
    cl = prov.get("content_level")
    data = art.get("data") if isinstance(art.get("data"), dict) else {}
    if data.get("retrieval_status") in ("zero_hits", "zero_candidates"):
        skipped.append({"reason": f"no_card_for_{data['retrieval_status']}"})
        return cards, skipped
    if cl == "abstract" and data.get("papers"):
        for p in data["papers"]:
            clean = _sanitized_paper_or_skip(p, strict=strict, skipped=skipped)
            if clean is None:
                continue
            cards.append(EB.abstract_card_from_paper(
                clean, tool_name=event.get("tool_name", "search_evidence"),
                query=data.get("query", "")))
    return cards, skipped
