"""确定性 EvidenceAccumulator（A.7.4.3）。

纯程序化的证据累加：标准化 → EvidenceCard 构建 → PMID/DOI 去重 → 版本识别 →
四级 novelty → evidence axes 更新 → scientific progress 判定 → no-progress 更新。
**不调用 LLM / 网络 / 账本**，全部由确定性规则完成。

本阶段边界（A.7.4.3）：
- 只实现累加/去重/novelty；**不**实现 Step Controller，**不**接 UI，**不**修改 no_progress/loop_guard，
  **不**接线 ssc_a1 / Executor / Router / Shadow / search_literature（这些模块不导入本模块）。
- `accumulate(state, observation)` 是**纯函数**：不原地修改输入 state；同一输入确定且幂等。

复用现有唯一权威，不复制：
- `pilot.open_task_contracts` 的冻结数据契约（LiteratureRecord / ObservationRecord /
  NoveltyAssessment / EvidenceAccumulatorState）；
- `schemas` 的三种 EvidenceCard 子类（不新建第二套卡）+ `ToolResult` + `Provenance`；
- `evidence_build.evidence_card_from_literature_record`（唯一的 record→card 构造）；
- `ids` 的 PMID/DOI 规范化；`tool_envelope.compute_hash`（唯一 SHA-256）。
"""

from __future__ import annotations

from typing import Optional

from pydantic import Field, ValidationError

import ids as _ids
from tool_envelope import compute_hash
from schemas import (EvidenceCard, ToolResult, NOT_REPORTED, _Strict)
from evidence_build import evidence_card_from_literature_record
from pilot.open_task_contracts import (
    LiteratureRecord, ObservationRecord, NoveltyAssessment,
    EvidenceAccumulatorState, ObservationStatus,
)

# 九个因果证据轴（与 CausalEvidenceAxes 对齐）。litrec-v1 只对其中三个有结构化字段可确认；
# 其余无结构化来源 → 本阶段永远保持 unknown（不得 LLM 推断）。
CANONICAL_AXES = [
    "association", "temporal_evidence", "dose_response", "intervention_evidence",
    "genetic_instrumental", "mechanistic_plausibility", "reverse_causation_addressed",
    "confounding_addressed", "clinical_evidence",
]
_CONFIRMABLE_AXES = frozenset({"association", "temporal_evidence", "intervention_evidence"})

# 因果层级（决策 novelty 依据），由已确认的 axes 确定性派生。
_CAUSAL_TIERS = ["none", "association", "temporal_association", "intervention_supported"]
_TIER_RANK = {t: i for i, t in enumerate(_CAUSAL_TIERS)}

# content-level 排序（litrec 的 fulltext 与 schemas 的 full_text 都算全文级）。
_LEVEL_RANK = {
    "metadata_only": 0, "unknown": 0,
    "abstract": 1, "local_dataset": 1,
    "fulltext": 2, "full_text": 2, "computational_analysis": 2,
}


class AccumulatorInputError(ValueError):
    """输入不是可接受的结构化证据（自然语言字符串 / 未校验 dict / 缺 provenance / 非法 ID / schema 不兼容）。"""


class AccumulationResult(_Strict):
    """`accumulate` 的返回。state 是**新**的 EvidenceAccumulatorState（输入不被原地修改）。"""
    state: EvidenceAccumulatorState
    novelty: NoveltyAssessment
    observation_status: ObservationStatus
    added_evidence_ids: list[str] = Field(default_factory=list)
    duplicate_ids: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    scientific_progress: bool = False
    scientific_no_progress_rounds: int = 0


# --------------------------- 输入标准化（fail-closed） ---------------------------
class _Obs:
    __slots__ = ("status", "records", "cards")

    def __init__(self, status: str, records: list, cards: list):
        self.status = status
        self.records = records
        self.cards = cards


def _error_status(error_type: Optional[str]) -> ObservationStatus:
    if error_type in ("source_error", "parse_error", "tool_error"):
        return error_type  # type: ignore[return-value]
    return "tool_error"


def _coerce_data(data) -> _Obs:
    if not isinstance(data, dict):
        raise AccumulatorInputError("ToolResult.data 必须是结构化 dict（含 records / retrieval_status）")
    status = data.get("retrieval_status", "success")
    if status in ("zero_hits", "zero_candidates"):
        return _Obs("zero_hits", [], [])
    if status in ("source_error", "parse_error", "tool_error"):
        return _Obs(_error_status(status), [], [])
    records = []
    for r in data.get("records", []) or []:
        records.append(_as_record(r))
    return _Obs("ok", records, [])


