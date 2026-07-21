"""A.6.3.2 §6：导入顺序防御测试。零真实 API。

隔离方式：需要真正"首次导入"语义的用例在**子进程**里跑
（在同一进程里 pop 掉 ssc_* 再重新导入会触发 transformers 的惰性子模块导入，
污染且不可靠）。子进程天然隔离 sys.modules 与环境变量，不影响其它测试。
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pilot import paid_transport as PT
from pilot.hard_gate import GateConfigError

SCRIPTS = ROOT / "tests" / "_import_order_scripts"


def run_py(code, env=None, timeout=300):
    e = dict(os.environ)
    e["PYTHONIOENCODING"] = "utf-8"
    e["PYTHONPATH"] = str(ROOT)
    e.setdefault("REUMANI_PILOT_PAID", "1")
    e.setdefault("REUMANI_PILOT_CONFIRM", "test")
    e.update(env or {})
    return subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=e,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


PRELUDE = f"""
import sys, json
sys.path.insert(0, r"{ROOT}")
from types import SimpleNamespace
from pilot import paid_transport as PT
from pilot.hard_gate import GatedModel, HardBudgetGate, GateConfigError

class FakeClient:
    def __init__(self, tag):
        self.tag = tag
        self.max_retries, self.timeout, self.max_tokens = 0, 120.0, 2000
        self.extra_body = dict(PT.THINKING_DISABLED)
    def invoke(self, *a, **k):
        return SimpleNamespace(content="", usage_metadata={{"input_tokens":1,"output_tokens":1}},
                               response_metadata={{}})
    def bind_tools(self, *a, **k):
        return self

def mkgate():
    import tempfile, pathlib
    return HardBudgetGate(stage="test",
        ledger_path=pathlib.Path(tempfile.mkdtemp())/"io.jsonl",
        max_usd_global=25.0, max_usd_stage=3.0, max_usd_task=1.5,
        max_calls_global=200, max_calls_task=21,
        max_calls_per_model={{"fake-model": 99}},
        max_calls_per_role={{"planner":2,"verifier":2,"claim_extractor":1,"executor":16}},
        task_timeout_s=600, max_retries=0, default_max_tokens=2000)

def fake_roles(gate):
    return {{r: GatedModel(FakeClient(r), gate, role=r, model_id="fake-model",
                          max_tokens=PT.MAX_TOKENS[r]) for r in PT.ROLES}}
"""


# 1 —— 正确顺序时六个绑定全部 wrapped
@pytest.mark.unit
def test_correct_order_wraps_all_six_bindings():
    r = run_py(PRELUDE + """
PT.assert_import_order_clean()
gate = mkgate(); roles = fake_roles(gate)
import ssc_pi_agent as P
P.judge_llm = roles["planner"]; P.deepseek_llm_pro = roles["executor"]
PT.neutralize_unused_paid_clients(gate)
names = PT.assert_bindings_after_import(roles, gate)
import ssc_a1, ssc_skill_agent
bad = [f"{m.__name__}.{a}" for m in (P, ssc_a1, ssc_skill_agent) for a in PT.PAID_ATTRS
       if getattr(m, a, None) is not None
       and not getattr(getattr(m, a), "_reumani_hard_gate_wrapped", False)]
print("RESULT", json.dumps({"n_bindings": len(names), "unwrapped": bad}))
""")
    assert r.returncode == 0, r.stdout + r.stderr
    out = [l for l in r.stdout.splitlines() if l.startswith("RESULT")][0]
    data = __import__("json").loads(out[len("RESULT "):])
    assert data["n_bindings"] == 6 and data["unwrapped"] == []


# 2 / 3 —— 提前导入即拒绝
@pytest.mark.unit
@pytest.mark.parametrize("early", ["ssc_a1", "ssc_skill_agent"])
def test_early_import_is_refused(early):
    r = run_py(PRELUDE + f"""
import {early}
try:
    PT.assert_import_order_clean()
    print("RESULT no-error")
except GateConfigError as e:
    print("RESULT refused:", "已在包装前被导入" in str(e))
""")
    assert "RESULT refused: True" in r.stdout, r.stdout + r.stderr


# 4 —— 只替换 ssc_pi_agent、旧绑定仍在 → 拒绝
@pytest.mark.unit
def test_stale_binding_after_partial_patch_is_refused():
    r = run_py(PRELUDE + """
import ssc_a1                       # 抢先导入，持有未包装绑定
gate = mkgate(); roles = fake_roles(gate)
import ssc_pi_agent as P
P.judge_llm = roles["planner"]; P.deepseek_llm_pro = roles["executor"]
stale = not getattr(ssc_a1.judge_llm, "_reumani_hard_gate_wrapped", False)
try:
    PT.assert_bindings_after_import(roles, gate)
    print("RESULT leaked")
except GateConfigError as e:
    print("RESULT refused", stale)
""")
    assert "RESULT refused True" in r.stdout, r.stdout + r.stderr


# 5 —— Shadow 函数内导入拿到包装对象
@pytest.mark.unit
def test_shadow_runtime_import_gets_wrapped_object():
    r = run_py(PRELUDE + """
