"""工具生命周期代理（A.6.6 §3）。

**为什么需要它**：离线真实 Agent 探针证明 —— 事后给 `BaseTool.callbacks` 赋值
在 `langchain.agents.create_agent` 的 ToolNode 路径下**不可靠**：工具确实执行、
ToolMessage 确实生成，但 callback 一条都没触发。因此改用显式代理，把观察点放在
**底层工具函数的调用边界上**，而不是依赖框架回调。

契约等价要求（有测试逐条验证）：
name / description / args_schema / response_format / return_direct / metadata / tags
全部与原工具一致；`content_and_artifact` 的 (content, artifact) 元组原样透传。
"""

import time

from langchain_core.tools import BaseTool

from tool_envelope import compute_hash


class ToolLifecycleProxy(BaseTool):
    """包住一个 BaseTool，在**底层函数执行前后**记录 executed / observed / failed。

    - `executed`：底层 `_run` / `_arun` 开始**之前**记录；
    - `observed`：底层返回、即将成为 ToolMessage 时记录；
    - `failed`：底层抛异常时记录（绝不伪装成成功）。
    代理自身的记录异常一律吞掉，**不得**把工具成功改写成失败、也不得反过来。
    """

    inner: object = None
    trace: object = None
    guard: object = None
    allowed: object = None
    reconciler: object = None

    def __init__(self, inner, trace=None, guard=None, allowed=None,
                 reconciler=None, **kw):
        super().__init__(
            name=inner.name,
            description=inner.description,
            args_schema=getattr(inner, "args_schema", None),
            return_direct=getattr(inner, "return_direct", False),
            metadata=getattr(inner, "metadata", None),
            tags=getattr(inner, "tags", None),
            response_format=getattr(inner, "response_format", "content"),
            **kw)
        object.__setattr__(self, "inner", inner)
        object.__setattr__(self, "trace", trace)
        object.__setattr__(self, "guard", guard)
        object.__setattr__(self, "allowed", set(allowed) if allowed else None)
        object.__setattr__(self, "reconciler", reconciler)

    # ---- 记录helpers：自身异常绝不影响工具语义 ----
    def _safe(self, fn, *a, **k):
        try:
            return fn(*a, **k)
        except Exception:
            return None

    def _call_id(self, run_manager):
        rid = getattr(run_manager, "run_id", None)
        return str(rid) if rid else f"noid-{id(run_manager)}"

    def _before(self, kwargs, call_id):
        # 未授权工具：在底层函数**之前**阻断
        if self.allowed is not None and self.name not in self.allowed:
            raise PermissionError(f"未授权工具在执行前被阻断：{self.name}")
        # 重复/循环/轮数护栏：同样在底层函数之前
        if self.guard is not None:
            self.guard.before_tool_round(self.name, kwargs)
        # 【§3】start 事件写入失败 → **不允许底层工具执行**，fail-closed
        if self.trace is not None:
            try:
                self.trace.record_tool_start(tool_call_id=call_id,
                                             tool_name=self.name, arguments=kwargs)
            except Exception as e:
                from pilot.lifecycle import TRACE_START_FAILED, LifecycleError
                raise LifecycleError(TRACE_START_FAILED,
                                     {"tool": self.name, "error": type(e).__name__,
                                      "message": str(e)[:200]}) from e
        rec = object.__getattribute__(self, "reconciler")
        if rec is not None:
            rec.mark_executed(call_id, self.name)       # executed：底层函数开始之前
        return time.time()

    def _after_ok(self, result, call_id, started):
        """底层函数正常返回 → 只记 `tool_returned`。
        **绝不**在此推测 observed —— observed 必须等真实 ToolMessage 进入消息状态。"""
        content, artifact = self._split(result)
        text = content if isinstance(content, str) else str(content)
        rhash = compute_hash(text)
        rec = object.__getattribute__(self, "reconciler")
        if rec is not None:
            r = rec.mark_returned(call_id, self.name, result_hash=rhash)
            r["progress_extra"] = self._progress_signals(content, artifact)
        if self.trace is not None:
            try:
                self.trace.record_tool_returned(
                    tool_call_id=call_id, tool_name=self.name, result=text,
                    structured=artifact is not None, started_at=started,
                    result_hash=rhash)
            except Exception:
                # 【§3】工具**已执行**后 end 写入失败：不得声称未执行
                if rec is not None:
                    rec.trace_incomplete = True
                    rec._flag("trace_incomplete", None, self.name)
        return result

    def _after_fail(self, exc, call_id, started):
        rec = object.__getattribute__(self, "reconciler")
        if rec is not None:
            rec.mark_failed(call_id, self.name, error_type=type(exc).__name__)
        if self.trace is not None:
            try:
                self.trace.record_tool_end(tool_call_id=call_id, tool_name=self.name,
                                           status="error",
                                           error_type=type(exc).__name__,
                                           returned_tool_message=False,
                                           started_at=started)
            except Exception:
                if rec is not None:
                    rec.trace_incomplete = True
        # 失败不产生任何进展信号（进展只在 observed 时由对账器给出）

    @staticmethod
    def _split(result):
        if (getattr(result, "__class__", None) is tuple and len(result) == 2):
            return result[0], result[1]
        return result, None

    @staticmethod
    def _progress_signals(content, artifact):
        """只有执行/观察侧的确定性新结果才算进展（Addendum 1 §4）。"""
        sig = []
        text = content if isinstance(content, str) else str(content)
        sig.append(f"result_hash:{compute_hash(text)}")
        if isinstance(artifact, dict):
            data = artifact.get("data") if isinstance(artifact.get("data"), dict) else {}
            for k in ("evidence_id", "dataset_id", "dataset_version"):
                if data.get(k):
                    sig.append(f"{k}:{data[k]}")
            for k in ("source_ids", "datasets", "dataset_ids"):
                for v in (data.get(k) or []):
                    sig.append(f"{k}:{v}")
            if data.get("content_level"):
                sig.append(f"content_level:{data['content_level']}")
            if data.get("retrieval_status"):
                sig.append(f"obs:{compute_hash(data)}")
        return sig

    @staticmethod
    def _tool_args(args, kwargs):
        """取出业务参数（剔除框架注入的 run_manager / config）。"""
        d = {k: v for k, v in kwargs.items() if k not in ("run_manager", "config")}
        if not d and args:
            return args[0] if isinstance(args[0], dict) else {"_": args[0]}
        return d

    @staticmethod
    def _forward_kwargs(fn, config, run_manager, kwargs):
        """按底层 `_run/_arun` 的真实签名决定转发哪些框架参数。
        LangChain 各版本对 config / run_manager 的要求不同，硬编码会碎。"""
        import inspect

        out = dict(kwargs)
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            return out
        has_var_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD
                         for p in params.values())
        if "config" in params or has_var_kw:
            out["config"] = config
        if "run_manager" in params:
            out["run_manager"] = run_manager
        return out

    # ---- 同步 ----
    def _run(self, *args, config=None, run_manager=None, **kwargs):
        call_id = self._call_id(run_manager)
        started = self._before(self._tool_args(args, kwargs), call_id)
        inner = object.__getattribute__(self, "inner")
        try:
            fwd = self._forward_kwargs(inner._run, config, run_manager, kwargs)
            res = inner._run(*args, **fwd)
        except Exception as e:
            self._after_fail(e, call_id, started)
            raise
        return self._after_ok(res, call_id, started)

    # ---- 异步 ----
    async def _arun(self, *args, config=None, run_manager=None, **kwargs):
        call_id = self._call_id(run_manager)
        started = self._before(self._tool_args(args, kwargs), call_id)
        inner = object.__getattribute__(self, "inner")
        try:
            fwd = self._forward_kwargs(inner._arun, config, run_manager, kwargs)
            res = await inner._arun(*args, **fwd)
        except Exception as e:
            self._after_fail(e, call_id, started)
            raise
        return self._after_ok(res, call_id, started)


def wrap_tools(tools, *, trace=None, guard=None, allowed=None, reconciler=None):
    """把一组 BaseTool 换成代理。已是代理的原样返回。"""
    out = []
    for t in tools:
        out.append(t if isinstance(t, ToolLifecycleProxy)
                   else ToolLifecycleProxy(t, trace=trace, guard=guard,
                                           allowed=allowed, reconciler=reconciler))
    return out


def assert_contract_equivalent(proxy, original):
    """证明代理与原工具契约等价 —— 供启动前检查与测试共用。"""
    fields = ("name", "description", "return_direct", "response_format")
    for f in fields:
        a, b = getattr(proxy, f, None), getattr(original, f, None)
        if a != b:
            raise AssertionError(f"契约不等价 {f}: {a!r} != {b!r}")
    if getattr(proxy, "args_schema", None) is not getattr(original, "args_schema", None):
        raise AssertionError("args_schema 不是同一对象")
    return True
