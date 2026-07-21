"""工具生命周期对账（A.6.6.1 §1/§2/§3/§5）。

五个阶段，**互不推测**：
  requested      模型产生结构化 tool_call
  executed       底层工具函数开始
  tool_returned  底层工具函数正常返回
  failed         底层工具函数抛异常
  observed       **真实 ToolMessage 已进入 Agent 消息状态**，且 tool_call_id 匹配成功

`observed` 只能由看到真实 ToolMessage 产生。以下**都不能**用来推测 observed：
工具函数正常返回 / result 非空 / artifact 存在 / callback 成功 / "即将生成 ToolMessage"。
"""

import time

from tool_envelope import compute_hash

REQUESTED, EXECUTED, RETURNED, FAILED, OBSERVED = (
    "requested", "executed", "tool_returned", "failed", "observed")

# 不一致标记
REQUESTED_NOT_EXECUTED = "requested_not_executed"
EXECUTION_INCOMPLETE = "execution_incomplete"
RESULT_NOT_OBSERVED = "tool_result_not_observed"
ORPHAN_OBSERVATION = "orphan_observation"
LIFECYCLE_CONFLICT = "lifecycle_conflict"
DUPLICATE_EXECUTION = "duplicate_execution"
INCONSISTENT = "tool_lifecycle_inconsistent"

# trace 自身失败
TRACE_START_FAILED = "trace_start_failed"
TRACE_INCOMPLETE = "trace_incomplete"
OBSERVATION_TRACE_FAILED = "observation_trace_failed"


class LifecycleError(RuntimeError):
    """生命周期 fail-closed。"""

    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail or {}


def cid_hash(tool_call_id):
    return compute_hash(str(tool_call_id))[:16]


class LifecycleReconciler:
    """按 tool_call_id hash 对账五个阶段。"""

    def __init__(self, trace=None, guard=None):
        self.trace = trace
        self.guard = guard
        self.calls = {}                 # cid_hash -> 阶段记录
        self.seen_tool_messages = set()  # 已计数的 ToolMessage，避免重复
        self.inconsistencies = []
        self.trace_incomplete = False
        self.observation_trace_failed = False

    # ---- 阶段登记 ----
    def _rec(self, cid):
        return self.calls.setdefault(cid, {
            "tool_call_id_hash": cid, "tool_name": None,
            REQUESTED: 0, EXECUTED: 0, RETURNED: 0, FAILED: 0, OBSERVED: 0,
            "result_hash": None, "ts": time.time()})

    def mark_requested(self, tool_call_id, tool_name):
        r = self._rec(cid_hash(tool_call_id))
        r["tool_name"] = tool_name
        r[REQUESTED] += 1
        return r

    def mark_executed(self, tool_call_id, tool_name):
        cid = cid_hash(tool_call_id)
        r = self._rec(cid)
        r["tool_name"] = r["tool_name"] or tool_name
        r[EXECUTED] += 1
        if r[EXECUTED] > 1:             # 同一 tool_call_id 被执行多次 → fail-closed
            self._flag(DUPLICATE_EXECUTION, cid, r["tool_name"])
            raise LifecycleError(DUPLICATE_EXECUTION,
                                 {"tool_call_id_hash": cid, "count": r[EXECUTED]})
        return r

    def mark_returned(self, tool_call_id, tool_name, result_hash=None):
        r = self._rec(cid_hash(tool_call_id))
        r[RETURNED] += 1
        r["result_hash"] = result_hash
        return r

    def mark_failed(self, tool_call_id, tool_name, error_type=None):
        r = self._rec(cid_hash(tool_call_id))
        r[FAILED] += 1
        r["error_type"] = error_type
        return r

    # ---- observed：只认真实 ToolMessage ----
    def reconcile_messages(self, messages):
        """扫描 Agent 消息状态里的 ToolMessage，匹配 tool_call_id 后才记 observed。
        同一个 ToolMessage 不重复计数。"""
        newly = []
        for m in _iter_messages(messages):
            if type(m).__name__ != "ToolMessage":
                continue
            key = id(m)
            tcid = getattr(m, "tool_call_id", None)
            uniq = f"{tcid}:{getattr(m, 'content', '')[:64]}" if tcid else key
            if uniq in self.seen_tool_messages:
                continue
            self.seen_tool_messages.add(uniq)
            if tcid is None:
                self._flag(ORPHAN_OBSERVATION, None, None)
                continue
            cid = cid_hash(tcid)
            r = self.calls.get(cid)
            if r is None or r[REQUESTED] == 0:
                self._flag(ORPHAN_OBSERVATION, cid, getattr(m, "name", None))
                continue
            status = str(getattr(m, "status", "") or "").lower()
            if r[FAILED] and status != "error":
                self._flag(LIFECYCLE_CONFLICT, cid, r["tool_name"])
            r[OBSERVED] += 1
            newly.append(r)
            try:
                if self.trace is not None:
                    self.trace.record_observed(tool_call_id=tcid,
                                               tool_name=r["tool_name"],
                                               result_hash=r.get("result_hash"))
            except Exception:
                self.observation_trace_failed = True
                self._flag(OBSERVATION_TRACE_FAILED, cid, r["tool_name"])
        # §5：只有被真实 ToolMessage 观察到的**首次出现**结果才算进展
        if self.guard is not None:
            signals = []
            for r in newly:
                if r[FAILED]:
                    continue                      # 异常不算进展
                if self.trace_incomplete:
                    continue                      # trace 不完整不得假定有进展
                if r.get("result_hash"):
                    signals.append(f"observed_result:{r['result_hash']}")
                for s in r.get("progress_extra") or []:
                    signals.append(s)
            if newly:
                self.guard.record_progress(signals)
        return newly

    def _flag(self, kind, cid, tool_name):
        self.inconsistencies.append({"kind": kind, "tool_call_id_hash": cid,
                                     "tool_name": tool_name})

    # ---- 进入下一次模型调用之前的强制检查 ----
    def assert_consistent_before_next_model_call(self):
        """若上一轮 requested 没有对应 observed → tool_lifecycle_inconsistent，fail-closed。"""
        pending = [r for r in self.calls.values()
                   if r[REQUESTED] and not r[OBSERVED] and not r[FAILED]]
        if pending:
            for r in pending:
                if not r[EXECUTED]:
                    self._flag(REQUESTED_NOT_EXECUTED, r["tool_call_id_hash"],
                               r["tool_name"])
                elif not r[RETURNED]:
                    self._flag(EXECUTION_INCOMPLETE, r["tool_call_id_hash"],
                               r["tool_name"])
                else:
                    self._flag(RESULT_NOT_OBSERVED, r["tool_call_id_hash"],
                               r["tool_name"])
            raise LifecycleError(INCONSISTENT, {"pending": len(pending),
                                                "flags": self.inconsistencies[-3:]})
        return True

    def summary(self):
        agg = {k: 0 for k in (REQUESTED, EXECUTED, RETURNED, FAILED, OBSERVED)}
        for r in self.calls.values():
            for k in agg:
                agg[k] += r[k]
        return {"counts": agg, "calls": len(self.calls),
                "inconsistencies": self.inconsistencies,
                "trace_incomplete": self.trace_incomplete,
                "observation_trace_failed": self.observation_trace_failed}


def _iter_messages(messages):
    if messages is None:
        return []
    if isinstance(messages, dict):
        messages = messages.get("messages", [])
    if not isinstance(messages, (list, tuple)):
        return []
    return messages
