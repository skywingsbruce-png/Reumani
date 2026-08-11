"""A.8.2b.1 §7 —— legacy provider 地基的零副作用证明 + 不触发式扫描器。

全部离线：零网络、零真实 key、零付费调用。
本文件**不**验证 legacy 已迁移 —— 它恰恰断言 legacy 仍未迁移。
"""
import subprocess
import sys
import threading

import pytest

pytestmark = pytest.mark.unit

REPO = str(__import__("pathlib").Path(__file__).resolve().parent.parent)


def _run(code):
    import os

    env = dict(os.environ, PYTHONIOENCODING="utf-8")   # 子进程可能输出中文断言
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=REPO, timeout=180, env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


# ============================================================ §1 specs 零副作用
def test_specs_import_has_zero_side_effects():
    rc, out = _run(
        "import sys, os\n"
        "os.environ.pop('DEEPSEEK_API_KEY', None); os.environ.pop('ANTHROPIC_API_KEY', None)\n"
        "import pilot.legacy_provider_specs as S\n"
        # 不得拉起任何客户端库，也不得拉起 legacy 模块
        "bad=[m for m in ('langchain_openai','langchain_anthropic','ssc_pi_agent','dotenv')"
        " if m in sys.modules]\n"
        "print('LOADED', bad)\n"
        "print('COUNT', S.validate_all())")
    assert rc == 0, out
    assert "LOADED []" in out, out
    assert "COUNT 5" in out


def test_specs_keep_pro_and_con_distinct():
    from pilot.legacy_provider_specs import spec_for
    pro, con = spec_for("debate_pro"), spec_for("debate_con")
    assert pro.temperature == 0.3 and con.temperature == 0.7
    assert pro.provider_id != con.provider_id
    assert pro.model_id == con.model_id            # 只有 temperature 不同


def test_specs_pin_models_and_reject_floating_aliases():
    """legacy 用的是浮动别名；新地基必须钉死版本，且拒绝把别名带进来。"""
    from pilot.legacy_provider_specs import (LEGACY_SPECS, LegacyProviderSpec,
                                             LegacyProviderSpecError)
    from pilot.paid_transport import FORBIDDEN_DEEPSEEK
    for s in LEGACY_SPECS:
        assert s.model_id not in FORBIDDEN_DEEPSEEK, f"{s.provider_id} 用了浮动别名"
    bad = LEGACY_SPECS[0].model_copy(update={"model_id": sorted(FORBIDDEN_DEEPSEEK)[0]})
    with pytest.raises(LegacyProviderSpecError, match="浮动别名"):
        bad.validate_spec()
    assert isinstance(bad, LegacyProviderSpec)


def test_specs_cover_five_roles_and_reject_unknown():
    from pilot.legacy_provider_specs import (LEGACY_ROLES, LegacyProviderSpecError,
                                             all_specs, spec_for)
    assert set(LEGACY_ROLES) == {"debate_pro", "debate_con", "judge",
                                 "general_deepseek", "general_claude"}
    assert len(all_specs()) == 5
    with pytest.raises(LegacyProviderSpecError):
        spec_for("anything_else")                  # 没有默认回退


def test_spec_validation_is_fail_closed():
    from pilot.legacy_provider_specs import LegacyProviderSpec, LegacyProviderSpecError
    base = dict(provider_id="x", provider="deepseek", role="judge", model_id="m",
                temperature=0.1, timeout=10.0, max_tokens=100, retry_policy="no_retry",
                provider_mode="json_object_only", gated_required=True)
    for bad in ({"retry_policy": "exponential"}, {"timeout": 0}, {"max_tokens": 0},
                {"temperature": 9.9}, {"provider": "openai"}, {"role": "nope"},
                {"provider_mode": "guessing"}):
        with pytest.raises(LegacyProviderSpecError):
            LegacyProviderSpec(**{**base, **bad}).validate_spec()


