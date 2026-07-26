"""
把不同来源的抽取结果构造成【校验过的】证据卡（保留 provenance）。只依赖 schemas，可单测。
- abstract_card_from_extraction：摘要级 LLM 抽取字典 + 文献元数据 → AbstractEvidenceCard（只初筛）。
- analysis_card：Reumani 自己的数据分析结果 → AnalysisEvidenceCard。
规则落地：摘要卡不设 supporting_excerpt（无逐字原文）→ 自动低可追溯、不能作关键结论；
         预印本/撤稿据来源标注；找不到的字段写 NOT_REPORTED，不猜测。
"""

from schemas import (AbstractEvidenceCard, AnalysisEvidenceCard, Provenance, NOT_REPORTED)
import ids


def _preprint(paper):
    blob = f"{paper.get('pub_type','')} {paper.get('journal','')} {paper.get('link','')}".lower()
    return "preprint" in blob or "ppr" in blob or "biorxiv" in blob or "medrxiv" in blob


def _first(v, default=NOT_REPORTED):
    if isinstance(v, list):
        return v[0] if v else default
    return v if v else default


def abstract_card_from_extraction(card: dict, paper: dict) -> AbstractEvidenceCard:
    """摘要级证据卡：结论强度固定为初筛，保留来源 provenance。"""
    pmid = paper.get("pmid")
    doi = paper.get("doi")
    prov = Provenance(
        tool_name="europepmc", source=paper.get("link") or "",
        retrieved_at=paper.get("retrieved_at"),
        parameters={"pmid": pmid, "doi": doi, "level": "abstract"},
        dataset_version="europepmc",
    )
    status = "preprint" if _preprint(paper) else "published"
    return AbstractEvidenceCard(
        evidence_id=str(pmid or doi or paper.get("title", "")[:40]),
        title=paper.get("title", NOT_REPORTED),
        provenance=prov,
        pmid=str(pmid) if pmid else None,
        doi=str(doi) if doi else None,
        publication_status=status,
        study_type=card.get("study_type") or NOT_REPORTED,
        species=_first(card.get("model_system")),
        tissue_or_cell=card.get("model_system"),
        sample_size=card.get("sample_size") or NOT_REPORTED,
        main_claims=card.get("main_findings", []) or [],
        limitations=card.get("limitations", []) or [],
        supporting_excerpt="",                       # 摘要抽取无逐字原文 → 不能作关键结论
        evidence_direction="inconclusive",
        extraction_confidence=0.5,
        evidence_grade="初筛",                        # 摘要级上限
        human_review_status="pending",
    )


