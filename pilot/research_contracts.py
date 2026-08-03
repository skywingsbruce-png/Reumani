"""A.7.5.3 —— 参数化 Research Run 桥接契约（HITL 控制层 ↔ 真实三角色科研链）。

纯数据/协议层：**不 import 任何模型客户端**（Anthropic / DeepSeek / SDK 一律不出现）。
HitlRun 只依赖本模块的 `ResearchExecutor` Protocol；具体执行器由服务端 registry 注入，
客户端只能提交**已注册的 executor ID**（未注册 → fail-closed）。

安全边界：
- 严格契约（extra=forbid），未知 schema / 未知字段 / 未注册 executor 一律 fail-closed；
- spec 中不得出现 API key、完整系统 Prompt、模型对象、worker generation、绝对路径；
- evidence_refs 只能引用**已存在**的结构化 EvidenceCard/artifact（按 evidence_id + content_hash）；
- Approval 冻结 question_hash / evidence_hash / policy_hash / executor_id，
  批准后任一项变化 → 拒绝执行（旧批准失效）。
"""

from __future__ import annotations

from typing import Literal, Optional, Protocol, runtime_checkable

from pydantic import Field, field_validator

from schemas import _Strict, Claim
from tool_envelope import compute_hash

RESEARCH_SPEC_SCHEMA = "research-run-v1"
RESEARCH_ARTIFACT_SCHEMA = "research-artifact-v1"

QUESTION_MAX = 2000            # 研究问题长度上限（超长 → fail-closed 拒绝）
LABEL_MAX = 300
MAX_EVIDENCE_REFS = 200
MAX_OPTIONS = 12


class ResearchContractError(ValueError):
    """未知 schema / 非法字段 / 冻结项被篡改 / 未注册 executor（fail-closed）。"""


class ExecutorNotRegistered(ResearchContractError):
    """客户端提交了未注册的 executor ID。"""


# ----------------------------- 证据引用 -----------------------------
class EvidenceReference(_Strict):
    """只引用**已有**结构化证据；本契约不承载证据正文，只承载 ID + 内容 hash + 来源标记。"""
    evidence_id: str
    content_hash: str
    hash_algorithm: Literal["sha256"] = "sha256"
    fixture: bool = False              # True = 测试夹具，禁止伪装成真实科研证据
    fixture_source: Optional[str] = None

    @field_validator("evidence_id", "content_hash")
    @classmethod
    def _nonempty(cls, v):
        if not str(v).strip():
            raise ValueError("evidence_id / content_hash 不能为空")
        return v


# ----------------------------- 执行策略 -----------------------------
class ResearchExecutionPolicy(_Strict):
    """执行权限与硬上限。本阶段（A.7.5.3）四个权限位强制为 False。"""
    allow_network: bool = False
    allow_code_execution: bool = False
    allow_device_control: bool = False
    allow_planner: bool = False
    max_model_calls: int = 3
    max_cost_usd: str = "0.00"         # 字符串承载十进制，避免浮点误差；fake 阶段为 0
    role_limits: dict[str, int] = Field(default_factory=lambda: {
        "synthesizer": 1, "verifier": 1, "claim_extractor": 1})
    require_approval: bool = True

    @field_validator("max_model_calls")
    @classmethod
    def _calls(cls, v):
        if not (0 <= int(v) <= 3):
            raise ValueError("max_model_calls 必须在 0..3（本阶段硬上限 3）")
        return int(v)

    @field_validator("role_limits")
    @classmethod
    def _roles(cls, v):
        allowed = {"synthesizer", "verifier", "claim_extractor"}
        if set(v) - allowed:
            raise ValueError(f"role_limits 只允许 {sorted(allowed)}")
        for role, n in v.items():
            if not (0 <= int(n) <= 1):
                raise ValueError(f"role_limits[{role}] 必须 ≤1（角色额度不可互借）")
        return {k: int(n) for k, n in v.items()}

    def assert_zero_paid_stage(self) -> None:
        """本阶段守卫：任何越权位为 True → fail-closed。"""
        for flag in ("allow_network", "allow_code_execution", "allow_device_control", "allow_planner"):
            if getattr(self, flag):
                raise ResearchContractError(f"A.7.5.3 禁止 {flag}=True（零付费 / 零网络 / 零设备 / 无 Planner）")

    def policy_hash(self) -> str:
        return compute_hash(self.model_dump(mode="json"))


