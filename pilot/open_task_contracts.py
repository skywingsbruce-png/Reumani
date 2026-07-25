"""开放式科研任务有界收敛执行契约的**纯数据契约**（Addendum 3 实现 A.7.4.1）。

只定义 Pydantic v2 严格契约 + 校验规则。**本模块不接入任何真实运行链**
（不被 ssc_a1 / Executor / Router / Middleware / Shadow / search_literature 导入或执行），
不实现状态转换/累计逻辑，不调用 LLM / 网络 / 数据库 / 账本。

复用现有唯一权威，**不复制**：
- `schemas._Strict`（extra="forbid" 基类）、`schemas.Provenance`、三种 `EvidenceCard` 子类；
- `ids.valid_pmid/valid_doi`（ID 判定权威）；
- `ids.normalize_pmid/normalize_doi`（ID 规范化权威已下沉到最低层 ids.py）。

无循环依赖：schemas / ids 不导入本模块（见报告 §7）。
"""

from __future__ import annotations

import re
from typing import Annotated, Literal, Optional, Union

from pydantic import Field, field_validator, model_validator

import ids as _ids
from ids import normalize_doi, normalize_pmid          # 规范化权威在最低层 ids.py
from schemas import (AbstractEvidenceCard, AnalysisEvidenceCard, ContentLevel,
                     FullTextEvidenceCard, Provenance, _Strict)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_GSE_KEY = re.compile(r"^GSE\d{3,7}$")

# 复用现有 EvidenceCard 子类体系（不新建第二套）；以 tier 作可判别联合。
EvidenceCardUnion = Annotated[
    Union[AbstractEvidenceCard, FullTextEvidenceCard, AnalysisEvidenceCard],
    Field(discriminator="tier"),
]

# content-level 边界：LiteratureRecord 对外遵守 Addendum 3 的 "fulltext"；
# 转成 Provenance/EvidenceCard（schemas.ContentLevel 用 "full_text"）时集中映射，禁止散落字符串判断。
_LIT_TO_PROV_CONTENT_LEVEL: dict[str, ContentLevel] = {
    "metadata_only": "metadata_only",
    "abstract": "abstract",
    "fulltext": "full_text",       # 唯一映射点：fulltext → schemas 的 full_text
    "unknown": "metadata_only",    # 保守：未知不得升级
}


def literature_content_level_to_provenance(level: str) -> ContentLevel:
    """LiteratureRecord content_level → schemas.Provenance/EvidenceCard 的 ContentLevel。
    集中定义 + 测试；运行时不得散落 'fulltext'/'full_text' 字符串判断。"""
    if level not in _LIT_TO_PROV_CONTENT_LEVEL:
        raise ValueError(f"未知 literature content_level：{level!r}")
    return _LIT_TO_PROV_CONTENT_LEVEL[level]

# ---------------- 枚举 ----------------
StepStatus = Literal["pending", "running", "satisfied", "insufficient", "failed", "blocked"]
TERMINAL_STEP_STATUSES = frozenset({"satisfied", "insufficient", "failed", "blocked"})
LiteratureContentLevel = Literal["metadata_only", "abstract", "fulltext", "unknown"]
ObservationStatus = Literal["ok", "zero_hits", "tool_error", "source_error", "parse_error"]
CausalStrength = Literal["insufficient", "association", "temporal_association",
                         "intervention_supported", "causal", "not_supported"]
OpenTaskStatus = Literal["running", "finished", "failed"]
LITREC_SCHEMA = "litrec-v1"


