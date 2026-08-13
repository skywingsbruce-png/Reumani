"""A.8.2b.2b.1 —— 第一批无状态 LLM 消费者的显式角色注入。

覆盖 §1 的十条目标：import 期不拉 legacy / 不读 key / 不构造客户端 / 不建 React Agent、
无模块级付费模型对象、调用边界按角色接收模型、可注入 fake、缺依赖 fail-closed、
公开签名与业务输出兼容、不绕过 Gate。

全部离线：零网络、零真实 key、零付费调用。
"""
import ast
import inspect
import pathlib
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit
REPO = pathlib.Path(__file__).resolve().parent.parent

WAVE1 = ("ssc_writer.py", "ssc_protocol.py", "ssc_evidence.py")


def _src(rel):
    return (REPO / rel).read_text(encoding="utf-8")


def _run(code):
    import os

    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(REPO), timeout=300,
                       env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


class FakeChat:
    """满足 ChatModelProtocol 的假模型。绝不联网。"""

    def __init__(self, reply="FAKE"):
        self.reply, self.calls, self.prompts = reply, 0, []

    def invoke(self, prompt, config=None, **kw):
        self.calls += 1
        self.prompts.append(prompt)
        return type("R", (), {"content": self.reply})()


# ============================================================ §1.1-1.4 import 期
@pytest.mark.parametrize("rel", WAVE1)
def test_import_pulls_no_legacy_no_key_no_client(rel):
    mod = rel[:-3]
    rc, out = _run(
        "import sys, os\n"
        "sys.path.insert(0, '.')\n"
        "import dotenv\n"
        "state = {'dotenv': 0, 'keys': []}\n"
        "def _d(*a, **k):\n"
        "    state['dotenv'] += 1\n"
        "    raise RuntimeError('LOAD_DOTENV_AT_IMPORT')\n"
        "dotenv.load_dotenv = _d\n"
        "P = ('DEEPSEEK_API_KEY', 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY')\n"
        "class C(dict):\n"
        "    def get(self, k, d=None):\n"
        "        if k in P: state['keys'].append(k)\n"
        "        return dict.get(self, k, d)\n"
        "os.environ = C(os.environ)\n"
        f"import {mod}\n"
        "print('LEGACY', 'ssc_pi_agent' in sys.modules)\n"
        "print('DOTENV', state['dotenv'])\n"
        "print('KEYS', len(state['keys']))\n"
        "print('CLIENTLIBS', [m for m in ('langchain_openai','langchain_anthropic')\n"
        "                     if m in sys.modules])")
    assert rc == 0, out
    assert "LEGACY False" in out, out
    assert "DOTENV 0" in out, out
    assert "KEYS 0" in out, out
    assert "CLIENTLIBS []" in out, out


@pytest.mark.parametrize("rel", WAVE1)
def test_no_react_agent_and_no_module_level_paid_model(rel):
    s = _src(rel)
    assert "create_react_agent" not in s
    tree = ast.parse(s)
    for node in tree.body:                       # 模块顶层赋值
        if isinstance(node, ast.Assign):
            for t in node.targets:
                name = getattr(t, "id", "")
                assert "llm" not in name.lower(), f"{rel} 有模块级模型对象 {name}"


@pytest.mark.parametrize("rel", WAVE1)
def test_no_direct_legacy_import_anywhere(rel):
    for node in ast.walk(ast.parse(_src(rel))):
        if isinstance(node, ast.ImportFrom):
            assert node.module != "ssc_pi_agent", f"{rel} 仍 import ssc_pi_agent"
        elif isinstance(node, ast.Import):
            assert all(a.name != "ssc_pi_agent" for a in node.names)


# ============================================================ §1.6-1.7 注入
def test_writer_accepts_injected_model_and_tool():
    import ssc_writer
    fake = FakeChat("DRAFT")
    out = ssc_writer.generate_draft("文献综述", "SSc", "lit", chat_model=fake)
    assert out == "DRAFT" and fake.calls == 1
    assert "SSc" in fake.prompts[0] and "lit" in fake.prompts[0]

    out2 = ssc_writer.refine_draft("history", chat_model=FakeChat("REFINED"))
    assert out2 == "REFINED"

    class FakeTool:
        def __init__(self): self.args = None
        def invoke(self, payload):
            self.args = payload
            return "LIT-LIST"

    ft = FakeTool()
    assert ssc_writer.retrieve_literature("q", max_results=3, search_tool=ft) == "LIT-LIST"
    assert ft.args == {"query": "q", "max_results": 3, "preprints_only": False}


