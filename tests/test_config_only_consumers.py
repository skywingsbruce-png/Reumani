"""A.8.2b.2a —— 纯配置消费者迁移的验证。

诚实边界：本轮只拆掉这些模块对 `ssc_pi_agent` 的**直接**依赖。其中 4 个模块仍会
通过它们各自的模型调用依赖（ssc_writer / ssc_a1 / ssc_protocol / ssc_skill_agent）
**间接**拉起 legacy —— 那些属于 A.8.2b.2b / A.8.2b.3 的范围，本轮明令不得触碰。
下面用数据驱动的清单把这个事实锁住，任何一边发生漂移都会失败。

全部离线：零网络、零真实 key、零付费调用。
"""
import ast
import pathlib
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit
REPO = pathlib.Path(__file__).resolve().parent.parent

# 本轮迁移的 5 个纯配置消费者（AST 审计判定 safe_for_A8_2b_2a=True）
MIGRATED = ("ssc_pi_agent_web.py", "pages/1_科研写作助手.py", "pages/4_SSc-A1.py",
            "pages/6_实验协议.py", "pages/8_实验副驾.py")

# 仍未迁移的模型消费者（A.8.2b.2b.1 已迁走 writer/protocol/evidence）
NOT_MIGRATED = ("ssc_eval.py", "ssc_action_discovery.py", "ssc_skill_agent.py",
                "ssc_a1.py", "experiment_copilot.py", "shadow.py",
                "pages/7_方向辩论(可选).py", "pages/9_数据对话.py")

# 迁移后**仍**经由这些依赖间接拉起 legacy 的模块 → 待 A.8.2b.2b/.3 处理
STILL_TRANSITIVE = {
    "ssc_pi_agent_web.py": "ssc_skill_agent",
    "pages/4_SSc-A1.py": "ssc_a1",
}
# 端到端真正 import-safe 的模块。A.8.2b.2b.1 迁走 ssc_writer / ssc_protocol 之后，
# page 1 与 page 6 也随之变干净（实测 legacy=False、dotenv=0）。
FULLY_CLEAN = ("pages/8_实验副驾.py", "pages/1_科研写作助手.py", "pages/6_实验协议.py")


def _src(rel):
    return (REPO / rel).read_text(encoding="utf-8")


def _direct_legacy_imports(rel):
    """AST：该模块自身是否 import ssc_pi_agent（不看注释、不看传递依赖）。"""
    out = []
    for node in ast.walk(ast.parse(_src(rel))):
        if isinstance(node, ast.ImportFrom) and node.module == "ssc_pi_agent":
            out.append(f"from ssc_pi_agent @L{node.lineno}")
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "ssc_pi_agent":
                    out.append(f"import ssc_pi_agent @L{node.lineno}")
    return out


def _module_level_local_deps(rel):
    deps = set()
    for node in ast.walk(ast.parse(_src(rel))):
        if getattr(node, "col_offset", 1) != 0:
            continue
        if isinstance(node, ast.ImportFrom) and node.module:
            deps.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            for a in node.names:
                deps.add(a.name.split(".")[0])
    return {d for d in deps if (REPO / f"{d}.py").exists()}


def _pulls_legacy_at_module_level(mod_name):
    for ln in _src(f"{mod_name}.py").splitlines():
        if ln.startswith(("from ssc_pi_agent import", "import ssc_pi_agent")):
            return True
    return False


# ------------------------------------------------------- 直接依赖已拆除
@pytest.mark.parametrize("rel", MIGRATED)
def test_migrated_modules_no_longer_import_ssc_pi_agent_directly(rel):
    assert _direct_legacy_imports(rel) == [], f"{rel} 仍直接 import ssc_pi_agent"


@pytest.mark.parametrize("rel", MIGRATED)
def test_migrated_modules_use_the_single_config_source(rel):
    assert "pilot.legacy_runtime_config" in _src(rel), f"{rel} 未接到唯一配置来源"


def test_no_second_config_table_was_created():
    """不得复制第二份模型配置表。"""
    names = [p.name for p in (REPO / "pilot").glob("*.py")]
    for banned in ("legacy_settings.py", "model_config.py", "legacy_config.py",
                   "provider_settings.py"):
        assert banned not in names, f"新增了平行配置模块：{banned}"
    # 迁移进来的配置**必须**来自唯一来源，不得在消费者里另起一份默认值
    for rel in MIGRATED:
        s = _src(rel)
        assert 'os.environ.get("DEEPSEEK' not in s, f"{rel} 自己读了 key 环境变量"
        assert "load_dotenv(" not in s, f"{rel} 自己调了 load_dotenv"
        assert "deepseek-chat" not in s, f"{rel} 硬编码了浮动别名"


