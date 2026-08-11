"""A.8.2a §5 —— ProviderRegistry 离线验收。零网络、零付费、零 key。"""
import os
import subprocess
import sys
import tempfile
import threading

import pytest

from pilot.hard_gate import HardBudgetGate, GatedModel, ENV_PAID, ENV_CONFIRM
from pilot.provider_registry import (ProviderRegistry, ProviderSpec, ProviderHandle,
                                     ProviderRegistryError)
from pilot.research_results import ROLE_MAX_TOKENS
from pilot.provider_migration import (MANIFEST, CONTROLLED_RUNTIME_MODULES,
                                      UNMIGRATED_LEGACY, LEGACY_SSC_PI_AGENT_IMPORT_SAFE)

pytestmark = pytest.mark.unit
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SPECS = {
    "synthesizer": ("syn-opus", "anthropic", "claude-opus-4-8", "native_json_schema",
                    "synthesis-result-v2"),
    "verifier": ("ver-opus", "anthropic", "claude-opus-4-8", "native_json_schema",
                 "verifier-result-v2"),
    "claim_extractor": ("clm-ds", "deepseek", "deepseek-v4-flash", "json_object_only",
                        "claim-extraction-result-v2"),
}


@pytest.fixture(autouse=True)
def _switches(monkeypatch):
    monkeypatch.setenv(ENV_PAID, "1")
    monkeypatch.setenv(ENV_CONFIRM, "A82a")
    monkeypatch.delenv("CI", raising=False)


def _gate():
    return HardBudgetGate(stage="A82a", ledger_path=os.path.join(tempfile.mkdtemp(), "l.jsonl"),
                          max_usd_global=.18, max_usd_stage=.18, max_usd_task=.18,
                          max_calls_global=3, max_calls_task=3,
                          max_calls_per_model={"claude-opus-4-8": 2, "deepseek-v4-flash": 1},
                          max_calls_per_role={r: 1 for r in SPECS},
                          task_timeout_s=60, max_retries=0, default_max_tokens=1600,
                          allow_ci=True)


class _Fake:
    def with_structured_output(self, *a, **k): return self
    def bind(self, **k): return self
    def invoke(self, p, **k): return type("R", (), {"content": "{}"})()


def spec_for(role, **over):
    pid, prov, model, mode, contract = SPECS[role]
    base = dict(provider_id=pid, provider=prov, model_id=model, role=role, provider_mode=mode,
                timeout=120.0, max_tokens=ROLE_MAX_TOKENS[role], retry_policy="no_retry",
                pricing_policy_id="research-budget-policy-v2", output_contract_id=contract)
    base.update(over)
    return ProviderSpec(**base)


def build_registry(gate=None, counter=None):
    gate = gate or _gate()
    reg = ProviderRegistry()
    counter = counter if counter is not None else {}
    for role in SPECS:
        def factory(spec, _g=gate, _c=counter):
            _c[spec.role] = _c.get(spec.role, 0) + 1
            return GatedModel(_Fake(), _g, role=spec.role, model_id=spec.model_id,
                              max_tokens=spec.max_tokens)
        reg.register(spec_for(role), factory)
    return reg, gate, counter


