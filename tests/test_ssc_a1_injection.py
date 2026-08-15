"""A.8.2b.2b.3c —— ssc_a1 的 Planner / Verifier / Shadow-Claim 显式注入。

Executor **不迁移**：它经 ssc_skill_agent.build_skill_agent 取模型，而该模块有
import 期的 skill_agent 单例。本轮不碰 ssc_skill_agent，也不改 Executor 行为。

全部离线：零网络、零真实 key、零付费调用。
"""
import ast
import inspect
import json
import pathlib
import subprocess
import sys
import tempfile

import pytest

pytestmark = pytest.mark.unit
REPO = pathlib.Path(__file__).resolve().parent.parent
PAGE4 = "pages/4_SSc-A1.py"


def _src(rel):
    return (REPO / rel).read_text(encoding="utf-8")


def _code_only(rel):
    """只保留**可执行代码**：剥掉 docstring 与 # 注释。

    文档里解释"绝不回退到 shadow.default_claim_extractor"是应该的，那不是依赖。
    """
    tree = ast.parse(_src(rel))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and ast.get_docstring(node):
            node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _run(code, cwd=None):
    import os

    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(cwd or REPO),
                       timeout=300, env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


class FakeChat:
    def __init__(self, reply="OK", raises=None):
        self.reply, self.raises, self.calls = reply, raises, 0

    def invoke(self, prompt, config=None, **kw):
        self.calls += 1
        if self.raises:
            raise self.raises
        return type("R", (), {"content": self.reply})()


def _state(**kw):
    import ssc_a1
    s = ssc_a1.AgentState(user_query="Q", plan="P")
    for k, v in kw.items():
        setattr(s, k, v)
    return s


