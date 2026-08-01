"""A.7.5 —— 人机协作控制的**单一权威运行状态**契约 + 严格请求契约。

纯数据/状态机：无 LLM / 网络 / 账本 / 线程。非法状态转换 fail-closed。
Clarification/Approval 请求只承载契约字段（不含 Prompt / 患者信息 / key / 认证头 / 模型正文）。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field, field_validator

from schemas import _Strict
from tool_envelope import compute_hash

RunControlState = Literal[
    "running", "awaiting_clarification", "awaiting_approval",
    "pausing", "paused", "completed", "failed", "stopped",
]
TERMINAL_STATES: frozenset = frozenset({"completed", "failed", "stopped"})
WAITING_STATES: frozenset = frozenset({"awaiting_clarification", "awaiting_approval"})

# 合法状态转换（非法即 fail-closed）。paused→running/awaiting_* 由 resume 还原到暂停前状态。
LEGAL_TRANSITIONS: dict = {
    "running": {"awaiting_clarification", "awaiting_approval", "pausing",
                "completed", "failed", "stopped"},
    "awaiting_clarification": {"running", "pausing", "paused", "stopped"},
    "awaiting_approval": {"running", "pausing", "paused", "stopped"},
    "pausing": {"paused", "stopped", "failed"},   # A.7.5.3.1：pausing 期间阶段异常 → failed（不停在 paused）
    "paused": {"running", "awaiting_clarification", "awaiting_approval", "stopped"},
    "completed": set(),
    "failed": set(),
    "stopped": set(),
}

FREE_TEXT_MAX = 200          # 自由文本有限长（脱敏后入事件，不作为代码/路径执行）


class IllegalTransition(RuntimeError):
    """fail-closed：不允许的状态转换。"""


class StaleState(RuntimeError):
    """expected_state_version 与当前不符（409 冲突）。"""


class ContractViolation(ValueError):
    """请求体含未知字段 / 非法值 / 不匹配的 hash 等。"""


def can_transition(src: str, dst: str) -> bool:
    return dst in LEGAL_TRANSITIONS.get(src, set())


def assert_transition(src: str, dst: str) -> None:
    if not can_transition(src, dst):
        raise IllegalTransition(f"非法状态转换：{src} → {dst}")


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


# ----------------------- Clarification -----------------------
ClarificationKind = Literal["single_select", "multi_select", "free_text", "single_or_other"]


class ClarificationOption(_Strict):
    id: str
    label: str


class ClarificationRequest(_Strict):
    request_id: str
    run_id: str
    state_version: int
    created_at: str
    reason: str
    requesting_step_id: int
    kind: ClarificationKind
    question_hash: str
    allowed_options: list[ClarificationOption] = Field(default_factory=list)
    allow_other: bool = False
    status: Literal["open", "answered"] = "open"

    def option_ids(self) -> set:
        return {o.id for o in self.allowed_options}


class ClarificationAnswer(_Strict):
    """写入端点请求体：只接受契约字段（拒绝未知字段/代码/路径/任意工具名）。"""
    request_id: str
    expected_state_version: int
    idempotency_key: str
    selected_option_ids: list[str] = Field(default_factory=list)
    other_text: Optional[str] = None

    @field_validator("other_text")
    @classmethod
    def _bounded_desensitized(cls, v):
        if v is None:
            return v
        s = str(v)[:FREE_TEXT_MAX]
        # 脱敏：换行/制表折叠为空格，去掉可能被当作路径/命令的字符语义（仅存文本）
        return " ".join(s.split())


def question_hash(question: str, option_ids: list, kind: str, allow_other: bool) -> str:
    return compute_hash({"q": question, "options": sorted(option_ids), "kind": kind,
                         "allow_other": bool(allow_other)})


# ----------------------- Approval -----------------------
RiskLevel = Literal["low", "medium", "high"]


class ApprovalRequest(_Strict):
    request_id: str
    run_id: str
    state_version: int
    created_at: str
    reason: str
    requesting_step_id: int
    tool_name: str
    arguments_hash: str            # normalized arguments hash
    risk_level: RiskLevel
    action_summary: str
    expected_side_effect: str
    action_hash: str               # 绑定 tool_name+args+risk；仅完全一致才有效
    is_simulation: bool = True     # 明确 fake/仿真
    status: Literal["open", "granted", "denied"] = "open"


class ApprovalDecision(_Strict):
    """写入端点请求体（approve/deny 共用）：只接受契约字段。"""
    request_id: str
    expected_state_version: int
    idempotency_key: str
    action_hash: str               # 必须与待批准动作完全一致，否则拒绝


class ControlAction(_Strict):
    """pause / resume / stop 端点请求体：只接受契约字段。"""
    idempotency_key: str
    expected_state_version: int


def normalized_arguments_hash(arguments: dict) -> str:
    """规范化参数 hash：键排序、值字符串化，稳定可复现。"""
    norm = {str(k): (arguments[k] if isinstance(arguments[k], (int, float, bool, str, type(None)))
                     else str(arguments[k])) for k in sorted(arguments or {})}
    return compute_hash(norm)


def action_hash(tool_name: str, arguments: dict, risk_level: str) -> str:
    """动作指纹：工具名 + 规范化参数 + 风险等级。参数/工具变化 → hash 变化 → 旧批准失效。"""
    return compute_hash({"tool": tool_name, "args_hash": normalized_arguments_hash(arguments),
                         "risk": risk_level})


__all__ = [
    "RunControlState", "TERMINAL_STATES", "WAITING_STATES", "LEGAL_TRANSITIONS", "FREE_TEXT_MAX",
    "IllegalTransition", "StaleState", "ContractViolation",
    "can_transition", "assert_transition", "is_terminal",
    "ClarificationKind", "ClarificationOption", "ClarificationRequest", "ClarificationAnswer",
    "question_hash", "RiskLevel", "ApprovalRequest", "ApprovalDecision", "ControlAction",
    "normalized_arguments_hash", "action_hash",
]
