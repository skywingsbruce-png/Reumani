"""A.8.2b.1 §1 —— legacy 付费 provider 的**纯声明层**（零副作用）。

为什么单独建这一层：`ssc_pi_agent.py` 在 import 期就构造 3 个客户端和 3 个 React Agent，
任何 `from ssc_pi_agent import <名字>` 都会完整触发。要拆掉它，必须先有一个
**可以被安全 import 的地方**来描述"这些模型本来是什么参数"。

本模块只有数据：
- 不 import ChatOpenAI / ChatAnthropic；
- 不读 os.environ、不调 load_dotenv；
- 不构造客户端、不构造 React Agent；
- import 时零网络、零 key。

范围诚实声明：本模块**不改变**、**不接管** `ssc_pi_agent` 现有的三个模块级单例。
本阶段结束时 `legacy_ssc_pi_agent_import_safe` 仍然是 False —— 见
`pilot/provider_migration.py`。这里只是把未来迁移的目标形状先定下来。

角色刻意分成 5 个而不是 3 个：`debate_pro` 与 `debate_con` **必须分开**，
因为二者唯一的差别就是 temperature（0.3 vs 0.7）；合并会改变旧辩论语义。
"""

from __future__ import annotations

from pydantic import ConfigDict, Field

from schemas import _Strict


class LegacyProviderSpecError(RuntimeError):
    """legacy spec 声明非法 → fail-closed，绝不猜默认值。"""


# 五个 legacy 角色。刻意不提供 "default" / "any" 之类的兜底角色。
LEGACY_ROLES = ("debate_pro", "debate_con", "judge", "general_deepseek", "general_claude")

_ALLOWED_MODES = ("json_object_only", "native_json_schema", "legacy_text")

# FORBIDDEN：`ssc_pi_agent.py` 现在用的是浮动别名 "deepseek-chat"（DEEPSEEK_MODEL 的
# 默认值）。浮动别名会在你不知情时换模型，因此 pilot 的可执行配置里**禁止**使用它
# （见 pilot/paid_transport.FORBIDDEN_DEEPSEEK 与 tests/test_pinned_models.py）。
# 这里如实记录"legacy 用的是别名"这一事实，但下面的 spec 一律使用**钉死版本**——
# 迁移的目的之一正是把浮动别名换掉，而不是把它带进新地基。
LEGACY_FLOATING_ALIAS_IN_USE = True                 # ssc_pi_agent 现状：是（待 A.8.2b.2 处理）
PINNED_DEEPSEEK_MODEL = "deepseek-v4-flash"         # 与 paid_transport.PINNED_DEEPSEEK 一致
PINNED_ANTHROPIC_MODEL = "claude-opus-4-8"


class LegacyProviderSpec(_Strict):
    """一个 legacy 付费模型的声明。只有数据，没有客户端。"""
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: str
    provider: str                     # deepseek / anthropic
    role: str                         # LEGACY_ROLES 之一
    model_id: str
    temperature: float
    timeout: float
    max_tokens: int
    retry_policy: str                 # 本项目恒为 "no_retry"
    provider_mode: str
    gated_required: bool              # 真实调用前是否必须包 Gate
    intended_consumers: tuple = Field(default_factory=tuple)
    note: str = ""

    def validate_spec(self) -> None:
        """resolve 之前必须逐项通过。任何一项不满足都不允许构造客户端。"""
        for f in ("provider_id", "provider", "role", "model_id", "retry_policy",
                  "provider_mode"):
            if not str(getattr(self, f) or "").strip():
                raise LegacyProviderSpecError(f"{self.provider_id} 缺少必填字段 {f}")
        if self.role not in LEGACY_ROLES:
            raise LegacyProviderSpecError(
                f"{self.provider_id} 的 role={self.role!r} 不在 {LEGACY_ROLES}")
        if self.retry_policy != "no_retry":
            raise LegacyProviderSpecError(
                f"{self.provider_id} 的 retry_policy={self.retry_policy!r} —— 本项目禁止重试")
        if self.timeout is None or self.timeout <= 0:
            raise LegacyProviderSpecError(f"{self.provider_id} 缺少有限的正 timeout")
        if not self.max_tokens or self.max_tokens <= 0:
            raise LegacyProviderSpecError(f"{self.provider_id} 缺少有限的正 max_tokens")
        if not (0.0 <= self.temperature <= 2.0):
            raise LegacyProviderSpecError(
                f"{self.provider_id} 的 temperature={self.temperature} 超出 [0, 2]")
        if self.provider_mode not in _ALLOWED_MODES:
            raise LegacyProviderSpecError(
                f"{self.provider_id} 的 provider_mode={self.provider_mode!r} 未知")
        if self.provider not in ("deepseek", "anthropic"):
            raise LegacyProviderSpecError(
                f"{self.provider_id} 的 provider={self.provider!r} 未知（不猜）")
        # 浮动别名一律拒绝：模型必须钉死版本，否则成本与行为都不可复现。
        # 惰性 import，保持本模块 import 期零副作用。
        from pilot.paid_transport import FORBIDDEN_DEEPSEEK
        if self.model_id in FORBIDDEN_DEEPSEEK:
            raise LegacyProviderSpecError(
                f"{self.provider_id} 使用了浮动别名 {self.model_id!r} → 必须钉死版本")