# ============================ 3. LiteratureRecord ============================
class LiteratureRecord(_Strict):
    schema_version: Literal["litrec-v1"] = LITREC_SCHEMA
    pmid: Optional[str] = None
    doi: Optional[str] = None
    title: Optional[str] = None
    year: Optional[str] = None
    journal: Optional[str] = None
    abstract: Optional[str] = None
    content_level: LiteratureContentLevel
    study_design: Optional[str] = None          # unknown → None
    species: Optional[str] = None
    longitudinal: Optional[bool] = None          # None = unknown（不猜）
    interventional: Optional[bool] = None
    source: str
    query: Optional[str] = None
    provenance: Provenance                       # 复用 schemas.Provenance，非空
    source_ids: list[str] = Field(default_factory=list)
    content_hash: str
    hash_algorithm: Literal["sha256"] = "sha256"
    # 全文可验证来源定位（Addendum 3 "至少包括" 之外的可选补充；标 fulltext 时必备其一）
    fulltext_ref: Optional[str] = None
    fulltext_content_hash: Optional[str] = None

    @field_validator("source")
    @classmethod
    def _source_nonempty(cls, v):
        if not v or not str(v).strip():
            raise ValueError("source 不能为空")
        return v

    @field_validator("content_hash")
    @classmethod
    def _hash64(cls, v):
        if not _HEX64.match(str(v or "")):
            raise ValueError("content_hash 必须为 64 位小写十六进制")
        return v

    @field_validator("source_ids")
    @classmethod
    def _dedup_sorted(cls, v):
        return sorted(set(v or []))              # 去重且稳定排序

    @model_validator(mode="after")
    def _check(self):
        # PMID/DOI 至少一个存在，且经 ids.py 权威规范化
        pmid = normalize_pmid(self.pmid) if self.pmid else None
        doi = normalize_doi(self.doi) if self.doi else None
        if self.pmid and not pmid:
            raise ValueError(f"PMID 非法：{self.pmid!r}")
        if self.doi and not doi:
            raise ValueError(f"DOI 非法：{self.doi!r}")
        if not (pmid or doi):
            raise ValueError("PMID/DOI 至少一个必须存在且合法")
        object.__setattr__(self, "pmid", pmid)
        object.__setattr__(self, "doi", doi)
        has_abstract = bool(self.abstract and self.abstract.strip())
        # 没有 abstract → 只能 metadata_only 或 unknown
        if not has_abstract and self.content_level in ("abstract", "fulltext"):
            raise ValueError("缺少 abstract 时 content_level 只能为 metadata_only 或 unknown")
        # 有 abstract → 最多 abstract；标 fulltext 必须有可验证全文来源定位
        if self.content_level == "fulltext":
            if self.fulltext_content_hash is not None and not _HEX64.match(self.fulltext_content_hash):
                raise ValueError("fulltext_content_hash 必须为 64 位小写十六进制或 None")
            if not (self.fulltext_ref or self.fulltext_content_hash):
                raise ValueError("标记 fulltext 必须提供可验证全文来源（fulltext_ref 或 fulltext_content_hash）；"
                                 "仅有 abstract 不得冒充 fulltext")
        return self


# ============================ 4. ObservationRecord ============================
class ObservationRecord(_Strict):
    observation_id: str
    step_id: int
    tool_name: str
    tool_call_id_hash: str
    status: ObservationStatus
    structured: bool                             # 是否结构化 artifact（True）/ legacy（False）
    schema_version: Optional[str] = None
    evidence_ids: list[str] = Field(default_factory=list)
    result_hash: Optional[str] = None
    error_type: Optional[str] = None
    provenance: Provenance
    # 禁止保存完整 Prompt / 认证头 / 敏感工具参数（契约里根本没有这些字段 → 结构上排除）

    @field_validator("evidence_ids")
    @classmethod
    def _dedup(cls, v):
        return sorted(set(v or []))

    @model_validator(mode="after")
    def _check(self):
        if self.result_hash is not None and not _HEX64.match(self.result_hash):
            raise ValueError("result_hash 必须为 64 位小写十六进制或 None")
        if self.status in ("tool_error", "source_error", "parse_error") and not self.error_type:
            raise ValueError(f"status={self.status} 必须给 error_type")
        return self


# ============================ 5. NoveltyAssessment ============================
class NoveltyAssessment(_Strict):
    transport_novelty: bool = False
    identifier_novelty: bool = False
    evidence_novelty: bool = False
    decision_novelty: bool = False
    new_identifiers: list[str] = Field(default_factory=list)
    new_evidence_axes: list[str] = Field(default_factory=list)
    decision_changes: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)

    @property
    def scientific_progress(self) -> bool:
        # 只读派生属性（非字段）：调用方无法传入矛盾值（extra="forbid" 使其不能作为字段传入），
        # 也不参与序列化 → JSON round-trip 干净；下游需要时按 property 读取。
        return bool(self.identifier_novelty or self.evidence_novelty or self.decision_novelty)


