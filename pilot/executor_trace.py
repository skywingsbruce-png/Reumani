"""Pilot Executor 事件轨迹（A.6.5 §2/§3/§8）。有界、脱敏、append-only。

默认**只保存** hash / 长度 / 枚举 / 工具名 / 白名单元数据。
默认**不保存**：完整 Prompt、完整模型正文、完整工具参数、API key、
Authorization、Cookie、.env、患者信息、用户绝对路径。

即使 `agent.invoke()` 最终抛异常，已发生的事件也已逐条落盘（append-only）。
"""

import json
import threading
import time
from pathlib import Path

from tool_envelope import HASH_ALGORITHM, compute_hash

MAX_SNIPPET = 120          # 允许的脱敏短片段长度上限
RESPONSE_META_WHITELIST = ("model_name", "model", "finish_reason", "system_fingerprint",
                           "service_tier", "stop_reason")

# 四类工具观察来源 —— 不得再用 selected 推断 called
SELECTED, REQUESTED, EXECUTED, OBSERVED = "selected", "requested", "executed", "observed"


def _sanitize_snippet(s):
    """短片段脱敏：截断 + 去掉疑似密钥/认证头/绝对路径。"""
    import re
    t = str(s or "")[:MAX_SNIPPET]
    t = re.sub(r"sk-[A-Za-z0-9_\-]{6,}", "[REDACTED]", t)
    t = re.sub(r"(?i)(authorization|cookie|api[_-]?key)\s*[:=]\s*\S+", "[REDACTED]", t)
    t = re.sub(r"[A-Za-z]:\\\\?[^\s\"']+", "[PATH]", t)
    t = re.sub(r"/(home|Users)/[^\s\"']+", "[PATH]", t)
    return t


