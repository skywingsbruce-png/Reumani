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
    description: str
    domains: list[str] = Field(default_factory=list)
    input_types: list[str] = Field(default_factory=list)
    output_types: list[str] = Field(default_factory=list)
    task_types: list[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "low"
    implemented: bool = False
    callable_path: Optional[str] = None
    data_version: Optional[str] = None


# ---------- 计划 ----------
class PlanStep(_Strict):
    n: int
    goal: str
    tool: Optional[str] = None
    expected_output: str
    verification: str                             # 这一步如何验证


class ResearchPlan(_Strict):
    query: str
    steps: list[PlanStep]
    constraints: Optional[str] = None


# ---------- 证据 ----------
class EvidenceCard(_Strict):
    """文献证据卡片。"""
    claim: str
    study_type: Optional[str] = None
    sample_size: Optional[str] = None
    population: Optional[str] = None
    main_finding: str
    limitations: Optional[str] = None
    evidence_strength: Literal["strong", "moderate", "weak", "unknown"] = "unknown"
    source: str                                   # PMID / DOI / 本地PDF页码，必填


class AnalysisEvidenceCard(_Strict):
    """计算分析证据卡片（数据分析产出的证据）。"""
    dataset: str                                  # 如 GSE58095
    method: str                                   # 如 signature 相关性
    result: str
    statistic: Optional[str] = None               # 如 "r=0.35, p=0.005"
    n: Optional[int] = None
    provenance: Provenance
    limitations: Optional[str] = None


class Claim(_Strict):
    text: str
    supported: Literal["yes", "no", "insufficient"] = "insufficient"
    evidence_refs: list[str] = Field(default_factory=list)   # PMID/DOI/卡片id
    confidence: Literal["high", "medium", "low"] = "low"


# ---------- 核查 ----------
VerifyStatus = Literal[
    "passed", "not_passed", "verification_error",
    "verifier_unavailable", "verifier_timeout",
    "tool_execution_failed", "insufficient_evidence",
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
    "PlanStep", "ResearchPlan", "EvidenceCard", "AnalysisEvidenceCard", "Claim",
    "VerificationResult", "AgentState", "RunManifest", "VerifyStatus",
]