# ============================ 6. PlanStepState ============================
class PlanStepState(_Strict):
    step_id: int
    objective: str
    allowed_tools: list[str] = Field(default_factory=list)
    call_budget: int
    attempts: int = 0
    status: StepStatus = "pending"
    observations: list[str] = Field(default_factory=list)   # observation_id 引用
    evidence_ids: list[str] = Field(default_factory=list)
    success_criteria: Optional[str] = None
    completion_reason: Optional[str] = None
    remaining_gaps: list[str] = Field(default_factory=list)

    @field_validator("allowed_tools", "evidence_ids")
    @classmethod
    def _dedup(cls, v):
        # 去重但保序（工具/证据顺序可能有意义）
        seen, out = set(), []
        for x in v or []:
            if x not in seen:
                seen.add(x); out.append(x)
        return out

    @model_validator(mode="after")
    def _check(self):
        if self.call_budget <= 0:
            raise ValueError("call_budget 必须 > 0")
        if self.attempts < 0:
            raise ValueError("attempts 必须 ≥ 0")
        if self.attempts > self.call_budget:
            raise ValueError("attempts 不得超过 call_budget")
        if self.status == "pending" and self.attempts != 0:
            raise ValueError("pending 时 attempts 必须为 0")
        if self.status == "running" and self.completion_reason:
            raise ValueError("running 不得已有 completion_reason")
        if self.status in ("satisfied", "insufficient", "failed", "blocked") \
                and not self.completion_reason:
            raise ValueError(f"{self.status} 必须有 completion_reason")
        if self.status == "insufficient" and not self.remaining_gaps:
            raise ValueError("insufficient 必须有非空 remaining_gaps")
        return self

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STEP_STATUSES


# ============================ 7. EvidenceAccumulatorState ============================
class EvidenceAccumulatorState(_Strict):
    # 强类型：复用现有 EvidenceCard 子类（可判别联合，非任意 dict）
    evidence_cards: list[EvidenceCardUnion] = Field(default_factory=list)
    # identifier_index：normalized PMID/DOI/GSE → evidence_id（稳定，不随卡重排失效）
    identifier_index: dict[str, str] = Field(default_factory=dict)
    evidence_axes: list[str] = Field(default_factory=list)
    claim_support_levels: dict[str, str] = Field(default_factory=dict)
    novelty_history: list[NoveltyAssessment] = Field(default_factory=list)
    scientific_no_progress_rounds: int = 0

    @property
    def evidence_ids(self) -> list[str]:
        """由 cards 派生的稳定证据 ID 列表（非第二套漂移列表）。"""
        return [c.evidence_id for c in self.evidence_cards]

    @model_validator(mode="after")
    def _check(self):
        eids = [c.evidence_id for c in self.evidence_cards]
        if len(eids) != len(set(eids)):
            raise ValueError("EvidenceCard evidence_id 不得重复")
        idset = set(eids)
        for key, eid in self.identifier_index.items():
            if eid not in idset:
                raise ValueError(f"identifier_index[{key!r}]={eid!r} 悬空（无对应 evidence_id）")
            # key 必须是**已规范化**的 PMID / DOI / GSE（等于其自身规范化形式）
            is_pmid = normalize_pmid(key) == key
            is_doi = normalize_doi(key) == key
            is_gse = bool(_GSE_KEY.match(key))
            if not (is_pmid or is_doi or is_gse):
                raise ValueError(f"identifier_index key 未规范化/非法 ID：{key!r}")
        if self.scientific_no_progress_rounds < 0:
            raise ValueError("scientific_no_progress_rounds 必须 ≥ 0")
        # transport-only novelty 的 scientific_progress 必为 False（派生保证）→ 不会自动重置
        return self


