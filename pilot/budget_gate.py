"""Round 2 Pilot 费用/调用闸门（仅供 pilot runner 使用，绝不进 CI 付费路径）。

职责单一：累计 token → 折算美元 → 任一硬上限超限立刻抛 BudgetExceeded 中止。
不修改任何系统模块；通过给已有 LLM 对象挂 callbacks 生效（运行时挂载，不改代码）。
铁律：只做计量与中止，不做重试、不自动提高预算、不吞异常。
"""

import time

# 每百万 token 单价（美元）。Opus 价格来自 Anthropic 官方定价。
# ⚠️ deepseek-chat 为**估计值**，运行前须从账单页确认；报告中必须标注为估计。
PRICES_PER_MTOK = {
    "claude-opus-4-8": {"input": 5.00, "output": 25.00, "source": "anthropic_official"},
    "deepseek-chat": {"input": 0.27, "output": 1.10, "source": "ESTIMATE_UNVERIFIED"},
}
_DEFAULT = {"input": 5.00, "output": 25.00, "source": "UNKNOWN_MODEL_ASSUME_OPUS"}


class BudgetExceeded(RuntimeError):
    """硬上限触发。调用方必须让它向上传播——不得捕获后继续付费运行。"""


def price_for(model_id):
    for k, v in PRICES_PER_MTOK.items():
        if k in (model_id or ""):
            return v
    return _DEFAULT


class BudgetGate:
    """LangChain callback：on_llm_end 累计 usage，超限即抛。

    max_usd / max_calls_global / max_calls_per_task / task_timeout_s 均为硬上限。
    """

    def __init__(self, *, max_usd, max_calls_global, max_calls_per_task, task_timeout_s):
        self.max_usd = float(max_usd)
        self.max_calls_global = int(max_calls_global)
        self.max_calls_per_task = int(max_calls_per_task)
        self.task_timeout_s = float(task_timeout_s)
        self.usd = 0.0
        self.calls_global = 0
        self.calls_task = 0
        self.in_tok = 0
        self.out_tok = 0
        self.per_task = {}            # task_id -> {calls,in,out,usd,seconds}
        self.task_id = None
        self._t0 = None
        self.stopped_reason = None

    # ---- 任务边界 ----
    def start_task(self, task_id):
        self.task_id = task_id
        self.calls_task = 0
        self._t0 = time.monotonic()
        self.per_task.setdefault(task_id, {"calls": 0, "in": 0, "out": 0, "usd": 0.0,
                                           "seconds": 0.0, "timed_out": False})

    def end_task(self):
        if self.task_id is not None and self._t0 is not None:
            self.per_task[self.task_id]["seconds"] = round(time.monotonic() - self._t0, 2)
        self.task_id, self._t0 = None, None

    def elapsed(self):
        return 0.0 if self._t0 is None else time.monotonic() - self._t0

    def check_timeout(self):
        """由 runner 在任务运行中/结束时调用；超时是硬上限之一。"""
        if self._t0 is not None and self.elapsed() > self.task_timeout_s:
            if self.task_id in self.per_task:
                self.per_task[self.task_id]["timed_out"] = True
            self._stop(f"task_timeout: {self.task_id} 超过 {self.task_timeout_s}s")
        return True

    # ---- 计量 ----
    def record(self, model_id, in_tok, out_tok):
        """纯函数式入口，便于测试与非 LangChain 路径复用。"""
        p = price_for(model_id)
        cost = in_tok / 1e6 * p["input"] + out_tok / 1e6 * p["output"]
        self.usd += cost
        self.in_tok += in_tok
        self.out_tok += out_tok
        self.calls_global += 1
        self.calls_task += 1
        if self.task_id is not None:
            d = self.per_task[self.task_id]
            d["calls"] += 1
            d["in"] += in_tok
            d["out"] += out_tok
            d["usd"] = round(d["usd"] + cost, 6)
        self._enforce()
        return cost

    def _enforce(self):
        if self.usd > self.max_usd:
            self._stop(f"budget_usd: ${self.usd:.4f} > ${self.max_usd:.2f}")
        if self.calls_global > self.max_calls_global:
            self._stop(f"max_calls_global: {self.calls_global} > {self.max_calls_global}")
        if self.calls_task > self.max_calls_per_task:
            self._stop(f"max_calls_per_task[{self.task_id}]: "
                       f"{self.calls_task} > {self.max_calls_per_task}")

    def _stop(self, reason):
        self.stopped_reason = reason
        raise BudgetExceeded(reason)

    # ---- LangChain BaseCallbackHandler 接口（鸭子类型，避免依赖具体基类）----
    raise_error = True
    ignore_llm = False
    ignore_chain = True
    ignore_agent = True
    ignore_retriever = True
    ignore_chat_model = False
    ignore_retry = True
    ignore_custom_event = True

    def on_llm_end(self, response, **kwargs):
        model_id, i, o = _extract_usage(response)
        self.record(model_id, i, o)

    def __getattr__(self, name):
        # 其它 on_* 回调一律 no-op，避免 LangChain 版本差异导致 AttributeError
        if name.startswith("on_"):
            return lambda *a, **k: None
        raise AttributeError(name)

    def summary(self):
        return {"usd_estimated": round(self.usd, 4), "calls": self.calls_global,
                "input_tokens": self.in_tok, "output_tokens": self.out_tok,
                "per_task": self.per_task, "stopped_reason": self.stopped_reason,
                "limits": {"max_usd": self.max_usd, "max_calls_global": self.max_calls_global,
                           "max_calls_per_task": self.max_calls_per_task,
                           "task_timeout_s": self.task_timeout_s},
                "price_note": "deepseek 单价为未核实估计值；opus 为官方定价"}


def _extract_usage(response):
    """从 LLMResult 取 (model_id, input_tokens, output_tokens)；取不到则回退 0。
    取不到 usage 不静默当成 0 成本——调用方会在报告里看到 calls 与 tokens 不匹配。"""
    model_id, i, o = "", 0, 0
    llm_out = getattr(response, "llm_output", None) or {}
    if isinstance(llm_out, dict):
        model_id = llm_out.get("model_name") or llm_out.get("model") or ""
        tu = llm_out.get("token_usage") or llm_out.get("usage") or {}
        if isinstance(tu, dict):
            i = tu.get("prompt_tokens") or tu.get("input_tokens") or 0
            o = tu.get("completion_tokens") or tu.get("output_tokens") or 0
    if not (i or o):
        try:
            msg = response.generations[0][0].message
            um = getattr(msg, "usage_metadata", None) or {}
            i = um.get("input_tokens", 0) or 0
            o = um.get("output_tokens", 0) or 0
            model_id = model_id or (getattr(msg, "response_metadata", {}) or {}).get("model", "")
        except Exception:
            pass
    return model_id, int(i or 0), int(o or 0)
