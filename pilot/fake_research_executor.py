"""A.7.5.3 —— 零付费 FakeResearchExecutor：按真实顺序跑完整八阶段科研链。

每个阶段是**独立对象 + 独立计数器**（不是手写一个最终 JSON 冒充全链执行）：
validate_evidence → evidence_accumulator → synthesizer → verifier → claim_extractor
→ claim_graph → shadow → artifact_builder

硬约束：
- Planner / ReAct Executor / 网络 / 代码执行 / 设备 一律为 0，并安装**哨兵**：一旦被触碰立即抛错；
- synthesizer / verifier / claim_extractor 各自角色额度 ≤1（不可互借）；
- 证据为**明确标记的测试夹具**（fixture=true、明显测试 ID），不伪造 PMID/DOI，不进入真实数据湖；
- Claim 只能引用已存在的 evidence_id；Shadow 只记录比较，**不新建证据、不翻转裁决**；
- Verifier 保留最终科学裁决权；无干预/纵向证据 → 因果封顶 insufficient_for_direct_causality。

阶段间阻塞用 threading.Event（可控、无 sleep），供 pause/stop 竞态测试稳定复现。
"""

from __future__ import annotations

import threading
from typing import Optional

from schemas import Claim
from tool_envelope import compute_hash
from pilot.research_contracts import (EvidenceReference, ResearchArtifact, ResearchRunContext,
                                      ResearchContractError)

EXECUTOR_ID = "fake-research-v1"

STAGES = ("validate_evidence", "evidence_accumulator", "synthesizer", "verifier",
          "claim_extractor", "claim_graph", "shadow", "artifact_builder")


class ForbiddenCapability(RuntimeError):
    """哨兵：Planner / ReAct / 网络 / 代码执行 / 设备 被触碰（本阶段必须为 0）。"""


class _Sentinel:
    """任何调用都立即 fail-closed，并把触碰次数记为可断言的证据。"""

    def __init__(self, name: str):
        self.name = name
        self.calls = 0

    def __call__(self, *a, **k):
        self.calls += 1
        raise ForbiddenCapability(f"A.7.5.3 禁止调用 {self.name}（allow_* 全为 False）")


# ----------------------------- 测试夹具证据（明确标记，不伪装真实文献） -----------------------------
def _fixture_cards() -> list:
    """明显的测试 ID；不使用真实 PMID/DOI；fixture=True。"""
    raw = [
        {"evidence_id": "FIXTURE-EV-001", "title": "[TEST FIXTURE] in-vitro mechanism observation",
         "study_type": "in_vitro", "species": "human_cell_line", "content_level": "abstract",
         "direction": "supports", "axes": ["mechanistic"]},
        {"evidence_id": "FIXTURE-EV-002", "title": "[TEST FIXTURE] cross-sectional association",
         "study_type": "cross_sectional", "species": "human", "content_level": "abstract",
         "direction": "supports", "axes": ["association"]},
        {"evidence_id": "FIXTURE-EV-003", "title": "[TEST FIXTURE] mouse model observation",
         "study_type": "animal_model", "species": "mouse", "content_level": "abstract",
         "direction": "mixed", "axes": ["mechanistic"]},
    ]
    for c in raw:
        c["fixture"] = True
        c["fixture_source"] = "pilot/fake_research_executor.py::_fixture_cards"
        c["content_hash"] = compute_hash({k: c[k] for k in sorted(c) if k != "content_hash"})
    return raw


def fixture_evidence_refs() -> list:
    """供 spec 使用的证据引用（只有 ID + hash + fixture 标记）。"""
    return [EvidenceReference(evidence_id=c["evidence_id"], content_hash=c["content_hash"],
                              fixture=True, fixture_source=c["fixture_source"])
            for c in _fixture_cards()]


# ----------------------------- 各阶段：独立对象 + 独立计数器 -----------------------------
class _Stage:
    name = "stage"

    def __init__(self):
        self.calls = 0

    def __call__(self, ctx: ResearchRunContext, state: dict) -> dict:
        self.calls += 1
        return self.run(ctx, state)

    def run(self, ctx, state) -> dict:                      # pragma: no cover - 抽象
        raise NotImplementedError


class ValidateEvidence(_Stage):
    name = "validate_evidence"

    def run(self, ctx, state):
        by_id = {c["evidence_id"]: c for c in _fixture_cards()}
        cards = []
        for ref in ctx.evidence_refs:
            card = by_id.get(ref.evidence_id)
            if card is None:
                raise ResearchContractError(f"未知 evidence_id（fail-closed）：{ref.evidence_id}")
            if card["content_hash"] != ref.content_hash:      # 证据被篡改 → 拒绝
                raise ResearchContractError(f"证据 content_hash 不一致：{ref.evidence_id}")
            cards.append(card)
        return {"validated_cards": cards, "evidence_ids": [c["evidence_id"] for c in cards]}


