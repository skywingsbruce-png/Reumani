"""A.8.2b.2b.2 —— 第二批无状态 LLM 消费者（ssc_eval / ssc_action_discovery）。

诚实边界：
- `ssc_eval.answer_with_agent` **被排除**：它经 `build_skill_agent` 走 React Agent
  与工具循环，且与未迁移的 ssc_skill_agent 强耦合。本轮不碰它。
- 两个模块仍有 import 期的**文件系统**副作用（读题库、mkdir），与 legacy/key/模型
  无关，本轮不在范围内，但用测试如实登记，防止被当成已解决。

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
WAVE2 = ("ssc_eval.py", "ssc_action_discovery.py")


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
    def __init__(self, reply="FAKE"):
        self.reply, self.calls, self.prompts = reply, 0, []

    def invoke(self, prompt, config=None, **kw):
        self.calls += 1
        self.prompts.append(prompt)
        return type("R", (), {"content": self.reply})()


# ------------------------------------------------------- import 期
@pytest.mark.parametrize("rel", WAVE2)
def test_import_pulls_no_legacy_no_key_no_client(rel):
    mod = rel[:-3]
    rc, out = _run(
        "import sys, os\n"
        "sys.path.insert(0, '.')\n"
        "import dotenv\n"
        "state = {'d': 0, 'k': []}\n"
        "def _d(*a, **k):\n"
        "    state['d'] += 1\n"
        "    raise RuntimeError('LOAD_DOTENV_AT_IMPORT')\n"
        "dotenv.load_dotenv = _d\n"
        "P = ('DEEPSEEK_API_KEY', 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY')\n"
        "class C(dict):\n"
        "    def get(self, k, d=None):\n"
        "        if k in P: state['k'].append(k)\n"
        "        return dict.get(self, k, d)\n"
        "os.environ = C(os.environ)\n"
        f"import {mod}\n"
        "print('LEGACY', 'ssc_pi_agent' in sys.modules)\n"
        "print('DOTENV', state['d'])\n"
        "print('KEYS', len(state['k']))\n"
        "print('CLIENTLIBS', [m for m in ('langchain_openai','langchain_anthropic')\n"
        "                     if m in sys.modules])")
    assert rc == 0, out
    for expect in ("LEGACY False", "DOTENV 0", "KEYS 0", "CLIENTLIBS []"):
        assert expect in out, out


@pytest.mark.parametrize("rel", WAVE2)
def test_no_direct_legacy_import_and_no_module_level_model(rel):
    tree = ast.parse(_src(rel))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module != "ssc_pi_agent", f"{rel} 仍 import ssc_pi_agent"
        elif isinstance(node, ast.Import):
            assert all(a.name != "ssc_pi_agent" for a in node.names)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                assert "llm" not in getattr(t, "id", "").lower()


# ------------------------------------------------------- 注入生效
def test_eval_baseline_answering_uses_injected_model():
    import ssc_eval
    fake = FakeChat("ANS")
    assert ssc_eval.answer_plain("Q?", "deepseek", chat_model=fake) == "ANS"
    assert fake.calls == 1 and "Q?" in fake.prompts[0]


def test_eval_scoring_uses_injected_model_and_is_not_claim_verification():
    import ssc_eval
    scorer = FakeChat('{"correct": true, "hallucination": false, "note": "ok"}')
    c, h, note = ssc_eval.judge({"q": "Q", "key_facts": ["f"]}, "A", chat_model=scorer)
    assert (c, h, note) == (1, False, "ok")
    assert scorer.calls == 1
    # 职责必须是评测评分，而不是科学论断核验
    src = _src("ssc_eval.py")
    assert "ScientificOperation.EVALUATION_SCORING" in src
    assert "ScientificOperation.CLAIM_VERIFICATION" not in src


def test_eval_scoring_parse_failure_still_fails_closed():
    """裁判解析失败仍返回 (0, False, 说明)，语义与迁移前一致。"""
    import ssc_eval
    c, h, note = ssc_eval.judge({"q": "Q", "key_facts": ["f"]}, "A",
                                chat_model=FakeChat("not json at all"))
    assert c == 0 and h is False and note


def test_action_extraction_uses_injected_model():
    import ssc_action_discovery as AD
    for fn in (AD.extract_actions_batch, AD.extract_wet_actions_batch):
        fake = FakeChat("[]")
        fn([{"title": "t", "abstract": "a"}], chat_model=fake)
        assert fake.calls == 1


def test_run_helpers_thread_injection_through():
    """编排函数必须把注入透传下去，不得自己再去找模型。"""
    assert "answer_model" in inspect.signature(__import__("ssc_eval").run).parameters
    assert "scoring_model" in inspect.signature(__import__("ssc_eval").run).parameters
    import ssc_action_discovery as AD
    assert "chat_model" in inspect.signature(AD.run_discovery).parameters
    assert "extractor(batch, chat_model=chat_model)" in _src("ssc_action_discovery.py")


# ------------------------------------------------------- fail-closed
@pytest.mark.parametrize("call", [
    "ssc_eval.answer_plain('q', 'deepseek')",
    "ssc_eval.judge({'q': 'q', 'key_facts': ['f']}, 'a')",
    "ssc_action_discovery.extract_actions_batch([{'title':'t','abstract':'a'}])",
    "ssc_action_discovery.extract_wet_actions_batch([{'title':'t','abstract':'a'}])",
])
def test_wave2_fails_closed_without_injection(call):
    rc, out = _run(
        "import sys\n"
        "sys.path.insert(0, '.')\n"
        "import ssc_eval, ssc_action_discovery\n"
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


# ------------------------------------------------------- operation 契约
def test_new_operations_are_distinct_and_not_provider_roles():
    from pilot.paid_transport import ROLES as A1_ROLES
    from pilot.role_contracts import ROLE_CONTRACTS
    from pilot.scientific_operations import OPERATION_VALUES, ScientificOperation

    for new in ("evaluation_scoring", "baseline_answering", "action_extraction"):
        assert new in OPERATION_VALUES
    assert OPERATION_VALUES & (set(ROLE_CONTRACTS) | set(A1_ROLES)) == set()
    # 三个新操作彼此不同，也不与既有操作重名
    assert len(OPERATION_VALUES) == len(set(ScientificOperation)) == 8


def test_new_operations_are_not_bound_to_provider_roles():
    from pilot.scientific_operations import ScientificOperation, provider_role_for
    for op in (ScientificOperation.EVALUATION_SCORING,
               ScientificOperation.BASELINE_ANSWERING,
               ScientificOperation.ACTION_EXTRACTION):
        assert provider_role_for(op) is None


def test_scoring_is_not_conflated_with_claim_verification():
    """评测评分 ≠ 科学论断核验。原代码用 Claude 不能成为判成 verifier 的理由。"""
    from pilot.scientific_operations import ScientificOperation as SO
    assert SO.EVALUATION_SCORING is not SO.CLAIM_VERIFICATION
    assert SO.BASELINE_ANSWERING is not SO.LITERATURE_DRAFTING
    assert SO.ACTION_EXTRACTION is not SO.EVIDENCE_EXTRACTION


# ------------------------------------------------------- 入口显式选用
def test_cli_entrypoints_opt_in_explicitly():
    for rel in WAVE2:
        assert "legacy_chat_model_for_preference" in _src(rel), f"{rel} 未显式选用兼容通道"


def test_scoring_keeps_claude_and_extraction_keeps_deepseek():
    """迁移前：评分固定 Claude、动作抽取固定 DeepSeek。语义不得改变。"""
    assert 'legacy_chat_model_for_preference("claude")' in _src("ssc_eval.py")
    assert 'legacy_chat_model_for_preference("deepseek")' in _src("ssc_action_discovery.py")


# ------------------------------------------------------- 排除项与遗留副作用
def test_react_agent_path_is_excluded_not_migrated():
    """answer_with_agent 走 React Agent，本轮排除；它不得被改成注入路径。"""
    src = _src("ssc_eval.py")
    body = src[src.index("def answer_with_agent"):src.index("def answer_plain")]
    assert "build_skill_agent" in body
    assert "require_injected_model" not in body


def test_ssc_skill_agent_and_a1_untouched_by_wave2():
    for rel in ("ssc_skill_agent.py", "ssc_a1.py", "shadow.py", "experiment_copilot.py",
                "pages/9_数据对话.py", "pages/7_方向辩论(可选).py"):
        assert "A.8.2b.2b.2" not in _src(rel), f"{rel} 不在本批范围内"


def test_remaining_import_time_filesystem_effects_are_recorded():
    """如实登记：两个模块 import 期仍有文件系统副作用（非 legacy/key/模型）。"""
    assert "RESULT_DIR.mkdir(exist_ok=True)" in _src("ssc_eval.py")
    assert "QUESTIONS = json.loads(" in _src("ssc_eval.py")
    assert "QUEUE_DIR.mkdir(exist_ok=True)" in _src("ssc_action_discovery.py")


def test_ssc_pi_agent_still_untouched():
    s = _src("ssc_pi_agent.py")
    for marker in ("deepseek_llm_pro = ChatOpenAI(", "judge_llm = ChatAnthropic(",
                   "debater_pro = create_react_agent(", "load_dotenv()"):
        assert marker in s


def test_manifest_still_honest():
    from pilot.provider_migration import MANIFEST as M
    assert M["legacy_ssc_pi_agent_import_safe"] is False
    assert M["controlled_model_migration_complete"] is False
    assert M["legacy_compat_is_controlled"] is False
    assert M["operations_bound_to_provider_role"] == []
