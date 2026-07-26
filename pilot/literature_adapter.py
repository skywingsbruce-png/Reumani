"""纯函数 adapter：Europe PMC 结构化响应 → LiteratureRecord[]（A.7.4.2）。

零 LLM、零网络、零账本。字段只取自结构化来源响应，**不由 LLM 推测**；
无法可靠判断的研究字段一律 None/unknown。复用现有唯一权威：
- `ids.normalize_pmid/normalize_doi`（ID 规范化）；
- `tool_envelope.compute_hash`（SHA-256 唯一实现）+ `now()`；
- `schemas.Provenance`；`pilot.open_task_contracts.LiteratureRecord` + content-level 集中映射。
不新增第二套 hash / Provenance / EvidenceCard 定义。
"""

from tool_envelope import compute_hash, now
from ids import normalize_doi, normalize_pmid
from schemas import Provenance
from pilot.open_task_contracts import (LiteratureRecord,
                                       literature_content_level_to_provenance)

SOURCE = "Europe PMC"


def _canonical_payload(pmid, doi, title, year, journal, abstract, content_level, source_ids):
    """content_hash 的规范化 payload：只含文献核心内容；**不含** run_id/retrieved_at/路径/密钥。
    key 排序由 compute_hash(json sort_keys) 保证 → 同内容跨运行稳定。"""
    return {"normalized_pmid": pmid, "normalized_doi": doi, "canonical_title": title,
            "year": year, "journal": journal, "abstract": abstract,
            "content_level": content_level, "source": SOURCE,
            "source_ids": sorted(source_ids)}


def epmc_item_to_record(item, query):
    """单条 EPMC item → (LiteratureRecord | None, skip_reason)。无合法 PMID/DOI → 跳过。"""
    pmid = normalize_pmid(item.get("pmid")) if item.get("pmid") else None
    doi = normalize_doi(item.get("doi")) if item.get("doi") else None
    if not (pmid or doi):
        return None, "no_valid_id"
    title = item.get("title") or None
    year = ((item.get("firstPublicationDate") or "")[:4]) or None
    journal = item.get("journalTitle") or item.get("source") or None
    abstract = item.get("abstractText") or None
    content_level = "abstract" if (abstract and abstract.strip()) else "metadata_only"
    source_ids = sorted({x for x in (pmid, doi) if x})
    chash = compute_hash(_canonical_payload(pmid, doi, title, year, journal, abstract,
                                            content_level, source_ids))
    prov = Provenance(
        tool_name="search_literature", source=SOURCE, retrieved_at=now(),
        parameters={"query": query}, source_ids=source_ids,
        content_level=literature_content_level_to_provenance(content_level),
        content_hash=chash, hash_algorithm="sha256")
    rec = LiteratureRecord(
        pmid=pmid, doi=doi, title=title, year=year, journal=journal, abstract=abstract,
        content_level=content_level, source=SOURCE, query=query, provenance=prov,
        source_ids=source_ids, content_hash=chash, hash_algorithm="sha256")
    # study_design/species/longitudinal/interventional 无结构化字段 → 保持 None（不猜）
    return rec, None


def build_literature_records(results, query):
    """EPMC result list → (records, warnings, skipped)。多条不串位；部分非法只跳过不修补。"""
    records, warnings, skipped = [], [], 0
    for item in results or []:
        rec, why = epmc_item_to_record(item, query)
        if rec is None:
            skipped += 1
        else:
            records.append(rec)
    if skipped:
        warnings.append(f"skipped {skipped} record(s) without valid PMID/DOI")
    return records, warnings, skipped


def records_to_data(records, query, warnings):
    """success 分支的 ToolResult.data。"""
    return {"query": query, "retrieval_status": "success",
            "record_count": len(records),
            "records": [r.model_dump() for r in records],
            "warnings": warnings, "source": SOURCE}


def zero_hits_data(query):
    return {"query": query, "retrieval_status": "zero_hits", "record_count": 0,
            "records": [], "warnings": [], "source": SOURCE}