def _as_record(r) -> LiteratureRecord:
    if isinstance(r, LiteratureRecord):
        return r
    if isinstance(r, dict):
        try:
            return LiteratureRecord.model_validate(r)
        except ValidationError as e:
            raise AccumulatorInputError(f"records 内含未通过 LiteratureRecord 校验的 dict：{e.error_count()} 处") from e
    raise AccumulatorInputError(f"records 元素类型非法：{type(r).__name__}")


def _coerce(observation) -> _Obs:
    if isinstance(observation, ObservationRecord):
        return _Obs(observation.status, [], [])
    if isinstance(observation, ToolResult):
        if not observation.ok:
            return _Obs(_error_status(observation.error_type), [], [])
        return _coerce_data(observation.data)
    if isinstance(observation, EvidenceCard):
        return _Obs("ok", [], [observation])
    if isinstance(observation, LiteratureRecord):
        return _Obs("ok", [observation], [])
    if isinstance(observation, (list, tuple)):
        recs: list = []
        cards: list = []
        for x in observation:
            sub = _coerce(x)
            if sub.status != "ok":
                raise AccumulatorInputError("列表中不得混入错误/zero_hits 观察；请单独提交")
            recs += sub.records
            cards += sub.cards
        return _Obs("ok", recs, cards)
    if isinstance(observation, dict):
        return _coerce_dict(observation)
    if isinstance(observation, str):
        raise AccumulatorInputError("拒绝自然语言文献字符串；只接受结构化 LiteratureRecord / ToolResult")
    raise AccumulatorInputError(f"不支持的输入类型：{type(observation).__name__}")


def _coerce_dict(d: dict) -> _Obs:
    if "ok" in d and "provenance" in d:               # ToolResult 形状
        try:
            return _coerce(ToolResult.model_validate(d))
        except ValidationError as e:
            raise AccumulatorInputError(f"ToolResult dict 校验失败：{e.error_count()} 处") from e
    if "records" in d or "retrieval_status" in d:      # ToolResult.data 载荷
        return _coerce_data(d)
    try:                                               # 否则必须自身是合法 LiteratureRecord
        return _Obs("ok", [LiteratureRecord.model_validate(d)], [])
    except ValidationError as e:
        raise AccumulatorInputError(
            "未经 LiteratureRecord 校验的 dict 被拒绝（缺 provenance / 非法 ID / schema 不兼容）："
            f"{e.error_count()} 处") from e


# --------------------------- 候选（record / card 统一视图） ---------------------------
def _candidate_from_record(rec: LiteratureRecord) -> dict:
    card = evidence_card_from_literature_record(rec)
    return {"card": card, "evidence_id": card.evidence_id, "pmid": rec.pmid, "doi": rec.doi,
            "content_hash": rec.content_hash, "content_level": rec.content_level,
            "study_design": rec.study_design, "species": rec.species,
            "longitudinal": rec.longitudinal, "interventional": rec.interventional,
            "has_abstract": bool(rec.abstract and rec.abstract.strip())}


def _card_content_hash(card: EvidenceCard) -> str:
    if card.provenance.content_hash:
        return card.provenance.content_hash
    # 无 provenance hash 的已验证卡：由内容确定性派生（不含易变字段）
    return compute_hash({"pmid": card.pmid, "doi": card.doi, "title": card.title,
                         "study_type": card.study_type, "species": card.species,
                         "excerpt": card.supporting_excerpt,
                         "content_level": card.provenance.content_level})


def _candidate_from_card(card: EvidenceCard) -> dict:
    pmid = _ids.normalize_pmid(card.pmid) if card.pmid else None
    doi = _ids.normalize_doi(card.doi) if card.doi else None
    if not (pmid or doi):
        raise AccumulatorInputError("已验证 EvidenceCard 缺少合法 PMID/DOI，无法累加")
    study = None if card.study_type in (NOT_REPORTED, "", None) else card.study_type
    species = None if card.species in (NOT_REPORTED, "", None) else card.species
    return {"card": card, "evidence_id": card.evidence_id, "pmid": pmid, "doi": doi,
            "content_hash": _card_content_hash(card), "content_level": card.provenance.content_level,
            "study_design": study, "species": species,
            "longitudinal": None, "interventional": None,
            "has_abstract": bool(card.supporting_excerpt.strip())}


# --------------------------- 确定性规则 ---------------------------
def _norm(s) -> str:
    return " ".join(str(s or "").lower().split())


def _level_rank(level) -> int:
    return _LEVEL_RANK.get(level, 0)


