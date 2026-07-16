"""工具权限层测试（含权限绕过）。确定性，不调 LLM/API。"""
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tool_registry import (UnknownToolError, resolve, select_tool_names,
                           apply_approvals, PermissionedToolset)


class QSchema(BaseModel):
    query: str


def _registry(calls):
    def mk(name):
        def f(**k):
            calls.append((name, k))
            return f"ran:{name}"
        return f
    return {
        "search_literature": {"func": mk("search_literature"), "schema": QSchema},
        "query_data_lake": {"func": mk("query_data_lake"), "schema": None},
        "run_python": {"func": mk("run_python"), "schema": None},
    }


@pytest.mark.unit
def test_resolve_unknown_raises():
    with pytest.raises(UnknownToolError):
        resolve(["search_literature", "definitely_not_a_tool"])


@pytest.mark.unit
def test_select_core_always_present():
    sel = select_tool_names("随便问点什么")
    assert "search_literature" in sel and "query_data_lake" in sel     # 安全核心
    assert "run_python" not in sel                                     # 无分析关键词 → 不选


@pytest.mark.unit
def test_high_risk_excluded_unless_approved():
    sel = select_tool_names("帮我用 python 画个火山图做统计分析")
    assert "run_python" in sel                                         # 关键词命中
    trace = []
    allowed = apply_approvals(sel, approved=None, trace=trace)
    assert "run_python" not in allowed                                 # 未批准 → 物理排除
    assert any(e["event"] == "blocked_pending_approval" and e["tool"] == "run_python" for e in trace)
    allowed2 = apply_approvals(sel, approved=["run_python"])
    assert "run_python" in allowed2                                    # 批准后进入工具集


@pytest.mark.unit
def test_bypass_unknown_tool_name():
    ts = PermissionedToolset(["search_literature"], _registry([]))
    with pytest.raises(UnknownToolError):                              # 不许用相近名替代
        ts.call("search_lit")


@pytest.mark.unit
def test_bypass_call_unselected_tool():
    calls = []
    ts = PermissionedToolset(["search_literature"], _registry(calls))
    r = ts.call("query_data_lake", query="x")                         # 未授权
    assert r.ok is False and r.error_type == "permission_denied"
    assert calls == []                                                # 真的没执行


@pytest.mark.unit
def test_bypass_high_risk_without_approval():
    calls = []
    ts = PermissionedToolset(["run_python"], _registry(calls))
    r = ts.call("run_python", code="print(1)")
    assert r.ok is False and r.error_type == "approval_required"
    assert calls == []                                                # 未批准，未执行
    # 批准后可执行
    calls2 = []
    ts2 = PermissionedToolset(["run_python"], _registry(calls2), approved=["run_python"])
    r2 = ts2.call("run_python", code="print(1)")
    assert r2.ok is True and calls2 and calls2[0][0] == "run_python"


@pytest.mark.unit
def test_param_schema_validated():
    calls = []
    ts = PermissionedToolset(["search_literature"], _registry(calls))
    bad = ts.call("search_literature", wrong_field=1)                 # 缺 query
    assert bad.ok is False and bad.error_type == "invalid_params"
    assert calls == []                                                # 校验失败不执行
    good = ts.call("search_literature", query="systemic sclerosis")
    assert good.ok is True and calls and calls[0][0] == "search_literature"


@pytest.mark.unit
def test_trace_records_selection_and_rejection():
    ts = PermissionedToolset(["search_literature"], _registry([]))
    ts.call("query_data_lake", query="x")     # rejected
    ts.call("search_literature", query="x")   # call+result
    events = [(e["event"], e["tool"]) for e in ts.trace]
    assert ("selected", "search_literature") in events
    assert ("rejected", "query_data_lake") in events
    assert ("call", "search_literature") in events
    assert ("result", "search_literature") in events


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                print(f"FAIL {name}: {e}")