# ==================================================== §12 静态边界
def test_ssc_a1_has_no_legacy_or_client_or_key_references():
    tree = ast.parse(_src("ssc_a1.py"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module != "ssc_pi_agent"
            assert node.module != "pilot.legacy_compat_adapter"
        elif isinstance(node, ast.Import):
            assert all(a.name != "ssc_pi_agent" for a in node.names)
    code = _code_only("ssc_a1.py")
    for banned in ("judge_llm", "deepseek_llm_pro", "ChatOpenAI(", "ChatAnthropic(",
                   "load_dotenv", "legacy_compat_adapter", "DEEPSEEK_API_KEY",
                   "ANTHROPIC_API_KEY", "default_claim_extractor"):
        assert banned not in code, f"ssc_a1 仍含 {banned}"
    for node in tree.body:                                   # 无模块级模型对象
        if isinstance(node, ast.Assign):
            for t in node.targets:
                assert "llm" not in getattr(t, "id", "").lower()


@pytest.mark.parametrize("rel", ("ssc_skill_agent.py", "shadow.py", "experiment_copilot.py",
                                 "pages/9_数据对话.py", "pages/7_方向辩论(可选).py",
                                 "ssc_pi_agent.py"))
def test_out_of_scope_files_untouched(rel):
    assert "A.8.2b.2b.3c" not in _src(rel), f"{rel} 本轮不得改动"


def test_module_level_skill_agent_singleton_still_present():
    """本轮**不得**删除它 —— 这正是 Executor 被阻塞的原因。"""
    assert 'skill_agent = build_skill_agent("deepseek")' in _src("ssc_skill_agent.py")


# ==================================================== §10 import safety
def test_import_ssc_a1_pulls_no_legacy_no_key_no_model():
    probe = r"""
import sys, json, os
REPO = %r
sys.path.insert(0, REPO)
rep = {"net": 0, "dotenv": 0, "keys": 0, "legacy": False, "clients": [],
       "react": 0, "error": None}
import dotenv
def _d(*a, **k):
    rep["dotenv"] += 1
    return True
dotenv.load_dotenv = _d
P = ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")
class C(dict):
    def get(self, k, d=None):
        if k in P: rep["keys"] += 1
        return dict.get(self, k, d)
os.environ = C(os.environ)
import socket
def blocked(*a, **k):
    rep["net"] += 1
    raise AssertionError("network")
socket.socket.connect = blocked; socket.create_connection = blocked
try:
    import ssc_a1
except Exception as e:
    rep["error"] = f"{type(e).__name__}: {str(e)[:120]}"
rep["legacy"] = "ssc_pi_agent" in sys.modules
rep["clients"] = [m for m in ("langchain_openai", "langchain_anthropic")
                  if m in sys.modules]
rep["react"] = 1 if "ssc_skill_agent" in sys.modules else 0
print("PROBE " + json.dumps(rep, ensure_ascii=False))
""" % str(REPO)
    rc, out = _run(probe)
    assert rc == 0, out
    d = json.loads([l for l in out.splitlines() if l.startswith("PROBE ")][0][6:])
    assert d["error"] is None, d["error"]
    assert d["legacy"] is False, "import ssc_a1 仍拉起 ssc_pi_agent"
    assert d["clients"] == [] and d["react"] == 0
    assert d["dotenv"] == 0 and d["keys"] == 0 and d["net"] == 0


def test_runs_dir_is_not_created_at_import():
    src = _src("ssc_a1.py")
    tree = ast.parse(src)
    for node in tree.body:
        for sub in ast.walk(node):
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                break
            if isinstance(sub, ast.Call) and getattr(sub.func, "attr", "") == "mkdir":
                pytest.fail(f"模块顶层仍有 mkdir @L{sub.lineno}")
    assert "RUNS_DIR.mkdir(exist_ok=True)            # 写之前才建目录" in src


def test_remaining_import_io_is_recorded_as_transitive():
    """诚实登记：剩余 import 期文件 I/O 来自 ssc_resources，不是 ssc_a1 自身。"""
    from pilot.provider_migration import MANIFEST as M
    assert M["ssc_a1_remaining_import_io"], "剩余传递副作用必须登记"
    assert "ssc_resources" in M["ssc_a1_remaining_import_io"][0]


# ==================================================== §3 Planner
def test_planner_fails_closed_without_injection():
    import ssc_a1
    from pilot.legacy_model_bridge import ModelDependencyMissing
    with pytest.raises(ModelDependencyMissing):
        ssc_a1.plan(_state())


def test_planner_uses_injected_fake():
    import ssc_a1
    fake = FakeChat("PLAN-TEXT")
    assert ssc_a1.plan(_state(), planner_model=fake) == "PLAN-TEXT"
    assert fake.calls == 1


def test_run_agent_fails_closed_before_reaching_executor(monkeypatch):
    """Planner 缺失/异常时不得进入 Executor。"""
    import ssc_a1
    from pilot.legacy_model_bridge import ModelDependencyMissing
    hits = {"exec": 0}
    monkeypatch.setattr(ssc_a1, "execute",
                        lambda *a, **k: hits.__setitem__("exec", hits["exec"] + 1))
    with pytest.raises(ModelDependencyMissing):
        ssc_a1.run_agent("Q", shadow=False)
    assert hits["exec"] == 0, "Planner 未注入时仍进入了 Executor"


# ==================================================== §4 Verifier
def test_verifier_fails_closed_without_model_or_call():
    import ssc_a1
    r = ssc_a1.verify(_state(evidence_cards=[{"x": 1}]), "output")
    assert r["passed"] is False
    assert r["status"] == "verifier_unavailable"


def test_verifier_uses_injected_model():
    import ssc_a1
    fake = FakeChat('{"passed": true, "reason": "ok", "missing": "无"}')
    r = ssc_a1.verify(_state(evidence_cards=[{"x": 1}]), "out", verifier_model=fake)
    assert r["passed"] is True and fake.calls == 1


def test_verifier_call_takes_precedence_over_verifier_model():
    """两者同时提供时：`verifier_call` 优先，`verifier_model` **不被使用**。"""
    import ssc_a1
    unused = FakeChat('{"passed": true, "reason": "model", "missing": "无"}')
    seen = {"n": 0}

    def my_call(prompt, judge_model):
        seen["n"] += 1
        return '{"passed": true, "reason": "call", "missing": "无"}'

    r = ssc_a1.verify(_state(evidence_cards=[{"x": 1}]), "out",
                      verifier_call=my_call, verifier_model=unused)
    assert r["reason"] == "call" and seen["n"] == 1
    assert unused.calls == 0, "verifier_call 存在时不得使用 verifier_model"


@pytest.mark.parametrize("reply,status", [
    ("", "verification_error"),
    ("not json", "verification_error"),
    ("[1, 2, 3]", "verification_error"),
])
def test_verifier_malformed_output_fails_closed(reply, status):
    import ssc_a1
    r = ssc_a1.verify(_state(evidence_cards=[{"x": 1}]), "out",
                      verifier_model=FakeChat(reply))
    assert r["passed"] is False and r["status"] == status


def test_verifier_exception_fails_closed():
    import ssc_a1
    r = ssc_a1.verify(_state(evidence_cards=[{"x": 1}]), "out",
                      verifier_model=FakeChat(raises=RuntimeError("down")))
    assert r["passed"] is False and r["status"] == "verifier_unavailable"


def test_verifier_scientific_criteria_unchanged():
    src = _src("ssc_a1.py")
    assert "你是严格的 Verifier" in src
    assert 'if tool_failed:' in src and '"tool_execution_failed"' in src
    assert '"insufficient_evidence"' in src


# ==================================================== §5 Claim Extractor
def test_shadow_requires_explicit_claim_extractor():
    import ssc_a1
    from pilot.legacy_model_bridge import ModelDependencyMissing
    with pytest.raises(ModelDependencyMissing, match="claim_extractor"):
        ssc_a1._require_claim_extractor(None)
    sentinel = object()
    assert ssc_a1._require_claim_extractor(sentinel) is sentinel


def test_no_legacy_default_claim_extractor_call():
    assert "default_claim_extractor" not in _code_only("ssc_a1.py")
    assert "from shadow import run_shadow" in _src("ssc_a1.py")


def test_shadow_disabled_means_zero_claim_calls(monkeypatch):
    import ssc_a1
    calls = {"claim": 0, "shadow": 0}
    import planner

    class _P:
        def model_dump(self): return {"steps": []}

    monkeypatch.setattr(planner, "make_plan", lambda *a, **k: _P())
    monkeypatch.setattr(planner, "render_plan_text", lambda p: "PLAN-TEXT")
    monkeypatch.setattr(ssc_a1, "execute", lambda *a, **k: ("out [PMID:1]", ["m"]))
    monkeypatch.setattr(ssc_a1, "_save_run", lambda *a, **k: "")
    monkeypatch.setattr(ssc_a1, "_require_claim_extractor",
                        lambda c: calls.__setitem__("claim", calls["claim"] + 1))
    ssc_a1.run_agent("Q", shadow=False, planner_model=FakeChat("PLAN"),
                     verifier_model=FakeChat('{"passed": true, "reason": "r", "missing": "无"}'))
    assert calls["claim"] == 0, "shadow=False 时 Claim 调用必须为 0"


# ==================================================== §9 最终裁决权
def _run_once(monkeypatch, verifier_reply, **kw):
    """Planner 的**计划内容**不是本轮重点：把 make_plan 换成确定性假计划，
    以便隔离出"只有 Verifier 能决定通过"这一条裁决语义。"""
    import planner
    import ssc_a1

    class _Plan:
        def model_dump(self): return {"steps": []}

    monkeypatch.setattr(planner, "make_plan", lambda *a, **k: _Plan())
    monkeypatch.setattr(planner, "render_plan_text", lambda p: "PLAN-TEXT")
    monkeypatch.setattr(ssc_a1, "execute", lambda *a, **k: ("out [PMID:1]", ["m"]))
    monkeypatch.setattr(ssc_a1, "_save_run", lambda *a, **k: "")
    return ssc_a1.run_agent("Q", shadow=False, max_iterations=1,
                            planner_model=FakeChat("PLAN"),
                            verifier_model=FakeChat(verifier_reply), **kw)


def test_only_verifier_passed_true_yields_a_successful_answer(monkeypatch):
    s = _run_once(monkeypatch, '{"passed": true, "reason": "r", "missing": "无"}')
    assert s.final_answer == "out [PMID:1]"
    assert s.verification_results[-1]["passed"] is True


@pytest.mark.parametrize("reply", [
    '{"passed": false, "reason": "no", "missing": "x"}',
    '{"passed": "true", "reason": "字符串不算", "missing": "无"}',
    "garbage",
])
def test_non_true_verdict_never_yields_a_successful_answer(monkeypatch, reply):
    s = _run_once(monkeypatch, reply)
    assert s.verification_results[-1]["passed"] is not True
    assert "未经核实" in s.final_answer or "未通过" in s.final_answer


def test_final_verdict_condition_is_unchanged():
    assert 'if v.get("passed") is True:' in _src("ssc_a1.py")


# ==================================================== §7 Executor 不变
def test_executor_behaviour_is_untouched():
    src = _src("ssc_a1.py")
    body = src[src.index("def execute("):src.index("VERIFY_TIMEOUT")]
    assert "from ssc_skill_agent import build_skill_agent" in body   # 仍惰性
    assert "build_skill_agent(executor_model, allowed_tools=state.allowed_tools or None)" in body
    assert "require_injected_model" not in body                      # 未被改成注入路径
    import ssc_a1
    assert list(inspect.signature(ssc_a1.execute).parameters) == ["state", "executor_model"]


def test_manifest_marks_executor_as_blocked():
    from pilot.provider_migration import MANIFEST as M
    assert M["executor_migrated"] is False
    assert M["executor_blocked_by_ssc_skill_agent"] is True
    assert M["ssc_a1_controlled_registry_wired"] is False


# ==================================================== §8 page 4
def test_page4_only_touches_legacy_after_the_user_clicks():
    tree = ast.parse(_src(PAGE4))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and "legacy_chat_model_for_preference" in ast.unparse(node):
            if "st.button" in ast.unparse(node.test):
                found.append(node.lineno)
    assert found, "兼容模型的取用必须在运行按钮分支内"
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)) and node.col_offset == 0:
            assert "legacy_compat_adapter" not in (getattr(node, "module", "") or "")