# ---------------------------------------------------------------- 1-2 import 安全
def test_importing_controlled_runtime_constructs_no_client_and_reads_no_key():
    """在**干净子进程**中 import 受控模块：不得出现付费客户端，也不得读 API key。

    `pilot.runtime_api` 依赖 starlette，而 CI 的精简 unit 环境（requirements-ci.txt）
    不装 starlette —— 与既有 3 处 `importorskip("starlette")` 同一约定。缺 starlette 时
    只把该模块移出本次检查，**其余 11 个受控模块的断言强度不变**（不 skip 整个测试、
    不放宽任何断言）。
    """
    import importlib.util
    mods = list(CONTROLLED_RUNTIME_MODULES)
    if importlib.util.find_spec("starlette") is None:
        mods = [m for m in mods if m != "pilot.runtime_api"]
    assert len(mods) >= 11
    code = (
        "import sys, builtins\n"
        "reads = []\n"
        "_open = builtins.open\n"
        "def spy(f, *a, **k):\n"
        "    if '.env' in str(f): reads.append(str(f))\n"
        "    return _open(f, *a, **k)\n"
        "builtins.open = spy\n"
        "import os\n"
        "os.environ.pop('ANTHROPIC_API_KEY', None); os.environ.pop('DEEPSEEK_API_KEY', None)\n"
        f"for m in {mods!r}:\n"
        "    __import__(m)\n"
        "bad = [m for m in ('langchain_anthropic','langchain_openai') if m in sys.modules]\n"
        "print('CLIENTMODS=' + ','.join(bad))\n"
        "print('ENVREADS=' + ','.join(reads))\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO,
                       timeout=180)
    assert r.returncode == 0, r.stderr[-800:]
    out = r.stdout
    assert "CLIENTMODS=\n" in out or "CLIENTMODS=" in out.split("\n")[0] + "\n"
    client_line = [l for l in out.splitlines() if l.startswith("CLIENTMODS=")][0]
    assert client_line == "CLIENTMODS=", f"受控模块 import 时加载了客户端库：{client_line}"
    env_line = [l for l in out.splitlines() if l.startswith("ENVREADS=")][0]
    assert env_line == "ENVREADS=", f"受控模块 import 时读取了 .env：{env_line}"


# ---------------------------------------------------------------- 3-7 惰性与并发
def test_register_does_not_call_the_factory():
    _, _, calls = build_registry()
    assert calls == {}                                  # 注册阶段零构造


def test_first_resolve_constructs_exactly_once_and_caches():
    reg, _, calls = build_registry()
    h1 = reg.resolve_role("synthesizer")
    h2 = reg.resolve_role("synthesizer")
    h3 = reg.resolve("syn-opus", "synthesizer")
    assert calls["synthesizer"] == 1
    assert h1 is h2 is h3
    assert isinstance(h1, ProviderHandle)


def test_concurrent_resolve_constructs_once():
    reg, _, calls = build_registry()
    seen, errs = [], []

    def go():
        try:
            seen.append(reg.resolve_role("verifier"))
        except Exception as e:                          # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=go) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    assert not errs and calls["verifier"] == 1
    assert len({id(h) for h in seen}) == 1


def test_factory_failure_is_not_cached():
    reg = ProviderRegistry()
    state = {"n": 0}

    def bad(spec):
        state["n"] += 1
        raise RuntimeError("boom")
    reg.register(spec_for("synthesizer"), bad)
    for _ in range(3):
        with pytest.raises(ProviderRegistryError):
            reg.resolve_role("synthesizer")
    assert state["n"] == 3                              # 每次都重试构造，未缓存半成品
    assert reg.resolved_count() == 0


# ---------------------------------------------------------------- 8-12 fail-closed
def test_close_rejects_further_resolve():
    reg, _, _ = build_registry()
    reg.resolve_role("synthesizer")
    reg.close()
    assert reg.closed
    with pytest.raises(ProviderRegistryError):
        reg.resolve_role("synthesizer")
    with pytest.raises(ProviderRegistryError):
        reg.register(spec_for("verifier"), lambda s: None)


def test_unknown_role_provider_or_model_fails_closed():
    reg, gate, _ = build_registry()
    with pytest.raises(ProviderRegistryError):
        reg.resolve_role("planner")                     # 未注册角色
    with pytest.raises(ProviderRegistryError):
        reg.resolve("nope", "synthesizer")              # 未注册 provider_id
    with pytest.raises(ProviderRegistryError):
        reg.resolve("syn-opus", "verifier")             # role 与声明不符
    r2 = ProviderRegistry()
    r2.register(spec_for("synthesizer", model_id="mystery-model"), lambda s: None)
    with pytest.raises(ProviderRegistryError):          # 价格未核实
        r2.resolve_role("synthesizer")


def test_missing_timeout_retry_or_max_tokens_fails_closed():
    for over in ({"timeout": 0}, {"max_tokens": 0}, {"retry_policy": "retry_twice"},
                 {"provider_mode": "legacy_text"}, {"output_contract_id": "wrong-v9"},
                 {"max_tokens": 999}, {"enabled": False}):
        r = ProviderRegistry()
        r.register(spec_for("synthesizer", **over), lambda s: None)
        with pytest.raises(ProviderRegistryError):
            r.resolve("syn-opus", "synthesizer")


