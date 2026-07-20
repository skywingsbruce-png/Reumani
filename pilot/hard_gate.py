"""Round 2 硬闸门（Commit A.6.2）。

与旧 BudgetGate 的根本区别：
- 旧：callback `on_llm_end` 抛异常 → **LangChain 吞掉** → 网络请求已经发生、已经计费 → 软中止。
- 新：在进入底层 `.invoke()/.ainvoke()/.stream()/.astream()/.batch()` **之前**同步检查并原子预留额度，
      任何一项不过就在**网络请求发生前**抛 BudgetExceeded。callback 只负责调用后读 usage 结算。

铁律：
- 不依赖 callback 异常传播；
- 无法可靠包装的付费对象 → Pilot 拒绝启动，不降级为软闸门；
- usage 缺失 → fail-closed，保留最坏费用预留，等人工核对；
- 异常不清空 reservation；
- 未知模型 / 未知价格 / 缺显式开关 → 拒绝。
"""

import json
import os
import threading
import time
from pathlib import Path

# ---- 单价（美元 / 每百万 token）。未列出的模型一律拒绝，不回退猜测。----
PRICES = {
    "claude-opus-4-8": {"input": 5.00, "output": 25.00, "source": "anthropic_official"},
    "deepseek-chat": {"input": 0.27, "output": 1.10, "source": "ESTIMATE_UNVERIFIED"},
    "fake-model": {"input": 0.0, "output": 0.0, "source": "test_only"},
}

# 两个必须同时显式开启的运行开关
ENV_PAID = "REUMANI_PILOT_PAID"          # 必须 == "1"
ENV_CONFIRM = "REUMANI_PILOT_CONFIRM"    # 必须 == 当前 stage 名


class BudgetExceeded(RuntimeError):
    """硬上限：在网络请求发生【之前】抛出。"""


class GateConfigError(RuntimeError):
    """fail-closed 配置错误（未知模型/未知价格/缺开关/无法包装）。"""


def price_for(model_id):
    for k, v in PRICES.items():
        if k and k in (model_id or ""):
            return v
    raise GateConfigError(f"未知模型或未知价格，拒绝调用：{model_id!r}")


def estimate_input_tokens(payload):
    """保守估算：按字符数 / 3（比常见的 /4 更保守，宁可高估）。"""
    try:
        s = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False,
                                                                default=str)
    except Exception:
        s = str(payload)
    return max(1, len(s) // 3)


class Ledger:
    """只追加的 JSONL 账本。重复 run_id 只追加，绝不覆盖既有事件。"""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, event):
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def events(self):
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out

    def open_reservations(self):
        """进程重启后恢复：reserved 但未 reconciled/released 的额度。"""
        res, done = {}, set()
        for e in self.events():
            if e.get("event") == "reserved":
                res[e["call_uid"]] = e
            elif e.get("event") in ("reconciled", "released", "usage_unknown", "failed_maybe_billed"):
                done.add(e["call_uid"])
        return {k: v for k, v in res.items() if k not in done}