# ----------------------------- 澄清 / 审批（spec 侧） -----------------------------
class ResearchOption(_Strict):
    id: str
    label: str
    recommended: bool = False


class ResearchClarificationSpec(_Strict):
    """参数化澄清：问题/选项/推荐项/答案 schema 全部来自 spec，不再写死。"""
    question: str
    kind: Literal["single_select", "multi_select", "free_text", "single_or_other"] = "single_or_other"
    options: list[ResearchOption] = Field(default_factory=list)
    allow_other: bool = True
    required: bool = True
    reason: str = ""

    @field_validator("question", "reason")
    @classmethod
    def _len(cls, v):
        if len(str(v)) > QUESTION_MAX:
            raise ValueError(f"文本超长（>{QUESTION_MAX}）")
        return v

    @field_validator("options")
    @classmethod
    def _opts(cls, v):
        if len(v) > MAX_OPTIONS:
            raise ValueError(f"选项过多（>{MAX_OPTIONS}）")
        if len({o.id for o in v}) != len(v):
            raise ValueError("选项 id 重复")
        return v

    def question_hash(self) -> str:
        return compute_hash({"q": self.question, "kind": self.kind,
                             "options": sorted(o.id for o in self.options),
                             "allow_other": self.allow_other})


class ResearchApprovalSpec(_Strict):
    """参数化审批：审批卡内容来自 spec；执行计划在批准时被冻结。"""
    action_summary: str
    expected_side_effect: str
    risk_level: Literal["low", "medium", "high"] = "high"
    is_simulation: bool = True
    reason: str = ""

    @field_validator("action_summary", "expected_side_effect", "reason")
    @classmethod
    def _len(cls, v):
        if len(str(v)) > QUESTION_MAX:
            raise ValueError(f"文本超长（>{QUESTION_MAX}）")
        return v


# ----------------------------- Research Run Spec -----------------------------
class ResearchRunSpec(_Strict):
    """一次 research run 的完整、版本化、可冻结描述。未知 schema/字段 → fail-closed。"""
    schema_version: Literal["research-run-v1"] = RESEARCH_SPEC_SCHEMA
    run_type: Literal["research"] = "research"
    question: str
    clarification: ResearchClarificationSpec
    approval: ResearchApprovalSpec
    evidence_refs: list[EvidenceReference] = Field(default_factory=list)
    execution_policy: ResearchExecutionPolicy = Field(default_factory=ResearchExecutionPolicy)
    executor_id: str                       # 只允许服务端 registry 中已注册的 ID
    expected_outputs: list[str] = Field(default_factory=lambda: ["research_artifact"])

    @field_validator("question")
    @classmethod
    def _q(cls, v):
        s = str(v)
        if not s.strip():
            raise ValueError("question 不能为空")
        if len(s) > QUESTION_MAX:
            raise ValueError(f"question 超长（>{QUESTION_MAX}）→ fail-closed")
        return s

    @field_validator("evidence_refs")
    @classmethod
    def _ev(cls, v):
        if len(v) > MAX_EVIDENCE_REFS:
            raise ValueError(f"evidence_refs 过多（>{MAX_EVIDENCE_REFS}）")
        if len({e.evidence_id for e in v}) != len(v):
            raise ValueError("evidence_id 重复")
        return v

    # ---- 冻结用 hash ----
    def question_hash(self) -> str:
        return compute_hash({"question": self.question})

    def evidence_hash(self) -> str:
        return compute_hash([{"id": e.evidence_id, "h": e.content_hash, "fixture": e.fixture}
                             for e in self.evidence_refs])

    def policy_hash(self) -> str:
        return self.execution_policy.policy_hash()

    def plan_hash(self) -> str:
        """执行计划指纹：问题 + 证据 + 策略 + executor。批准后任一项变化 → 旧批准失效。"""
        return compute_hash({"question_hash": self.question_hash(),
                             "evidence_hash": self.evidence_hash(),
                             "policy_hash": self.policy_hash(),
                             "executor_id": self.executor_id,
                             "schema_version": self.schema_version})

    def public_view(self) -> dict:
        """脱敏公开视图（供 UI/审批卡）。不含正文证据、不含 Prompt、不含模型对象。"""
        return {"run_type": self.run_type, "schema_version": self.schema_version,
                "question": self.question[:LABEL_MAX],
                "question_hash": self.question_hash(),
                "evidence_count": len(self.evidence_refs),
                "evidence_ids": [e.evidence_id for e in self.evidence_refs][:MAX_EVIDENCE_REFS],
                "fixture_evidence": all(e.fixture for e in self.evidence_refs) if self.evidence_refs else False,
                "executor_id": self.executor_id,
                "policy_hash": self.policy_hash(),
                "evidence_hash": self.evidence_hash(),
                "plan_hash": self.plan_hash(),
                "expected_outputs": list(self.expected_outputs),
                "policy": self.execution_policy.model_dump(mode="json")}


