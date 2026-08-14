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


def _imported_modules(rel) -> set:
    """该文件**真实 import** 的模块名（含函数内惰性 import）。不看字符串/注释。"""
    names = set()
    for node in ast.walk(ast.parse(_src(rel))):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
    return names


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


# ============================================================ 核心路径 fail-closed
def test_core_resolver_has_no_implicit_fallback():
    """A.8.2b.2b.1.1：核心 resolver 不接受"注入为空"，也不去 legacy 里找替身。"""
    from pilot.legacy_model_bridge import (ModelDependencyMissing, ScientificOperation,
                                           require_injected_model)
    op = ScientificOperation.LITERATURE_DRAFTING
    with pytest.raises(ModelDependencyMissing, match="explicit model required"):
        require_injected_model(op)
    with pytest.raises(ModelDependencyMissing):
        require_injected_model(op, None)


def test_core_module_never_imports_legacy_in_any_path():
    """源码层：核心模块任何分支都不得 **import** legacy 或兼容适配器。

    判定基于 AST 的真实 import，不看字符串 —— 错误消息里指路
    "请显式使用 pilot.legacy_compat_adapter" 是应该的，那不是依赖。
    """
    assert _imported_modules("pilot/legacy_model_bridge.py") & {
        "ssc_pi_agent", "pilot.legacy_compat_adapter"} == set()


def test_core_module_runtime_never_loads_legacy():
    """运行时：把核心 resolver 的失败路径全跑一遍，legacy 不得被 import。"""
    rc, out = _run(
        "import sys\n"
        "from pilot.legacy_model_bridge import (ModelDependencyMissing,\n"
        "                                       ScientificOperation,\n"
        "                                       require_injected_model,\n"
        "                                       require_injected_tool)\n"
        "for fn, args in ((require_injected_model, (ScientificOperation.CLAIM_VERIFICATION,)),\n"
        "                 (require_injected_model, ('no_such_role',)),\n"
        "                 (require_injected_tool, ('search_literature',))):\n"
        "    try:\n"
        "        fn(*args)\n"
        "    except ModelDependencyMissing:\n"
        "        pass\n"
        "print('LEGACY', 'ssc_pi_agent' in sys.modules)")
    assert rc == 0, out
    assert "LEGACY False" in out, out


@pytest.mark.parametrize("call", [
    "ssc_writer.generate_draft('文献综述', 't', 'lit')",
    "ssc_writer.refine_draft('h')",
    "ssc_writer.retrieve_literature('q')",
    "ssc_protocol.generate_protocol('d')",
    "ssc_evidence.make_evidence_cards([{'title':'t','abstract':'a','pub_type':'j',"
    "'authors':'A','journal':'J','year':'2026','date':'2026','link':'x'}])",
    "ssc_evidence.verify_claim('c', [])",
])
def test_consumers_fail_closed_without_explicit_injection(call):
    """不注入就必须抛，且**不得**在过程中 import ssc_pi_agent。"""
    rc, out = _run(
        "import sys\n"
        "sys.path.insert(0, '.')\n"
        "import ssc_writer, ssc_protocol, ssc_evidence\n"
        "from pilot.legacy_model_bridge import ModelDependencyMissing\n"
        "try:\n"
        f"    {call}\n"
        "    print('RESULT no_raise')\n"
        "except ModelDependencyMissing:\n"
        "    print('RESULT fail_closed')\n"
        "print('LEGACY', 'ssc_pi_agent' in sys.modules)")
    assert rc == 0, out
    assert "RESULT fail_closed" in out, out
    assert "LEGACY False" in out, out


def test_injected_object_without_invoke_is_rejected():
    from pilot.legacy_model_bridge import (ModelInjectionError, ScientificOperation,
                                           require_injected_model)
    with pytest.raises(ModelInjectionError, match="invoke"):
        require_injected_model(ScientificOperation.PROTOCOL_DRAFTING, object())


def test_unknown_role_fails_closed():
    from pilot.legacy_model_bridge import ModelDependencyMissing, require_injected_model
    for bad in ("no_such_role", "synthesizer", "general_claude", "general_deepseek"):
        with pytest.raises(ModelDependencyMissing):
            require_injected_model(bad, FakeChat())


def test_operations_are_scientific_not_provider_names():
    """operation 必须描述科研步骤；provider 名称不得充当 operation。"""
    from pilot.scientific_operations import OPERATION_VALUES
    assert OPERATION_VALUES == {"literature_drafting", "literature_revision",
                                "protocol_drafting", "evidence_extraction",
                                "claim_verification"}
    for banned in ("claude", "deepseek", "anthropic", "opus", "general"):
        assert not any(banned in v for v in OPERATION_VALUES), f"operation 混入 provider 词 {banned}"