def abstract_cards_from_ids(pmids, dois, tool_name, source=""):
    """【唯一】从工具输出里【真实出现】的 PMID/DOI 构建摘要级证据卡（legacy 路径）。
    不猜测样本量/原文；content_level=abstract；不能作关键结论。"""
    cards = []
    for pmid in pmids:
        if not ids.valid_pmid(pmid):
            continue
        cards.append(_id_card(f"PMID:{pmid}", tool_name, pmid=str(pmid),
                              src=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"))
    for doi in dois:
        if not ids.valid_doi(doi):
            continue
        cards.append(_id_card(f"DOI:{doi}", tool_name, doi=str(doi), src=f"https://doi.org/{doi}"))
    return cards


def _id_card(eid, tool_name, *, pmid=None, doi=None, src=""):
    prov = Provenance(tool_name=tool_name, source=src, content_level="abstract",
                      source_ids=[eid], parameters={"provenance_quality": "legacy_unstructured"})
    return AbstractEvidenceCard(evidence_id=eid, title="(来自工具检索结果)", provenance=prov,
                               pmid=pmid, doi=doi, publication_status="unknown",
                               supporting_excerpt="", evidence_grade="初筛",
                               extraction_confidence=0.2, human_review_status="pending")


def abstract_card_from_paper(paper, *, tool_name="search_evidence", query=""):
    """从 search_evidence 的【结构化 paper】构建摘要卡。supporting_excerpt 取自真实摘要，不LLM改写。"""
    pmid, doi = paper.get("pmid"), paper.get("doi")
    level = paper.get("content_level", "abstract")             # abstract 或 metadata_only
    excerpt = (paper.get("supporting_excerpt") or "")[:400] if level == "abstract" else ""
    status = "preprint" if paper.get("preprint") else "published"
    prov = Provenance(tool_name=tool_name, source=paper.get("link") or "", content_level=level,
                      retrieved_at=paper.get("retrieved_at"), source_ids=[str(pmid or doi or "")],
                      parameters={"query": query, "source_database": paper.get("source_database", "Europe PMC")})
    return AbstractEvidenceCard(
        evidence_id=str(pmid or doi or paper.get("title", "")[:40]),
        title=paper.get("title", NOT_REPORTED), provenance=prov,
        pmid=str(pmid) if pmid else None, doi=str(doi) if doi else None,
        publication_status=status, supporting_excerpt=excerpt,
        main_claims=[], evidence_grade="初筛", extraction_confidence=0.4,
        human_review_status="pending")


def evidence_card_from_literature_record(rec, *, identifier_only_grade="候选"):
    """确定性地把【已校验的】LiteratureRecord 构造成 AbstractEvidenceCard（A.7.4.3）。

    复用现有 schemas 证据卡类型，不新建第二套卡：
    - content_level=abstract → 摘要级卡，supporting_excerpt 取自真实 abstract（不改写）；
    - content_level=metadata_only → 明确标记的 identifier-only 低等级候选卡
      （evidence_grade=候选、extraction_confidence 低、无 excerpt、provenance 标 identifier_only）；
    - content_level=fulltext → 本阶段不产 FullTextEvidenceCard，保守降级为摘要级卡（记录原始层级）；
    - study_design/species 未报告 → 保留 NOT_REPORTED，不从标题猜测；
    - 保留原始 provenance / source_ids / content_hash；evidence_id 稳定可复现（含内容 hash 前缀 → 版本可区分）。
    """
    from pilot.open_task_contracts import LiteratureRecord  # 延迟导入，避免根层与 pilot 循环耦合
    if not isinstance(rec, LiteratureRecord):
        raise TypeError("evidence_card_from_literature_record 只接受已校验的 LiteratureRecord")
    primary = rec.pmid or rec.doi
    evidence_id = f"{primary}::{rec.content_hash[:12]}"     # 同内容→同 id（幂等）；内容变→新版本卡
    is_abstract_level = rec.content_level in ("abstract", "fulltext")   # fulltext 降级为摘要级卡
    excerpt = (rec.abstract or "")[:400] if (is_abstract_level and rec.abstract) else ""
    grade = "初筛" if is_abstract_level else identifier_only_grade
    confidence = 0.4 if is_abstract_level else 0.1
    quality = "structured_abstract" if is_abstract_level else "identifier_only_metadata"
    # 复用记录自带 provenance（已含 content_hash/source_ids/content_level）；只补充质量标注，不改内容
    prov = rec.provenance.model_copy(update={
        "parameters": {**dict(rec.provenance.parameters),
                       "provenance_quality": quality,
                       "literature_content_level": rec.content_level}})
    return AbstractEvidenceCard(
        evidence_id=evidence_id, title=rec.title or NOT_REPORTED, provenance=prov,
        pmid=rec.pmid, doi=rec.doi, publication_status="unknown",
        study_type=rec.study_design or NOT_REPORTED,
        species=rec.species or NOT_REPORTED,
        supporting_excerpt=excerpt, main_claims=[],
        evidence_direction="inconclusive",
        extraction_confidence=confidence, evidence_grade=grade,
        human_review_status="pending")


def analysis_card(evidence_id, title, dataset, method, *, result="", statistic=None,
                  sample_size=None, code_commit=None, dataset_version=None,
                  limitations=None, direction="inconclusive", excerpt="") -> AnalysisEvidenceCard:
    """Reumani 自己跑出来的分析结果 → 证据卡，provenance 记录数据集/方法/代码版本。"""
    prov = Provenance(tool_name="reumani-analysis", source=dataset, parameters={"method": method},
                      code_commit=code_commit, dataset_version=dataset_version or dataset)
    return AnalysisEvidenceCard(
        evidence_id=str(evidence_id), title=title, provenance=prov,
        dataset=dataset, method=method,
        outcome=result or None, sample_size=sample_size,
        supporting_excerpt=excerpt or (statistic or ""),
        effect_size=statistic, evidence_direction=direction,
        limitations=limitations or [], species="human",
        evidence_grade="analysis", human_review_status="pending",
    )