def _axes_for(cand: dict) -> set:
    axes = set()
    if cand["study_design"]:
        axes.add("association")
    if cand["longitudinal"] is True:
        axes.add("temporal_evidence")
    if cand["interventional"] is True:
        axes.add("intervention_evidence")
        axes.add("association")            # 干预研究天然含关联观测
    return axes & _CONFIRMABLE_AXES


def _tier_from_axes(axes: set) -> str:
    if "intervention_evidence" in axes:
        return "intervention_supported"
    if "temporal_evidence" in axes:
        return "temporal_association"
    if "association" in axes:
        return "association"
    return "none"


def _conflicts(existing: EvidenceCard, cand: dict) -> list:
    """同一 identifier、内容互相冲突的字段（用于 rule 4：conflict/human_review，不自动选有利版本）。"""
    out = []
    old_sd = None if existing.study_type in (NOT_REPORTED, "", None) else existing.study_type
    if old_sd and cand["study_design"] and _norm(old_sd) != _norm(cand["study_design"]):
        out.append("study_design")
    old_sp = None if existing.species in (NOT_REPORTED, "", None) else existing.species
    if old_sp and cand["species"] and _norm(old_sp) != _norm(cand["species"]):
        out.append("species")
    return out


def _is_richer(existing: EvidenceCard, cand: dict) -> bool:
    """rule 3：同 identifier 且不冲突时，判断新记录是否更丰富（→ evidence novelty，新版本卡，不覆盖旧卡）。"""
    if _level_rank(cand["content_level"]) > _level_rank(existing.provenance.content_level):
        return True
    if cand["study_design"] and existing.study_type in (NOT_REPORTED, "", None):
        return True
    if cand["species"] and existing.species in (NOT_REPORTED, "", None):
        return True
    if cand["has_abstract"] and not existing.supporting_excerpt.strip():
        return True
    return False


# --------------------------- 主纯函数 ---------------------------
def accumulate(state: EvidenceAccumulatorState, observation) -> AccumulationResult:
    """确定性累加一个观察，返回**新** state + NoveltyAssessment（输入 state 不被原地修改）。"""
    obs = _coerce(observation)

    # 复制（不原地修改输入）：卡本身不可变，复制引用即可
    cards = list(state.evidence_cards)
    index = dict(state.identifier_index)
    axes_set = set(state.evidence_axes)
    prev_tier = _tier_from_axes(axes_set)
    card_by_id = {c.evidence_id: c for c in cards}

    added: list = []
    duplicates: list = []
    conflicts: list = []
    warnings: list = []
    reasons: list = []
    new_identifiers: list = []
    new_axes: list = []
    upgraded = False
    processed_any = False

    if obs.status != "ok":
        warnings.append(f"观察 status={obs.status}：记录为 Observation，不构建 EvidenceCard")
        novelty = NoveltyAssessment(reasons=[f"non-evidence observation: {obs.status}"])
        return _finish(state, cards, index, axes_set, novelty, obs.status,
                       added, duplicates, conflicts, warnings)

    candidates = [_candidate_from_record(r) for r in obs.records] + \
                 [_candidate_from_card(c) for c in obs.cards]

    for cand in candidates:
        processed_any = True
        id_keys = [k for k in (cand["pmid"], cand["doi"]) if k]
        existing_eids = {index[k] for k in id_keys if k in index}

        if len(existing_eids) > 1:
            # rule 4/5 边界：identifiers 指向多个既有实体 → 不自动合并
            conflicts.append(cand["evidence_id"])
            warnings.append(f"identifier 冲突：{id_keys} 指向多个证据实体 {sorted(existing_eids)}，"
                            "不自动合并（human_review）")
            continue

        if not existing_eids:
            # 新 identifier → 新卡
            eid = cand["evidence_id"]
            if eid in card_by_id:
                duplicates.append(eid)
                continue
            card_by_id[eid] = cand["card"]
            cards.append(cand["card"])
            added.append(eid)
            for k in id_keys:
                if k not in index:
                    index[k] = eid
                    new_identifiers.append(k)
            cand_axes = _axes_for(cand)
            for a in cand_axes:
                if a not in axes_set:
                    axes_set.add(a)
                    new_axes.append(a)
            reasons.append(f"new identifier card {eid}")
            continue

        # identifier 已知（同一实体）
        existing_eid = next(iter(existing_eids))
        existing_card = card_by_id[existing_eid]
        # rule 5：把新出现的同实体 identifier 别名并入同一 eid（合并，不算科研进展）
        for k in id_keys:
            if k not in index:
                index[k] = existing_eid
                warnings.append(f"合并 identifier 别名 {k} → {existing_eid}（同一论文 PMID/DOI）")

        if cand["content_hash"] == _card_content_hash(existing_card):
            duplicates.append(existing_eid)                      # rule 1：完全重复
            continue

        conflict_fields = _conflicts(existing_card, cand)
        if conflict_fields:
            conflicts.append(existing_eid)                        # rule 4：冲突 → 不覆盖，不选有利版本
            warnings.append(f"内容冲突 {existing_eid} 字段 {conflict_fields}（human_review，未覆盖原卡）")
            continue

        cand_axes = _axes_for(cand)
        brings_new_axis = bool(cand_axes - axes_set)              # 同实体带来新因果轴（如新增纵向/干预）
        if _is_richer(existing_card, cand) or brings_new_axis:
            # rule 3：更丰富 → 新版本卡，不静默覆盖旧卡；index 指向更丰富版本，记录升级理由
            new_eid = cand["evidence_id"]
            if new_eid == existing_eid or new_eid in card_by_id:
                duplicates.append(existing_eid)
                continue
            card_by_id[new_eid] = cand["card"]
            cards.append(cand["card"])
            added.append(new_eid)
            for k in id_keys:
                index[k] = new_eid                                 # 指向更丰富版本（旧卡仍保留在 cards）
            upgraded = True
            reasons.append(f"evidence upgraded {existing_eid} → {new_eid}（内容更丰富，旧卡保留）")
            for a in cand_axes:
                if a not in axes_set:
                    axes_set.add(a)
                    new_axes.append(a)
            continue

        # 同实体、hash 不同但既不冲突也不更丰富（排序/格式/空白差异）→ transport，不新增卡
        duplicates.append(existing_eid)

    identifier_novelty = bool(added and new_identifiers)
    evidence_novelty = bool(new_axes) or upgraded
    new_tier = _tier_from_axes(axes_set)
    decision_novelty = _TIER_RANK[new_tier] > _TIER_RANK[prev_tier]
    scientific = identifier_novelty or evidence_novelty or decision_novelty
    transport_novelty = (not scientific) and processed_any and bool(duplicates or conflicts)

    decision_changes = [f"{prev_tier}->{new_tier}"] if decision_novelty else []
    if transport_novelty:
        reasons.append("transport-only：仅重复/重排，无新科研信息")

    novelty = NoveltyAssessment(
        transport_novelty=transport_novelty,
        identifier_novelty=identifier_novelty,
        evidence_novelty=evidence_novelty,
        decision_novelty=decision_novelty,
        new_identifiers=sorted(set(new_identifiers)),
        new_evidence_axes=sorted(set(new_axes)),
        decision_changes=decision_changes,
        reasons=reasons,
    )
    return _finish(state, cards, index, axes_set, novelty, obs.status,
                   added, duplicates, conflicts, warnings)


