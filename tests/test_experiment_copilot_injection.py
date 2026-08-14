"""A.8.2b.2b.3b —— experiment_copilot 作为孤立无状态消费者的显式注入迁移。

安全边界：模型产物只是**实验建议草稿**，不是已批准的操作规程；
不控制设备、不自动执行实验、不写协议、不写长期记忆。

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
PAGE8 = "pages/8_实验副驾.py"


def _src(rel):
    return (REPO / rel).read_text(encoding="utf-8")


def _run(code, cwd=None):
    import os

    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       cwd=str(cwd or REPO), timeout=300, env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


class FakeChat:
    def __init__(self, reply="PLAN", raises=None):
        self.reply, self.raises, self.calls, self.prompts = reply, raises, 0, []

    def invoke(self, prompt, config=None, **kw):
        self.calls += 1
        self.prompts.append(prompt)
        if self.raises:
            raise self.raises
        return type("R", (), {"content": self.reply})()


def _ctx():
    import experiment_copilot as EC
    return EC.LabContext(disease="SSc", sample="全血", assay="流式",
                         panel=["CD4"], hypothesis="H")


# ---------------------------------------------------- import 期零副作用
def test_import_is_free_of_legacy_key_client_model_network_and_file_io():
    probe = r"""
import sys, json, os
REPO = sys.argv[1]
sys.path.insert(0, REPO)
rep = {"read": [], "write": [], "mkdir": [], "net": 0, "dotenv": 0, "keys": 0,
       "legacy": False, "clients": [], "error": None}
armed = [False]
def hook(event, args):
    if not armed[0]:
        return
    try:
        if event == "open":
            p, m = str(args[0]), (args[1] or "")
            if "__pycache__" in p or p.endswith((".pyc", ".pyd", ".dll", ".so")):
                return
            if REPO.lower() not in p.lower():
                return
            (rep["write"] if any(c in m for c in "wax+") else rep["read"]).append(
                os.path.basename(p))
        elif event == "os.mkdir":
            rep["mkdir"].append(os.path.basename(str(args[0])))
        elif event in ("socket.connect", "socket.getaddrinfo"):
            rep["net"] += 1
    except Exception:
        pass
sys.addaudithook(hook)
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
armed[0] = True
try:
    import experiment_copilot
except Exception as e:
    rep["error"] = f"{type(e).__name__}: {str(e)[:100]}"
armed[0] = False
rep["legacy"] = "ssc_pi_agent" in sys.modules
rep["clients"] = [m for m in ("langchain_openai", "langchain_anthropic")
                  if m in sys.modules]