def test_operation_and_provider_role_vocabularies_are_disjoint():
    """A.8.2b.2b.1.2：两层词汇必须互不相交，且 operation 不得冒充 role。"""
    from pilot.paid_transport import ROLES as A1_ROLES
    from pilot.role_contracts import ROLE_CONTRACTS
    from pilot.scientific_operations import OPERATION_VALUES

    provider_roles = set(ROLE_CONTRACTS) | set(A1_ROLES)
    assert OPERATION_VALUES & provider_roles == set(), "operation 与 ProviderRole 词汇重叠"
    # 受控链角色仍是原来那三个 —— 本轮不得往里加 operation
    assert set(ROLE_CONTRACTS) == {"synthesizer", "verifier", "claim_extractor"}


def test_operations_never_reach_registry_gate_or_budget():
    """五个 operation 名不得出现在任何注册 / 计费 / 预算的可执行配置里。"""
    import pathlib as _pl
    from pilot.scientific_operations import OPERATION_VALUES

    allowed = {"scientific_operations.py", "legacy_model_bridge.py", "provider_migration.py"}
    offenders = []
    for f in (_pl.Path(REPO) / "pilot").glob("*.py"):
        if f.name in allowed:
            continue
        code = "\n".join(ln.split("#", 1)[0] for ln in
                         f.read_text(encoding="utf-8").splitlines())
        for op in OPERATION_VALUES:
            if op in code:
                offenders.append(f"{f.name}:{op}")
    assert offenders == [], f"operation 名泄漏进了受控配置：{offenders}"


def test_operation_is_rejected_where_a_provider_role_is_required():
    from pilot.scientific_operations import (ScientificOperationError,
                                             assert_not_a_provider_role)
    assert_not_a_provider_role("synthesizer")          # 真正的 role 放行
    for op in ("literature_drafting", "claim_verification"):
        with pytest.raises(ScientificOperationError, match="不是 ProviderRole"):
            assert_not_a_provider_role(op)


def test_no_operation_is_bound_to_a_controlled_provider_role_yet():
    """现状是全部未绑定；谎称绑定会制造"看起来受控"的假象。"""
    from pilot.scientific_operations import (ScientificOperation,
                                             is_bound_to_controlled_runtime,
                                             provider_role_for)
    for op in ScientificOperation:
        assert provider_role_for(op) is None
        assert is_bound_to_controlled_runtime(op) is False


def test_operation_module_does_not_copy_provider_role_names():
    """operation 模块只能引用受控角色权威，不得复制名字。"""
    src = _src("pilot/scientific_operations.py")
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    for role in ("synthesizer", "planner", "executor", "claim_extractor"):
        assert f'"{role}"' not in code, f"operation 模块复制了 ProviderRole 名 {role}"
    from pilot.scientific_operations import controlled_provider_roles
    assert controlled_provider_roles() == {"synthesizer", "verifier", "claim_extractor"}


def test_unknown_operation_fails_closed():
    from pilot.scientific_operations import ScientificOperationError, operation_from
    for bad in ("synthesizer", "verifier", "no_such_op", ""):
        with pytest.raises(ScientificOperationError):
            operation_from(bad)


def test_tool_is_not_a_model_role():
    from pilot.legacy_model_bridge import ModelDependencyMissing, require_injected_tool
    from pilot.scientific_operations import OPERATION_VALUES
    assert "search_literature" not in OPERATION_VALUES
    with pytest.raises(ModelDependencyMissing, match="explicit tool required"):
        require_injected_tool("search_literature")


# ============================================================ 兼容适配器是唯一 legacy 通道
def test_compat_adapter_is_the_only_module_reaching_legacy():
    import pathlib as _pl
    offenders = []
    for f in (_pl.Path(REPO) / "pilot").glob("legacy_*.py"):
        if f.name == "legacy_compat_adapter.py":
            continue
        # 只看真实 import；文档里解释 ssc_pi_agent 的现状是必要的，不算依赖
        if "ssc_pi_agent" in _imported_modules(f"pilot/{f.name}"):
            offenders.append(f.name)
    assert offenders == [], f"这些模块不应 import legacy：{offenders}"
    assert "ssc_pi_agent" in _imported_modules("pilot/legacy_compat_adapter.py"), \
        "兼容适配器本应是唯一触及 legacy 的地方"


def test_compat_adapter_import_is_side_effect_free():
    rc, out = _run(
        "import sys\n"
        "import pilot.legacy_compat_adapter as A\n"
        "print('LOADED', [m for m in ('ssc_pi_agent','langchain_openai',\n"
        "                             'langchain_anthropic','dotenv') if m in sys.modules])")
    assert rc == 0, out
    assert "LOADED []" in out, out