# ----------------------------- 执行期上下文 / 控制 / 结果 -----------------------------
class ResearchRunContext(_Strict):
    """传给 executor 的最小上下文（无模型对象、无 key、无绝对路径）。"""
    run_id: str
    question: str
    question_hash: str
    clarification_answer: Optional[str] = None
    answer_hash: Optional[str] = None
    evidence_refs: list[EvidenceReference] = Field(default_factory=list)
    policy: ResearchExecutionPolicy = Field(default_factory=ResearchExecutionPolicy)


class ResearchArtifact(_Strict):
    """结构化科研产物。Claim 只能引用已有 evidence_id；Shadow 不得新建证据。"""
    schema_version: Literal["research-artifact-v1"] = RESEARCH_ARTIFACT_SCHEMA
    run_id: str
    question_hash: str
    evidence_ids: list[str] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    verifier_verdict: str
    shadow_verdict: Optional[str] = None
    causal_tier: str
    limitations: list[str] = Field(default_factory=list)
    # A.7.5.5 §12：冻结输入溯源 + 用量。这些字段**在 content_hash 之内**，
    # 因此产物 hash 绑定它所依据的证据子集；换一份证据必然换一个 hash。
    subset_id: str = ""
    subset_hash: str = ""
    source_pack_hash: str = ""
    protocol_hash: str = ""
    verifier_fact_conflict: bool = False
    contradictions: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    model_calls_by_role: dict = Field(default_factory=dict)
    token_usage_by_role: dict = Field(default_factory=dict)
    cost_by_role: dict = Field(default_factory=dict)
    total_cost: float = 0.0
    fixture: bool = False                 # True = 测试夹具产物，禁止当作真实科研结论
    content_hash: str = ""
    hash_algorithm: Literal["sha256"] = "sha256"

    def compute_content_hash(self) -> str:
        d = self.model_dump(mode="json")
        d.pop("content_hash", None)
        return compute_hash(d)

    def finalize(self) -> "ResearchArtifact":
        return self.model_copy(update={"content_hash": self.compute_content_hash()})

    def assert_claims_cite_known_evidence(self) -> None:
        known = set(self.evidence_ids)
        for c in self.claims:
            unknown = (set(c.supporting_evidence_ids) | set(c.contradicting_evidence_ids)
                       | set(c.unresolved_evidence_ids)) - known
            if unknown:
                raise ResearchContractError(f"Claim {c.claim_id} 引用了不存在的 evidence_id：{sorted(unknown)}")