def test_preexisting_hardcoded_display_label_is_recorded():
    """既存事实（非本轮引入、非 import 依赖）：page 1 有一处硬编码的展示用模型名。

    它不通过 ssc_pi_agent，因此不在 A.8.2b.2a 的迁移目标内；在此登记以免被遗忘。
    """
    assert 'st.info("Claude 主脑：claude-opus-4-8")' in _src("pages/1_科研写作助手.py")


# ------------------------------------------------------- 未迁移的仍原样
@pytest.mark.parametrize("rel", NOT_MIGRATED)
def test_model_calling_consumers_are_untouched(rel):
    assert _direct_legacy_imports(rel), f"{rel} 本轮不应被迁移，但它已不再 import legacy"


def test_ssc_pi_agent_itself_is_untouched():
    s = _src("ssc_pi_agent.py")
    for marker in ("deepseek_llm_pro = ChatOpenAI(", "deepseek_llm_con = ChatOpenAI(",
                   "judge_llm = ChatAnthropic(", "debater_pro = create_react_agent(",
                   "debater_con = create_react_agent(", "judge_agent = create_react_agent(",
                   "load_dotenv()"):
        assert marker in s, f"本轮不得改动 ssc_pi_agent：{marker}"
    assert "__getattr__" not in s               # 未使用 PEP 562


def test_page7_and_page9_were_not_modified():
    """page 7 明令禁止修改；page 9 的模型调用路径本轮不动。"""
    for rel in ("pages/7_方向辩论(可选).py", "pages/9_数据对话.py"):
        assert "A.8.2b.2a" not in _src(rel), f"{rel} 本轮不应被改动"


# ------------------------------------------------------- 诚实的传递依赖清单
@pytest.mark.parametrize("rel,blocker", sorted(STILL_TRANSITIVE.items()))
def test_transitive_legacy_pull_is_recorded_not_hidden(rel, blocker):
    """这些模块**仍**会间接拉起 legacy。清单必须与源码一致（两个方向都不许漂移）。"""
    deps = _module_level_local_deps(rel)
    assert blocker in deps, f"{rel} 不再依赖 {blocker} → 请更新 STILL_TRANSITIVE"
    assert _pulls_legacy_at_module_level(blocker), (
        f"{blocker} 已不再拉起 legacy → {rel} 现在是干净的，请把它移到 FULLY_CLEAN")


@pytest.mark.parametrize("rel", FULLY_CLEAN)
def test_fully_clean_modules_have_no_legacy_path_at_all(rel):
    assert _direct_legacy_imports(rel) == []
    for dep in _module_level_local_deps(rel):
        assert not _pulls_legacy_at_module_level(dep), (
            f"{rel} 经由 {dep} 仍会拉起 legacy → 它不属于 FULLY_CLEAN")


def test_migration_scope_is_exactly_the_audited_set():
    assert set(STILL_TRANSITIVE) | set(FULLY_CLEAN) == set(MIGRATED)


# ------------------------------------------------------- 配置来源仍零副作用
def _run(code):
    import os

    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(REPO), timeout=300,
                       env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def test_config_module_import_is_still_side_effect_free():
    rc, out = _run(
        "import sys\n"
        "import pilot.legacy_runtime_config as C\n"
        "bad = [m for m in ('langchain_openai','langchain_anthropic','ssc_pi_agent','dotenv')\n"
        "       if m in sys.modules]\n"
        "print('LOADED', bad)")
    assert rc == 0, out
    assert "LOADED []" in out, out


def test_display_settings_construct_no_client_or_agent():
    rc, out = _run(
        "import sys\n"
        "from pilot.legacy_runtime_config import legacy_display_settings as L\n"
        "s = L(env={'DEEPSEEK_API_KEY': 'sk-fake-not-real'})\n"
        "bad = [m for m in ('langchain_openai','langchain_anthropic','ssc_pi_agent')\n"
        "       if m in sys.modules]\n"
        "print('LOADED', bad)\n"
        "print('READY', s.deepseek_key_configured)")
    assert rc == 0, out
    assert "LOADED []" in out, out
    assert "READY True" in out


def test_display_settings_make_no_network_call():
    rc, out = _run(
        "import socket\n"
        "def blocked(*a, **k):\n"
        "    raise AssertionError('配置读取发起了网络连接')\n"
        "socket.socket.connect = blocked; socket.create_connection = blocked\n"
        "from pilot.legacy_runtime_config import legacy_display_settings as L\n"
        "L(env={}); print('NO_NETWORK_OK')")
    assert rc == 0, out
    assert "NO_NETWORK_OK" in out


