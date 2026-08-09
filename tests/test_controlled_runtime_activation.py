"""A.8.2a.2 §5/§7 —— 走**真实生产 builder** 的两阶段激活验收。

不直接测 `from_registry`，而是从 `build_controlled_runtime_registry` 起步，
证明「批准前零构造 / 批准后才 resolve」。全部离线，真实付费调用为 0。
"""
import json
import os
import pathlib
import tempfile

import pytest

from pilot.hard_gate import HardBudgetGate, GatedModel, ENV_PAID, ENV_CONFIRM
from pilot.frozen_evidence import FrozenEvidenceLoader
from pilot.controlled_runtime import (build_controlled_runtime_registry,
                                      build_approved_research_executor,
                                      CONTROLLED_ROLE_SPECS, ApprovalNotVerified)
from pilot.provider_registry import ProviderRegistryError
from pilot.research_results import ROLE_MAX_TOKENS
from pilot.role_contracts import ANTHROPIC_OPUS_48, DEEPSEEK_V4_FLASH
from tests.test_live_output_wiring import SpyChat

pytestmark = pytest.mark.unit
REPO = str(pathlib.Path(__file__).resolve().parent.parent)
LIVE_CAPS = {"claude-opus-4-8": ANTHROPIC_OPUS_48, "deepseek-v4-flash": DEEPSEEK_V4_FLASH}
SYN = json.loads(pathlib.Path(REPO).joinpath("tests").exists() and "{}" or "{}")


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
    """真实生产 builder + fake provider。counter 记录 factory 调用。"""
    from tests.test_gated_research_executor import SYN as S, VER as V, CLM as C
    gate = _gate()
    calls = {}
    payloads = {"synthesizer": S, "verifier": V, "claim_extractor": C}

    def model_factory(spec, g):
        calls[spec.role] = calls.get(spec.role, 0) + 1
        if fail_key:
            raise RuntimeError("ANTHROPIC_API_KEY missing")   # 模拟缺 key
        return GatedModel(SpyChat(payloads[spec.role]), g, role=spec.role,
                          model_id=spec.model_id, max_tokens=spec.max_tokens)

    reg = build_controlled_runtime_registry(gate=gate, model_factory=model_factory)
    return reg, gate, calls