# ============================ 8. CausalEvidenceAxes ============================
class CausalEvidenceAxes(_Strict):
    # 九个独立维度；None = unknown。**不得**用单一总分替代（extra="forbid" 排除 total_score 等）
    association: Optional[bool] = None
    temporal_evidence: Optional[bool] = None
    dose_response: Optional[bool] = None
    intervention_evidence: Optional[bool] = None
    genetic_instrumental: Optional[bool] = None
    mechanistic_plausibility: Optional[bool] = None
    reverse_causation_addressed: Optional[bool] = None
    confounding_addressed: Optional[bool] = None
    clinical_evidence: Optional[bool] = None


# ============================ 9. ControlledInsufficientConclusion ============================
class ControlledInsufficientConclusion(_Strict):
    resolved_question: str
    available_evidence: list[dict] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    causal_strength: CausalStrength
    missing_evidence: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    recommended_next_action: Optional[str] = None

    @model_validator(mode="after")
    def _check(self):
        if not self.resolved_question or not self.resolved_question.strip():
            raise ValueError("resolved_question 非空")
        # 证据为空时 missing_evidence 必须非空（禁止空白/“还缺：[]”等价状态）
        if not self.available_evidence and not self.missing_evidence:
            raise ValueError("无可用证据时 missing_evidence 必须非空（禁止“还缺：[]”）")
        return self


# ============================ 10. OpenTaskRunState ============================
class OpenTaskRunState(_Strict):
    run_id: str
    question: str
    route: Literal["open"]
    steps: list[PlanStepState] = Field(default_factory=list)
    current_step_id: Optional[int] = None
    observations: list[ObservationRecord] = Field(default_factory=list)
    accumulator: EvidenceAccumulatorState = Field(default_factory=EvidenceAccumulatorState)
    causal_axes: CausalEvidenceAxes = Field(default_factory=CausalEvidenceAxes)
    conclusion: Optional[ControlledInsufficientConclusion] = None
    status: OpenTaskStatus = "running"
    primary_failure: Optional[str] = None
    human_review: bool = False

    @model_validator(mode="after")
    def _check(self):
        step_ids = [s.step_id for s in self.steps]
        step_id_set = set(step_ids)
        if len(step_ids) != len(step_id_set):
            raise ValueError("step_id 必须唯一")
        if self.current_step_id is not None and self.current_step_id not in step_id_set:
            raise ValueError("current_step_id 必须引用已有步骤")
        all_terminal = bool(self.steps) and all(s.is_terminal() for s in self.steps)
        # conclusion 只能在存在步骤且全部终态后出现；空 steps 不得有 conclusion
        if self.conclusion is not None and not all_terminal:
            raise ValueError("步骤为空或未全部终态时不得存在 conclusion")
        # 所有步骤终态后 current_step_id 应为 None
        if all_terminal and self.current_step_id is not None:
            raise ValueError("所有步骤终态后 current_step_id 应为 None")
        # status 与 conclusion / primary_failure 的一致性
        if self.status == "finished" and self.conclusion is None:
            raise ValueError("finished 必须有 conclusion")
        if self.status == "running" and self.primary_failure:
            raise ValueError("running 不得有 primary_failure")
        if self.status == "failed" and not self.primary_failure:
            raise ValueError("failed 时 primary_failure 必须存在")
        # observation 引用完整性
        obs_ids = [o.observation_id for o in self.observations]
        if len(obs_ids) != len(set(obs_ids)):
            raise ValueError("observation_id 不得重复")
        obs_id_set = set(obs_ids)
        for o in self.observations:
            if o.step_id not in step_id_set:
                raise ValueError(f"observation.step_id={o.step_id} 引用不存在的 step")
        for s in self.steps:
            for oid in s.observations:
                if oid not in obs_id_set:
                    raise ValueError(f"PlanStep {s.step_id} 的 observation 引用悬空：{oid!r}")
        return self


__all__ = [
    "StepStatus", "LiteratureContentLevel", "ObservationStatus", "CausalStrength",
    "OpenTaskStatus", "LITREC_SCHEMA", "TERMINAL_STEP_STATUSES",
    "LiteratureRecord", "ObservationRecord", "NoveltyAssessment", "PlanStepState",
    "EvidenceAccumulatorState", "CausalEvidenceAxes", "ControlledInsufficientConclusion",
    "OpenTaskRunState",
]
