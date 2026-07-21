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
        # 【A.6.6 §5】**请求本身不算进展**。新 tool_call_id / 新 arguments_hash /
        # 新工具名 / 新请求参数 / 新模型正文 / 新 finish_reason 都不能重置 no-progress。
        # 进展只由**执行/观察侧**的确定性新结果产生 —— 由 ToolLifecycleProxy 在
        # 工具真实返回后调用 guard.record_progress(...)。
        #
        # 因此这里只在"本轮模型没有请求任何工具"时记一次无进展轮：
        # 那意味着这一轮既不会有工具执行，也就不可能产生新的观察结果。
        if not self.pending_tool_calls:
            self.guard.record_progress([])


class ToolTraceCallback:
    """【已弃用，A.6.6】事后给 `BaseTool.callbacks` 赋值的接线方式。

    离线真实 Agent 探针（`tests/test_tool_lifecycle_probe.py`）证明：在
    `langchain.agents.create_agent` 的 ToolNode 路径下，**工具确实执行、
    ToolMessage 确实生成，但本回调一条都不会触发** —— 这正是 A1-rerun 中
    `executed=[]` / `observed=[]` 的成因（可观测性失败，而非工具未执行）。

    `install()` 已改用 `pilot.tool_proxy.ToolLifecycleProxy`。
    此类仅保留给那份反例测试作证据，**不要用于新接线**。
    """

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

    # 【A.6.6 §3】不再依赖"事后给 BaseTool 赋 callbacks" —— 离线真实 Agent 探针证明
    # 那条路在 create_agent 的 ToolNode 下**不可靠**（工具执行了但回调不触发）。
    # 改为把技能工具整体换成 ToolLifecycleProxy，观察点落在底层函数调用边界上。
    from pilot.tool_proxy import assert_contract_equivalent, wrap_tools

    wrapped = []
    try:
        import ssc_skill_agent as SK
        originals = list(getattr(SK, "SKILL_AGENT_TOOLS", []) or [])
        proxies = wrap_tools(originals, trace=trace, guard=guard,
                             allowed=selected_tools)
        for p, o in zip(proxies, originals):
            assert_contract_equivalent(p, o)      # 契约不等价即拒绝启动
            wrapped.append(p.name)
        SK.SKILL_AGENT_TOOLS = proxies            # 运行时替换，不改源码
    except Exception as e:
        raise RuntimeError(f"工具生命周期代理安装失败，拒绝启动：{e}")
    return trace, guard, hooks, wrapped


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