def test_protocol_accepts_injected_model():
    import ssc_protocol
    fake = FakeChat('{"steps": ["a"], "controls": ["c"]}')
    out = ssc_protocol.generate_protocol("测抗体", chat_model=fake)
    assert fake.calls == 1
    assert isinstance(out, dict) or out is not None


def test_evidence_accepts_injected_model():
    import ssc_evidence
    fake = FakeChat('[{"index": 0, "study_type": "RCT"}]')
    paper = {"title": "t", "abstract": "a", "pub_type": "journal-article",
             "authors": "A", "journal": "J", "year": "2026", "date": "2026",
             "link": "http://x"}
    ssc_evidence.make_evidence_cards([paper], chat_model=fake)
    assert fake.calls == 1
    f2 = FakeChat("VERDICT")
    assert ssc_evidence.verify_claim("claim", [], chat_model=f2) == "VERDICT"


# ============================================================ §1.6 角色映射
def test_role_mapping_preserves_pre_migration_semantics():
    """迁移前是 `judge_llm if model == "claude" else deepseek_llm_pro` —— 不得收紧或放宽。"""
    from pilot.legacy_model_bridge import (ROLE_GENERAL_CLAUDE, ROLE_GENERAL_DEEPSEEK,
                                           role_for_model_choice)
    assert role_for_model_choice("claude") == ROLE_GENERAL_CLAUDE
    assert role_for_model_choice("deepseek") == ROLE_GENERAL_DEEPSEEK
    for other in ("", "Claude", "gpt-4", "anything", None):
        assert role_for_model_choice(other) == ROLE_GENERAL_DEEPSEEK


def test_injected_model_wins_over_legacy():
    from pilot.legacy_model_bridge import ROLE_GENERAL_CLAUDE, resolve_chat_model
    fake = FakeChat()
    assert resolve_chat_model(ROLE_GENERAL_CLAUDE, fake) is fake


# ============================================================ §1.8 fail-closed
def test_unknown_role_fails_closed():
    from pilot.legacy_model_bridge import ModelInjectionError, resolve_chat_model
    with pytest.raises(ModelInjectionError):
        resolve_chat_model("no_such_role")
    with pytest.raises(ModelInjectionError):
        resolve_chat_model("synthesizer")        # 受控链角色不在本桥服务范围


def test_injected_object_without_invoke_is_rejected():
    from pilot.legacy_model_bridge import ModelInjectionError, resolve_chat_model
    from pilot.legacy_model_bridge import ROLE_GENERAL_DEEPSEEK
    with pytest.raises(ModelInjectionError, match="invoke"):
        resolve_chat_model(ROLE_GENERAL_DEEPSEEK, object())


def test_missing_legacy_attribute_fails_closed_without_substitute(monkeypatch):
    """拿不到就抛，绝不换一个模型顶替、也不返回 None。"""
    import ssc_pi_agent as P
    from pilot.legacy_model_bridge import (ModelInjectionError, ROLE_GENERAL_CLAUDE,
                                           legacy_chat_model)
    monkeypatch.delattr(P, "judge_llm", raising=False)
    with pytest.raises(ModelInjectionError):
        legacy_chat_model(ROLE_GENERAL_CLAUDE)


def test_unknown_tool_fails_closed():
    from pilot.legacy_model_bridge import ModelInjectionError, legacy_tool
    with pytest.raises(ModelInjectionError):
        legacy_tool("no_such_tool_9137")


# ============================================================ §1.10 不绕过 Gate
def test_bridge_never_caches_so_gate_rebinding_still_applies(monkeypatch):
    """preflight / round2_runner 会把 legacy 属性重绑为 GatedModel；
    桥必须**现取**，否则会拿到未包 Gate 的旧对象。"""
    import ssc_pi_agent as P
    from pilot.legacy_model_bridge import ROLE_GENERAL_CLAUDE, legacy_chat_model

    first = legacy_chat_model(ROLE_GENERAL_CLAUDE)
    sentinel = FakeChat("GATED")
    monkeypatch.setattr(P, "judge_llm", sentinel, raising=False)
    assert legacy_chat_model(ROLE_GENERAL_CLAUDE) is sentinel, "桥缓存了模型 → 会绕过 Gate"
    assert first is not sentinel


