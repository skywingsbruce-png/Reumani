"""A.8.2a §4 —— provider 迁移状态的**机器可读**清单。

存在的意义是防止把局部成果说成全局成果：受控科研链已迁移到 ProviderRegistry，
但 `ssc_pi_agent.py` 的三个模块级付费单例**本轮完全未动**（A.8.2b 处理）。
测试直接读取本清单断言，因此它不能与事实漂移。
"""

from __future__ import annotations

# 受控科研链：import 时零客户端、零 key 读取（由测试逐模块验证）
CONTROLLED_RUNTIME_IMPORT_SAFE = True

# legacy：ssc_pi_agent 在 import 时就构造付费客户端 —— 本轮不动，**不得**声称已解决
LEGACY_SSC_PI_AGENT_IMPORT_SAFE = False

CONTROLLED_RUNTIME_MODULES = (
    "pilot.provider_registry", "pilot.gated_research_executor", "pilot.provider_output",
    "pilot.live_cost", "pilot.output_contract", "pilot.role_contracts",
    "pilot.budget_policy", "pilot.research_contracts", "pilot.research_results",
    "pilot.runtime_api", "pilot.hitl", "pilot.frozen_evidence",
)

MIGRATED_CONSUMERS = (
    {"consumer": "pilot/gated_research_executor.py",
     "was": "三个 GatedModel 由启动脚本按位置注入",
     "now": "显式接收 ProviderRegistry / 按角色 resolve 的 ProviderHandle",
     "roles": ["synthesizer", "verifier", "claim_extractor"]},
    {"consumer": "pilot/controlled_runtime.py::build_controlled_runtime_registry",
     "was": "无两阶段边界：from_registry 会在任何时点立即 resolve 付费客户端",
     "now": "阶段 A 只注册并 validate（factory_calls=0）；授权由 ApprovalGrant 与真实 "
            "approval_granted 事件绑定（A.8.2a.4a 已删除旧的布尔旁路与批准后工厂）",
     "roles": ["synthesizer", "verifier", "claim_extractor"]},
    {"consumer": "pilot/runtime_api.py::build_gated_research_executor",
     "was": "调用方自行构造并传入三个模型",
     "now": "接受 ProviderRegistry，按角色 resolve",
     "roles": ["synthesizer", "verifier", "claim_extractor"]},
)

# 未迁移的 legacy 消费者。理由必须具体，不能写"以后再说"。
UNMIGRATED_LEGACY = (
    {"symbol": "ssc_pi_agent.deepseek_llm_pro", "constructed_at_import": True,
     "reason": "A.8.2b.2b.1 已把 ssc_writer / ssc_protocol / ssc_evidence 改为调用期"
               "按角色解析（pilot/legacy_model_bridge），直接消费者由 10 降至 7；"
               "其余仍以 `from ssc_pi_agent import ...` 做模块级名字绑定",
     "consumers": 7, "planned_phase": "A.8.2b.2b"},
    {"symbol": "ssc_pi_agent.deepseek_llm_con", "constructed_at_import": True,
     "reason": "模块外**零**直接消费者；仅经 debater_con 使用。temperature=0.7，"
               "与 pro 的 0.3 不同，合并会改变旧辩论语义",
     "consumers": 0, "planned_phase": "A.8.2b.3"},
    {"symbol": "ssc_pi_agent.judge_llm", "constructed_at_import": True,
     "reason": "A.8.2b.2b.1 之后直接消费者由 9 降至 6；旧 judge/裁决路径仍在使用",
     "consumers": 6, "planned_phase": "A.8.2b.2b"},
    {"symbol": "ssc_pi_agent.debater_pro/debater_con/judge_agent",
     "constructed_at_import": True,
     "reason": "三个 React Agent 在 import 期就绑定了模型对象与工具；page 7 跨 "
               "Streamlit rerun 复用同一 judge_agent 与 judge_history，重建会断对话。"
               "因此**不能**只懒化客户端而保留这三个单例的 import-time 构造",
     "consumers": 1, "planned_phase": "A.8.2b.3"},
    {"symbol": "pilot/real_runtime.py", "constructed_at_import": True,
     "reason": "唯一仍在 import 期 `import ssc_pi_agent` 的 pilot 模块（旧 demo 链）。"
               "不在 CONTROLLED_RUNTIME_MODULES 内；runtime_api 已改为惰性 import 它",
     "consumers": 3, "planned_phase": "A.8.2b.2"},
    {"symbol": "quant_agent.llm", "constructed_at_import": True,
     "reason": "**不属于受控 Runtime**：独立旧脚本，与 SSc 无关。api_key 无占位兜底，"
               "缺 key 时 import 即可能抛",
     "consumers": 0, "in_controlled_runtime": False, "planned_phase": "A.8.2b.6"},
    {"symbol": "ryn_agent_all_in_one.llm", "constructed_at_import": True,
     "reason": "**不属于受控 Runtime**：OpenAI gpt-4o 股票脚本，与 SSc 无关",
     "consumers": 0, "in_controlled_runtime": False, "planned_phase": "A.8.2b.6"},
    {"symbol": "ryn_agent_all_in_one copy.sub_llm/master_llm", "constructed_at_import": True,
     "reason": "**不属于受控 Runtime**：文件名带 ' copy'，需人工确认后归档",
     "consumers": 0, "in_controlled_runtime": False, "planned_phase": "A.8.2b.6"},
    {"symbol": "ryn_stock-main/", "constructed_at_import": True,
     "reason": "**不属于受控 Runtime**：嵌在主仓里的独立子项目，自带 .env（未被 git "
               "跟踪，公开仓不存在该文件 → 无泄漏），但使主仓存在两套 dotenv 语义",
     "consumers": 0, "in_controlled_runtime": False, "planned_phase": "A.8.2b.6"},
)