class EvidenceAccumulator(_Stage):
    name = "evidence_accumulator"

    def run(self, ctx, state):
        axes = sorted({a for c in state["validated_cards"] for a in c["axes"]})
        # 关键科学判定：夹具中不存在 interventional / longitudinal 轴
        return {"axes": axes,
                "has_interventional": "interventional" in axes,
                "has_longitudinal": "longitudinal" in axes,
                "evidence_count": len(state["validated_cards"])}


class FakeSynthesizer(_Stage):
    """占用 synthesizer 角色额度（fake，零付费）。"""
    name = "synthesizer"
    role = "synthesizer"

    def run(self, ctx, state):
        return {"synthesis": {
            "association": True,
            "mechanistic_support": "mechanistic" in state["axes"],
            "longitudinal_support": state["has_longitudinal"],
            "interventional_support": state["has_interventional"],
            "direct_causal_support": False,
            "insufficient_evidence": not (state["has_interventional"] or state["has_longitudinal"]),
            "summary": "[FIXTURE] association + partial mechanistic support; "
                       "no interventional or longitudinal evidence in the frozen fixture set."}}


class FakeVerifier(_Stage):
    """占用 verifier 角色额度。保留最终裁决权；无干预/纵向证据 → 因果封顶。"""
    name = "verifier"
    role = "verifier"

    def run(self, ctx, state):
        s = state["synthesis"]
        if s["interventional_support"] or s["longitudinal_support"]:
            tier, verdict = "supported_mechanistic", "partially_supported"
        else:
            tier, verdict = "insufficient_for_direct_causality", "insufficient_evidence"
        return {"verifier_verdict": verdict, "causal_tier": tier,
                "verifier_is_final": True,
                "limitations": ["fixture evidence only (not real literature)",
                                "no interventional evidence",
                                "no longitudinal evidence",
                                "abstract-level content only"]}


class FakeClaimExtractor(_Stage):
    """占用 claim_extractor 角色额度。只能引用已有 evidence_id，不得新增证据。"""
    name = "claim_extractor"
    role = "claim_extractor"

    def run(self, ctx, state):
        ids = list(state["evidence_ids"])
        claims = [
            Claim(claim_id="C1", text="[FIXTURE] Pathway activity is associated with fibroblast activation.",
                  claim_type="association", causal_strength="associative",
                  supporting_evidence_ids=ids[:2], verdict="partially_supported",
                  uncertainty="cross-sectional fixture evidence"),
            Claim(claim_id="C2", text="[FIXTURE] Pathway activation directly causes sustained activation.",
                  claim_type="causal", causal_strength="unknown",
                  supporting_evidence_ids=ids[:1], unresolved_evidence_ids=ids[2:],
                  verdict="insufficient_evidence", human_review_required=True,
                  uncertainty="no interventional or longitudinal evidence"),
        ]
        return {"claims": claims}


class FakeClaimGraph(_Stage):
    name = "claim_graph"

    def run(self, ctx, state):
        return {"claim_graph": {"nodes": len(state["claims"]),
                                "edges": max(0, len(state["claims"]) - 1),
                                "verdicts": [c.verdict for c in state["claims"]]}}


class FakeShadow(_Stage):
    """Shadow 只记录比较，**不新建证据、不翻转 Verifier 裁决**。"""
    name = "shadow"

    def run(self, ctx, state):
        shadow_verdict = "agrees_with_verifier"
        disagreement = shadow_verdict != "agrees_with_verifier"
        return {"shadow_verdict": shadow_verdict,
                "shadow_created_evidence": 0,          # 断言用：Shadow 永远不新建证据
                "shadow_overrode_verifier": False,
                "shadow_disagreement": disagreement}


class ArtifactBuilder(_Stage):
    name = "artifact_builder"

    def run(self, ctx, state):
        return {"artifact_ready": True}


