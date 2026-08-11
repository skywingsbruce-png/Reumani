"""A.8.2b.1.1 —— 付费客户端全局状态的**精确快照 / 恢复**（仅测试用，非生产代码）。

为什么需要它：`PT.neutralize_unused_paid_clients(gate)` 的语义就是"扫描并替换
**所有**未包装的付费客户端"。测试调用它一次，`ssc_pi_agent.judge_llm` /
`deepseek_llm_pro` / `deepseek_llm_con` 就都被换成了 GatedModel 包装对象。
原测试的 finally 只删掉了自己加的随机属性，没有恢复这些被顺带替换的属性，
于是后续 `test_production_path_untouched_without_wrapping` 必然失败。

设计要点：
- **不能只保存固定的三个名字** —— 扫描器的目的正是动态发现未知属性名；
- 因此按 `(module, attr)` 对整个模块字典做身份快照，恢复时逐项比对 `is`；
- 全程使用 `vars(module)` / `module.__dict__`，**不调用 getattr**，
  以免触发模块级 `__getattr__`（与 A.8.2b.1 §4 的不触发式扫描一致）；
- **不通过重新 import 恢复**：模块已在 sys.modules 缓存里，重新 import 既无效，
  又可能再次构造付费客户端。
"""

from __future__ import annotations

import sys

# 与 pilot.paid_transport.SCAN_MODULES 一致；额外把 legacy 消费者也纳入，
# 因为 neutralize 的扫描面可能随 SCAN_MODULES 变化。
def _target_modules():
    try:
        from pilot.paid_transport import SCAN_MODULES
    except Exception:                                  # noqa: BLE001
        SCAN_MODULES = ()
    extra = ("ssc_pi_agent", "ssc_a1", "ssc_skill_agent", "shadow")
    seen, out = set(), []
    for name in tuple(SCAN_MODULES) + extra:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return tuple(out)


def _module_dict(mod):
    """只读模块字典；绝不走属性协议。"""
    try:
        d = object.__getattribute__(mod, "__dict__")
    except Exception:                                  # noqa: BLE001
        return None
    return d if isinstance(d, dict) else None


def snapshot_module_globals(module_names=None) -> dict:
    """对目标模块的字典做**身份**快照。

    返回 {module_name: {attr: object}}。只记录已导入的模块；未导入的模块没有
    可被污染的对象，无需快照（也不会因此把它导入进来）。
    """
    snap = {}
    for name in (_target_modules() if module_names is None else module_names):
        mod = sys.modules.get(name)
        if mod is None:
            continue
        d = _module_dict(mod)
        if d is None:
            continue
        snap[name] = dict(d)                           # 浅拷贝：保存的是对象引用/身份
    return snap


def diff_module_globals(snap: dict) -> list:
    """列出与快照相比发生了变化的 (module, attr, kind)。kind ∈ replaced/added/removed。"""
    changes = []
    for name, before in snap.items():
        mod = sys.modules.get(name)
        d = _module_dict(mod) if mod is not None else None
        if d is None:
            continue
        for attr, obj in before.items():
            if attr not in d:
                changes.append((name, attr, "removed"))
            elif d[attr] is not obj:
                changes.append((name, attr, "replaced"))
        for attr in d:
            if attr not in before:
                changes.append((name, attr, "added"))
    return changes


def restore_module_globals(snap: dict) -> list:
    """把模块字典恢复到快照时的**对象身份**。返回实际被恢复的项。

    - 快照时存在的属性 → 恢复原对象；
    - 快照时不存在、现在存在的属性 → 删除；
    - 全程直接操作 `__dict__`，不触发 `__getattr__`、不重新 import。
    """
    restored = []
    for name, before in snap.items():
        mod = sys.modules.get(name)
        d = _module_dict(mod) if mod is not None else None
        if d is None:
            continue
        for attr, obj in before.items():
            if attr not in d or d[attr] is not obj:
                d[attr] = obj
                restored.append(f"{name}.{attr}")
        for attr in [a for a in d if a not in before]:
            del d[attr]
            restored.append(f"-{name}.{attr}")
    return restored


class paid_client_globals_restored:                    # noqa: N801 —— 当上下文管理器用
    """with 块内随便污染，退出时按身份精确恢复。"""

    def __init__(self, module_names=None):
        self._names = module_names
        self._snap = {}
        self.restored = []

    def __enter__(self):
        self._snap = snapshot_module_globals(self._names)
        return self

    def __exit__(self, *exc):
        self.restored = restore_module_globals(self._snap)
        return False                                   # 不吞异常


__all__ = ["snapshot_module_globals", "restore_module_globals", "diff_module_globals",
           "paid_client_globals_restored"]
