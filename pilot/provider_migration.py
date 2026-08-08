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
    {"consumer": "pilot/controlled_runtime.py::build_controlled_runtime_registry + "
                 "build_approved_research_executor",
     "was": "无两阶段边界：from_registry 会在任何时点立即 resolve 付费客户端",
     "now": "阶段 A 只注册并 validate（factory_calls=0）；阶段 B 需 approval_verified 才 resolve",
     "roles": ["synthesizer", "verifier", "claim_extractor"]},
    {"consumer": "pilot/runtime_api.py::build_gated_research_executor",
     "was": "调用方自行构造并传入三个模型",
     "now": "接受 ProviderRegistry，按角色 resolve",
     "roles": ["synthesizer", "verifier", "claim_extractor"]},
)

# 未迁移的 legacy 消费者。理由必须具体，不能写"以后再说"。
UNMIGRATED_LEGACY = (
    {"symbol": "ssc_pi_agent.deepseek_llm_pro", "constructed_at_import": True,
     "reason": "10 个模块以 `from ssc_pi_agent import ...` 做模块级名字绑定，"
               "另有 15 处测试 monkeypatch 该对象；惰性化会改变对象身份语义",
     "planned_phase": "A.8.2b"},
    {"symbol": "ssc_pi_agent.deepseek_llm_con", "constructed_at_import": True,
     "reason": "同上；旧 debate 路径内部使用", "planned_phase": "A.8.2b"},
    {"symbol": "ssc_pi_agent.judge_llm", "constructed_at_import": True,
     "reason": "同上；旧 judge/裁决路径使用", "planned_phase": "A.8.2b"},
    {"symbol": "quant_agent.llm", "constructed_at_import": True,
     "reason": "与受控科研链无关的独立旧脚本", "planned_phase": "A.8.2b"},
    {"symbol": "ryn_agent_all_in_one.llm", "constructed_at_import": True,
     "reason": "与受控科研链无关的独立旧脚本", "planned_phase": "A.8.2b"},
    {"symbol": "ryn_agent_all_in_one copy.sub_llm/master_llm", "constructed_at_import": True,
     "reason": "疑似副本文件，需人工确认是否废弃", "planned_phase": "A.8.2b"},
)

# 这些 legacy 项**不阻断** A.8.3：受控链已自足，且 Canary 启动时靠
# neutralize_unused_paid_clients + assert_no_raw_paid_client_reachable 兜底。
BLOCKS_A83 = False
# A.8.2a.2：受控生产入口已改为「阶段 A 只注册 / 阶段 B 批准后 resolve」的 Registry 路径
CONTROLLED_RUNTIME_REGISTRY_ACTIVE = True
BLOCKS_A8_3_UNTIL_A8_2B = True

MANIFEST = {
    "schema": "provider-migration-v1",
    "phase": "A.8.2a",
    "controlled_runtime_import_safe": CONTROLLED_RUNTIME_IMPORT_SAFE,
    "controlled_runtime_registry_active": CONTROLLED_RUNTIME_REGISTRY_ACTIVE,
    "blocks_A8_3_until_A8_2b": BLOCKS_A8_3_UNTIL_A8_2B,
    "legacy_ssc_pi_agent_import_safe": LEGACY_SSC_PI_AGENT_IMPORT_SAFE,
    "controlled_runtime_modules": list(CONTROLLED_RUNTIME_MODULES),
    "migrated_consumers": list(MIGRATED_CONSUMERS),
    "unmigrated_legacy": list(UNMIGRATED_LEGACY),
    "next_phase": "A.8.2b",
    "blocks_a83": BLOCKS_A83,
    "note": "受控科研链已迁移；全仓库的 import-time 付费客户端**尚未**消除。"
            "任何声称'全仓库已完成'的说法都是错误的。",
}

__all__ = ["MANIFEST", "CONTROLLED_RUNTIME_REGISTRY_ACTIVE", "BLOCKS_A8_3_UNTIL_A8_2B", "CONTROLLED_RUNTIME_MODULES", "MIGRATED_CONSUMERS",
           "UNMIGRATED_LEGACY", "CONTROLLED_RUNTIME_IMPORT_SAFE",
           "LEGACY_SSC_PI_AGENT_IMPORT_SAFE", "BLOCKS_A83"]
