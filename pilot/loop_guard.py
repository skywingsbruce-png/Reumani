"""Pilot 专用 Executor 循环守卫（A.6.5 §7）。

预算闸门不再是唯一的循环终止机制。本守卫是**更严格的内部安全边界**，
不扩大预算、不改评分、不改 v2 协议：
  - 工具轮数上限（保守值，独立于 v2 的 16 次模型调用上限）
  - 重复调用检测（tool_name + arguments_hash，不只看工具名）
  - 无进展检测（确定性信号，不让 LLM 自己判断"是否有进展"）
触发时产生结构化事件并停止本次 Executor，**不生成伪成功答案**，交外层 fail-closed。
"""

import time

from tool_envelope import compute_hash

# 冻结的 Pilot 内部安全值。与以下三者都不同：
#   v2 Executor 模型调用上限 16 / 外层 max_iterations=2 / 单题总调用 21
MAX_TOOL_ROUNDS = 8
REPEAT_WARN_AT = 2        # 完全相同调用连续 2 次 → warning
REPEAT_BLOCK_AT = 3       # 连续 3 次 → 下一次执行前阻断
CYCLE_LEN = 2             # A→B→A→B 视为一个 2 长度循环
CYCLE_BLOCK_REPEATS = 2   # 该循环再重复一次 → 阻断
NO_PROGRESS_ROUNDS = 3    # 连续多少轮没有新进展信号 → 停止


class LoopGuardTriggered(RuntimeError):
    """循环守卫触发：结构化停止，不是伪成功。"""

    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail or {}


def call_signature(tool_name, arguments):
    """规范化签名：工具名 + 参数 hash。**不能只看工具名** —— 同名不同参可能是合理的。"""
    return f"{tool_name}::{compute_hash(arguments)[:16]}"


class ExecutorLoopGuard:
    def __init__(self, *, max_tool_rounds=MAX_TOOL_ROUNDS,
                 no_progress_rounds=NO_PROGRESS_ROUNDS):
        self.max_tool_rounds = int(max_tool_rounds)
        self.no_progress_rounds = int(no_progress_rounds)
        self.rounds = 0
        self.signatures = []          # 按顺序记录每轮的调用签名
        self.progress_signals = set()  # 见过的确定性进展信号
        self.rounds_without_progress = 0
        self.events = []

    # ---- 事件 ----
    def _event(self, kind, **kw):
        e = {"event": kind, "round": self.rounds, "ts": time.time()}
        e.update(kw)
        self.events.append(e)
        return e

    # ---- 进展信号（确定性，不问 LLM）----
    def record_progress(self, signals):
        """signals：本轮产生的确定性信号集合，例如
        新 evidence_id / 新 dataset_id / 新结构化 Observation hash / 新工具结果 hash /
        状态字段变化。出现任一**新**信号即视为有进展。"""
        new = {s for s in (signals or []) if s and s not in self.progress_signals}
        if new:
            self.progress_signals |= new
            self.rounds_without_progress = 0
            self._event("progress", new_signals=sorted(new)[:8])
            return True
        self.rounds_without_progress += 1
        self._event("no_progress_round", streak=self.rounds_without_progress)
        if self.rounds_without_progress >= self.no_progress_rounds:
            self._event("loop_guard_triggered", reason="no_progress",
                        streak=self.rounds_without_progress)
            raise LoopGuardTriggered("no_progress",
                                     {"rounds_without_progress":
                                      self.rounds_without_progress})
        return False

    # ---- 每次工具执行之前调用 ----
    def before_tool_round(self, tool_name, arguments):
        sig = call_signature(tool_name, arguments)

        # 1) 轮数上限
        if self.rounds + 1 > self.max_tool_rounds:
            self._event("loop_guard_triggered", reason="max_tool_rounds",
                        limit=self.max_tool_rounds)
            raise LoopGuardTriggered("max_tool_rounds",
                                     {"limit": self.max_tool_rounds})

        # 2) 连续重复（完全相同的 name+args）
        streak = 1
        for prev in reversed(self.signatures):
            if prev == sig:
                streak += 1
            else:
                break
        if streak >= REPEAT_BLOCK_AT:
            self._event("loop_guard_triggered", reason="repeated_call",
                        signature=sig, streak=streak)
            raise LoopGuardTriggered("repeated_call",
                                     {"signature": sig, "streak": streak})
        if streak >= REPEAT_WARN_AT:
            self._event("repeat_warning", signature=sig, streak=streak)

        # 3) 非连续循环 A→B→A→B
        seq = self.signatures + [sig]
        if len(seq) >= CYCLE_LEN * (CYCLE_BLOCK_REPEATS + 1):
            block = seq[-CYCLE_LEN:]
            reps = 1
            i = len(seq) - CYCLE_LEN
            while i - CYCLE_LEN >= 0 and seq[i - CYCLE_LEN:i] == block:
                reps += 1
                i -= CYCLE_LEN
            if reps > CYCLE_BLOCK_REPEATS:
                self._event("loop_guard_triggered", reason="cycle",
                            cycle=block, repeats=reps)
                raise LoopGuardTriggered("cycle", {"cycle": block, "repeats": reps})
            if reps == CYCLE_BLOCK_REPEATS:
                self._event("cycle_warning", cycle=block, repeats=reps)

        self.rounds += 1
        self.signatures.append(sig)
        self._event("tool_round", tool_name=tool_name, signature=sig)
        return sig

    def summary(self):
        return {"tool_rounds": self.rounds, "max_tool_rounds": self.max_tool_rounds,
                "distinct_signatures": len(set(self.signatures)),
                "rounds_without_progress": self.rounds_without_progress,
                "progress_signal_count": len(self.progress_signals),
                "events": self.events[-50:]}
