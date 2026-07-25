"""开放式科研任务有界收敛执行契约的**纯数据契约**（Addendum 3 实现 A.7.4.1）。

只定义 Pydantic v2 严格契约 + 校验规则。**本模块不接入任何真实运行链**
（不被 ssc_a1 / Executor / Router / Middleware / Shadow / search_literature 导入或执行），
不实现状态转换/累计逻辑，不调用 LLM / 网络 / 数据库 / 账本。

复用现有唯一权威，**不复制**：
- `schemas._Strict`（extra="forbid" 基类）、`schemas.Provenance`、`schemas.AbstractEvidenceCard`；
- `ids.valid_pmid/valid_doi`（ID 判定权威）；
- `pilot.exact_id_resolver.normalize_pmid/normalize_doi`（ID 规范化权威，内部即委托 ids.py）。

无循环依赖：schemas / ids 不导入本模块；exact_id_resolver 不导入本模块（见报告 §5）。
"""

from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import Field, field_validator, model_validator

import ids as _ids
from schemas import Provenance, _Strict
from pilot.exact_id_resolver import normalize_doi, normalize_pmid

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

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
        # 缺 abstract 不得自动标为 fulltext
        if self.content_level == "fulltext" and not (self.abstract and self.abstract.strip()):
            raise ValueError("缺少全文/摘要内容时不得标为 fulltext")
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
    evidence_cards: list[dict] = Field(default_factory=list)   # AbstractEvidenceCard.model_dump()
    evidence_ids: list[str] = Field(default_factory=list)
    identifier_index: dict[str, int] = Field(default_factory=dict)   # id → evidence_cards 下标
    evidence_axes: list[str] = Field(default_factory=list)
    claim_support_levels: dict[str, str] = Field(default_factory=dict)
    novelty_history: list[NoveltyAssessment] = Field(default_factory=list)
    scientific_no_progress_rounds: int = 0

    @model_validator(mode="after")
    def _check(self):
        eids = [c.get("evidence_id") for c in self.evidence_cards]
        if len(eids) != len(set(e for e in eids if e is not None)):
            raise ValueError("EvidenceCard evidence_id 不得重复")
        for key, idx in self.identifier_index.items():
            if not (0 <= idx < len(self.evidence_cards)):
                raise ValueError(f"identifier_index[{key!r}]={idx} 悬空（无对应 EvidenceCard）")
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
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step_id 必须唯一")
        if self.current_step_id is not None and self.current_step_id not in set(step_ids):
            raise ValueError("current_step_id 必须引用已有步骤")
        if self.conclusion is not None and self.steps \
                and not all(s.is_terminal() for s in self.steps):
            raise ValueError("所有步骤进入终态前不得存在 conclusion")
        if self.status == "failed" and not self.primary_failure:
            raise ValueError("failed 时 primary_failure 必须存在")
        return self


__all__ = [
    "StepStatus", "LiteratureContentLevel", "ObservationStatus", "CausalStrength",
    "OpenTaskStatus", "LITREC_SCHEMA", "TERMINAL_STEP_STATUSES",
    "LiteratureRecord", "ObservationRecord", "NoveltyAssessment", "PlanStepState",
    "EvidenceAccumulatorState", "CausalEvidenceAxes", "ControlledInsufficientConclusion",
    "OpenTaskRunState",
]
