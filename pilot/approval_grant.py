"""A.8.2a.3 —— 与真实审批绑定的执行授权凭证。

背景：A.8.2a.2 用的是 `approval_verified: bool`，调用方直接传 `True` 即可，
与没有闸门在安全上等价。本模块用一个**绑定了具体审批事实**的凭证取代它。

安全边界必须说清楚（不得夸大）：
- Python 层**无法**做到真正不可伪造。`_ISSUER` 哨兵只能挡住"HTTP 调用方顺手构造一个"，
  同进程内的代码仍可读到它。
- 因此安全性**不建立在对象不可伪造上**，而建立在
  `DeferredRegistryResearchExecutor.authorize()` 对**全部绑定字段的重新校验**上：
  run_id / request_id / action_hash / preview_hash / executor_id / policy_id /
  state_version / approval_granted 事件序号，任一不符即 fail-closed。
  伪造一个凭证并不能让它通过这些比对，除非同时篡改已持久化的审批事实。
"""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict

from schemas import _Strict

APPROVAL_GRANT_SCHEMA = "approval-grant-v1"

# 仅用于阻挡"随手构造"，**不是**安全保证本身（见模块 docstring）。
_ISSUER = object()


class ApprovalGrantError(RuntimeError):
    """授权缺失 / 绑定字段不符 → fail-closed。"""


class ApprovalGrant(_Strict):
    """一次成功审批的执行授权。由 `HitlRun.approve()` 在全部校验通过后签发。

    绝不出现在 HTTP 请求或响应里；只在进程内由 HitlRun 交给 executor。
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["approval-grant-v1"] = APPROVAL_GRANT_SCHEMA
    run_id: str
    request_id: str
    action_hash: str
    preview_hash: str
    approved_state_version: int
    executor_id: str
    policy_id: str
    granted_event_sequence: int

    def binding(self) -> dict:
        """参与二次校验的全部绑定字段。"""
        return {"run_id": self.run_id, "request_id": self.request_id,
                "action_hash": self.action_hash, "preview_hash": self.preview_hash,
                "approved_state_version": self.approved_state_version,
                "executor_id": self.executor_id, "policy_id": self.policy_id,
                "granted_event_sequence": self.granted_event_sequence}


def issue_grant(issuer, **fields) -> ApprovalGrant:
    """内部签发口。`issuer` 必须是本模块的 `_ISSUER`。

    再次强调：这只是防手滑，不是安全保证 —— 真正的防线是 authorize() 的字段比对。
    """
    if issuer is not _ISSUER:
        raise ApprovalGrantError(
            "ApprovalGrant 只能由 HitlRun.approve() 在校验通过后签发（fail-closed）")
    for k, v in fields.items():
        if k.endswith("hash") and not str(v or "").strip():
            raise ApprovalGrantError(f"授权缺少绑定字段 {k}")
    return ApprovalGrant(**fields)


__all__ = ["ApprovalGrant", "ApprovalGrantError", "issue_grant", "APPROVAL_GRANT_SCHEMA",
           "_ISSUER"]