def _finish(state, cards, index, axes_set, novelty, status,
            added, duplicates, conflicts, warnings) -> AccumulationResult:
    sci = novelty.scientific_progress
    # transport-only / error / zero_hits / 纯重复 → no-progress +1；有科研进展 → 归零
    no_progress = 0 if sci else state.scientific_no_progress_rounds + 1
    new_state = EvidenceAccumulatorState(
        evidence_cards=cards,
        identifier_index=index,
        evidence_axes=sorted(axes_set),
        claim_support_levels=dict(state.claim_support_levels),
        novelty_history=[*state.novelty_history, novelty],
        scientific_no_progress_rounds=no_progress,
    )
    return AccumulationResult(
        state=new_state, novelty=novelty, observation_status=status,
        added_evidence_ids=added, duplicate_ids=duplicates, conflicts=conflicts,
        warnings=warnings, scientific_progress=sci,
        scientific_no_progress_rounds=no_progress,
    )


def summarize(state: EvidenceAccumulatorState) -> dict:
    """只读遥测摘要（§8）：仅证据/去重/novelty/axes/no-progress；**不**统计模型调用/费用/工具生命周期。
    供未来 RunMetrics / UI 读取，本阶段不接入它们。"""
    axes_set = set(state.evidence_axes)
    return {
        "evidence_card_count": len(state.evidence_cards),
        "identifier_index_size": len(state.identifier_index),
        "evidence_axes": sorted(axes_set),
        "unconfirmed_axes": [a for a in CANONICAL_AXES if a not in axes_set],
        "causal_tier": _tier_from_axes(axes_set),
        "novelty_rounds": len(state.novelty_history),
        "scientific_no_progress_rounds": state.scientific_no_progress_rounds,
    }


__all__ = [
    "accumulate", "summarize", "AccumulationResult", "AccumulatorInputError",
    "CANONICAL_AXES",
]