def test_display_settings_never_expose_the_key():
    from pilot.legacy_runtime_config import legacy_display_settings
    s = legacy_display_settings(env={"DEEPSEEK_API_KEY": "sk-super-secret-abcdefgh",
                                     "ANTHROPIC_API_KEY": "sk-ant-secret-ijklmnop"})
    blob = repr(s) + str(s) + str(s.model_dump())
    assert "super-secret" not in blob and "secret-ijkl" not in blob
    assert s.deepseek_key_configured is True and s.anthropic_key_configured is True


# ------------------------------------------------------- 展示语义保持
def test_readiness_semantics_match_the_legacy_boolean():
    from pilot.legacy_runtime_config import legacy_display_settings as L
    assert L(env={}).deepseek_key_configured is False
    assert L(env={"DEEPSEEK_API_KEY": ""}).deepseek_key_configured is False
    assert L(env={"DEEPSEEK_API_KEY": "sk-x-real"}).deepseek_key_configured is True


def test_placeholder_key_is_reported_as_not_ready():
    """唯一有意的语义改动：占位 key 判为未就绪（否则 UI 说'已就绪'然后调用才失败）。"""
    from pilot.legacy_runtime_config import legacy_display_settings as L
    for ph in ("not-configured", "changeme", "your-api-key"):
        assert L(env={"DEEPSEEK_API_KEY": ph}).deepseek_key_configured is False


def test_model_label_reflects_what_legacy_actually_uses():
    """展示必须与 legacy 客户端真实使用的模型名一致，不能显示成钉死版本（那是撒谎）。"""
    from pilot.legacy_runtime_config import (FORBIDDEN_DEEPSEEK_DISPLAY_DEFAULT,
                                             legacy_display_settings as L)
    legacy_default = _src("ssc_pi_agent.py").split('DEEPSEEK_MODEL = os.environ.get(')[1]
    assert FORBIDDEN_DEEPSEEK_DISPLAY_DEFAULT in legacy_default, "展示默认值与 legacy 不一致"
    assert L(env={}).deepseek_model_label == FORBIDDEN_DEEPSEEK_DISPLAY_DEFAULT
    assert L(env={"DEEPSEEK_MODEL": "deepseek-v9"}).deepseek_model_label == "deepseek-v9"


def test_display_default_is_never_used_to_build_a_client():
    """展示用的浮动别名绝不能进入任何客户端构造路径。"""
    from pilot.legacy_provider_specs import LEGACY_SPECS
    from pilot.legacy_runtime_config import (DEFAULT_DEEPSEEK_MODEL,
                                             FORBIDDEN_DEEPSEEK_DISPLAY_DEFAULT)
    from pilot.paid_transport import FORBIDDEN_DEEPSEEK
    assert FORBIDDEN_DEEPSEEK_DISPLAY_DEFAULT in FORBIDDEN_DEEPSEEK
    assert DEFAULT_DEEPSEEK_MODEL not in FORBIDDEN_DEEPSEEK
    for s in LEGACY_SPECS:
        assert s.model_id != FORBIDDEN_DEEPSEEK_DISPLAY_DEFAULT
    factory_src = (REPO / "pilot" / "legacy_provider_factory.py").read_text(encoding="utf-8")
    assert "FORBIDDEN_DEEPSEEK_DISPLAY_DEFAULT" not in factory_src


def test_manifest_still_reports_legacy_as_not_import_safe():
    from pilot.provider_migration import MANIFEST
    assert MANIFEST["legacy_ssc_pi_agent_import_safe"] is False


# =========================================================================
# A.8.2b.2a.1 —— import 期不得加载任何密钥/配置
# =========================================================================

# 5 个迁移模块全部采用显式渲染边界（page 8 的调用原本在顶层 if 分支里，一并收进函数）
BOUNDARY_PAGES = MIGRATED          # 5 个迁移模块统一采用显式渲染边界

# import 期一律禁止出现在**模块顶层**的配置/密钥调用
FORBIDDEN_AT_MODULE_LEVEL = ("legacy_display_settings", "from_environment",
                             "load_local_dotenv_then_environment", "secret_for",
                             "load_dotenv")


def _module_level_calls(rel):
    """AST：模块顶层（含顶层 if/with/for 体内）实际会在 import 时执行的调用名。"""
    tree = ast.parse(_src(rel))
    names = []

    def walk_stmt(stmt):
        for node in ast.walk(stmt):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue                        # 函数体不在 import 期执行
            if isinstance(node, ast.Call):
                f = node.func
                n = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
                if n:
                    names.append(n)

    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        walk_stmt(stmt)
    return names


@pytest.mark.parametrize("rel", MIGRATED)
def test_no_secret_or_config_loading_at_module_level(rel):
    calls = _module_level_calls(rel)
    bad = [c for c in calls if c in FORBIDDEN_AT_MODULE_LEVEL]
    assert not bad, f"{rel} 在模块顶层执行了配置/密钥读取：{bad}"


