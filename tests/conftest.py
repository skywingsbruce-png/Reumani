"""A.8.2b.1.1 —— 测试级全局状态隔离。**不包含任何生产代码改动**。

两个 autouse 安全网，保证测试顺序永远不再影响结果：

1. `_paid_client_globals_isolated`：每个测试前后对付费客户端所在模块的字典做
   身份快照 / 恢复。`PT.neutralize_unused_paid_clients()` 会替换它**扫到的一切**
   （不止三个固定名字），单个测试的 finally 不可能穷举，因此在框架层兜住。
2. `_prices_table_isolated`：价格表是模块级可变字典，多处测试直接增删条目；
   有的没写在 finally 里，断言一旦失败就会泄漏到后续测试。

两者都只恢复**被改动**的项，不重新 import 任何模块，也不调用 getattr。
"""

import pytest

from tests.global_state_guard import restore_module_globals, snapshot_module_globals


@pytest.fixture(autouse=True)
def _paid_client_globals_isolated():
    snap = snapshot_module_globals()
    yield
    restore_module_globals(snap)


@pytest.fixture(autouse=True)
def _prices_table_isolated():
    """价格表是模块级可变字典，测试改动必须在测试边界还原。"""
    try:
        from pilot import prices as PR
    except Exception:                                  # noqa: BLE001 —— 依赖缺失时不拦测试
        yield
        return
    before = dict(PR.PRICES)
    yield
    if PR.PRICES != before:
        PR.PRICES.clear()
        PR.PRICES.update(before)


@pytest.fixture
def restore_paid_globals():
    """显式版本：需要在测试体内断言"恢复前/后"差异时使用。"""
    from tests.global_state_guard import paid_client_globals_restored
    with paid_client_globals_restored() as guard:
        yield guard