# ============================================================ §3 config 边界
def test_config_import_reads_no_environment_and_no_dotenv():
    rc, out = _run(
        "import sys, os\n"
        "import schemas, pydantic\n"                  # 先加载共享依赖，只测量本模块
        "seen = []\n"
        "class Probe(dict):\n"
        "    def get(self, k, d=None):\n"
        "        seen.append(str(k)); return dict.get(self, k, d)\n"
        "    def __getitem__(self, k):\n"
        "        seen.append(str(k)); return dict.__getitem__(self, k)\n"
        "real = os.environ; os.environ = Probe(real)\n"
        "import pilot.legacy_runtime_config as C\n"
        "os.environ = real\n"
        "print('DOTENV_LOADED', 'dotenv' in sys.modules)\n"
        "print('READS', sorted(set(seen)))\n"
        "print('OK', C.empty_config().source)")
    assert rc == 0, out
    assert "DOTENV_LOADED False" in out, out
    assert "OK unset" in out
    reads = out.split("READS ", 1)[1].split("\n", 1)[0]
    # 本模块自己一个环境变量都不读。唯一出现的是 pydantic 建模型类时探测自己的
    # 插件开关（第三方实现细节，与 key 无关）—— 如实允许它，但**只允许它**。
    for k in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_MODEL",
              "ANTHROPIC_MODEL", "OPENAI_API_KEY"):
        assert k not in reads, f"import 期读取了 {k}：{reads}"
    allowed = {"'PYDANTIC_DISABLE_PLUGINS'"}
    actual = {t.strip() for t in reads.strip("[]").split(",") if t.strip()}
    assert actual <= allowed, f"import 期出现了预期外的环境读取：{actual - allowed}"


def test_config_masks_keys_in_repr_and_dump():
    from pilot.legacy_runtime_config import explicit_config
    c = explicit_config(deepseek_api_key="sk-super-secret-value-1234",
                        anthropic_api_key="sk-ant-another-secret-5678")
    for blob in (repr(c), str(c), str(c.model_dump())):
        assert "super-secret" not in blob and "another-secret" not in blob
        assert "sk-" not in blob
    assert c.secret_for("deepseek") == "sk-super-secret-value-1234"   # 显式取才给


def test_placeholder_key_does_not_count_as_configured():
    """ssc_pi_agent 现在正是用 'not-configured' 兜底 —— 它必须被识别为未配置。"""
    from pilot.legacy_runtime_config import LegacyRuntimeConfigError, explicit_config
    for placeholder in ("not-configured", "", "changeme", "  "):
        c = explicit_config(deepseek_api_key=placeholder)
        assert c.is_configured("deepseek") is False
        with pytest.raises(LegacyRuntimeConfigError):
            c.secret_for("deepseek")


def test_from_environment_is_explicit_and_injectable():
    from pilot.legacy_runtime_config import SOURCE_ENVIRONMENT, from_environment
    c = from_environment({"DEEPSEEK_API_KEY": "sk-fake", "DEEPSEEK_MODEL": "deepseek-x"})
    assert c.source == SOURCE_ENVIRONMENT
    assert c.is_configured("deepseek") and not c.is_configured("anthropic")
    assert c.model_for("deepseek") == "deepseek-x"


def test_config_defaults_are_pinned_not_floating_aliases():
    from pilot.legacy_runtime_config import (DEFAULT_ANTHROPIC_MODEL, DEFAULT_DEEPSEEK_MODEL,
                                             empty_config)
    from pilot.paid_transport import FORBIDDEN_DEEPSEEK
    assert DEFAULT_DEEPSEEK_MODEL not in FORBIDDEN_DEEPSEEK
    c = empty_config()
    assert c.model_for("deepseek") == DEFAULT_DEEPSEEK_MODEL
    assert c.model_for("anthropic") == DEFAULT_ANTHROPIC_MODEL


def test_library_modules_do_not_call_load_dotenv():
    import pathlib
    for m in ("legacy_provider_specs", "legacy_runtime_config", "legacy_provider_factory"):
        src = (pathlib.Path(REPO) / "pilot" / f"{m}.py").read_text(encoding="utf-8")
        top = [ln for ln in src.splitlines() if ln.startswith("load_dotenv(")]
        assert not top, f"{m} 在模块级调用了 load_dotenv"


# ============================================================ §2 factory
class _FakeClient:
    _reumani_gated = True                          # 冒充已包 Gate，供离线验证

    def __init__(self, spec, config):
        self.model = spec.model_id
        self.temperature = spec.temperature
        self.key_len = len(config.secret_for(spec.provider))

    def invoke(self, *a, **kw):                    # pragma: no cover - 绝不应被调用
        raise AssertionError("测试中不得真正调用模型")


def _fake_factory(spec, config):
    return _FakeClient(spec, config)


def _configured():
    from pilot.legacy_runtime_config import explicit_config
    return explicit_config(deepseek_api_key="sk-fake-ds", anthropic_api_key="sk-fake-an")