# A.8.2b.0 §5 —— 对 legacy 全局对象的**实证**引用计数。旧清单曾写"15 处测试
# monkeypatch"，与事实不符；这里按类别拆开，测试直接读取本表断言，防止再次漂移。
LEGACY_REBIND_SITES = {
    "real_monkeypatch": (
        "tests/test_claim_extractor_role.py:205 (P.deepseek_llm_pro)",
        "tests/test_real_orchestration_fake_models.py:169 (ssc_a1.judge_llm)",
    ),
    "subprocess_import_order_scripts": (
        "tests/test_import_order.py:76", "tests/test_import_order.py:113",
        "tests/test_import_order.py:131", "tests/test_import_order.py:150",
    ),
    "production_runner_rebind": (
        "pilot/preflight_a1.py:190-191", "pilot/round2_runner.py:57-58",
    ),
    "read_only_reference": (
        "tests/test_hard_gate.py:366", "tests/test_provider_registry.py:259-270",
        "tests/test_controlled_runtime_activation.py:119-121",
        "tests/test_role_separation.py:180-181", "pilot/paid_transport.py:384-401",
    ),
}
REBIND_COUNTS = {k: len(v) for k, v in LEGACY_REBIND_SITES.items()}

# A.8.2b.1 §4：扫描器已改为**不触发式**（vars(module) 而非 dir+getattr）。
# 这是后续把 legacy 改惰性的前置条件——否则守卫自己会构造付费客户端。
NON_TRIGGERING_SCANNER = True

# A.8.2b.2b.1：第一批无状态消费者（writer/protocol/evidence）已接入调用期角色注入。
# 桥不构造、不缓存任何模型（缓存会绕过 preflight/round2_runner 的 Gate 重绑）。
STATELESS_WAVE1_MIGRATED = ("ssc_writer.py", "ssc_protocol.py", "ssc_evidence.py")
LEGACY_MODEL_BRIDGE = "pilot.legacy_model_bridge"

# A.8.2b.2b.1.1：核心路径已移除隐式回退。缺显式注入即 fail-closed，核心不再
# 自动 import ssc_pi_agent。旧行为隔离到具名适配器，由应用入口主动选用。
CORE_PATH_FAIL_CLOSED = True
LEGACY_COMPAT_ADAPTER = "pilot.legacy_compat_adapter"
# **这条兼容通道未受控**：拿到的是 legacy 裸客户端，未经 per-run Registry /
# Gate / HITL 授权与审计。不得把它描述成"正式受控模型迁移完成"。
LEGACY_COMPAT_IS_CONTROLLED = False
CONTROLLED_MODEL_MIGRATION_COMPLETE = False
# 角色是科学职责，不是 provider 名称
SCIENTIFIC_ROLES_IN_USE = ("literature_drafting", "literature_revision",
                           "protocol_drafting", "evidence_extraction",
                           "claim_verification")
# 显式选用兼容通道的应用入口（出现在调用点，不藏在 resolver 默认值里）
COMPAT_OPT_IN_ENTRYPOINTS = ("pages/1_科研写作助手.py", "pages/6_实验协议.py",
                             "ssc_skill_agent.py")

# A.8.2b.1 §1-3：无副作用地基已建立，但**尚未接入任何消费者**。
LEGACY_FOUNDATION_MODULES = ("pilot.legacy_provider_specs", "pilot.legacy_runtime_config",
                             "pilot.legacy_provider_factory")
LEGACY_FOUNDATION_WIRED_TO_CONSUMERS = False