class ResearchFailureManifest(_Strict):
    """失败诊断产物（research-failure-v1）。**绝不冒充科研成功结论**：claims 恒为空、无成功产物。"""
    schema_version: Literal["research-failure-v1"] = "research-failure-v1"
    run_id: str
    failed_stage: str
    error_type: str
    error_summary: str = ""            # 脱敏单行摘要；不含 traceback / 路径 / key / Prompt
    completed_stages: list[str] = Field(default_factory=list)
    evidence_count: int = 0
    claims: list = Field(default_factory=list)      # 恒为空
    research_artifact_created: bool = False         # 恒为 False
    human_review: bool = True
    worker_generation: int = 0
    content_hash: str = ""
    hash_algorithm: Literal["sha256"] = "sha256"

    @field_validator("claims")
    @classmethod
    def _no_claims(cls, v):
        if v:
            raise ValueError("失败 Manifest 不得包含 claims（不得伪装成科研结论）")
        return []

    @field_validator("research_artifact_created")
    @classmethod
    def _no_artifact(cls, v):
        if v:
            raise ValueError("失败 Manifest 不得声称已生成成功产物")
        return False

    @field_validator("error_summary")
    @classmethod
    def _bounded(cls, v):
        return " ".join(str(v or "").split())[:400]

    def finalize(self) -> "ResearchFailureManifest":
        d = self.model_dump(mode="json")
        d.pop("content_hash", None)
        return self.model_copy(update={"content_hash": compute_hash(d)})


@runtime_checkable
class ResearchExecutor(Protocol):
    """研究执行器接口。HitlRun 只依赖本 Protocol，不依赖任何具体模型客户端。

    `stages` 声明阶段顺序；HitlRun 逐阶段驱动（每个阶段之间是 pause/stop 的 safe boundary），
    因此阶段游标、暂停恢复、stale worker 拒绝、重启恢复全部复用已审计的 HITL 机制。
    """
    executor_id: str
    stages: tuple

    def run_stage(self, *, stage: str, ctx: ResearchRunContext, state: dict, emit) -> dict:
        """执行单个阶段；返回该阶段对 state 的增量（纯数据）。不得自行推进到下一阶段。"""
        ...

    def build_artifact(self, *, ctx: ResearchRunContext, state: dict) -> ResearchArtifact:
        """由累积 state 构造最终产物（每个 run 至多一次，由 HitlRun 保证）。"""
        ...


# ----------------------------- 服务端 executor registry -----------------------------
_REGISTRY: dict[str, object] = {}


def register_executor(executor) -> None:
    """服务端注册；客户端只能按 ID 选择，不能注入对象。"""
    eid = getattr(executor, "executor_id", None)
    if not eid or not isinstance(eid, str):
        raise ResearchContractError("executor 必须提供字符串 executor_id")
    _REGISTRY[eid] = executor


def get_executor(executor_id: str):
    """未注册 → fail-closed（不 fallback 到 demo action，也不 fallback 到真实模型）。"""
    ex = _REGISTRY.get(str(executor_id))
    if ex is None:
        raise ExecutorNotRegistered(f"未注册的 executor_id：{executor_id!r}（fail-closed，不做任何回退）")
    return ex


def registered_executor_ids() -> list:
    return sorted(_REGISTRY)


__all__ = [
    "RESEARCH_SPEC_SCHEMA", "RESEARCH_ARTIFACT_SCHEMA", "QUESTION_MAX",
    "ResearchContractError", "ExecutorNotRegistered",
    "EvidenceReference", "ResearchExecutionPolicy", "ResearchOption",
    "ResearchClarificationSpec", "ResearchApprovalSpec", "ResearchRunSpec",
    "ResearchRunContext", "ResearchArtifact", "ResearchFailureManifest", "ResearchExecutor",
    "register_executor", "get_executor", "registered_executor_ids",
]