def test_factory_import_and_register_construct_nothing():
    rc, out = _run(
        "import sys\n"
        "from pilot.legacy_provider_factory import LegacyProviderFactory\n"
        "f = LegacyProviderFactory(); n = f.register_specs()\n"
        "bad=[m for m in ('langchain_openai','langchain_anthropic') if m in sys.modules]\n"
        "print('REGISTERED', n, 'RESOLVED', f.resolve_count(), 'CLIENTLIBS', bad)")
    assert rc == 0, out
    assert "REGISTERED 5 RESOLVED 0 CLIENTLIBS []" in out, out


def test_resolve_constructs_only_on_demand():
    from pilot.legacy_provider_factory import LegacyProviderFactory
    f = LegacyProviderFactory()
    f.register_specs(client_factory=_fake_factory)
    assert f.resolve_count() == 0
    h = f.resolve_model("debate_pro", _configured())
    assert f.resolve_count() == 1
    assert h.role == "debate_pro" and h.temperature == 0.3
    f.resolve_model("debate_con", _configured())
    assert f.resolve_count() == 2


def test_pro_con_judge_identities_are_independent():
    from pilot.legacy_provider_factory import LegacyProviderFactory
    f = LegacyProviderFactory()
    f.register_specs(client_factory=_fake_factory)
    cfg = _configured()
    pro = f.resolve_model("debate_pro", cfg)
    con = f.resolve_model("debate_con", cfg)
    judge = f.resolve_model("judge", cfg)
    assert pro.client is not con.client is not judge.client
    assert pro.client.temperature != con.client.temperature
    assert len({id(pro.client), id(con.client), id(judge.client)}) == 3


def test_missing_key_fails_at_resolve_not_at_import():
    from pilot.legacy_provider_factory import (LegacyProviderFactory,
                                               LegacyProviderFactoryError)
    from pilot.legacy_runtime_config import empty_config
    f = LegacyProviderFactory()
    f.register_specs(client_factory=_fake_factory)   # 注册阶段不校验 key
    with pytest.raises(LegacyProviderFactoryError, match="key"):
        f.resolve_model("judge", empty_config())
    assert f.resolve_count() == 0


def test_unknown_role_and_unregistered_role_fail_closed():
    from pilot.legacy_provider_factory import (LegacyProviderFactory,
                                               LegacyProviderFactoryError)
    f = LegacyProviderFactory()
    with pytest.raises(LegacyProviderFactoryError):
        f.resolve_model("totally_unknown", _configured())     # 不在 LEGACY_ROLES
    with pytest.raises(LegacyProviderFactoryError, match="未注册"):
        f.resolve_model("judge", _configured())               # 未 register
    with pytest.raises(LegacyProviderFactoryError):
        f.resolve_model("judge", None)                        # 不隐式读环境


def test_concurrent_resolve_constructs_once():
    from pilot.legacy_provider_factory import LegacyProviderFactory
    built = []

    def counting(spec, config):
        built.append(spec.role)
        return _FakeClient(spec, config)

    f = LegacyProviderFactory()
    f.register_specs(client_factory=counting)
    cfg = _configured()
    out, barrier = [], threading.Barrier(8)

    def worker():
        barrier.wait()
        out.append(f.resolve_model("judge", cfg))

    ts = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert len(built) == 1, f"并发构造了 {len(built)} 次"
    assert len({id(h.client) for h in out}) == 1
    assert f.resolve_count() == 1


def test_failed_construction_is_not_cached():
    from pilot.legacy_provider_factory import (LegacyProviderFactory,
                                               LegacyProviderFactoryError)
    calls = []

    def flaky(spec, config):
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("boom")
        return _FakeClient(spec, config)

    f = LegacyProviderFactory()
    f.register_specs(client_factory=flaky)
    cfg = _configured()
    for _ in range(2):
        with pytest.raises(RuntimeError):
            f.resolve_model("judge", cfg)
        assert f.resolve_count() == 0
        assert not f.is_resolved("judge")            # 没有半成品缓存
    assert f.resolve_model("judge", cfg).role == "judge"
    assert f.resolve_count() == 1


def test_ungated_client_is_refused_when_gate_required():
    from pilot.legacy_provider_factory import (LegacyProviderFactory,
                                               LegacyProviderFactoryError)

    class Raw:                                       # 未包 Gate
        pass

    f = LegacyProviderFactory()
    f.register_specs(client_factory=lambda s, c: Raw())
    with pytest.raises(LegacyProviderFactoryError, match="未包 Gate"):
        f.resolve_model("judge", _configured())
    assert f.resolve_count() == 0