def test_page4_passes_all_three_dependencies():
    src = _src(PAGE4)
    for kw in ("planner_model=_planner", "verifier_model=_verifier",
               "claim_extractor=_claim"):
        assert kw in src, f"page 4 未传入 {kw}"


def test_page4_import_makes_zero_model_calls_without_click():
    rc, out = _run(
        "import sys, types\n"
        "sys.path.insert(0, '.')\n"
        "class _Any:\n"
        "    def __init__(self, *a, **k): pass\n"
        "    def __getattr__(self, n): return _Any()\n"
        "    def __call__(self, *a, **k): return _Any()\n"
        "    def __enter__(self): return _Any()\n"
        "    def __exit__(self, *a): return False\n"
        "    def __bool__(self): return False\n"
        "    def __iter__(self): return iter([])\n"
        "st = types.ModuleType('streamlit'); st.__getattr__ = lambda n: _Any()\n"
        "sys.modules['streamlit'] = st\n"
        "code = compile(open(r'pages/4_SSc-A1.py', encoding='utf-8').read(), 'p4', 'exec')\n"
        "m = types.ModuleType('__main__'); m.__dict__['__file__'] = 'p4'\n"
        "sys.modules['__main__'] = m\n"
        "try:\n"
        "    exec(code, m.__dict__)\n"
        "except Exception as e:\n"
        "    print('ERR', type(e).__name__)\n"
        "print('LEGACY', 'ssc_pi_agent' in sys.modules)\n"
        "print('CLIENTS', [x for x in ('langchain_openai','langchain_anthropic')\n"
        "                  if x in sys.modules])")
    assert rc == 0, out
    assert "LEGACY False" in out, out
    assert "CLIENTS []" in out, out


