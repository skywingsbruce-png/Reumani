"""
核心数据契约（P0-3）。所有科研结果用严格 schema 传递，便于验证与可复现。
用 Pydantic v2（langchain 已依赖，无冲突）。规则：
- extra="forbid"：拒绝未知字段，防止拼错/夹带。
- 关键字段显式建模，不用 Any 逃避（仅 payload/参数这类天然动态处用 object/dict）。
- ToolResult：失败必须带 error_type+error_message，且 as_text() 明确标注失败，
  禁止把失败伪装成看起来正常的字符串。

已接线使用：ToolResult / Provenance（ssc_sandbox）。
其余为【契约定义】，供各模块逐步迁移到此统一来源（见 AUDIT.md 阶段计划）。
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------- 来源与产物 ----------
class Provenance(_Strict):
    tool_name: str
    tool_version: Optional[str] = None
    source: Optional[str] = None                 # 数据来源(URL/库名/本地路径)
    retrieved_at: Optional[str] = None           # ISO 时间戳(调用方填)
    parameters: dict[str, object] = Field(default_factory=dict)
    code_commit: Optional[str] = None
    dataset_version: Optional[str] = None         # 语料/数据集快照 id


class Artifact(_Strict):
    path: str
    kind: Literal["figure", "table", "file", "model", "other"] = "file"
    description: Optional[str] = None


# ---------- 工具调用 ----------
class ToolRequest(_Strict):
    tool_name: str
    parameters: dict[str, object] = Field(default_factory=dict)
    request_id: Optional[str] = None
    reason: Optional[str] = None                  # 为什么调用它


class ToolResult(_Strict):
    ok: bool
    data: Optional[object] = None                 # 成功时的载荷(天然动态)
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    provenance: Provenance
    warnings: list[str] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistency(self):
        # 失败必须有错误信息；成功不得带错误字段——杜绝"失败伪装成功"
        if not self.ok:
            if not self.error_type or not self.error_message:
                raise ValueError("失败的 ToolResult 必须提供 error_type 和 error_message")
        else:
            if self.error_type or self.error_message:
                raise ValueError("成功的 ToolResult 不得带 error_type/error_message")
        return self

    def as_text(self) -> str:
        if self.ok:
            return str(self.data)
        return f"[工具失败:{self.error_type}] {self.error_message}"


# ---------- 资源 ----------
class ResourceSpec(_Strict):
    name: str
    kind: str                                     # tool / database / software / knowhow ...
    domains: list[str] = Field(default_factory=list)
    description: str = ""
    input_schema: dict[str, object] = Field(default_factory=dict)
    output_schema: dict[str, object] = Field(default_factory=dict)
    input_types: list[str] = Field(default_factory=list)
    output_types: list[str] = Field(default_factory=list)
    task_types: list[str] = Field(default_factory=list)
    risk_level: str = "low"                       # low / medium / high（宽松，兼容既有注册表）
    timeout_seconds: int = 30
    implemented: bool = True                       # False = 已注册但未实现（诚实占位）
    callable_path: str = ""
    data_version: str = ""
    data_updated_at: str = ""
    notes: str = ""

    def to_dict(self):                             # 兼容既有 registry.to_json() 的 s.to_dict()
        return self.model_dump()


# ---------- 计划 ----------
class PlanStep(_Strict):
    step_id: int
    objective: str
    tool_name: str                                # 必须是本任务 allowed_tools 里的真实工具
    arguments: dict[str, object] = Field(default_factory=dict)
    expected_output: str
    success_criteria: str                         # 必填：每步必须定义成功条件
    risk_level: Literal["low", "medium", "high"] = "low"
    requires_human_approval: bool = False
    on_failure: Literal["stop", "retry", "skip"] = "stop"


class ResearchPlan(_Strict):
    question: str
    constraints: str = ""
    selected_resources: list[str] = Field(default_factory=list)
    steps: list[PlanStep]
    stop_conditions: list[str] = Field(default_factory=list)
    maximum_retries: int = 2

    @model_validator(mode="after")
    def _non_empty(self):
        if not self.steps:
            raise ValueError("ResearchPlan 至少要有一个步骤")
        return self


# ---------- 证据（分层）----------
EvidenceTier = Literal["abstract", "fulltext", "analysis"]
PublicationStatus = Literal["published", "preprint", "retracted", "corrected", "unknown"]
EvidenceDirection = Literal["supports", "refutes", "mixed", "inconclusive", "correlational"]
# 只读摘要的结论强度上限：候选/初筛
ABSTRACT_MAX_GRADES = {"候选", "初筛", "candidate", "screening"}
NOT_REPORTED = "未报告"          # 找不到信息统一写这个，禁止猜测


class EvidenceCard(_Strict):
    """证据卡基类。所有卡必须保留 provenance。用三个子类区分证据层级（能支撑多强的结论）。"""
    evidence_id: str
    tier: EvidenceTier
    title: str
    provenance: Provenance                        # 原始来源，必填
    pmid: Optional[str] = None
    pmcid: Optional[str] = None
    doi: Optional[str] = None
    publication_version: Optional[str] = None
    publication_status: PublicationStatus = "unknown"
    study_type: str = NOT_REPORTED
    species: str = NOT_REPORTED
    tissue_or_cell: Optional[str] = None
    disease_subtype: Optional[str] = None
    sample_size: Optional[str] = None
    intervention_or_exposure: Optional[str] = None
    comparator: Optional[str] = None
    outcome: Optional[str] = None
    effect_size: Optional[str] = None
    confidence_interval: Optional[str] = None
    raw_p_value: Optional[str] = None
    adjusted_p_value: Optional[str] = None
    main_claims: list[str] = Field(default_factory=list)
    supporting_excerpt: str = ""                   # 支持性原文摘录；无 → 不能作关键结论证据
    source_section: Optional[str] = None
    source_page: Optional[str] = None
    source_figure_or_table: Optional[str] = None
    limitations: list[str] = Field(default_factory=list)
    evidence_direction: EvidenceDirection = "inconclusive"
    extraction_confidence: float = 0.0
    evidence_grade: str = "候选"
    human_review_status: str = "pending"

    @model_validator(mode="after")
    def _integrity_rules(self):
        # 更正论文必须记录更正版本
        if self.publication_status == "corrected" and not self.publication_version:
            raise ValueError("更正(corrected)论文必须记录 publication_version")
        return self

    # ---- 可追溯性 / 可用性判断（供检索与核查层调用）----
    def traceability(self) -> str:
        loc = self.source_section or self.source_page or self.source_figure_or_table
        return "high" if loc else "low"          # 没有来源定位 → 低可追溯

    def usable_for_key_conclusion(self):
        """能否作为关键结论证据。返回 (bool, 原因列表)。"""
        reasons = []
        if not self.supporting_excerpt.strip():
            reasons.append("无 supporting_excerpt，不能作为关键结论证据")
        if self.publication_status == "retracted":
            reasons.append("撤稿论文不得用于正向结论")
        if self.tier == "abstract":
            reasons.append("仅摘要级证据，只能作候选/初筛，不能声称全文证明")
        if self.traceability() == "low":
            reasons.append("低可追溯性（缺来源定位）")
        return (len(reasons) == 0, reasons)

    def clinical_caveats(self):
        """临床外推的硬性告诫（动物/体外/相关性/预印本）。"""
        c = []
        sp = (self.species or "").lower()
        tc = (self.tissue_or_cell or "").lower()
        if sp and sp not in ("human", "人", "患者", "homo sapiens", NOT_REPORTED.lower()):
            c.append("动物研究不能直接支持临床疗效")
        if any(k in tc for k in ("cell line", "细胞系", "in vitro", "体外", "organoid", "类器官", "primary cell")):
            c.append("体外研究不能直接支持患者治疗")
        if self.evidence_direction == "correlational" or (self.study_type or "").find("横断面") >= 0 \
                or "cross-sectional" in (self.study_type or "").lower():
            c.append("横断面/相关性不能自动升级为因果")
        if self.publication_status == "preprint":
            c.append("预印本，未经同行评审")
        return c


class AbstractEvidenceCard(EvidenceCard):
    """只据标题+摘要。结论强度上限：候选/初筛。"""
    tier: Literal["abstract"] = "abstract"

    @model_validator(mode="after")
    def _cap_grade(self):
        if self.evidence_grade not in ABSTRACT_MAX_GRADES:
            raise ValueError(f"AbstractEvidenceCard 的 evidence_grade 上限为 候选/初筛，收到 {self.evidence_grade!r}"
                             "（只读摘要不能声称全文证明）")
        return self


class FullTextEvidenceCard(EvidenceCard):
    """据全文/图表/补充材料，且有明确来源定位。"""
    tier: Literal["fulltext"] = "fulltext"

    @model_validator(mode="after")
    def _need_excerpt(self):
        if not self.supporting_excerpt.strip():
            raise ValueError("FullTextEvidenceCard 必须提供 supporting_excerpt")
        return self


class AnalysisEvidenceCard(EvidenceCard):
    """据 Reumani 自己运行的数据分析结果。provenance 必须能追溯到数据集与方法。"""
    tier: Literal["analysis"] = "analysis"
    dataset: str
    method: str

    @model_validator(mode="after")
    def _need_data_provenance(self):
        if not (self.provenance.dataset_version or self.provenance.source or self.dataset):
            raise ValueError("AnalysisEvidenceCard 必须能追溯到数据来源(dataset/provenance)")
        return self


class LiteratureQuality(_Strict):
    """论文质量标签（结构化，不是一个不透明总分）。据研究设计打标，【不看期刊影响因子】。
    全量保存策略：低等级论文也留在数据湖，只是按任务动态降权，不删除。"""
    study_type: str = NOT_REPORTED
    human_evidence: bool = False
    animal_evidence: bool = False
    in_vitro_evidence: bool = False
    sample_size: Optional[int] = None
    longitudinal: Optional[bool] = None
    multicenter: Optional[bool] = None
    randomized: Optional[bool] = None
    preregistered: Optional[bool] = None
    adjusted_analysis: Optional[bool] = None
    multiplicity_control: Optional[str] = None
    independent_replication: Optional[bool] = None
    full_text_available: bool = False
    preprint: bool = False
    retracted: bool = False
    corrected: bool = False
    extraction_confidence: float = 0.0


ClaimType = Literal["existence", "association", "causal", "mechanistic", "clinical_efficacy", "other"]
CausalStrength = Literal["none", "correlational", "associative", "mechanistic", "causal", "unknown"]
ClaimVerdict = Literal["supported", "partially_supported", "not_supported",
                       "contradicted", "insufficient_evidence", "technically_unverifiable"]


class Claim(_Strict):
    """原子主张。不同 claim_type 的证据要求不同，不能互相替代（相关≠因果，动物≠临床）。"""
    claim_id: str
    text: str
    claim_type: ClaimType = "other"
    causal_strength: CausalStrength = "unknown"          # 该 claim 所【主张】的因果强度
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    unresolved_evidence_ids: list[str] = Field(default_factory=list)
    verdict: ClaimVerdict = "insufficient_evidence"
    uncertainty: str = ""
    human_review_required: bool = False


# ---------- 核查 ----------
VerifyStatus = Literal[
    "passed", "not_passed", "verification_error",
    "verifier_unavailable", "verifier_timeout",
    "tool_execution_failed", "insufficient_evidence",
    # 四层 Verifier 专用
    "citation_error", "claim_contradicted", "claim_unsupported", "adversarial_counterevidence",
]


class VerificationResult(_Strict):
    passed: bool = False
    status: VerifyStatus = "verification_error"
    reason: str = ""
    missing: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _passed_iff_status(self):
        if self.passed and self.status != "passed":
            raise ValueError("passed=True 时 status 必须为 'passed'")
        if not self.passed and self.status == "passed":
            raise ValueError("status='passed' 时 passed 必须为 True")
        return self


# ---------- 状态与运行清单 ----------
class AgentState(_Strict):
    user_query: str
    constraints: str = ""
    selected_resources: str = ""
    plan: str = ""
    current_step: int = 0
    observations: list[str] = Field(default_factory=list)
    evidence_cards: list[EvidenceCard] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    verification_results: list[VerificationResult] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    final_answer: str = ""
    retry_count: int = 0
    max_iterations: int = 2


class RunManifest(_Strict):
    """一次运行的可复现清单。"""
    run_id: str
    created_at: str
    query: str
    code_commit: Optional[str] = None
    dataset_version: Optional[str] = None
    plan: Optional[ResearchPlan] = None
    tool_calls: list[ToolRequest] = Field(default_factory=list)
    verification: Optional[VerificationResult] = None
    artifacts: list[Artifact] = Field(default_factory=list)
    final_status: Literal["passed", "unverified", "failed"] = "unverified"


__all__ = [
    "Provenance", "Artifact", "ToolRequest", "ToolResult", "ResourceSpec",
    "PlanStep", "ResearchPlan", "EvidenceCard", "AbstractEvidenceCard",
    "FullTextEvidenceCard", "AnalysisEvidenceCard", "Claim",
    "VerificationResult", "AgentState", "RunManifest", "VerifyStatus",
    "EvidenceTier", "PublicationStatus", "EvidenceDirection", "NOT_REPORTED",
    "LiteratureQuality", "ClaimType", "CausalStrength", "ClaimVerdict",
]
