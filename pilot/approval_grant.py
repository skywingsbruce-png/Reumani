"""A.8.2a.4a —— 与真实审批绑定的执行授权契约。

历史教训（两次都被驳回）：
- A.8.2a.2 用一个普通布尔参数当闸门，调用方传 True 即可 —— 等于没有闸门；
- A.8.2a.3 的 `authorize()` 只比对 8 项中的 3 项，且事件序号查不到时用 `-1` 继续授权
  （fail-open）。两者都使"安全性建立在字段重校验上"的说法**不成立**。

本模块因此把绑定做完整：审批**请求**时冻结 `PendingApprovalBinding`，审批**通过并落盘**后
签发 `ApprovalGrant`，两者逐项比对。

安全边界如实声明：Python 无法做到真正不可伪造。`_ISSUER` 哨兵只挡"随手构造"，
同进程代码仍可取到它。真正的防线是 `authorize()` 对**全部绑定字段 + 真实事件**的重校验——
伪造的凭证除非同时篡改已持久化的 approval_granted 事件，否则通不过。
"""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict

from schemas import _Strict

APPROVAL_BINDING_SCHEMA = "pending-approval-binding-v1"
APPROVAL_GRANT_SCHEMA = "approval-grant-v2"

# 仅用于阻挡"随手构造"，**不是**安全保证本身（见模块 docstring）。
_ISSUER = object()

# 两个 state_version 含义不同，绝不可混用：
#   request_state_version —— 创建 pending approval request 时的状态版本（客户端须回显它）
#   granted_state_version —— approval_granted 成功落盘之后的状态版本
_BINDING_FIELDS = ("run_id", "request_id", "action_hash", "preview_hash",
                   "request_state_version", "executor_id", "policy_id")


class ApprovalGrantError(RuntimeError):
    """授权缺失 / 绑定不符 / 事件不可信 → fail-closed（provider 调用为 0）。"""


class PendingApprovalBinding(_Strict):
    """审批**请求**时冻结的绑定。由 HitlRun 在创建 pending request 后交给 executor。"""
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["pending-approval-binding-v1"] = APPROVAL_BINDING_SCHEMA
    run_id: str
    request_id: str
    action_hash: str
    preview_hash: str
    request_state_version: int
    executor_id: str
    policy_id: str

    def binding(self) -> dict:
        return {f: getattr(self, f) for f in _BINDING_FIELDS}


class ApprovalGrant(_Strict):
    """审批**通过且 approval_granted 已落盘**后签发的执行授权。

    绝不出现在 HTTP 请求或响应里；只在进程内由 HitlRun 交给 executor。
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["approval-grant-v2"] = APPROVAL_GRANT_SCHEMA
    run_id: str
    request_id: str
    action_hash: str
    preview_hash: str
    request_state_version: int          # 与 binding 比对
    granted_state_version: int          # approval_granted 落盘后的状态版本
    executor_id: str
    policy_id: str
    granted_event_sequence: int         # 必须 >= 0，且指向真实存在的 approval_granted

    def binding(self) -> dict:
        return {f: getattr(self, f) for f in _BINDING_FIELDS}

    def identity(self) -> dict:
        d = self.binding()
        d.update(granted_state_version=self.granted_state_version,
                 granted_event_sequence=self.granted_event_sequence)
        return d


def _require(cond, msg):
    if not cond:
        raise ApprovalGrantError(msg)


def issue_binding(issuer, **fields) -> PendingApprovalBinding:
    _require(issuer is _ISSUER,
             "PendingApprovalBinding 只能由 HitlRun 在创建 pending request 后签发")
    return PendingApprovalBinding(**fields)


def issue_grant(issuer, **fields) -> ApprovalGrant:
    """内部签发口。**不声称**哨兵不可伪造 —— 防线是 authorize() 的重校验。"""
    _require(issuer is _ISSUER,
             "ApprovalGrant 只能由 HitlRun.approve() 在校验通过且事件落盘后签发")
    for k in ("run_id", "request_id", "action_hash", "preview_hash", "executor_id", "policy_id"):
        _require(str(fields.get(k) or "").strip(), f"授权缺少绑定字段 {k}")
    seq = fields.get("granted_event_sequence")
    _require(isinstance(seq, int) and seq >= 0,
             f"granted_event_sequence 非法：{seq!r}（不接受 -1 / unknown）")
    for k in ("request_state_version", "granted_state_version"):
        v = fields.get(k)
        _require(isinstance(v, int) and v >= 0, f"{k} 非法：{v!r}")
    return ApprovalGrant(**fields)


def compare_binding(grant: ApprovalGrant, binding: PendingApprovalBinding) -> None:
    """逐项比对；**第一次授权同样必须走这里**，不是只在重复授权时才比。"""
    _require(isinstance(grant, ApprovalGrant), "authorize() 需要 ApprovalGrant")
    _require(isinstance(binding, PendingApprovalBinding),
             "未冻结 PendingApprovalBinding，拒绝授权（fail-closed）")
    g, b = grant.binding(), binding.binding()
    for f in _BINDING_FIELDS:
        _require(g[f] == b[f],
                 f"授权与审批请求绑定不符（{f}）→ 拒绝执行（provider 调用为 0）")


__all__ = ["ApprovalGrant", "PendingApprovalBinding", "ApprovalGrantError",
           "issue_grant", "issue_binding", "compare_binding",
           "APPROVAL_GRANT_SCHEMA", "APPROVAL_BINDING_SCHEMA", "_ISSUER"]