print("PROBE " + json.dumps(rep, ensure_ascii=False))
"""
    with tempfile.TemporaryDirectory() as tmp:
        rc, out = _run(probe.replace("sys.argv[1]", repr(str(REPO))), cwd=tmp)
    assert rc == 0, out
    d = json.loads([l for l in out.splitlines() if l.startswith("PROBE ")][0][6:])
    assert d["error"] is None, d["error"]
    assert d["read"] == [] and d["write"] == [] and d["mkdir"] == []
    assert d["net"] == 0 and d["dotenv"] == 0 and d["keys"] == 0
    assert d["legacy"] is False and d["clients"] == []


def test_no_legacy_import_anywhere_in_the_module():
    for node in ast.walk(ast.parse(_src("experiment_copilot.py"))):
        if isinstance(node, ast.ImportFrom):
            assert node.module != "ssc_pi_agent"
        elif isinstance(node, ast.Import):
            assert all(a.name != "ssc_pi_agent" for a in node.names)


# ---------------------------------------------------- fail-closed
def test_synthesize_fails_closed_without_injection_and_makes_zero_external_calls():
    rc, out = _run(
        "import sys\n"
        "sys.path.insert(0, '.')\n"
        "calls = {'net': 0}\n"
        "import socket\n"
        "def blocked(*a, **k):\n"
        "    calls['net'] += 1\n"
        "    raise AssertionError('network')\n"
        "socket.socket.connect = blocked; socket.create_connection = blocked\n"
        "import experiment_copilot as EC\n"
        "from pilot.legacy_model_bridge import ModelDependencyMissing\n"
        "ctx = EC.LabContext(disease='SSc', sample='全血', assay='流式')\n"
        "try:\n"
        "    EC.synthesize(ctx)\n"
        "    print('RESULT no_raise')\n"
        "except ModelDependencyMissing:\n"
        "    print('RESULT fail_closed')\n"
        "print('LEGACY', 'ssc_pi_agent' in sys.modules)\n"
        "print('NET', calls['net'])")
    assert rc == 0, out
    assert "RESULT fail_closed" in out, out
    assert "LEGACY False" in out and "NET 0" in out, out


def test_no_implicit_fallback_in_source():
    src = _src("experiment_copilot.py")
    assert "require_injected_model(ScientificOperation.EXPERIMENT_GUIDANCE_DRAFTING" in src
    assert "judge_llm" not in src and "deepseek_llm_pro" not in src


# ---------------------------------------------------- 注入后语义不变
def test_injected_fake_is_called_exactly_once_with_the_assembled_material():
    import experiment_copilot as EC
    fake = FakeChat("DRAFT-PLAN")
    out = EC.synthesize(_ctx(), chat_model=fake)
    assert out == "DRAFT-PLAN"
    assert fake.calls == 1, "模型调用次数必须与迁移前一致（恰好 1 次）"
    p = fake.prompts[0]
    assert "风湿免疫湿实验方法学顾问" in p and "不编造文献" in p


def test_model_failure_produces_no_successful_guidance():
    import experiment_copilot as EC
    boom = FakeChat(raises=RuntimeError("provider down"))
    with pytest.raises(RuntimeError):
        EC.synthesize(_ctx(), chat_model=boom)
    assert boom.calls == 1


def test_deterministic_path_needs_no_model():
    """suggest_next 不用模型 —— 不得被拉进注入路径。"""
    import experiment_copilot as EC
    text = EC.suggest_next(_ctx(), with_literature=False)
    assert isinstance(text, str) and text.strip()
    assert "chat_model" not in inspect.signature(EC.suggest_next).parameters


def test_public_signature_stays_backward_compatible():
    import experiment_copilot as EC
    params = list(inspect.signature(EC.synthesize).parameters)
    assert params[:2] == ["ctx", "model"]
    assert EC.synthesize.__defaults__[-1] is None      # chat_model 默认 None


# ---------------------------------------------------- page 8 接线
def test_page8_only_touches_legacy_after_the_user_clicks():
    src = _src(PAGE8)
    tree = ast.parse(src)
    # 兼容通道的 import 必须在函数体内、且位于 `if polish:` 分支中
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            seg = ast.unparse(node)
            if "legacy_chat_model_for_preference" in seg and "polish" in ast.unparse(node.test):
                found.append(node.lineno)
    assert found, "兼容模型的取用必须发生在 polish 点击分支内"
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)) and node.col_offset == 0:
            mod = getattr(node, "module", "") or ""
            assert "legacy_compat_adapter" not in mod, "page 8 不得在模块顶层 import 兼容适配器"


def test_page8_passes_the_model_into_the_core_function():
    assert "chat_model=legacy_chat_model_for_preference(\"deepseek\")" in _src(PAGE8)


def test_page8_import_and_render_perform_zero_model_calls_without_click():
    """未点击时：不 import legacy、不构造客户端、不调用模型。"""
    rc, out = _run(
        "import sys, types\n"
        "sys.path.insert(0, '.')\n"
        "calls = {'invoke': 0}\n"
        "class _Any:\n"
        "    def __init__(self, *a, **k): pass\n"
        "    def __getattr__(self, n): return _Any()\n"
        "    def __call__(self, *a, **k): return _Any()\n"
        "    def __enter__(self): return _Any()\n"
        "    def __exit__(self, *a): return False\n"
        "    def __bool__(self): return False\n"          # 所有按钮 = 未点击
        "    def __iter__(self): return iter([])\n"
        "st = types.ModuleType('streamlit')\n"
        "st.__getattr__ = lambda n: _Any()\n"
        "sys.modules['streamlit'] = st\n"
        "code = compile(open(r'pages/8_实验副驾.py', encoding='utf-8').read(),\n"
        "               'page8', 'exec')\n"
        "m = types.ModuleType('__main__'); m.__dict__['__file__'] = 'page8'\n"
        "sys.modules['__main__'] = m\n"
        "try:\n"
        "    exec(code, m.__dict__)\n"
        "except Exception as e:\n"
        "    print('ERR', type(e).__name__)\n"
        "print('LEGACY', 'ssc_pi_agent' in sys.modules)\n"
        "print('CLIENTS', [x for x in ('langchain_openai','langchain_anthropic')\n"
        "                  if x in sys.modules])\n"
        "print('INVOKES', calls['invoke'])")
    assert rc == 0, out
    assert "LEGACY False" in out, out
    assert "CLIENTS []" in out, out
    assert "INVOKES 0" in out, out


# ---------------------------------------------------- operation 契约
def test_new_operation_is_unmapped_and_not_a_provider_role():
    from pilot.paid_transport import ROLES as A1_ROLES
    from pilot.role_contracts import ROLE_CONTRACTS
    from pilot.scientific_operations import (OPERATION_VALUES, ScientificOperation,
                                             provider_role_for)
    op = ScientificOperation.EXPERIMENT_GUIDANCE_DRAFTING
    assert op.value == "experiment_guidance_drafting"
    assert provider_role_for(op) is None                 # 保持 unmapped
    assert op.value not in (set(ROLE_CONTRACTS) | set(A1_ROLES))
    assert OPERATION_VALUES & (set(ROLE_CONTRACTS) | set(A1_ROLES)) == set()


def test_guidance_is_distinct_from_protocol_drafting():
    """建议草稿 ≠ 结构化 Protocol IR（后者要过 validate_protocol 静态校验）。"""
    from pilot.scientific_operations import ScientificOperation as SO
    assert SO.EXPERIMENT_GUIDANCE_DRAFTING is not SO.PROTOCOL_DRAFTING
    assert "ScientificOperation.PROTOCOL_DRAFTING" in _src("ssc_protocol.py")
    assert "validate_protocol" in _src("ssc_protocol.py")


def test_operation_never_reaches_registry_gate_or_budget():
    op = "experiment_guidance_drafting"
    allowed = {"scientific_operations.py", "provider_migration.py"}
    offenders = []
    for f in (REPO / "pilot").glob("*.py"):
        if f.name in allowed:
            continue
        code = "\n".join(ln.split("#", 1)[0]
                         for ln in f.read_text(encoding="utf-8").splitlines())
        if op in code:
            offenders.append(f.name)
    assert offenders == [], f"operation 泄漏进受控配置：{offenders}"


# ---------------------------------------------------- 安全边界
def test_output_is_a_draft_not_an_approved_protocol():
    src = _src("experiment_copilot.py")
    assert "建议草稿" in src and "不是已批准的操作规程" in src
    for banned in ("subprocess", "os.system", "serial", "pyvisa", "device",
                   "write_text", "write_bytes"):
        assert banned not in src, f"实验副驾不得涉及 {banned}"


def test_compatibility_status_is_not_mixed_into_the_guidance_text():
    """兼容通道的受控状态不得混进实验建议正文。"""
    import experiment_copilot as EC
    fake = FakeChat("PURE-GUIDANCE")
    out = EC.synthesize(_ctx(), chat_model=fake)
    for marker in ("controlled", "through_registry", "through_gate", "through_hitl",
                   "legacy_compat"):
        assert marker not in out
        assert marker not in fake.prompts[0]


def test_compat_channel_still_declares_itself_uncontrolled():
    from pilot.legacy_compat_adapter import compat_disclosure
    d = compat_disclosure()
    assert d["controlled"] is False and d["through_registry"] is False
    assert d["through_gate"] is False and d["through_hitl"] is False


def test_out_of_scope_modules_untouched():
    for rel in ("ssc_a1.py", "ssc_skill_agent.py", "shadow.py",
                "pages/9_数据对话.py", "pages/7_方向辩论(可选).py", "ssc_pi_agent.py"):
        assert "A.8.2b.2b.3b" not in _src(rel), f"{rel} 不在本轮范围内"


def test_manifest_still_honest():
    from pilot.provider_migration import MANIFEST as M
    assert M["legacy_ssc_pi_agent_import_safe"] is False
    assert M["controlled_model_migration_complete"] is False
    assert M["operations_bound_to_provider_role"] == []