# ---------------------------------------------------------------------------
# 冻结的 legacy 声明。参数照抄 ssc_pi_agent.py 现状，**不做任何"顺手优化"**——
# 这层的价值在于如实描述现状，任何参数漂移都会改变旧行为。
# ---------------------------------------------------------------------------
LEGACY_SPECS: tuple = (
    LegacyProviderSpec(
        provider_id="legacy-deepseek-debate-pro",
        provider="deepseek", role="debate_pro",
        model_id=PINNED_DEEPSEEK_MODEL,           # 钉死版本，不用 legacy 的浮动别名
        temperature=0.3,                         # ssc_pi_agent.py:160
        timeout=120.0, max_tokens=4096, retry_policy="no_retry",
        provider_mode="json_object_only", gated_required=True,
        intended_consumers=("ssc_pi_agent.debater_pro", "pages/7_方向辩论(可选).py"),
        note="与 debate_con 唯一差别是 temperature；不得合并。",
    ),
    LegacyProviderSpec(
        provider_id="legacy-deepseek-debate-con",
        provider="deepseek", role="debate_con",
        model_id=PINNED_DEEPSEEK_MODEL,
        temperature=0.7,                         # ssc_pi_agent.py:166
        timeout=120.0, max_tokens=4096, retry_policy="no_retry",
        provider_mode="json_object_only", gated_required=True,
        intended_consumers=("ssc_pi_agent.debater_con", "pages/7_方向辩论(可选).py"),
        note="模块外零直接消费者；仅经 debater_con 使用。",
    ),
    LegacyProviderSpec(
        provider_id="legacy-anthropic-judge",
        provider="anthropic", role="judge",
        model_id=PINNED_ANTHROPIC_MODEL,         # ssc_pi_agent.py:239
        temperature=0.0,
        timeout=180.0, max_tokens=4096, retry_policy="no_retry",
        provider_mode="native_json_schema", gated_required=True,
        intended_consumers=("ssc_pi_agent.judge_agent", "pages/7_方向辩论(可选).py"),
        note="page 7 跨 Streamlit rerun 复用同一对象与 judge_history。",
    ),
    LegacyProviderSpec(
        provider_id="legacy-deepseek-general",
        provider="deepseek", role="general_deepseek",
        model_id=PINNED_DEEPSEEK_MODEL,
        temperature=0.3,                         # 现状：通用消费者复用 deepseek_llm_pro
        timeout=120.0, max_tokens=4096, retry_policy="no_retry",
        provider_mode="json_object_only", gated_required=True,
        intended_consumers=("ssc_writer", "ssc_protocol", "ssc_evidence", "ssc_eval",
                            "ssc_action_discovery", "ssc_skill_agent", "ssc_a1",
                            "experiment_copilot", "shadow", "pages/9_数据对话.py"),
        note="现状下这些消费者与 debate_pro 共用同一个对象；分开是本次的目的之一。",
    ),
    LegacyProviderSpec(
        provider_id="legacy-anthropic-general",
        provider="anthropic", role="general_claude",
        model_id=PINNED_ANTHROPIC_MODEL,
        temperature=0.0,
        timeout=180.0, max_tokens=4096, retry_policy="no_retry",
        provider_mode="native_json_schema", gated_required=True,
        intended_consumers=("ssc_writer", "ssc_protocol", "ssc_evidence", "ssc_eval",
                            "ssc_skill_agent", "ssc_a1", "experiment_copilot", "shadow",
                            "pages/9_数据对话.py"),
        note="现状下这些消费者与 judge 共用同一个对象。",
    ),
)


def spec_for(role: str) -> LegacyProviderSpec:
    """按角色取声明。未知角色 fail-closed —— **没有默认回退模型**。"""
    for s in LEGACY_SPECS:
        if s.role == role:
            return s
    raise LegacyProviderSpecError(
        f"未知 legacy 角色 {role!r}；已声明的只有 {LEGACY_ROLES}（不提供默认回退）")


def all_specs() -> tuple:
    return LEGACY_SPECS


def validate_all() -> int:
    """逐条校验声明本身，不构造任何客户端。返回校验条数。"""
    seen = set()
    for s in LEGACY_SPECS:
        s.validate_spec()
        if s.provider_id in seen:
            raise LegacyProviderSpecError(f"provider_id 重复：{s.provider_id}")
        seen.add(s.provider_id)
    roles = [s.role for s in LEGACY_SPECS]
    if len(set(roles)) != len(roles):
        raise LegacyProviderSpecError(f"角色重复：{roles}")
    return len(LEGACY_SPECS)


__all__ = ["LegacyProviderSpec", "LegacyProviderSpecError", "LEGACY_SPECS", "LEGACY_ROLES",
           "spec_for", "all_specs", "validate_all"]
