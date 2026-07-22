"""逐 tool_call 关联的 AgentMiddleware（A.6.6.3 §1）。

**权威 tool_call_id 来源**：LangChain 1.x `AgentMiddleware.wrap_tool_call(request, handler)`。
`request.tool_call["id"]` 是模型产生的真实 tool_call_id；`handler(request)` 返回的
ToolMessage 携带同一个 id。因此一个 id 贯穿四阶段：

    requested   (ExecutorHooks 在模型响应后按 tool_call.id 登记)
    → executed  (handler 调用之前)
    → tool_returned / failed  (handler 返回 / 抛异常)
    → observed  (返回的 ToolMessage.tool_call_id 匹配同一 id)

不依赖工具名 / 事件顺序 / 数量 / 运行管理器的运行标识 / 参数 hash 配对。
不改变工具 schema、artifact 或返回语义 —— 只在 handler 前后打点。
"""

import time

from langchain.agents.middleware import AgentMiddleware

from tool_envelope import compute_hash


class MiddlewareUnavailable(RuntimeError):
    """当前 LangChain 不提供权威 tool_call_id → fail-closed，不退回顺序猜测。"""


def assert_middleware_available():
    """启动前检查：wrap_tool_call 与 ToolCallRequest.tool_call 必须可用。"""
    if not hasattr(AgentMiddleware, "wrap_tool_call"):
        raise MiddlewareUnavailable("AgentMiddleware.wrap_tool_call 不可用")
    try:
        from langchain.agents.middleware import ToolCallRequest  # noqa: F401
    except Exception as e:
        raise MiddlewareUnavailable(f"ToolCallRequest 不可用：{e}")
    return True


class LifecycleMiddleware(AgentMiddleware):
    """在真实 create_agent 的工具执行边界上，用权威 tool_call_id 做四阶段关联。"""

    def __init__(self, reconciler, trace=None, guard=None, allowed=None):
        super().__init__()
        self._rec = reconciler
        self._trace = trace
        self._guard = guard
        self._allowed = set(allowed) if allowed else None

    # ---- 同步工具边界 ----
    def wrap_tool_call(self, request, handler):
        return self._run(request, handler, is_async=False)

    async def awrap_tool_call(self, request, handler):
        tc = getattr(request, "tool_call", None) or {}
        tcid, name, args = tc.get("id"), tc.get("name"), tc.get("args") or {}
        self._before(tcid, name, args)
        started = time.time()
        try:
            resp = await handler(request)
        except Exception as e:
            self._on_fail(tcid, name, e, started)
            raise
        self._on_return(tcid, name, resp, started)
        return resp

    def _run(self, request, handler, is_async):
        tc = getattr(request, "tool_call", None) or {}
        tcid, name, args = tc.get("id"), tc.get("name"), tc.get("args") or {}
        self._before(tcid, name, args)
        started = time.time()
        try:
            resp = handler(request)
        except Exception as e:
            self._on_fail(tcid, name, e, started)
            raise
        self._on_return(tcid, name, resp, started)
        return resp

    # ---- 打点：全部按权威 tool_call.id ----
    def _before(self, tcid, name, args):
        if self._allowed is not None and name not in self._allowed:
            raise PermissionError(f"未授权工具在执行前被阻断：{name}")
        if self._guard is not None:
            self._guard.before_tool_round(name, args)
        if self._trace is not None:
            try:
                self._trace.record_tool_start(tool_call_id=tcid, tool_name=name,
                                               arguments=args)
            except Exception:
                from pilot.lifecycle import TRACE_START_FAILED, LifecycleError
                raise LifecycleError(TRACE_START_FAILED, {"tool": name})
        if self._rec is not None:
            self._rec.mark_executed(tcid, name)     # executed：handler 之前

    def _on_return(self, tcid, name, resp, started):
        content = getattr(resp, "content", "") or ""
        rhash = compute_hash(content if isinstance(content, str) else str(content))
        if self._rec is not None:
            self._rec.mark_returned(tcid, name, result_hash=rhash)
        if self._trace is not None:
            try:
                self._trace.record_tool_returned(
                    tool_call_id=tcid, tool_name=name,
                    result=content if isinstance(content, str) else str(content),
                    structured=getattr(resp, "artifact", None) is not None,
                    started_at=started, result_hash=rhash)
            except Exception:
                if self._rec is not None:
                    self._rec.trace_incomplete = True

    def _on_fail(self, tcid, name, exc, started):
        if self._rec is not None:
            self._rec.mark_failed(tcid, name, error_type=type(exc).__name__)
        if self._trace is not None:
            try:
                self._trace.record_tool_end(tool_call_id=tcid, tool_name=name,
                                            status="error", error_type=type(exc).__name__,
                                            returned_tool_message=False,
                                            started_at=started)
            except Exception:
                if self._rec is not None:
                    self._rec.trace_incomplete = True
