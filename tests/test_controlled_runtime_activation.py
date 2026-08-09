"""A.8.2a.2 / A.8.2a.4a —— 阶段 A（只注册、零构造）与静态守卫。

授权后的行为（阶段 B）已迁移到 `tests/test_deferred_approval_lifecycle.py`，
在**真实 HitlRun 授权链**上验证 —— 旧的 `build_approved_research_executor` 布尔闸门
已在 A.8.2a.4a 删除，因此不再有针对它的测试。全部离线，真实付费调用为 0。
"""
import os
import pathlib
import tempfile

import pytest

from pilot.hard_gate import HardBudgetGate, GatedModel, ENV_PAID, ENV_CONFIRM
from pilot.controlled_runtime import build_controlled_runtime_registry, CONTROLLED_ROLE_SPECS
from pilot.deferred_research_executor import DeferredRegistryResearchExecutor
from pilot.research_results import ROLE_MAX_TOKENS
from pilot.role_contracts import ANTHROPIC_OPUS_48, DEEPSEEK_V4_FLASH
from tests.test_live_output_wiring import SpyChat

pytestmark = pytest.mark.unit
REPO = str(pathlib.Path(__file__).resolve().parent.parent)
LIVE_CAPS = {"claude-opus-4-8": ANTHROPIC_OPUS_48, "deepseek-v4-flash": DEEPSEEK_V4_FLASH}


@pytest.fixture(autouse=True)
def _switches(monkeypatch):
    monkeypatch.setenv(ENV_PAID, "1")
    monkeypatch.setenv(ENV_CONFIRM, "A82a2")
    monkeypatch.delenv("CI", raising=False)


def _gate():
    return HardBudgetGate(stage="A82a2", ledger_path=os.path.join(tempfile.mkdtemp(), "l.jsonl"),
                          max_usd_global=.18, max_usd_stage=.18, max_usd_task=.18,
                          max_calls_global=3, max_calls_task=3,
                          max_calls_per_model={"claude-opus-4-8": 2, "deepseek-v4-flash": 1},
                          max_calls_per_role={"synthesizer": 1, "verifier": 1,
                                              "claim_extractor": 1},
                          task_timeout_s=60, max_retries=0, default_max_tokens=1600,
                          allow_ci=True)


def make_stack(fail_key=False):
    from tests.test_gated_research_executor import SYN as S, VER as V, CLM as C
    gate = _gate()
    calls = {}
    payloads = {"synthesizer": S, "verifier": V, "claim_extractor": C}

    def model_factory(spec, g):
        calls[spec.role] = calls.get(spec.role, 0) + 1
        if fail_key:
            raise RuntimeError("credential missing")
        return GatedModel(SpyChat(payloads[spec.role]), g, role=spec.role,
                          model_id=spec.model_id, max_tokens=spec.max_tokens)

    reg = build_controlled_runtime_registry(gate=gate, model_factory=model_factory)
    return reg, gate, calls


