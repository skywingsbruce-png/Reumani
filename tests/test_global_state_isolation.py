"""A.8.2b.1.1 §3 —— 全局状态污染回归测试。

证明：调用 `neutralize_unused_paid_clients` 的测试跑完之后，`ssc_pi_agent` 的
付费客户端属性**按对象身份**恢复原状，随机属性消失，包装标记不残留。

全部离线：零网络、零真实 key、零付费调用。
"""
import json
import pathlib
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit
REPO = str(pathlib.Path(__file__).resolve().parent.parent)

PROD_ATTRS = ("judge_llm", "deepseek_llm_pro", "deepseek_llm_con")
WRAPPED = "_reumani_hard_gate_wrapped"


def _run(code):
    import os

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=REPO, timeout=600, env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _pytest(args):
    import os

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-m", "pytest", "-p", "no:warnings", "-q", *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=REPO, timeout=900, env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


POLLUTER = ("tests/test_role_separation.py::"
            "test_discovers_paid_client_with_arbitrary_attr_name")
VICTIM = "tests/test_hard_gate.py::test_production_path_untouched_without_wrapping"


# ------------------------------------------------------- 身份恢复
def test_neutralize_test_restores_production_object_identities(tmp_path):
    """跑完污染测试后，三个生产属性必须还是**同一个对象**。"""
    import ssc_pi_agent as P
    before = {a: vars(P).get(a) for a in PROD_ATTRS}
    assert all(o is not None for o in before.values())

    from pilot import paid_transport as PT
    from tests.global_state_guard import paid_client_globals_restored
    from tests.test_hard_gate import mkgate

    with paid_client_globals_restored():
        PT.neutralize_unused_paid_clients(mkgate(tmp_path))
        # with 块内确实被污染了 —— 否则这个回归测试没有意义
        assert any(vars(P).get(a) is not before[a] for a in PROD_ATTRS)

    for a in PROD_ATTRS:
        assert vars(P).get(a) is before[a], f"{a} 未按身份恢复"


def test_wrapped_marker_does_not_leak_after_restore(tmp_path):
    import ssc_pi_agent as P
    from pilot import paid_transport as PT
    from tests.global_state_guard import paid_client_globals_restored
    from tests.test_hard_gate import mkgate

    with paid_client_globals_restored():
        PT.neutralize_unused_paid_clients(mkgate(tmp_path))
    for a in PROD_ATTRS:
        obj = vars(P).get(a)
        assert not getattr(obj, WRAPPED, False), f"{a} 残留了包装标记"


def test_random_attribute_is_gone_after_the_polluting_test():
    rc, out = _pytest([POLLUTER])
    assert rc == 0, out
    rc2, out2 = _run(
        "import ssc_pi_agent as P\n"
        "print('LEFTOVER', 'zz_some_random_llm_name_9137' in vars(P))")
    assert rc2 == 0, out2
    assert "LEFTOVER False" in out2


def test_guard_restores_added_removed_and_replaced_entries():
    import types

    from tests.global_state_guard import (diff_module_globals, restore_module_globals,
                                          snapshot_module_globals)

    name = "_a82b11_probe_mod"
    m = types.ModuleType(name)
    keep, replaced_orig = object(), object()
    m.keep, m.replaced, m.removed_later = keep, replaced_orig, object()
    removed_orig = m.removed_later
    sys.modules[name] = m
    try:
        snap = snapshot_module_globals([name])
        m.replaced = object()                       # replaced
        m.added = object()                          # added
        del m.removed_later                         # removed
        kinds = {k for _, _, k in diff_module_globals(snap)}
        assert kinds == {"replaced", "added", "removed"}
        restore_module_globals(snap)
        assert m.replaced is replaced_orig
        assert m.removed_later is removed_orig
        assert not hasattr(m, "added")
        assert m.keep is keep
        assert diff_module_globals(snap) == []
    finally:
        sys.modules.pop(name, None)


def test_restore_never_triggers_module_getattr():
    """恢复机制本身不得触发模块级 __getattr__（否则会构造付费客户端）。"""
    rc, out = _run(
        "import sys, types\n"
        "from tests.global_state_guard import (snapshot_module_globals,\n"
        "                                      restore_module_globals, diff_module_globals)\n"
        "calls = []\n"
        "m = types.ModuleType('ssc_a1')\n"
        "def boom(name):\n"
        "    calls.append(name); raise AssertionError('触发了 __getattr__: ' + name)\n"
        "m.__getattr__ = boom\n"
        "m.judge_llm = object()\n"
        "sys.modules['ssc_a1'] = m\n"
        "snap = snapshot_module_globals(['ssc_a1'])\n"
        "m.judge_llm = object()\n"
        "diff_module_globals(snap); restore_module_globals(snap)\n"
        "print('GETATTR_CALLS', len(calls))")
    assert rc == 0, out
    assert "GETATTR_CALLS 0" in out, out


def test_restore_does_not_reimport_or_construct_clients(tmp_path):
    """恢复不得重新 import ssc_pi_agent（缓存仍在，重新 import 可能再造客户端）。"""
    src = (pathlib.Path(REPO) / "tests" / "global_state_guard.py").read_text(encoding="utf-8")
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    assert "importlib.reload" not in code and "import ssc_pi_agent" not in code
    assert "getattr(mod" not in code

    import ssc_pi_agent as P
    from pilot import paid_transport as PT
    from tests.global_state_guard import paid_client_globals_restored
    from tests.test_hard_gate import mkgate

    ids_before = {a: id(vars(P).get(a)) for a in PROD_ATTRS}
    with paid_client_globals_restored():
        PT.neutralize_unused_paid_clients(mkgate(tmp_path))
    assert {a: id(vars(P).get(a)) for a in PROD_ATTRS} == ids_before


def test_prices_table_is_restored_between_tests():
    from pilot import prices as PR
    assert "rs-priced" not in PR.PRICES
    assert "expensive-test" not in PR.PRICES
    assert "settle-test" not in PR.PRICES


def test_environment_is_not_polluted_by_paid_switches():
    import os
    from pilot.hard_gate import ENV_CONFIRM, ENV_PAID
    # 其他测试用 monkeypatch 设置它们；monkeypatch 生命周期结束后不得残留
    assert os.environ.get(ENV_PAID) in (None, "", "0")
    assert os.environ.get(ENV_CONFIRM) in (None, "")


# ------------------------------------------------------- 顺序验证
@pytest.mark.parametrize("order,label", [
    ([POLLUTER, VICTIM], "污染→受害"),
    ([VICTIM, POLLUTER], "受害→污染"),
    ([POLLUTER, POLLUTER, VICTIM], "污染×2→受害"),
    ([VICTIM, POLLUTER, VICTIM], "受害→污染→受害"),
])
def test_orders_all_pass(order, label):
    rc, out = _pytest(list(order))
    assert rc == 0, f"顺序【{label}】失败：\n{out[-2500:]}"


def test_both_whole_files_pass_together():
    rc, out = _pytest(["tests/test_role_separation.py", "tests/test_hard_gate.py"])
    assert rc == 0, out[-2500:]
    rc2, out2 = _pytest(["tests/test_hard_gate.py", "tests/test_role_separation.py"])
    assert rc2 == 0, out2[-2500:]


def test_repeated_execution_is_idempotent():
    """同一污染测试连续跑两次，结果一致（不因残留状态而改变）。"""
    a = _pytest([POLLUTER])
    b = _pytest([POLLUTER])
    assert a[0] == b[0] == 0, (a[1][-1500:], b[1][-1500:])
