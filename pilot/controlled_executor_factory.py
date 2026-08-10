"""A.8.2a.4b —— per-run 受控执行器工厂。

为什么必须 per-run：`research_contracts._REGISTRY[eid] = executor` 存的是**共享实例**，
而 `RunManager.start_research()` 用 `get_executor(id)` 取同一个对象。
`DeferredRegistryResearchExecutor` 是**有状态的**（`_binding` / `_grant` / `_inner`），
一旦作为全局单例注册，A 的批准会解锁 B、B 的 deny 会清掉 A —— 跨 run 授权串位。

因此注册的是**工厂**而不是已解析实例：服务启动只保存不可变配置与安全构造函数
（零网络、零 key、零 client），每个 run 各自拿到独立的 Registry / Gate / Deferred executor
和一个**绑定到本 run_id** 的只读事件查询口。
"""

from __future__ import annotations

import threading

from pilot.approval_grant import ApprovalGrantError
from pilot.controlled_runtime import build_controlled_runtime_registry
from pilot.deferred_research_executor import DeferredRegistryResearchExecutor
from pilot.gated_research_executor import EXECUTOR_ID


class ControlledExecutorFactoryError(RuntimeError):
    """工厂配置不合法 → 拒绝启动 run（provider 调用为 0）。"""


def scoped_approval_event_lookup(event_store, run_id: str):
    """只读、且**锁定在单个 run_id** 上的事件查询口。

    调用方无法查询或冒充其它 run：run_id 由闭包固定，传入不符即拒绝。
    返回的是 EventStore 里的事件对象，本函数不提供任何写入路径。
    """
    fixed = str(run_id)

    def lookup(asked_run_id, sequence):
        if str(asked_run_id) != fixed:
            raise ApprovalGrantError(
                f"事件查询越权：本 lookup 仅限 {fixed}，被问及 {asked_run_id}")
        for ev in event_store.list(fixed):          # 只读遍历
            if ev.sequence == sequence:
                return ev
        return None

    return lookup


class ControlledResearchExecutorFactory:
    """注册到 executor registry 的**工厂**（不是已解析 executor）。

    `executor_id` 与真实 executor 相同，使客户端仍然只按 ID 选择；
    但 `RunManager` 识别出它是工厂后，会为每个 run 调用 `create_for_run()`。
    """

    executor_id = EXECUTOR_ID
    is_per_run_factory = True                       # RunManager 据此分派

    def __init__(self, *, gate_factory, model_factory, evidence_loader_factory,
                 budget_policy_id: str = "research-budget-policy-v2",
                 capabilities=None, timeout: float = 120.0):
        for name, f in (("gate_factory", gate_factory), ("model_factory", model_factory),
                        ("evidence_loader_factory", evidence_loader_factory)):
            if not callable(f):
                raise ControlledExecutorFactoryError(f"{name} 必须可调用")
        self._gate_factory = gate_factory
        self._model_factory = model_factory
        self._evidence_loader_factory = evidence_loader_factory
        self._budget_policy_id = budget_policy_id
        self._capabilities = capabilities
        self._timeout = float(timeout)
        self._lock = threading.RLock()
        self.created_runs = []                      # 可审计：本进程为哪些 run 建过 executor

    # ------------------------------------------------------------ per-run 构造
    def create_for_run(self, run_id: str, event_store):
        """为单个 run 建立**完全独立**的 Registry / Gate / Deferred executor。

        此处仍然零 resolve、零 key、零 client —— provider 只会在授权后的首次
        run_stage 由该 run 自己的 Registry factory 构造。
        """
        if not run_id:
            raise ControlledExecutorFactoryError("缺少 run_id，拒绝创建受控执行器")
        if event_store is None:
            raise ControlledExecutorFactoryError("缺少 EventStore，无法核实审批事件 → 拒绝")
        gate = self._gate_factory(run_id)
        if gate is None:
            raise ControlledExecutorFactoryError(f"gate_factory 未为 {run_id} 提供 HardBudgetGate")
        registry = build_controlled_runtime_registry(
            gate=gate, model_factory=self._model_factory, timeout=self._timeout,
            pricing_policy_id=self._budget_policy_id)
        ex = DeferredRegistryResearchExecutor(
            registry=registry, gate=gate,
            evidence_loader=self._evidence_loader_factory(run_id),
            budget_policy_id=self._budget_policy_id,
            capabilities=self._capabilities,
            approval_event_lookup=scoped_approval_event_lookup(event_store, run_id))
        # 便于测试/审计断言隔离性（都是本 run 私有对象）
        ex.run_id = run_id
        ex.registry = registry
        ex.gate = gate
        with self._lock:
            self.created_runs.append(run_id)
        return ex


def configure_controlled_research_runtime(manager, *, gate_factory, model_factory,
                                          evidence_loader_factory,
                                          budget_policy_id="research-budget-policy-v2",
                                          capabilities=None, timeout=120.0):
    """生产配置入口：注册**工厂**，不注册已解析 executor。

    调用它时：factory_calls == 0、不读 key、不构造 ChatAnthropic/ChatOpenAI、
    不调用 from_registry、不 resolve 任何 provider。
    """
    from pilot.research_contracts import register_executor
    factory = ControlledResearchExecutorFactory(
        gate_factory=gate_factory, model_factory=model_factory,
        evidence_loader_factory=evidence_loader_factory,
        budget_policy_id=budget_policy_id, capabilities=capabilities, timeout=timeout)
    register_executor(factory)
    if manager is not None:
        manager.controlled_factory = factory
    return factory


__all__ = ["ControlledResearchExecutorFactory", "configure_controlled_research_runtime",
           "scoped_approval_event_lookup", "ControlledExecutorFactoryError"]