PT.assert_import_order_clean()
gate = mkgate(); roles = fake_roles(gate)
import ssc_pi_agent as P
P.judge_llm = roles["planner"]; P.deepseek_llm_pro = roles["executor"]
PT.neutralize_unused_paid_clients(gate)
PT.assert_bindings_after_import(roles, gate)
import shadow, pathlib
src = pathlib.Path(shadow.__file__).read_text(encoding="utf-8")
runtime_import = "from ssc_pi_agent import deepseek_llm_pro, judge_llm" in src
import ssc_pi_agent as P2
print("RESULT", runtime_import, P2.deepseek_llm_pro is roles["executor"])
""")
    assert "RESULT True True" in r.stdout, r.stdout + r.stderr


# 6 —— 未包装原始客户端不可从 Pilot 路径访问
@pytest.mark.unit
def test_no_raw_client_reachable_from_pilot_path():
    r = run_py(PRELUDE + """
PT.assert_import_order_clean()
gate = mkgate(); roles = fake_roles(gate)
import ssc_pi_agent as P
P.judge_llm = roles["planner"]; P.deepseek_llm_pro = roles["executor"]
PT.neutralize_unused_paid_clients(gate)
PT.assert_bindings_after_import(roles, gate)
import ssc_a1, ssc_skill_agent
raw = [f"{m.__name__}.{a}" for m in (P, ssc_a1, ssc_skill_agent) for a in PT.PAID_ATTRS
       if getattr(m, a, None) is not None
       and not getattr(getattr(m, a), "_reumani_hard_gate_wrapped", False)]
print("RESULT", json.dumps(raw))
""")
    assert 'RESULT []' in r.stdout, r.stdout + r.stderr


# 7-10 —— preflight 失败时的行为
def _run_preflight(env=None):
    e = dict(os.environ)
    e["PYTHONIOENCODING"] = "utf-8"
    e.update(env or {})
    return subprocess.run([sys.executable, str(ROOT / "pilot" / "preflight_a1.py")],
                          cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=e, timeout=600)


def _snapshot():
    """(round2_results 文件集, runs 目录集, 已有账本的 name->sha256)。"""
    import hashlib
    rr = ROOT / "pilot" / "round2_results"
    runs = ROOT / "runs"
    ledgers = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
               for p in rr.glob("*_ledger*")}
    return (set(rr.glob("*")), set(runs.glob("*")) if runs.exists() else set(), ledgers)


@pytest.mark.unit
def test_preflight_failure_zero_calls_no_new_artifacts_nonzero_exit():
    """**delta 断言**（A.6.4 修正）：失败的 preflight 不得**新增**账本或 run 目录。

    原断言写成"目录里永远不存在任何账本"，会被**历史合法产物**（如 A1 真实运行留下的
    stage1_ledger.jsonl）误判为失败。这里改为对比运行前后的差集，
    既不放宽安全要求，也不要求删除真实运行现场。
    """
    before_files, before_runs, before_ledgers = _snapshot()
    r = _run_preflight({"DEEPSEEK_API_KEY": "", "ANTHROPIC_API_KEY": ""})
    out = (r.stdout or "") + (r.stderr or "")
    after_files, after_runs, after_ledgers = _snapshot()

    assert r.returncode != 0, "失败的 preflight 退出码必须非 0"            # 4
    new_ledgers = set(after_ledgers) - set(before_ledgers)
    assert not new_ledgers, f"失败的 preflight 新建了账本：{new_ledgers}"   # 1/2
    for name, h in before_ledgers.items():                                 # 3
        assert after_ledgers.get(name) == h, f"历史账本被改动：{name}"
    assert not (after_runs - before_runs), "失败的 preflight 新建了 run 目录"
    new = {p.name for p in after_files - before_files}
    assert new <= {"A1_preflight.json"}, f"意外新增产物：{new}"
    for leak in ("sk-", "Bearer ", "Authorization", "Cookie"):
        assert leak not in out, f"preflight 日志泄露 {leak}"                # 5


@pytest.mark.unit
def test_preflight_failure_creates_no_ledger_in_empty_dir(tmp_path, monkeypatch):
    """场景 1：目录本来为空时，失败的 preflight 同样不产生账本。"""
    import hashlib
    rr = ROOT / "pilot" / "round2_results"
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in rr.glob("*_ledger*")}
    r = _run_preflight({"DEEPSEEK_API_KEY": "", "ANTHROPIC_API_KEY": "",
                        "REUMANI_EXPECT_DEV_HEAD": "", "REUMANI_CI_EVIDENCE": ""})
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in rr.glob("*_ledger*")}
    assert r.returncode != 0
    assert set(after) == set(before) and after == before


@pytest.mark.unit
def test_preflight_is_dry_run_by_default_and_never_calls_models():
    src = (ROOT / "pilot" / "preflight_a1.py").read_text(encoding="utf-8")
    assert "--dry-run" in src and "default=True" in src
    for banned in (".invoke(", ".ainvoke(", ".stream(", ".batch("):
        assert banned not in src, f"preflight 不得出现 {banned}"
    assert '"real_api_calls": 0' in src


@pytest.mark.unit
def test_import_order_guard_symbols_exist():
    assert PT.GUARDED_MODULES == ("ssc_a1", "ssc_skill_agent")
    assert "judge_llm" in PT.PAID_ATTRS and "deepseek_llm_pro" in PT.PAID_ATTRS
    assert callable(PT.assert_import_order_clean)
    assert callable(PT.assert_bindings_after_import)