class ExecutorTrace:
    """append-only JSONL；每条事件写完即 flush，异常也不丢。"""

    def __init__(self, path, run_id):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self._lock = threading.Lock()
        self._i = 0
        self.selected_tools = []
        self.requested, self.executed, self.observed = [], [], []

    def _append(self, rec):
        with self._lock:
            self._i += 1
            rec["event_index"] = self._i
            rec["run_id"] = self.run_id
            rec.setdefault("ts", time.time())
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                f.flush()
            return rec

    # ---- 来源 1：Retriever 允许使用 ----
    def record_selected(self, tool_names):
        self.selected_tools = list(tool_names or [])
        return self._append({"event": SELECTED, "tools": self.selected_tools})

    # ---- 模型响应 ----
    def record_model_response(self, *, outer_iteration, executor_call_index, provider,
                              model, role, response, input_tokens=None,
                              output_tokens=None, next_graph_node=None,
                              termination_reason=None):
        content = getattr(response, "content", "") or ""
        tool_calls = list(getattr(response, "tool_calls", None) or [])
        invalid = list(getattr(response, "invalid_tool_calls", None) or [])
        meta = getattr(response, "response_metadata", None) or {}
        rec = {
            "event": "model_response",
            "outer_iteration": outer_iteration,
            "executor_call_index": executor_call_index,
            "provider": provider, "model": model, "role": role,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "finish_reason": meta.get("finish_reason") or meta.get("stop_reason"),
            "content_present": bool(content),
            "content_length": len(content),
            "content_hash": compute_hash(content) if content else None,
            "hash_algorithm": HASH_ALGORITHM,
            "tool_calls_count": len(tool_calls),
            "tool_names": [tc.get("name") for tc in tool_calls if isinstance(tc, dict)],
            "arguments_hash": [compute_hash(tc.get("args")) for tc in tool_calls
                               if isinstance(tc, dict)],
            "invalid_tool_calls_count": len(invalid),
            "response_metadata": {k: meta[k] for k in RESPONSE_META_WHITELIST
                                  if k in meta},
            "next_graph_node": next_graph_node,
            "termination_reason": termination_reason,
        }
        for tc in tool_calls:
            if isinstance(tc, dict) and tc.get("name"):
                self.requested.append(tc["name"])
        return self._append(rec)

    # ---- 工具执行 ----
    def record_tool_start(self, *, tool_call_id, tool_name, arguments):
        args = arguments if isinstance(arguments, dict) else {"_": arguments}
        self.executed.append(tool_name)
        return self._append({
            "event": "tool_start",
            "tool_call_id_hash": compute_hash(tool_call_id)[:16],
            "tool_name": tool_name,
            "argument_keys": sorted(args.keys()),
            "arguments_hash": compute_hash(args),
            "started_at": time.time(),
        })

    def record_tool_end(self, *, tool_call_id, tool_name, status, result=None,
                        structured=None, error_type=None, returned_tool_message=None,
                        started_at=None):
        text = "" if result is None else (result if isinstance(result, str) else str(result))
        if returned_tool_message:
            self.observed.append(tool_name)
        return self._append({
            "event": "tool_end",
            "tool_call_id_hash": compute_hash(tool_call_id)[:16],
            "tool_name": tool_name,
            "completed_at": time.time(),
            "elapsed_s": (round(time.time() - started_at, 3) if started_at else None),
            "status": status,                        # ok / error / blocked
            "structured": ("structured" if structured else
                           ("legacy" if structured is False else None)),
            "result_length": len(text),
            "result_hash": compute_hash(text) if text else None,
            "hash_algorithm": HASH_ALGORITHM,
            "error_type": error_type,
            "returned_tool_message": bool(returned_tool_message),
        })

    def record_tool_returned(self, *, tool_call_id, tool_name, result=None,
                             structured=None, started_at=None, result_hash=None):
        """底层工具函数**正常返回**（≠ observed）。"""
        text = "" if result is None else str(result)
        return self._append({
            "event": "tool_returned",
            "tool_call_id_hash": compute_hash(tool_call_id)[:16],
            "tool_name": tool_name, "completed_at": time.time(),
            "elapsed_s": (round(time.time() - started_at, 3) if started_at else None),
            "structured": ("structured" if structured else "legacy"),
            "result_length": len(text),
            "result_hash": result_hash or (compute_hash(text) if text else None),
            "hash_algorithm": HASH_ALGORITHM,
        })

    def record_observed(self, *, tool_call_id, tool_name, result_hash=None):
        """**真实 ToolMessage 已进入 Agent 消息状态**，且 tool_call_id 匹配成功。"""
        self.observed.append(tool_name)
        return self._append({
            "event": OBSERVED,
            "tool_call_id_hash": compute_hash(tool_call_id)[:16],
            "tool_name": tool_name, "result_hash": result_hash,
            "hash_algorithm": HASH_ALGORITHM,
        })

    def record_guard(self, reason, detail=None):
        return self._append({"event": "loop_guard_triggered", "reason": reason,
                             "detail": detail or {}})

    # ---- §3 一致性检查 ----
    def consistency(self):
        sel, req, exe, obs = (set(self.selected_tools), list(self.requested),
                              list(self.executed), list(self.observed))
        from collections import Counter
        cr, ce, co = Counter(req), Counter(exe), Counter(obs)
        return {
            "selected": sorted(sel),
            "requested": sorted(cr), "executed": sorted(ce), "observed": sorted(co),
            "requested_not_executed": sorted((cr - ce).elements()),
            "executed_without_tool_message": sorted((ce - co).elements()),
            "observed_without_request": sorted((co - cr).elements()),
            "requested_outside_selected": sorted(t for t in cr if t not in sel),
            "unauthorized_executed": sorted(t for t in ce if t not in sel),
        }


def build_failure_manifest(*, run_id, failure_stage, failure_reason, trace,
                           budget_summary, guard_summary=None, evidence_cards=None,
                           extra=None):
    """失败诊断 Manifest（§8）。**不是**科研结论 Manifest，但必须可审计。"""
    from manifest_safety import sanitize_manifest
    cons = trace.consistency() if trace else {}
    m = {
        "run_id": run_id,
        "shadow_status": "not_run_due_to_upstream_failure",
        "manifest_kind": "failure_diagnostic",
        "status": "failed",
        "failure_stage": failure_stage,
        "failure_reason": failure_reason,
        "selected_tools": cons.get("selected", []),
        "tool_events": [{"requested": cons.get("requested", []),
                         "executed": cons.get("executed", []),
                         "observed": cons.get("observed", [])}],
        "unauthorized_tool_calls": cons.get("unauthorized_executed", []),
        "evidence_cards": evidence_cards or [],
        "claims": [],
        "note": json.dumps({"consistency": cons,
                            "budget": budget_summary,
                            "loop_guard": guard_summary or {},
                            **(extra or {})}, ensure_ascii=False, default=str)[:4000],
    }
    return sanitize_manifest(m)