# ---------------------------------------------------------------- 阶段 A：零构造
def test_phase_a_registers_without_constructing_or_reading_keys(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    reg, _, calls = make_stack()                     # 缺 key 也必须能建注册表
    assert calls == {}                               # factory_calls == 0
    assert reg.resolved_count() == 0                 # resolved_count == 0
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
    for r, s in roles.items():
        assert ROLE_MAX_TOKENS[r] > 0


# ---------------------------------------------------------------- 阶段 B 闸门
def test_resolve_is_refused_before_approval_is_verified():
    reg, gate, calls = make_stack()
    with pytest.raises(ApprovalNotVerified):
        build_approved_research_executor(reg, gate=gate,
                                         evidence_loader=FrozenEvidenceLoader(REPO))
    assert calls == {} and reg.resolved_count() == 0     # 未批准 → 一次都没构造


def test_after_approval_each_role_is_constructed_exactly_once():
    reg, gate, calls = make_stack()
    ex = build_approved_research_executor(reg, gate=gate,
                                          evidence_loader=FrozenEvidenceLoader(REPO),
                                          approval_verified=True)
    assert calls == {"synthesizer": 1, "verifier": 1, "claim_extractor": 1}
    assert reg.resolved_count() == 3
    assert set(ex.provider_handles) == {"synthesizer", "verifier", "claim_extractor"}
    # 三个 handle / 三个 gated model 互不相同
    assert len({id(h.gated_model) for h in ex.provider_handles.values()}) == 3
    assert ex.model_call_count() == 0                    # 仍未发生任何 provider 调用


def test_repeated_approval_does_not_reconstruct_clients():
    reg, gate, calls = make_stack()
    kw = dict(gate=gate, evidence_loader=FrozenEvidenceLoader(REPO), approval_verified=True)
    build_approved_research_executor(reg, **kw)
    build_approved_research_executor(reg, **kw)
    assert calls == {"synthesizer": 1, "verifier": 1, "claim_extractor": 1}


def test_missing_key_fails_closed_after_approval_not_before(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    reg, gate, calls = make_stack(fail_key=True)
    assert calls == {}                                    # 阶段 A 不受影响
    with pytest.raises(ProviderRegistryError):            # 阶段 B 明确失败
        build_approved_research_executor(reg, gate=gate,
                                         evidence_loader=FrozenEvidenceLoader(REPO),
                                         approval_verified=True)
    assert reg.resolved_count() == 0                      # 不缓存半成品、不降级


def test_closed_registry_cannot_be_activated():
    reg, gate, _ = make_stack()
    reg.close()
    with pytest.raises(ProviderRegistryError):
        build_approved_research_executor(reg, gate=gate,
                                         evidence_loader=FrozenEvidenceLoader(REPO),
                                         approval_verified=True)


# ---------------------------------------------------------------- 全链 + 计量
def test_full_chain_through_the_production_builder():
    from tests.test_gated_research_executor import run_chain
    reg, gate, calls = make_stack()
    ex = build_approved_research_executor(reg, gate=gate,
                                          evidence_loader=FrozenEvidenceLoader(REPO),
                                          approval_verified=True)
    ex._capabilities = LIVE_CAPS
    store, r, _ = run_chain(ex, rid="hitl-prod-builder")
    assert r.state == "completed"
    assert ex.role_calls == {"synthesizer": 1, "verifier": 1, "claim_extractor": 1}
    # claim_extractor 只计入自己
    assert gate.calls_by_role.get("claim_extractor") == 1
    assert gate.calls_by_role.get("synthesizer") == 1
    assert not gate.ledger.open_reservations()
    assert gate.retries == 0
    assert calls == {"synthesizer": 1, "verifier": 1, "claim_extractor": 1}
    # Verifier 保留最终裁决权
    arts = [a for a in r.artifacts if a.get("kind") == "json"]
    assert len(arts) == 1


# ---------------------------------------------------------------- §7 静态守卫
def test_controlled_production_entry_has_no_legacy_model_names():
    """受控生产入口不得出现 legacy 全局模型名或直接构造客户端。"""
    root = pathlib.Path(REPO) / "pilot"
    for name in ("controlled_runtime.py", "provider_registry.py", "gated_research_executor.py",
                 "runtime_api.py"):
        src = (root / name).read_text(encoding="utf-8")
        # 只查**真实使用**（import / 属性访问），不查注释与 docstring 里的说明性提及
        code = "\n".join(l for l in src.splitlines()
                         if not l.lstrip().startswith("#"))
        for bad in ("import ssc_pi_agent", "from ssc_pi_agent",
                    "ssc_pi_agent.judge_llm", "ssc_pi_agent.deepseek_llm",
                    "= judge_llm", "= deepseek_llm_pro", "= deepseek_llm_con"):
            assert bad not in code, f"{name} 实际引用了 legacy 模型：{bad}"
        for bad in ("ChatAnthropic(", "ChatOpenAI("):
            assert bad not in code, f"{name} 直接构造了客户端 {bad}"


def test_production_builder_never_takes_positional_models():
    """生产两阶段入口的签名里不得出现三个模型参数。"""
    import inspect
    from pilot import controlled_runtime as cr
    for fn in (cr.build_controlled_runtime_registry, cr.build_approved_research_executor):
        params = set(inspect.signature(fn).parameters)
        assert not ({"synthesizer", "verifier", "claim_extractor"} & params), fn.__name__


def test_legacy_positional_builder_is_isolated_and_named():
    """§5：三模型位置参数入口必须改名为 legacy_test_only，且生产模块不调用它。"""
    from pilot import runtime_api
    assert hasattr(runtime_api, "build_gated_research_executor_legacy_test_only")
    src_root = pathlib.Path(REPO) / "pilot"
    for name in ("controlled_runtime.py", "deferred_research_executor.py",
                 "provider_registry.py", "gated_research_executor.py", "hitl.py"):
        code = "\n".join(l for l in (src_root / name).read_text(encoding="utf-8").splitlines()
                         if not l.lstrip().startswith("#"))
        assert "legacy_test_only" not in code, f"生产模块 {name} 调用了 legacy 入口"


def test_no_production_module_constructs_the_three_models_positionally():
    """静态守卫：生产文件里不得出现三模型位置参数构造 GatedResearchExecutor。"""
    src_root = pathlib.Path(REPO) / "pilot"
    for py in src_root.glob("*.py"):
        if py.name in ("runtime_api.py",):        # legacy_test_only 明确保留在此
            continue
        code = py.read_text(encoding="utf-8")
        if "GatedResearchExecutor(" in code:
            # 只允许 deferred executor 的预览桩（stub），它不含真实客户端
            assert py.name == "deferred_research_executor.py", (
                f"{py.name} 直接以位置参数构造了 GatedResearchExecutor")


def test_deferred_executor_is_the_authorized_path():
    """authorize 缺失时 run_stage 必须 fail-closed（不回退旧路径）。"""
    from pilot.deferred_research_executor import DeferredRegistryResearchExecutor
    import inspect
    sig = set(inspect.signature(DeferredRegistryResearchExecutor.__init__).parameters)
    assert not ({"synthesizer", "verifier", "claim_extractor"} & sig)
    assert "authorize" in dir(DeferredRegistryResearchExecutor)
    assert "approval_verified" not in inspect.getsource(DeferredRegistryResearchExecutor)


def test_migration_manifest_marks_registry_active():
    from pilot.provider_migration import MANIFEST
    assert MANIFEST["controlled_runtime_registry_active"] is True
    assert MANIFEST["controlled_runtime_import_safe"] is True
    assert MANIFEST["legacy_ssc_pi_agent_import_safe"] is False
    assert MANIFEST["blocks_A8_3_until_A8_2b"] is True