def test_compat_adapter_declares_itself_uncontrolled():
    """不得把这条通道说成受控。"""
    from pilot.legacy_compat_adapter import compat_disclosure
    d = compat_disclosure()
    assert d["controlled"] is False
    assert d["through_registry"] is False
    assert d["through_gate"] is False
    assert d["through_hitl"] is False


def test_compat_adapter_preserves_provider_preference_semantics():
    """provider 偏好语义留在兼容层：只有 "claude" 走 Claude，其余一律 DeepSeek。"""
    import ssc_pi_agent as P
    from pilot.legacy_compat_adapter import legacy_chat_model_for_preference as L
    assert L("claude") is P.judge_llm
    for other in ("deepseek", "", "gpt-4", "Claude", "anything"):
        assert L(other) is P.deepseek_llm_pro


def test_compat_adapter_never_caches_so_gate_rebinding_still_applies(monkeypatch):
    import ssc_pi_agent as P
    from pilot.legacy_compat_adapter import legacy_chat_model_for_preference as L
    first = L("claude")
    sentinel = FakeChat("GATED")
    monkeypatch.setattr(P, "judge_llm", sentinel, raising=False)
    assert L("claude") is sentinel, "适配器缓存了模型 → 会绕过 Gate"
    assert first is not sentinel


def test_compat_adapter_fails_closed_without_substitute(monkeypatch):
    import ssc_pi_agent as P
    from pilot.legacy_compat_adapter import (LegacyCompatUnavailable, legacy_search_tool,
                                             legacy_chat_model_for_preference)
    monkeypatch.delattr(P, "judge_llm", raising=False)
    with pytest.raises(LegacyCompatUnavailable):
        legacy_chat_model_for_preference("claude")
    with pytest.raises(LegacyCompatUnavailable):
        legacy_search_tool("no_such_tool_9137")


def test_entry_points_opt_in_explicitly():
    """兼容通道必须出现在**调用点**，不能藏在 resolver 默认值里。"""
    for rel, needle in (("pages/1_科研写作助手.py", "legacy_chat_model_for_preference"),
                        ("pages/1_科研写作助手.py", "legacy_search_tool"),
                        ("pages/6_实验协议.py", "legacy_chat_model_for_preference"),
                        ("ssc_skill_agent.py", "legacy_chat_model_for_preference")):
        assert needle in _src(rel), f"{rel} 未显式选用兼容通道：{needle}"


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
@pytest.mark.parametrize("rel", ("ssc_a1.py", "ssc_eval.py", "ssc_action_discovery.py",
                                 "pages/7_方向辩论(可选).py", "pages/9_数据对话.py"))
def test_wave1_did_not_touch_out_of_scope_modules(rel):
    assert "A.8.2b.2b.1" not in _src(rel), f"{rel} 不在本批范围内"


def test_ssc_skill_agent_changed_only_at_the_call_site():
    """它**未被迁移**：只是在调用点显式选用了兼容通道，其余原样。"""
    src = _src("ssc_skill_agent.py")
    assert "legacy_chat_model_for_preference" in src
    assert "from ssc_pi_agent import" in src          # 自身仍绑定 legacy 单例
    assert "require_injected_model" not in src        # 未改成核心注入路径


def test_ssc_pi_agent_still_untouched():
    s = _src("ssc_pi_agent.py")
    for marker in ("deepseek_llm_pro = ChatOpenAI(", "judge_llm = ChatAnthropic(",
                   "debater_pro = create_react_agent(", "load_dotenv()"):
        assert marker in s


def test_manifest_still_reports_legacy_as_not_import_safe():
    from pilot.provider_migration import MANIFEST
    assert MANIFEST["legacy_ssc_pi_agent_import_safe"] is False


def test_manifest_does_not_claim_controlled_migration():
    """不得把本轮说成"正式受控模型迁移完成"。"""
    from pilot.provider_migration import MANIFEST as M
    assert M["core_path_fail_closed"] is True
    assert M["legacy_compat_is_controlled"] is False
    assert M["controlled_model_migration_complete"] is False
    assert M["legacy_foundation_wired_to_consumers"] is False
    assert set(M["scientific_operations"]) == {
        "literature_drafting", "literature_revision", "protocol_drafting",
        "evidence_extraction", "claim_verification"}
    # A.8.2b.2b.1.2：两层分开，且现状是一个 operation 都没绑定受控 ProviderRole
    assert M["operations_bound_to_provider_role"] == []
    assert M["unified_provider_role_type_exists"] is False
    assert set(M["provider_role_authorities"]) == {
        "controlled_research_chain", "legacy_a1_round2_chain"}
    assert set(M["compat_opt_in_entrypoints"]) == {
        "pages/1_科研写作助手.py", "pages/6_实验协议.py", "ssc_skill_agent.py"}