class HardBudgetGate:
    """调用前检查 + 原子预留；调用后结算。线程安全。"""

    def __init__(self, *, stage, ledger_path, max_usd_global, max_usd_stage, max_usd_task,
                 max_calls_global, max_calls_task, max_calls_per_model, task_timeout_s,
                 max_retries, default_max_tokens, allow_ci=False):
        self.stage = stage
        self.ledger = Ledger(ledger_path)
        self.lim = dict(max_usd_global=float(max_usd_global), max_usd_stage=float(max_usd_stage),
                        max_usd_task=float(max_usd_task),
                        max_calls_global=int(max_calls_global), max_calls_task=int(max_calls_task),
                        max_calls_per_model=dict(max_calls_per_model),
                        task_timeout_s=float(task_timeout_s), max_retries=int(max_retries))
        self.default_max_tokens = int(default_max_tokens)
        self.allow_ci = allow_ci
        self._lock = threading.RLock()
        # 计量：reserved 为"已承诺"（含未结算最坏值），actual 为已结算真实值
        self.reserved_usd = 0.0
        self.actual_usd = 0.0
        self.calls_global = 0
        self.calls_task = 0
        self.calls_by_model = {}
        self.usd_stage = 0.0
        self.usd_task = 0.0
        self.task_id = None
        self._t0 = None
        self.task_stopped = None
        self.retries = 0
        self._uid = 0
        self.rejected_before_invoke = 0
        self.resume(ledger_only=True)

    # ---- 开关：两个都必须显式开启，且默认禁止在 CI 中付费 ----
    def check_switches(self):
        if os.environ.get("CI") and not self.allow_ci:
            raise GateConfigError("CI 环境禁止任何付费模型调用")
        if os.environ.get(ENV_PAID) != "1":
            raise GateConfigError(f"缺少显式开关 {ENV_PAID}=1")
        if os.environ.get(ENV_CONFIRM) != self.stage:
            raise GateConfigError(f"缺少显式开关 {ENV_CONFIRM}=={self.stage!r}")
        return True

    def resume(self, ledger_only=False):
        """进程重启后把未结算的 reservation 计回已承诺额度（不清空）。"""
        open_res = self.ledger.open_reservations()
        for e in open_res.values():
            self.reserved_usd += float(e.get("worst_case_usd", 0.0))
        return len(open_res)

    # ---- 任务边界 ----
    def start_task(self, task_id):
        with self._lock:
            self.task_id, self.calls_task, self.usd_task = task_id, 0, 0.0
            self._t0 = time.monotonic()
            self.task_stopped = None

    def stop_task(self, reason):
        with self._lock:
            self.task_stopped = reason

    def end_task(self):
        with self._lock:
            self.task_id, self._t0 = None, None

    def elapsed(self):
        return 0.0 if self._t0 is None else time.monotonic() - self._t0

    @property
    def committed_usd(self):
        """已承诺 = 已结算真实费用 + 未结算的最坏预留。"""
        return self.actual_usd + self.reserved_usd

    # ---- 核心：进入网络调用之前，同步执行 12 项检查并原子预留 ----
    def before_call(self, *, model_id, role, payload, max_tokens=None, is_retry=False):
        with self._lock:                                  # 原子：并发调用不能共同越界
            self.check_switches()                          # 1 两个显式开关
            if self.task_stopped:                          # 2 任务是否已停止
                self._reject(f"task_stopped: {self.task_stopped}")
            if self.calls_task + 1 > self.lim["max_calls_task"]:   # 3 单任务调用次数
                self._reject(f"max_calls_task[{self.task_id}]: "
                             f"{self.calls_task + 1} > {self.lim['max_calls_task']}")
            cap = self.lim["max_calls_per_model"].get(_norm(model_id))  # 4 每模型调用次数
            used = self.calls_by_model.get(_norm(model_id), 0)
            if cap is None:
                self._reject(f"未为模型配置调用上限，拒绝：{model_id!r}")
            if used + 1 > cap:
                self._reject(f"max_calls_per_model[{_norm(model_id)}]: {used + 1} > {cap}")
            if self.calls_global + 1 > self.lim["max_calls_global"]:   # 5 全局调用次数
                self._reject(f"max_calls_global: {self.calls_global + 1} > "
                             f"{self.lim['max_calls_global']}")

            p = price_for(model_id)                        # 未知模型/价格 → GateConfigError
            in_tok = estimate_input_tokens(payload)        # 9 估算输入 token
            out_tok = int(max_tokens or self.default_max_tokens)   # 10 按 max_tokens 算最坏
            worst = in_tok / 1e6 * p["input"] + out_tok / 1e6 * p["output"]

            if self.usd_task + worst > self.lim["max_usd_task"]:      # 6 任务预算
                self._reject(f"max_usd_task[{self.task_id}]: "
                             f"${self.usd_task + worst:.4f} > ${self.lim['max_usd_task']:.2f}")
            if self.usd_stage + worst > self.lim["max_usd_stage"]:    # 7 Stage 预算
                self._reject(f"max_usd_stage[{self.stage}]: "
                             f"${self.usd_stage + worst:.4f} > ${self.lim['max_usd_stage']:.2f}")
            if self.committed_usd + worst > self.lim["max_usd_global"]:   # 8 全局预算
                self._reject(f"max_usd_global: ${self.committed_usd + worst:.4f} > "
                             f"${self.lim['max_usd_global']:.2f}")
            if self._t0 is not None and self.elapsed() > self.lim["task_timeout_s"]:  # 11 超时
                self._reject(f"task_timeout[{self.task_id}]: "
                             f"{self.elapsed():.1f}s > {self.lim['task_timeout_s']}s")
            if is_retry:
                self.retries += 1
                if self.retries > self.lim["max_retries"]:            # 11 重试上限
                    self._reject(f"max_retries: {self.retries} > {self.lim['max_retries']}")

            # 12 原子预留：先记账，再允许网络调用
            self._uid += 1
            uid = f"{self.stage}:{self.task_id}:{self._uid}"
            self.calls_global += 1
            self.calls_task += 1
            self.calls_by_model[_norm(model_id)] = used + 1
            self.reserved_usd += worst
            self.usd_task += worst
            self.usd_stage += worst
            self.ledger.append({"event": "reserved", "call_uid": uid, "stage": self.stage,
                                "task_id": self.task_id, "role": role, "model": model_id,
                                "est_input_tokens": in_tok, "max_tokens": out_tok,
                                "worst_case_usd": round(worst, 6), "is_retry": bool(is_retry),
                                "ts": time.time()})
            return uid, worst

    def _reject(self, reason):
        self.rejected_before_invoke += 1
        self.ledger.append({"event": "rejected_before_invoke", "stage": self.stage,
                            "task_id": self.task_id, "reason": reason, "ts": time.time()})
        raise BudgetExceeded(reason)

    # ---- 结算 ----
    def reconcile(self, uid, model_id, in_tok, out_tok, worst):
        """调用成功且拿到真实 usage：把预留换成实际，释放未用额度。"""
        with self._lock:
            p = price_for(model_id)
            actual = in_tok / 1e6 * p["input"] + out_tok / 1e6 * p["output"]
            self.reserved_usd -= worst
            self.actual_usd += actual
            self.usd_task += actual - worst
            self.usd_stage += actual - worst
            self.ledger.append({"event": "reconciled", "call_uid": uid, "model": model_id,
                                "input_tokens": in_tok, "output_tokens": out_tok,
                                "actual_usd": round(actual, 6),
                                "released_usd": round(worst - actual, 6), "ts": time.time()})
            return actual

    def usage_unknown(self, uid, model_id, worst):
        """调用成功但拿不到 usage → fail-closed：保留最坏费用，等人工核对。"""
        with self._lock:
            self.ledger.append({"event": "usage_unknown", "call_uid": uid, "model": model_id,
                                "held_usd": round(worst, 6),
                                "note": "usage 缺失，按最坏费用计入，需人工核对", "ts": time.time()})
            # 预留不释放：从 reserved 转入 actual，保持已承诺额度不变
            self.reserved_usd -= worst
            self.actual_usd += worst
            return worst

    def failed_call(self, uid, model_id, worst, *, request_sent, error):
        """调用失败：绝不简单记零费用，也绝不自动清空 reservation。"""
        with self._lock:
            state = "provider_may_have_billed" if request_sent else "confirmed_not_sent"
            self.ledger.append({"event": "failed_maybe_billed", "call_uid": uid,
                                "model": model_id, "billing_state": state,
                                "held_usd": round(worst, 6) if request_sent else 0.0,
                                "error": str(error)[:300], "ts": time.time()})
            if request_sent:
                self.reserved_usd -= worst      # 转为已承诺实际（保守）
                self.actual_usd += worst
            else:
                self.reserved_usd -= worst      # 确认未发出 → 释放
                self.usd_task -= worst
                self.usd_stage -= worst
            return state

    def summary(self):
        return {"stage": self.stage, "actual_usd": round(self.actual_usd, 6),
                "reserved_open_usd": round(self.reserved_usd, 6),
                "committed_usd": round(self.committed_usd, 6),
                "calls_global": self.calls_global, "calls_by_model": dict(self.calls_by_model),
                "rejected_before_invoke": self.rejected_before_invoke,
                "retries": self.retries, "limits": self.lim,
                "ledger": str(self.ledger.path)}