def test_raw_client_never_escapes_the_public_interface():
    reg = ProviderRegistry()
    reg.register(spec_for("synthesizer"), lambda s: _Fake())   # 返回未包 Gate 的 raw client
    with pytest.raises(ProviderRegistryError, match="Gate"):
        reg.resolve_role("synthesizer")
    reg2, _, _ = build_registry()
    h = reg2.resolve_role("synthesizer")
    from pilot.hard_gate import GatedModel as GM
    assert isinstance(h.gated_model, GM)
    for s in reg2.list_specs():                          # 声明里不含任何客户端对象
        assert not hasattr(s, "client")


def test_no_two_implicit_defaults_for_one_role():
    reg, _, _ = build_registry()
    with pytest.raises(ProviderRegistryError):
        reg.register(spec_for("synthesizer", provider_id="syn-other"), lambda s: None)


# ---------------------------------------------------------------- 13-14 角色隔离
def test_roles_get_independent_handles_and_metering():
    reg, gate, calls = build_registry()
    hs = {r: reg.resolve_role(r) for r in SPECS}
    assert len({id(h) for h in hs.values()}) == 3
    assert len({id(h.gated_model) for h in hs.values()}) == 3
    assert hs["claim_extractor"].gated_model is not hs["synthesizer"].gated_model
    assert hs["claim_extractor"].model_id == "deepseek-v4-flash"
    assert hs["synthesizer"].provider_mode == "native_json_schema"
    assert hs["claim_extractor"].provider_mode == "json_object_only"
    assert calls == {"synthesizer": 1, "verifier": 1, "claim_extractor": 1}
    # 每个 handle 带齐费用相关信息
    for r, h in hs.items():
        assert h.max_tokens == ROLE_MAX_TOKENS[r]
        assert h.output_contract.role == r
        assert callable(h.cost_estimator)
        assert h.metadata["pricing_policy_id"] == "research-budget-policy-v2"


def test_claim_extractor_calls_are_metered_only_to_claim_extractor():
    reg, gate, _ = build_registry()
    h = reg.resolve_role("claim_extractor")
    h.gated_model.invoke("hello")
    assert gate.calls_by_role.get("claim_extractor") == 1
    assert gate.calls_by_role.get("synthesizer") is None
    assert gate.calls_by_role.get("verifier") is None


def test_validate_checks_all_specs_without_constructing():
    reg, _, calls = build_registry()
    reg.validate()
    assert calls == {}


# ---------------------------------------------------------------- 23 legacy 诚实边界
def test_manifest_does_not_claim_repo_wide_completion():
    assert MANIFEST["controlled_runtime_import_safe"] is True
    assert MANIFEST["legacy_ssc_pi_agent_import_safe"] is False
    assert LEGACY_SSC_PI_AGENT_IMPORT_SAFE is False
    syms = {u["symbol"] for u in UNMIGRATED_LEGACY}
    for s in ("ssc_pi_agent.deepseek_llm_pro", "ssc_pi_agent.deepseek_llm_con",
              "ssc_pi_agent.judge_llm"):
        assert s in syms, f"清单遗漏未迁移的 legacy 单例 {s}"
    # A.8.2b.1 §5：批次已细化为 A.8.2b.2 / .3 / .6，但都仍属 A.8.2b，未提前宣称完成。
    assert all(u["planned_phase"].startswith("A.8.2b") for u in UNMIGRATED_LEGACY)
    assert "尚未" in MANIFEST["note"] or "not" in MANIFEST["note"].lower()


def test_legacy_singletons_really_are_still_import_time():
    """如实锁定现状：ssc_pi_agent 仍在 import 期构造付费客户端（本轮不动）。"""
    import pathlib
    src = (pathlib.Path(REPO) / "ssc_pi_agent.py").read_text(encoding="utf-8", errors="replace")
    for name in ("deepseek_llm_pro", "deepseek_llm_con", "judge_llm"):
        assert f"\n{name} = Chat" in src, f"{name} 已被改动 —— 本轮不应触碰它"