def test_factory_offers_no_default_model_fallback():
    import pathlib
    src = (pathlib.Path(REPO) / "pilot" / "legacy_provider_factory.py").read_text(
        encoding="utf-8")
    # 公共接口不得出现"取不到就用某个默认模型"的写法
    for bad in ("or spec_for(", 'get(role, ', "except KeyError:\n        return"):
        assert bad not in src
    from pilot.legacy_provider_factory import LegacyProviderFactory
    assert not hasattr(LegacyProviderFactory, "default_model")


def test_snapshot_is_desensitized_and_does_not_trigger_resolution():
    from pilot.legacy_provider_factory import LegacyProviderFactory
    f = LegacyProviderFactory()
    f.register_specs(client_factory=_fake_factory)
    assert f.resolved_snapshot() == ()               # 未解析 → 空，且不触发
    assert f.resolve_count() == 0
    f.resolve_model("judge", _configured())
    snap = f.resolved_snapshot()
    assert len(snap) == 1 and snap[0]["role"] == "judge"
    blob = str(snap)
    assert "sk-" not in blob and "api_key" not in blob and "client" not in snap[0]
    assert f.resolve_count() == 1                    # 快照本身没有再构造


def test_close_releases_and_refuses_further_resolution():
    from pilot.legacy_provider_factory import (LegacyProviderFactory,
                                               LegacyProviderFactoryError)
    f = LegacyProviderFactory()
    f.register_specs(client_factory=_fake_factory)
    f.resolve_model("judge", _configured())
    f.close()
    assert f.closed and f.resolved_snapshot() == ()
    with pytest.raises(LegacyProviderFactoryError):
        f.resolve_model("judge", _configured())


# ============================================================ §4 不触发式扫描器
def test_scanner_never_invokes_module_getattr():
    """哨兵：模块级 __getattr__ 一旦被调用就抛。扫描器必须仍能跑完。"""
    rc, out = _run(
        "import sys, types\n"
        "import pilot.paid_transport as PT\n"
        "calls = []\n"
        "m = types.ModuleType('ssc_a1')\n"
        "def boom(name):\n"
        "    calls.append(name)\n"
        "    raise AssertionError('scanner 触发了 module __getattr__: ' + name)\n"
        "m.__getattr__ = boom\n"
        "m.judge_llm = None\n"                       # 真实存在的条目照样可见
        "sys.modules['ssc_a1'] = m\n"
        "found = PT.discover_paid_clients()\n"
        "PT.assert_import_order_clean.__wrapped__ if False else None\n"
        "print('GETATTR_CALLS', len(calls))\n"
        "print('SCAN_OK', isinstance(found, list))")
    assert rc == 0, out
    assert "GETATTR_CALLS 0" in out, out
    assert "SCAN_OK True" in out


def test_import_order_guard_never_invokes_module_getattr():
    rc, out = _run(
        "import sys, types\n"
        "import pilot.paid_transport as PT\n"
        "calls = []\n"
        "m = types.ModuleType('some_consumer')\n"
        "def boom(name):\n"
        "    calls.append(name)\n"
        "    raise AssertionError('guard 触发了 module __getattr__: ' + name)\n"
        "m.__getattr__ = boom\n"
        "sys.modules['some_consumer'] = m\n"
        "sys.modules.pop('ssc_a1', None); sys.modules.pop('ssc_skill_agent', None)\n"
        "PT.assert_import_order_clean()\n"
        "print('GETATTR_CALLS', len(calls))")
    assert rc == 0, out
    assert "GETATTR_CALLS 0" in out, out


def test_scanner_does_not_resolve_the_factory():
    from pilot.legacy_provider_factory import LegacyProviderFactory
    from pilot.paid_transport import discover_factory_snapshots
    f = LegacyProviderFactory()
    f.register_specs(client_factory=_fake_factory)
    before = f.resolve_count()
    assert discover_factory_snapshots([f]) == []
    assert f.resolve_count() == before == 0          # 扫描前后构造计数相同


def test_scanner_finds_already_resolved_fake_handles():
    from pilot.legacy_provider_factory import LegacyProviderFactory
    from pilot.paid_transport import discover_factory_snapshots
    f = LegacyProviderFactory()
    f.register_specs(client_factory=_fake_factory)
    f.resolve_model("debate_pro", _configured())
    rows = discover_factory_snapshots([f])
    assert len(rows) == 1 and rows[0]["role"] == "debate_pro"
    assert f.resolve_count() == 1                    # 审计没有额外构造
    assert "api_key" not in rows[0] and "client" not in rows[0]