# ==================================================== §6 operation 契约
def test_new_operations_exist_unmapped_and_are_not_provider_roles():
    from pilot.paid_transport import ROLES as A1_ROLES
    from pilot.role_contracts import ROLE_CONTRACTS
    from pilot.scientific_operations import (OPERATION_VALUES, ScientificOperation,
                                             provider_role_for)
    for op in (ScientificOperation.RESEARCH_PLANNING,
               ScientificOperation.RESEARCH_VERIFICATION,
               ScientificOperation.SHADOW_CLAIM_EXTRACTION):
        assert provider_role_for(op) is None                 # 保持 unmapped
        assert op.value in OPERATION_VALUES
    # 名字相似不等于同一层：planner/verifier/claim_extractor 是 ProviderRole
    provider_roles = set(ROLE_CONTRACTS) | set(A1_ROLES)
    assert OPERATION_VALUES & provider_roles == set()
    for v in OPERATION_VALUES:
        for banned in ("claude", "deepseek", "anthropic", "opus", "gpt"):
            assert banned not in v


def test_operations_never_reach_registry_gate_or_budget():
    ops = ("research_planning", "research_verification", "shadow_claim_extraction")
    allowed = {"scientific_operations.py", "provider_migration.py"}
    offenders = []
    for f in (REPO / "pilot").glob("*.py"):
        if f.name in allowed:
            continue
        code = "\n".join(ln.split("#", 1)[0]
                         for ln in f.read_text(encoding="utf-8").splitlines())
        offenders += [f"{f.name}:{o}" for o in ops if o in code]
    assert offenders == [], f"operation 泄漏进受控配置：{offenders}"


# ==================================================== §13 manifest
def test_manifest_flags_are_exactly_as_specified():
    from pilot.provider_migration import MANIFEST as M
    for k, v in (("ssc_a1_import_decoupled", True),
                 ("planner_explicit_injection", True),
                 ("verifier_explicit_injection", True),
                 ("shadow_claim_explicit_injection", True),
                 ("planner_implicit_fallback", False),
                 ("verifier_implicit_fallback", False),
                 ("claim_implicit_fallback", False),
                 ("executor_migrated", False),
                 ("executor_blocked_by_ssc_skill_agent", True),
                 ("ssc_a1_controlled_registry_wired", False),
                 ("page4_compat_entrypoint", True),
                 ("legacy_compat_is_controlled", False),
                 ("controlled_model_migration_complete", False),
                 ("legacy_ssc_pi_agent_import_safe", False),
                 ("blocks_A8_3_until_A8_2b", True)):
        assert M[k] is v, f"{k} 应为 {v}"
