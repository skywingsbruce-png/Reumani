"""
把不同来源的抽取结果构造成【校验过的】证据卡（保留 provenance）。只依赖 schemas，可单测。
- abstract_card_from_extraction：摘要级 LLM 抽取字典 + 文献元数据 → AbstractEvidenceCard（只初筛）。
- analysis_card：Reumani 自己的数据分析结果 → AnalysisEvidenceCard。
规则落地：摘要卡不设 supporting_excerpt（无逐字原文）→ 自动低可追溯、不能作关键结论；
         预印本/撤稿据来源标注；找不到的字段写 NOT_REPORTED，不猜测。
"""

from schemas import (AbstractEvidenceCard, AnalysisEvidenceCard, Provenance, NOT_REPORTED)


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