def test_scanner_uses_module_dict_not_dir_getattr():
    import pathlib
    src = (pathlib.Path(REPO) / "pilot" / "paid_transport.py").read_text(encoding="utf-8")

    def code_only(text):                             # 注释里提到旧写法是允许的
        return "\n".join(ln.split("#", 1)[0] for ln in text.splitlines())

    body = code_only(
        src[src.index("def discover_paid_clients"):src.index("def discover_factory_snapshots")])
    assert "dir(mod)" not in body and "getattr(mod" not in body
    assert "_module_dict(mod)" in body
    guard = code_only(src[src.index("def assert_import_order_clean"):src.index("UNUSED_ROLE")])
    assert "getattr(mod, attr" not in guard
    assert "_module_dict(mod)" in guard


def test_unauditable_modules_reports_instead_of_forcing_construction():
    import sys
    import types

    from pilot.paid_transport import unauditable_modules

    class NoDict(types.ModuleType):
        @property
        def __dict__(self):                          # pragma: no cover - 属性协议
            raise RuntimeError("不可审计")

    name = "_a82b1_probe_module"
    sys.modules[name] = NoDict(name)
    try:
        bad = unauditable_modules([name])
        assert bad and "unauditable" in bad[0]
    finally:
        sys.modules.pop(name, None)
    assert unauditable_modules(["_definitely_not_imported_"]) == []


# ============================================================ §5 清单诚实边界
def test_manifest_still_reports_legacy_as_not_migrated():
    from pilot.provider_migration import MANIFEST
    assert MANIFEST["legacy_ssc_pi_agent_import_safe"] is False
    assert MANIFEST["blocks_A8_3_until_A8_2b"] is True
    assert MANIFEST["legacy_foundation_wired_to_consumers"] is False
    assert MANIFEST["non_triggering_scanner"] is True
    assert MANIFEST["phase"] == "A.8.2b.1"


def test_manifest_rebind_counts_match_reality():
    """旧清单写'15 处 monkeypatch'与事实不符；这里锁住实证数量。"""
    from pilot.provider_migration import REBIND_COUNTS
    assert REBIND_COUNTS["real_monkeypatch"] == 2
    assert REBIND_COUNTS["subprocess_import_order_scripts"] == 4
    assert REBIND_COUNTS["production_runner_rebind"] == 2


def test_manifest_records_real_runtime_and_isolated_projects():
    from pilot.provider_migration import UNMIGRATED_LEGACY
    syms = {e["symbol"] for e in UNMIGRATED_LEGACY}
    assert "pilot/real_runtime.py" in syms
    isolated = [e for e in UNMIGRATED_LEGACY if e.get("in_controlled_runtime") is False]
    assert len(isolated) == 4                        # quant + ryn + copy + ryn_stock-main


def test_manifest_freezes_the_batch_order():
    from pilot.provider_migration import MIGRATION_BATCHES
    assert [b["batch"] for b in MIGRATION_BATCHES] == [
        "A.8.2b.2", "A.8.2b.3", "A.8.2b.4", "A.8.2b.5", "A.8.2b.6"]
    assert "debate" in MIGRATION_BATCHES[1]["scope"]


def test_ssc_pi_agent_singletons_are_untouched_this_phase():
    """本阶段禁止改动 legacy 单例 —— 静态确认它们仍在原处。"""
    import pathlib
    src = (pathlib.Path(REPO) / "ssc_pi_agent.py").read_text(encoding="utf-8")
    for marker in ("deepseek_llm_pro = ChatOpenAI(", "deepseek_llm_con = ChatOpenAI(",
                   "judge_llm = ChatAnthropic(", "debater_pro = create_react_agent(",
                   "debater_con = create_react_agent(", "judge_agent = create_react_agent("):
        assert marker in src, f"本阶段不得改动：{marker}"
    assert "__getattr__" not in src, "本阶段禁止实现 PEP 562 __getattr__"


# ============================================================ 无网络 / 无付费
def test_foundation_makes_no_network_or_paid_calls():
    rc, out = _run(
        "import socket\n"
        "def blocked(*a, **k):\n"
        "    raise AssertionError('地基层发起了网络连接')\n"
        "socket.socket.connect = blocked\n"
        "socket.create_connection = blocked\n"
        "import pilot.legacy_provider_specs as S\n"
        "import pilot.legacy_runtime_config as C\n"
        "from pilot.legacy_provider_factory import LegacyProviderFactory\n"
        "f = LegacyProviderFactory(); f.register_specs()\n"
        "S.validate_all(); C.empty_config()\n"
        "print('NO_NETWORK_OK', f.resolve_count())")
    assert rc == 0, out
    assert "NO_NETWORK_OK 0" in out, out