def _norm(model_id):
    for k in PRICES:
        if k in (model_id or ""):
            return k
    return model_id or "unknown"


# ---------- 调用包装器：所有付费入口都必须过这里 ----------
_WRAPPED = "_reumani_hard_gate_wrapped"
_METHODS = ("invoke", "ainvoke", "stream", "astream", "batch", "abatch")


class GatedModel:
    """包住 LangChain chat model。任何 _METHODS 在进入底层实现之前先过 gate.before_call()。"""

    def __init__(self, inner, gate, *, role, model_id, max_tokens=None):
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_gate", gate)
        object.__setattr__(self, "_role", role)
        object.__setattr__(self, "_model_id", model_id)
        object.__setattr__(self, "_max_tokens", max_tokens)
        object.__setattr__(self, _WRAPPED, True)

    def __getattr__(self, name):
        inner = object.__getattribute__(self, "_inner")
        if name in _METHODS:
            return object.__getattribute__(self, "_gated")(name)
        return getattr(inner, name)

    def _gated(self, name):
        inner = object.__getattribute__(self, "_inner")
        gate = object.__getattribute__(self, "_gate")
        role = object.__getattribute__(self, "_role")
        model_id = object.__getattribute__(self, "_model_id")
        mt = object.__getattribute__(self, "_max_tokens")
        target = getattr(inner, name)

        def sync_call(*a, **k):
            uid, worst = gate.before_call(model_id=model_id, role=role,
                                          payload=a[0] if a else k, max_tokens=mt)
            try:
                res = target(*a, **k)
            except Exception as e:
                gate.failed_call(uid, model_id, worst, request_sent=True, error=e)
                raise
            _settle(gate, uid, model_id, worst, res)
            return res

        async def async_call(*a, **k):
            uid, worst = gate.before_call(model_id=model_id, role=role,
                                          payload=a[0] if a else k, max_tokens=mt)
            try:
                res = await target(*a, **k)
            except Exception as e:
                gate.failed_call(uid, model_id, worst, request_sent=True, error=e)
                raise
            _settle(gate, uid, model_id, worst, res)
            return res

        return async_call if name.startswith("a") else sync_call

    # 让 LangChain 的 isinstance/绑定行为尽量透明
    def bind_tools(self, *a, **k):
        inner = object.__getattribute__(self, "_inner")
        gate = object.__getattribute__(self, "_gate")
        return GatedModel(inner.bind_tools(*a, **k), gate,
                          role=object.__getattribute__(self, "_role"),
                          model_id=object.__getattribute__(self, "_model_id"),
                          max_tokens=object.__getattribute__(self, "_max_tokens"))

    def bind(self, *a, **k):
        inner = object.__getattribute__(self, "_inner")
        gate = object.__getattribute__(self, "_gate")
        return GatedModel(inner.bind(*a, **k), gate,
                          role=object.__getattribute__(self, "_role"),
                          model_id=object.__getattribute__(self, "_model_id"),
                          max_tokens=object.__getattribute__(self, "_max_tokens"))