# ----------------------------- 执行器 -----------------------------
class FakeResearchExecutor:
    """零付费执行器：实现 ResearchExecutor Protocol。不 import 任何模型客户端。"""

    executor_id = EXECUTOR_ID
    stages = STAGES

    def __init__(self, *, stage_gates: Optional[dict] = None):
        self._stage_gates = dict(stage_gates or {})        # {stage_name: threading.Event}
        # 声明是否存在阻塞阶段 → HitlRun 据此决定用后台 worker（可在阶段间暂停）还是同步跑完
        self.has_blocking_stages = bool(self._stage_gates)
        self._impl = {
            "validate_evidence": ValidateEvidence(),
            "evidence_accumulator": EvidenceAccumulator(),
            "synthesizer": FakeSynthesizer(),
            "verifier": FakeVerifier(),
            "claim_extractor": FakeClaimExtractor(),
            "claim_graph": FakeClaimGraph(),
            "shadow": FakeShadow(),
            "artifact_builder": ArtifactBuilder(),
        }
        # 越权能力哨兵（本阶段必须保持 0 次调用）
        self.sentinels = {n: _Sentinel(n) for n in
                          ("planner", "react_executor", "network", "code_execution", "device")}
        self.artifacts_built = 0

    # ---- 计数（供断言/遥测） ----
    def stage_counts(self) -> dict:
        return {n: s.calls for n, s in self._impl.items()}

    def role_counts(self) -> dict:
        return {r: self._impl[r].calls for r in ("synthesizer", "verifier", "claim_extractor")}

    def forbidden_counts(self) -> dict:
        return {n: s.calls for n, s in self.sentinels.items()}

    def model_call_count(self) -> int:
        return sum(self.role_counts().values())

    # ---- Protocol ----
    def run_stage(self, *, stage: str, ctx: ResearchRunContext, state: dict, emit=None) -> dict:
        if stage not in self._impl:
            raise ResearchContractError(f"未知阶段（fail-closed）：{stage}")
        impl = self._impl[stage]
        role = getattr(impl, "role", None)
        if role is not None:                                # 角色额度：≤1，不可互借
            limit = int(ctx.policy.role_limits.get(role, 0))
            if impl.calls >= limit:
                raise ResearchContractError(f"角色 {role} 超出额度（limit={limit}）")
            if self.model_call_count() >= int(ctx.policy.max_model_calls):
                raise ResearchContractError("超过 max_model_calls 上限")
        gate = self._stage_gates.get(stage)
        if gate is not None:
            gate.wait(timeout=30)                           # 可控阻塞（无 sleep）
        return impl(ctx, state)

    def build_artifact(self, *, ctx: ResearchRunContext, state: dict) -> ResearchArtifact:
        self.artifacts_built += 1
        art = ResearchArtifact(
            run_id=ctx.run_id, question_hash=ctx.question_hash,
            evidence_ids=list(state["evidence_ids"]), claims=list(state["claims"]),
            verifier_verdict=state["verifier_verdict"],          # Verifier 最终裁决
            shadow_verdict=state["shadow_verdict"],              # Shadow 仅记录
            causal_tier=state["causal_tier"],
            limitations=list(state["limitations"]), fixture=True).finalize()
        art.assert_claims_cite_known_evidence()                  # Claim 只引用已有证据
        return art


def build_default_spec(question: str = None, executor_id: str = EXECUTOR_ID):
    """A.7.5.3 验收演示用的参数化 spec（fake 证据 + 零权限策略）。"""
    from pilot.research_contracts import (ResearchRunSpec, ResearchClarificationSpec,
                                          ResearchApprovalSpec, ResearchOption,
                                          ResearchExecutionPolicy)
    q = question or ("根据提供的测试证据，判断一个机制假说是否达到直接因果证据标准。"
                     "请区分相关性、机制支持与直接因果证据，并明确证据不足之处。")
    policy = ResearchExecutionPolicy()
    policy.assert_zero_paid_stage()
    return ResearchRunSpec(
        question=q,
        clarification=ResearchClarificationSpec(
            question="你希望结论采用哪种证据标准？",
            kind="single_or_other", allow_other=True, required=True,
            reason="证据标准会实质改变结论表述（相关性 / 机制假说 / 直接因果）",
            options=[
                ResearchOption(id="strict_causal", label="严格因果标准（推荐）", recommended=True),
                ResearchOption(id="mechanistic_hypothesis", label="机制假说标准（须标记待验证）"),
                ResearchOption(id="association_only", label="仅总结相关性"),
            ]),
        approval=ResearchApprovalSpec(
            action_summary="在冻结测试证据上运行 fake 三角色科研链（Synthesizer/Verifier/Claim extractor）",
            expected_side_effect="仅生成结构化测试产物；不访问网络、不执行代码、不连接设备、不调用付费模型",
            risk_level="high", is_simulation=True,
            reason="执行会消耗角色额度并产生结构化产物，需人工批准具体执行计划"),
        evidence_refs=fixture_evidence_refs(),
        execution_policy=policy,
        executor_id=executor_id)


__all__ = ["FakeResearchExecutor", "EXECUTOR_ID", "STAGES", "ForbiddenCapability",
           "fixture_evidence_refs", "build_default_spec"]