@pytest.mark.parametrize("rel", BOUNDARY_PAGES)
def test_pages_expose_an_explicit_render_boundary(rel):
    s = _src(rel)
    assert "def get_display_settings():" in s, f"{rel} 缺少显式配置边界"
    assert "def render_page(settings=None):" in s, f"{rel} 缺少显式渲染边界"
    assert 'if __name__ == "__main__":' in s and "render_page()" in s, (
        f"{rel} 缺少执行守卫 —— Streamlit 以 __main__ 执行脚本，普通 import 则不应渲染")


@pytest.mark.parametrize("rel", BOUNDARY_PAGES)
def test_render_page_is_injectable_for_testing(rel):
    """settings 可注入 → 渲染路径可在不碰环境的前提下被测试。"""
    assert "settings = settings or get_display_settings()" in _src(rel)


# ---- 子进程实测：区分 class-1（本轮配置副作用）与 class-2（旧模型副作用） ----
_PROBE = r'''
import sys, types, json, os
MODE = sys.argv[2]
report = {"load_dotenv": 0, "key_reads": [], "rendered": 0, "error": None}

import dotenv
def _d(*a, **k):
    report["load_dotenv"] += 1
    return True
dotenv.load_dotenv = _d

PROTECTED = ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")
class Counting(dict):
    def get(self, k, d=None):
        if k in PROTECTED: report["key_reads"].append(k)
        return dict.get(self, k, d)
    def __getitem__(self, k):
        if k in PROTECTED: report["key_reads"].append(k)
        return dict.__getitem__(self, k)
os.environ = Counting(os.environ)

class _Any:
    def __init__(self, *a, **k): pass
    def __getattr__(self, n): return _Any()
    def __call__(self, *a, **k): return _Any()
    def __enter__(self): return _Any()
    def __exit__(self, *a): return False
    def __bool__(self): return False
    def __iter__(self): return iter([])
st = types.ModuleType("streamlit")
st.set_page_config = lambda *a, **k: report.__setitem__("rendered", report["rendered"] + 1)
st.__getattr__ = lambda n: _Any()
sys.modules["streamlit"] = st

# class-2 打桩：旧模型消费者经 ssc_pi_agent 的副作用属于 A.8.2b.2b 阻塞项
for _m in ("ssc_a1", "ssc_writer", "ssc_protocol", "ssc_skill_agent",
           "experiment_copilot", "lab_knowledge", "skill_loader"):
    _s = types.ModuleType(_m); _s.__getattr__ = lambda n: _Any(); sys.modules[_m] = _s

TARGET = sys.argv[1]
try:
    code = compile(open(TARGET, encoding="utf-8").read(), TARGET, "exec")
    mod = types.ModuleType("__main__" if MODE == "streamlit" else "_probe")
    mod.__dict__["__file__"] = TARGET
    if MODE == "streamlit":
        sys.modules["__main__"] = mod
    exec(code, mod.__dict__)
except Exception as e:
    report["error"] = f"{type(e).__name__}: {str(e)[:110]}"
print("PROBE_JSON " + json.dumps(report, ensure_ascii=False))
'''


def _probe(rel, mode):
    import json
    import os

    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    r = subprocess.run([sys.executable, "-c", _PROBE, rel, mode], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", cwd=str(REPO),
                       timeout=300, env=env)
    ln = [l for l in (r.stdout or "").splitlines() if l.startswith("PROBE_JSON ")]
    assert ln, f"探针未产出结果：{(r.stdout or '')[-400:]}{(r.stderr or '')[-400:]}"
    return json.loads(ln[0][len("PROBE_JSON "):])


@pytest.mark.parametrize("rel", MIGRATED)
def test_plain_import_loads_no_dotenv_and_reads_no_key(rel):
    d = _probe(rel, "import")
    assert d["load_dotenv"] == 0, f"{rel} import 期调用了 load_dotenv"
    assert d["key_reads"] == [], f"{rel} import 期读取了 {sorted(set(d['key_reads']))}"


@pytest.mark.parametrize("rel", BOUNDARY_PAGES)
def test_plain_import_does_not_render(rel):
    assert _probe(rel, "import")["rendered"] == 0, f"{rel} import 期就渲染了"


@pytest.mark.parametrize("rel", BOUNDARY_PAGES)
def test_streamlit_style_execution_still_renders_and_reads_config(rel):
    """Streamlit 以 __main__ 执行脚本 —— 渲染必须发生，配置在渲染边界读取。"""
    d = _probe(rel, "streamlit")
    assert d["rendered"] >= 1, f"{rel} 在 Streamlit 执行模式下没有渲染：{d['error']}"
    assert d["load_dotenv"] >= 1, f"{rel} 渲染时未读取配置"
    assert "DEEPSEEK_API_KEY" in d["key_reads"]