# 冻结的后续批次顺序（A.8.2b.0 §6 审计结论 + 用户裁定）
MIGRATION_BATCHES = (
    {"batch": "A.8.2b.2", "scope": "迁移只做 model 选择的 8 个简单消费者；配置字符串"
     "消费者改用安全 settings；**不处理 debate / page 7**"},
    {"batch": "A.8.2b.3", "scope": "debate_pro / debate_con / judge；page 7；"
     "session_state 历史；React Agent 惰性创建"},
    {"batch": "A.8.2b.4", "scope": "从 ssc_pi_agent 删除 import-time load_dotenv；"
     "删除三个客户端单例；删除三个 React Agent 单例；处理可选兼容 API"},
    {"batch": "A.8.2b.5", "scope": "测试去全局 monkeypatch；删除 neutralize / "
     "import-order 机制；import 全部核心模块零客户端、零 key 读取"},
    {"batch": "A.8.2b.6", "scope": "隔离 quant_agent / ryn_agent_all_in_one 及 copy "
     "文件；不混入核心迁移提交"},
)

# 这些 legacy 项**不阻断** A.8.3：受控链已自足，且 Canary 启动时靠
# neutralize_unused_paid_clients + assert_no_raw_paid_client_reachable 兜底。
BLOCKS_A83 = False
# A.8.2a.2：受控生产入口已改为「阶段 A 只注册 / 阶段 B 批准后 resolve」的 Registry 路径
CONTROLLED_RUNTIME_REGISTRY_ACTIVE = True
BLOCKS_A8_3_UNTIL_A8_2B = True

MANIFEST = {
    "schema": "provider-migration-v1",
    "phase": "A.8.2b.2b.1.1",
    "controlled_runtime_import_safe": CONTROLLED_RUNTIME_IMPORT_SAFE,
    "controlled_runtime_registry_active": CONTROLLED_RUNTIME_REGISTRY_ACTIVE,
    "blocks_A8_3_until_A8_2b": BLOCKS_A8_3_UNTIL_A8_2B,
    "legacy_ssc_pi_agent_import_safe": LEGACY_SSC_PI_AGENT_IMPORT_SAFE,
    "controlled_runtime_modules": list(CONTROLLED_RUNTIME_MODULES),
    "migrated_consumers": list(MIGRATED_CONSUMERS),
    "unmigrated_legacy": list(UNMIGRATED_LEGACY),
    "legacy_rebind_sites": {k: list(v) for k, v in LEGACY_REBIND_SITES.items()},
    "rebind_counts": dict(REBIND_COUNTS),
    "non_triggering_scanner": NON_TRIGGERING_SCANNER,
    "legacy_foundation_modules": list(LEGACY_FOUNDATION_MODULES),
    "legacy_foundation_wired_to_consumers": LEGACY_FOUNDATION_WIRED_TO_CONSUMERS,
    "stateless_wave1_migrated": list(STATELESS_WAVE1_MIGRATED),
    "legacy_model_bridge": LEGACY_MODEL_BRIDGE,
    "core_path_fail_closed": CORE_PATH_FAIL_CLOSED,
    "legacy_compat_adapter": LEGACY_COMPAT_ADAPTER,
    "legacy_compat_is_controlled": LEGACY_COMPAT_IS_CONTROLLED,
    "controlled_model_migration_complete": CONTROLLED_MODEL_MIGRATION_COMPLETE,
    "scientific_roles_in_use": list(SCIENTIFIC_ROLES_IN_USE),
    "compat_opt_in_entrypoints": list(COMPAT_OPT_IN_ENTRYPOINTS),
    "migration_batches": list(MIGRATION_BATCHES),
    "next_phase": "A.8.2b.2",
    "blocks_a83": BLOCKS_A83,
    "note": "受控科研链已迁移；全仓库的 import-time 付费客户端**尚未**消除。"
            "A.8.2b.1 只建立了无副作用地基（specs / config / factory）并修好了扫描器，"
            "**没有**接入任何消费者，也**没有**改动 ssc_pi_agent 的三个单例。"
            "任何声称'legacy 已迁移'或'全仓库已完成'的说法都是错误的。",
}

__all__ = ["MANIFEST", "CONTROLLED_RUNTIME_REGISTRY_ACTIVE", "BLOCKS_A8_3_UNTIL_A8_2B", "CONTROLLED_RUNTIME_MODULES", "MIGRATED_CONSUMERS",
           "UNMIGRATED_LEGACY", "CONTROLLED_RUNTIME_IMPORT_SAFE",
           "LEGACY_SSC_PI_AGENT_IMPORT_SAFE", "BLOCKS_A83", "LEGACY_REBIND_SITES",
           "REBIND_COUNTS", "NON_TRIGGERING_SCANNER", "LEGACY_FOUNDATION_MODULES",
           "LEGACY_FOUNDATION_WIRED_TO_CONSUMERS", "MIGRATION_BATCHES",
           "STATELESS_WAVE1_MIGRATED", "LEGACY_MODEL_BRIDGE", "CORE_PATH_FAIL_CLOSED",
           "LEGACY_COMPAT_ADAPTER", "LEGACY_COMPAT_IS_CONTROLLED",
           "CONTROLLED_MODEL_MIGRATION_COMPLETE", "SCIENTIFIC_ROLES_IN_USE",
           "COMPAT_OPT_IN_ENTRYPOINTS"]