def test_bridge_constructs_nothing_and_holds_no_state():
    tree = ast.parse(_src("pilot/legacy_model_bridge.py"))
    # 只看**可执行代码**：剥掉模块/函数/类的 docstring 与 # 注释，
    # 否则"这不是第二套 ProviderRegistry"这样的说明文字会被误判。
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and ast.get_docstring(node):
            node.body = node.body[1:] or [ast.Pass()]
    code = ast.unparse(tree)
    for banned in ("ChatOpenAI(", "ChatAnthropic(", "create_react_agent",
                   "load_dotenv", "ProviderRegistry", "HardBudgetGate"):
        assert banned not in code, f"桥里出现了 {banned}"
    # 模块级只允许常量/协议/函数，不允许可变容器缓存模型
    for node in ast.parse(_src("pilot/legacy_model_bridge.py")).body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                nm = getattr(t, "id", "")
                assert nm.isupper() or nm.startswith("_"), f"桥有非常量模块级状态 {nm}"


def test_bridge_import_is_side_effect_free():
    rc, out = _run(
        "import sys\n"
        "import pilot.legacy_model_bridge as B\n"
        "print('LOADED', [m for m in ('ssc_pi_agent','langchain_openai',\n"
        "                             'langchain_anthropic','dotenv') if m in sys.modules])")
    assert rc == 0, out
    assert "LOADED []" in out, out


# ============================================================ §1.9 签名兼容
def test_public_signatures_stay_backward_compatible():
    import ssc_evidence
    import ssc_protocol
    import ssc_writer

    expected = {
        (ssc_writer.generate_draft, ("scenario", "topic", "literature_text", "model",
                                     "extra_requirement")),
        (ssc_writer.refine_draft, ("history_prompt", "model")),
        (ssc_writer.retrieve_literature, ("query", "max_results", "preprints_only")),
        (ssc_protocol.generate_protocol, ("experiment_description", "model")),
        (ssc_evidence.make_evidence_cards, ("papers", "model")),
        (ssc_evidence.verify_claim, ("claim", "cards", "model")),
    }
    for fn, old_params in expected:
        params = list(inspect.signature(fn).parameters)
        assert params[:len(old_params)] == list(old_params), (
            f"{fn.__name__} 的既有参数顺序/名称改变了：{params}")
        for extra in params[len(old_params):]:   # 新增的必须都是有默认值的关键字参数
            assert inspect.signature(fn).parameters[extra].default is not inspect.Parameter.empty


def test_callers_still_import_the_same_names():
    """page 1 / page 6 / ssc_skill_agent 的 import 语句不需要改。"""
    assert "from ssc_writer import SCENARIOS, retrieve_literature, generate_draft, refine_draft" \
        in _src("pages/1_科研写作助手.py")
    assert "from ssc_protocol import generate_protocol, validate_protocol" \
        in _src("pages/6_实验协议.py")
    assert "from ssc_evidence import" in _src("ssc_skill_agent.py")


def test_pure_functions_are_untouched():
    """不含模型调用的函数不得因迁移而改变。"""
    import ssc_protocol
    ir = {"materials": [{"name": "PBS", "amount": "10 mL"}],
          "steps": [{"desc": "混匀", "volume_ul": 100}],
          "controls": ["阴性对照"], "readouts": ["OD450"]}
    ok, issues = ssc_protocol.validate_protocol(ir)
    assert isinstance(ok, bool) and isinstance(issues, list)
    bad, bad_issues = ssc_protocol.validate_protocol({"error": "x"})
    assert bad is False and bad_issues == ["x"]      # fail-closed 语义未变


# ============================================================ 未迁移模块仍原样
@pytest.mark.parametrize("rel", ("ssc_a1.py", "ssc_skill_agent.py", "ssc_eval.py",
                                 "ssc_action_discovery.py", "pages/7_方向辩论(可选).py",
                                 "pages/9_数据对话.py"))
def test_wave1_did_not_touch_out_of_scope_modules(rel):
    assert "A.8.2b.2b.1" not in _src(rel), f"{rel} 不在本批范围内"


def test_ssc_pi_agent_still_untouched():
    s = _src("ssc_pi_agent.py")
    for marker in ("deepseek_llm_pro = ChatOpenAI(", "judge_llm = ChatAnthropic(",
                   "debater_pro = create_react_agent(", "load_dotenv()"):
        assert marker in s


def test_manifest_still_reports_legacy_as_not_import_safe():
    from pilot.provider_migration import MANIFEST
    assert MANIFEST["legacy_ssc_pi_agent_import_safe"] is False