def _settle(gate, uid, model_id, worst, res):
    i, o = _usage_from_result(res)
    if i is None and o is None:
        gate.usage_unknown(uid, model_id, worst)      # fail-closed
    else:
        gate.reconcile(uid, model_id, int(i or 0), int(o or 0), worst)


def _usage_from_result(res):
    um = getattr(res, "usage_metadata", None)
    if isinstance(um, dict) and (um.get("input_tokens") or um.get("output_tokens")):
        return um.get("input_tokens"), um.get("output_tokens")
    rm = getattr(res, "response_metadata", None) or {}
    tu = (rm.get("token_usage") or rm.get("usage") or {}) if isinstance(rm, dict) else {}
    if tu:
        return (tu.get("prompt_tokens") or tu.get("input_tokens"),
                tu.get("completion_tokens") or tu.get("output_tokens"))
    return None, None


def wrap_all(gate, specs):
    """把每个 (module, attr, role, model_id, max_tokens) 就地换成 GatedModel。
    任何一个无法包装 → GateConfigError，Pilot 拒绝启动（不降级为软闸门）。"""
    wrapped = []
    for mod, attr, role, model_id, max_tokens in specs:
        obj = getattr(mod, attr, None)
        if obj is None:
            raise GateConfigError(f"找不到付费对象 {mod.__name__}.{attr}，Pilot 拒绝启动")
        if getattr(obj, _WRAPPED, False):
            wrapped.append(f"{mod.__name__}.{attr}(already)")
            continue
        g = GatedModel(obj, gate, role=role, model_id=model_id, max_tokens=max_tokens)
        try:
            setattr(mod, attr, g)
        except Exception as e:
            raise GateConfigError(f"无法包装 {mod.__name__}.{attr}: {e}；Pilot 拒绝启动")
        if not getattr(getattr(mod, attr), _WRAPPED, False):
            raise GateConfigError(f"包装校验失败 {mod.__name__}.{attr}；Pilot 拒绝启动")
        wrapped.append(f"{mod.__name__}.{attr}")
    return wrapped


def assert_all_paid_entrypoints_wrapped(modules_attrs):
    """动态证明：列出的每个付费入口当前都是 GatedModel。"""
    bad = [f"{m.__name__}.{a}" for m, a in modules_attrs
           if not getattr(getattr(m, a, None), _WRAPPED, False)]
    if bad:
        raise GateConfigError(f"以下付费入口未被包装，Pilot 拒绝启动：{bad}")
    return True
