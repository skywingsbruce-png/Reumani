"""A.8.2b.2b.1.2 —— **ScientificOperation** 契约：科研步骤在做什么。

为什么必须与 ProviderRole 分开：A.8.2b.2b.1.1 把
`literature_drafting` / `claim_verification` 之类的名字放进了和
`synthesizer` / `verifier` / `claim_extractor` 同一个 "role" 词汇表。那是两件事——

- **ScientificOperation**：这一步在科研上做什么（起草综述、核验论断……）。
  它是**领域语义**，与用哪个模型、走哪条预算无关。
- **ProviderRole**：受控 Runtime 里由哪种模型职责去执行，它才是
  ProviderRegistry 注册、HardBudgetGate 计费、OutputContract 约束的那个键。

把 operation 当成 role 用，会让 Gate 按一个从未登记过额度的名字计费、
让 Registry 接受一个没有价格与输出契约的键 —— 两者都是 fail-open 的入口。
本模块因此**只定义 operation**，并显式声明它**尚未**绑定到任何受控 ProviderRole。

import 零副作用：不碰网络、不读 key、不构造客户端、不引入受控 Runtime。
"""

from __future__ import annotations

from enum import StrEnum


class ScientificOperationError(RuntimeError):
    """未知科研操作 → fail-closed，不猜。"""


class ScientificOperation(StrEnum):
    """科研步骤。**不是** provider role，不得用于 Registry / Gate / 预算。"""

    LITERATURE_DRAFTING = "literature_drafting"
    LITERATURE_REVISION = "literature_revision"
    PROTOCOL_DRAFTING = "protocol_drafting"
    EVIDENCE_EXTRACTION = "evidence_extraction"
    CLAIM_VERIFICATION = "claim_verification"


#: 这些字符串**永远不允许**出现在 ProviderRegistry / Gate / 预算的 role 位置。
OPERATION_VALUES: frozenset = frozenset(op.value for op in ScientificOperation)

#: operation → 受控 ProviderRole 的绑定。
#:
#: 现状是**全部未绑定**，而且这不是疏漏：A.8.2b.2b.1.1 的调用路径审计已经证明，
#: 这五个操作的生产调用者（Streamlit 页面、旧 ssc_skill_agent）都**不具备**
#: HITL / ProviderRegistry / Gate 上下文。在把它们接进受控 Runtime 之前，
#: 谎称一个绑定只会制造"看起来受控"的假象。
#:
#: 真正接入时，值必须取自受控 Runtime **已存在**的角色权威
#: （`pilot.role_contracts.ROLE_CONTRACTS` 的键），而不是在这里新造名字。
OPERATION_TO_PROVIDER_ROLE: dict = {op: None for op in ScientificOperation}


def operation_from(value: str) -> ScientificOperation:
    """把字符串解析成 operation。未知值 fail-closed，绝不回落到某个默认操作。"""
    try:
        return ScientificOperation(str(value))
    except ValueError as e:
        raise ScientificOperationError(
            f"未知科研操作 {value!r}；已声明的只有 {sorted(OPERATION_VALUES)}") from e


def provider_role_for(operation) -> None:
    """查询该操作绑定的受控 ProviderRole。

    现在一律返回 None —— 表示"尚未接入受控 Runtime"。调用方**不得**把 None
    当作"随便挑一个 role"，而应据此走显式兼容通道或直接失败。
    """
    op = operation if isinstance(operation, ScientificOperation) else operation_from(operation)
    return OPERATION_TO_PROVIDER_ROLE[op]


def is_bound_to_controlled_runtime(operation) -> bool:
    return provider_role_for(operation) is not None


def controlled_provider_roles() -> frozenset:
    """受控科研链**现有**角色权威的只读视图。本模块不复制、不新增角色名。

    惰性 import：保持本模块 import 零副作用。
    """
    from pilot.role_contracts import ROLE_CONTRACTS
    return frozenset(ROLE_CONTRACTS)


def assert_not_a_provider_role(value: str) -> None:
    """守卫：任何要用作 Registry / Gate / 预算 role 的字符串都必须先过这一关。"""
    if str(value) in OPERATION_VALUES:
        raise ScientificOperationError(
            f"{value!r} 是 ScientificOperation，不是 ProviderRole —— "
            "不得用于 ProviderRegistry 注册、Gate 计费或预算配额。")


__all__ = ["ScientificOperation", "ScientificOperationError", "OPERATION_VALUES",
           "OPERATION_TO_PROVIDER_ROLE", "operation_from", "provider_role_for",
           "is_bound_to_controlled_runtime", "controlled_provider_roles",
           "assert_not_a_provider_role"]
