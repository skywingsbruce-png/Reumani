"""把事件轨迹与循环护栏**接进 Pilot 的真实执行路径**（A.6.5 §2 接线部分）。

设计要点：
- 模型侧：走 `GatedModel` 的 hooks。`pre_invoke` 在**网络请求之前**执行，
  循环护栏在此硬中止 —— 与预算闸门同一层级，不依赖 callback 异常传播。
- 工具侧：给每个 BaseTool 挂 LangChain callbacks，**只记录不阻断**
  （callback 异常会被框架吞掉，A.6.2 的教训：那条路不能用来做强制）。
- 不修改任何生产科研逻辑：只在运行时替换模块属性 / 设置对象字段。
"""

from pilot.executor_trace import ExecutorTrace
from pilot.loop_guard import ExecutorLoopGuard, LoopGuardTriggered


class ExecutorHooks:
    """挂到 executor 角色的 GatedModel 上：调用前查护栏，响应后记轨迹。"""

    def __init__(self, trace, guard, *, outer_iteration=1):
        self.trace = trace
        self.guard = guard
        self.outer_iteration = outer_iteration
        self.executor_call_index = 0
        self.pending_tool_calls = []

    # ---- 网络请求之前 ----
    def pre_invoke(self, *, role, model_id):
        if role != "executor":
            return
        # 上一次响应要求的每个工具调用，在**下一次模型调用之前**过一遍护栏。
        for tc in self.pending_tool_calls:
            self.guard.before_tool_round(tc.get("name"), tc.get("args"))
        self.pending_tool_calls = []

    # ---- 响应之后 ----
    def post_response(self, *, role, model_id, response):
        if role != "executor":
            return
        self.executor_call_index += 1
        tool_calls = list(getattr(response, "tool_calls", None) or [])
        um = getattr(response, "usage_metadata", None) or {}
        self.trace.record_model_response(
            outer_iteration=self.outer_iteration,
            executor_call_index=self.executor_call_index,
            provider=(model_id or "").split("-")[0], model=model_id, role=role,
            response=response,
            input_tokens=um.get("input_tokens"), output_tokens=um.get("output_tokens"),
            next_graph_node=("tools" if tool_calls else "END"),
            termination_reason=(None if tool_calls else "no_tool_calls"))
        self.pending_tool_calls = [tc for tc in tool_calls if isinstance(tc, dict)]
        # 进展信号：本轮请求的工具调用签名（确定性，不问 LLM）
        from pilot.loop_guard import call_signature
        self.guard.record_progress([call_signature(tc.get("name"), tc.get("args"))
                                    for tc in self.pending_tool_calls])


class ToolTraceCallback:
    """LangChain 工具回调：**只记录，不阻断**（强制在 pre_invoke 那一层做）。"""

    raise_error = False
    ignore_llm = True
    ignore_chain = True
    ignore_agent = True
    ignore_retriever = True
    ignore_chat_model = True
    ignore_retry = True
    ignore_custom_event = True

    def __init__(self, trace):
        self.trace = trace
        self._started = {}

    def __getattr__(self, name):
        if name.startswith("on_"):
            return lambda *a, **k: None
        raise AttributeError(name)

    def on_tool_start(self, serialized, input_str, *, run_id=None, inputs=None, **kw):
        import time
        name = (serialized or {}).get("name") or kw.get("name") or "unknown"
        args = inputs if isinstance(inputs, dict) else {"input": str(input_str)[:200]}
        self._started[str(run_id)] = (name, time.time())
        try:
            self.trace.record_tool_start(tool_call_id=str(run_id), tool_name=name,
                                         arguments=args)
        except Exception:
            pass

    def on_tool_end(self, output, *, run_id=None, **kw):
        name, started = self._started.pop(str(run_id), ("unknown", None))
        try:
            self.trace.record_tool_end(tool_call_id=str(run_id), tool_name=name,
                                       status="ok", result=output,
                                       structured=hasattr(output, "artifact"),
                                       returned_tool_message=True, started_at=started)
        except Exception:
            pass

    def on_tool_error(self, error, *, run_id=None, **kw):
        name, started = self._started.pop(str(run_id), ("unknown", None))
        try:
            self.trace.record_tool_end(tool_call_id=str(run_id), tool_name=name,
                                       status="error", error_type=type(error).__name__,
                                       returned_tool_message=False, started_at=started)
        except Exception:
            pass


def install(*, run_id, trace_path, selected_tools=None, max_tool_rounds=None,
            no_progress_rounds=None):
    """建立 trace + guard，并把工具回调挂到技能工具上。返回 (trace, guard, hooks)。"""
    trace = ExecutorTrace(trace_path, run_id)
    if selected_tools:
        trace.record_selected(selected_tools)
    kw = {}
    if max_tool_rounds is not None:
        kw["max_tool_rounds"] = max_tool_rounds
    if no_progress_rounds is not None:
        kw["no_progress_rounds"] = no_progress_rounds
    guard = ExecutorLoopGuard(**kw)
    hooks = ExecutorHooks(trace, guard)

    cb = ToolTraceCallback(trace)
    attached = []
    try:
        import ssc_skill_agent as SK
        for t in getattr(SK, "SKILL_AGENT_TOOLS", []) or []:
            try:
                t.callbacks = [cb]          # 运行时挂载，不改源码
                attached.append(t.name)
            except Exception:
                pass
    except Exception:
        pass
    return trace, guard, hooks, attached


def classify_executor_failure(exc):
    """把 Executor 失败归类到 failure_reason（供失败诊断 Manifest 使用）。"""
    if isinstance(exc, LoopGuardTriggered):
        return f"loop_guard:{exc.reason}"
    name = type(exc).__name__
    text = str(exc)
    if "max_calls_per_role" in text:
        return "role_cap"
    if "max_usd" in text:
        return "budget_cap"
    if name in ("BudgetExceeded", "GateConfigError"):
        return f"gate:{name}"
    if "parse" in text.lower() or "json" in text.lower():
        return "parser_error"
    return f"tool_or_other:{name}"