# ---------------------------------------------------------------- 阶段 A：零构造
def test_phase_a_registers_without_constructing_or_reading_keys(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    reg, _, calls = make_stack()                     # 缺凭证也必须能建注册表
    assert calls == {}
    assert reg.resolved_count() == 0
    assert len(reg.list_specs()) == 3
    reg.validate()
    assert calls == {} and reg.resolved_count() == 0


def test_specs_declare_three_distinct_roles_and_modes():
    roles = {s["role"]: s for s in CONTROLLED_ROLE_SPECS}
    assert set(roles) == {"synthesizer", "verifier", "claim_extractor"}
    assert roles["synthesizer"]["provider_mode"] == "native_json_schema"
    assert roles["verifier"]["provider_mode"] == "native_json_schema"
    assert roles["claim_extractor"]["provider_mode"] == "json_object_only"
    assert roles["claim_extractor"]["model_id"] == "deepseek-v4-flash"
    for r in roles:
        assert ROLE_MAX_TOKENS[r] > 0


def test_deferred_executor_construction_resolves_nothing():
    """构造 Deferred executor 本身也不得触发任何 resolve。"""
    from pilot.frozen_evidence import FrozenEvidenceLoader
    reg, gate, calls = make_stack()
    ex = DeferredRegistryResearchExecutor(registry=reg, gate=gate,
                                          evidence_loader=FrozenEvidenceLoader(REPO),
                                          capabilities=LIVE_CAPS)
    assert calls == {} and reg.resolved_count() == 0 and ex.model_call_count() == 0
    assert not ex.authorized and ex.pending_binding is None


# ---------------------------------------------------------------- §1 旧旁路已删除
def test_boolean_approval_bypass_is_gone_from_production():
    """pilot/ 中不得再出现旧的布尔闸门或"批准后"工厂（命中数必须为 0）。"""
    root = pathlib.Path(REPO) / "pilot"
    banned = ("approval" + "_verified", "build_approved" + "_research_executor")
    for py in root.glob("*.py"):
        src = py.read_text(encoding="utf-8")
        for bad in banned:
            assert bad not in src, f"{py.name} 仍残留已删除的授权旁路：{bad}"


def test_no_test_calls_the_removed_function():
    root = pathlib.Path(REPO) / "tests"
    needle = "build_approved" + "_research_executor("
    for py in root.glob("*.py"):
        assert needle not in py.read_text(encoding="utf-8"), f"{py.name} 仍在调用已删除的函数"


# ---------------------------------------------------------------- 静态守卫
def test_controlled_production_entry_has_no_legacy_model_names():
    root = pathlib.Path(REPO) / "pilot"
    for name in ("controlled_runtime.py", "provider_registry.py", "gated_research_executor.py",
                 "runtime_api.py", "deferred_research_executor.py", "approval_grant.py"):
        src = (root / name).read_text(encoding="utf-8")
        code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
        for bad in ("import ssc_pi_agent", "from ssc_pi_agent",
                    "ssc_pi_agent.judge_llm", "ssc_pi_agent.deepseek_llm",
                    "= judge_llm", "= deepseek_llm_pro", "= deepseek_llm_con"):
            assert bad not in code, f"{name} 实际引用了 legacy 模型：{bad}"
        for bad in ("ChatAnthropic(", "ChatOpenAI("):
            assert bad not in code, f"{name} 直接构造了客户端 {bad}"


def test_legacy_positional_builder_is_isolated_and_named():
    # 用源码文本核对，**不 import** runtime_api（它依赖 starlette，CI 精简环境未装）
    src_root = pathlib.Path(REPO) / "pilot"
    api_src = (src_root / "runtime_api.py").read_text(encoding="utf-8")
    assert "def build_gated_research_executor_legacy_test_only(" in api_src
    for name in ("controlled_runtime.py", "deferred_research_executor.py",
                 "provider_registry.py", "gated_research_executor.py", "hitl.py"):
        code = "\n".join(l for l in (src_root / name).read_text(encoding="utf-8").splitlines()
                         if not l.lstrip().startswith("#"))
        assert "legacy_test_only" not in code, f"生产模块 {name} 调用了 legacy 入口"


def test_no_production_module_constructs_the_three_models_positionally():
    src_root = pathlib.Path(REPO) / "pilot"
    for py in src_root.glob("*.py"):
        if py.name == "runtime_api.py":            # legacy_test_only 明确保留在此
            continue
        code = py.read_text(encoding="utf-8")
        if "GatedResearchExecutor(" in code:
            assert py.name == "deferred_research_executor.py", (
                f"{py.name} 直接以位置参数构造了 GatedResearchExecutor")


def test_deferred_executor_exposes_no_positional_models_and_no_bool_gate():
    import inspect
    sig = set(inspect.signature(DeferredRegistryResearchExecutor.__init__).parameters)
    assert not ({"synthesizer", "verifier", "claim_extractor"} & sig)
    src = inspect.getsource(DeferredRegistryResearchExecutor)
    assert ("approval" + "_verified") not in src
    for m in ("authorize", "bind_pending_approval", "revoke"):
        assert m in dir(DeferredRegistryResearchExecutor)


def test_migration_manifest_marks_registry_active():
    from pilot.provider_migration import MANIFEST
    assert MANIFEST["controlled_runtime_registry_active"] is True
    assert MANIFEST["controlled_runtime_import_safe"] is True
    assert MANIFEST["legacy_ssc_pi_agent_import_safe"] is False
    assert MANIFEST["blocks_A8_3_until_A8_2b"] is True
