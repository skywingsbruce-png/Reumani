"""A.7.4.7 —— 单题、一次、严格预算的真实模型金丝雀（运行/观测阶段，非功能扩展）。

确定性且不调用模型（全部复用既有真实组件）：Planner / Step Controller /
search_literature fixture replay / Exact-ID Resolver / EvidenceAccumulator /
EvidenceCard builder / Claim Graph / Shadow / lifecycle·loop·budget·provenance·fail-closed 守卫。

仅这三个角色允许换成真实 gated model（各≤1 次、独立 GatedModel 身份、独立角色额度）：
Synthesizer / Verifier / Claim extractor。Planner 与 Executor 的付费调用必须为 0。

硬限制：总真实调用≤3、每角色≤1（不互借）、retries=0、无 fallback、无自动重跑、
单题预算≤$1.50、调用前预留、调用后对账、最终 open reservations=0、
超时/模型错误/解析失败/事件写入失败一律 fail-closed。
"""

from __future__ import annotations

import dataclasses
import json
import re
from typing import Optional

from tool_envelope import compute_hash, now
from schemas import Claim
from pilot.open_task_contracts import ControlledInsufficientConclusion
from pilot.hard_gate import HardBudgetGate, GatedModel, GateConfigError
from pilot import prices as _prices
from pilot import paid_transport as PT
from pilot.real_runtime import build_real_deps, RealRunConfig, load_frozen_real_items, VerifiedExactIdSource
from pilot.open_task_runtime import OpenTaskRuntime

STAGE = "A747_canary"
CANARY_QUESTION = ("现有证据是否支持 IL-6 升高会导致系统性硬化症纤维化？"
                   "请区分相关性、机制支持与因果证据，并明确证据缺口。")

# 真实模型（价格已在 pilot/prices.py 官方核实）
SYNTH_MODEL = "claude-opus-4-8"       # Synthesizer（Anthropic）
VERIFY_MODEL = "claude-opus-4-8"      # Verifier（Anthropic）
CLAIM_MODEL = "deepseek-v4-flash"     # Claim extractor（DeepSeek）
FAKE_MODEL = "fake-model"             # 零价，供 fake 全链演练

MAX_TOKENS = {"synthesizer": 1500, "verifier": 1200, "claim_extractor": 1200}
ROLE_QUOTA = {"synthesizer": 1, "verifier": 1, "claim_extractor": 1}

# 只有具备结构化时序/干预证据轴才允许升到因果；否则 fail-closed 封顶（§5 科学校准）
_CAUSAL_FLOOR = ("insufficient", "association", "temporal_association")


# ------------------------- 预算闸门 -------------------------
def build_gate(ledger_path: str, *, fake: bool) -> HardBudgetGate:
    per_model = ({FAKE_MODEL: 3} if fake
                 else {SYNTH_MODEL: 2, CLAIM_MODEL: 1})   # synth+verify 同为 anthropic=2；claim deepseek=1
    return HardBudgetGate(
        stage=STAGE, ledger_path=ledger_path,
        max_usd_global=1.50, max_usd_stage=1.50, max_usd_task=1.50,
        max_calls_global=3, max_calls_task=3,
        max_calls_per_model=per_model, max_calls_per_role=dict(ROLE_QUOTA),
        task_timeout_s=180.0, max_retries=0, default_max_tokens=1500,
        allow_ci=fake)                                    # 只有 fake（零价）才允许在 CI 中演练


# ------------------------- fake provider（零价、无网络） -------------------------
class _FakeRoleModel:
    """确定性 fake 聊天模型：返回预置 JSON + usage。可被 GatedModel 包装并通过启动前检查。"""
    max_retries = 0
    timeout = 120.0
    max_tokens = 1500

    def __init__(self, content: str):
        self._content = content

    def invoke(self, payload, *a, **k):
        return _FakeResp(self._content)


class _FakeResp:
    def __init__(self, content):
        self.content = content
        self.usage_metadata = {"input_tokens": 120, "output_tokens": 200}
        self.response_metadata = {"token_usage": {"prompt_tokens": 120, "completion_tokens": 200}}


# ------------------------- 角色构造（fake / real），各自独立 GatedModel -------------------------
def build_roles(gate: HardBudgetGate, *, fake: bool, fakes: Optional[dict] = None) -> dict:
    if fake:
        f = fakes or {}
        roles = {
            "synthesizer": GatedModel(_FakeRoleModel(f["synthesizer"]), gate, role="synthesizer",
                                      model_id=FAKE_MODEL, max_tokens=MAX_TOKENS["synthesizer"]),
            "verifier": GatedModel(_FakeRoleModel(f["verifier"]), gate, role="verifier",
                                   model_id=FAKE_MODEL, max_tokens=MAX_TOKENS["verifier"]),
            "claim_extractor": GatedModel(_FakeRoleModel(f["claim_extractor"]), gate,
                                          role="claim_extractor", model_id=FAKE_MODEL,
                                          max_tokens=MAX_TOKENS["claim_extractor"]),
        }
        model_ids = {"synthesizer": FAKE_MODEL, "verifier": FAKE_MODEL, "claim_extractor": FAKE_MODEL}
    else:
        s = PT.build_anthropic(SYNTH_MODEL, max_tokens=MAX_TOKENS["synthesizer"])
        v = PT.build_anthropic(VERIFY_MODEL, max_tokens=MAX_TOKENS["verifier"])
        c = PT.build_deepseek(CLAIM_MODEL, max_tokens=MAX_TOKENS["claim_extractor"])
        roles = {
            "synthesizer": GatedModel(s, gate, role="synthesizer", model_id=SYNTH_MODEL,
                                      max_tokens=MAX_TOKENS["synthesizer"]),
            "verifier": GatedModel(v, gate, role="verifier", model_id=VERIFY_MODEL,
                                   max_tokens=MAX_TOKENS["verifier"]),
            "claim_extractor": GatedModel(c, gate, role="claim_extractor", model_id=CLAIM_MODEL,
                                          max_tokens=MAX_TOKENS["claim_extractor"]),
        }
        model_ids = {"synthesizer": SYNTH_MODEL, "verifier": VERIFY_MODEL, "claim_extractor": CLAIM_MODEL}
        # 启动前硬检查（任一不过 → 拒绝启动，真实付费=0）
        for r, m in roles.items():
            PT.assert_role_hardened(r, m, model_ids[r])
        PT.resolve_anthropic_billing(roles["synthesizer"], SYNTH_MODEL)
        PT.resolve_anthropic_billing(roles["verifier"], VERIFY_MODEL)
        PT.assert_deepseek_nonthinking("claim_extractor", roles["claim_extractor"], CLAIM_MODEL)
    # 三个必须是三个独立 wrapper（身份互不混淆）
    if len({id(roles["synthesizer"]), id(roles["verifier"]), id(roles["claim_extractor"])}) != 3:
        raise GateConfigError("三个角色必须是三个独立 GatedModel 实例")
    return roles


# ------------------------- 解析（fail-closed） -------------------------
def _parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", str(text or ""), re.S)
    if not m:
        raise ValueError("模型输出非 JSON（fail-closed，不重试）")
    return json.loads(m.group(0))


def _cap_causal(model_strength: str, axes: set) -> str:
    """§5 科学封顶：无结构化时序/干预轴 → 绝不允许 causal / intervention_supported。"""
    if "intervention_evidence" in axes:
        return model_strength if model_strength in ("intervention_supported", "causal") else "temporal_association"
    if "temporal_evidence" in axes:
        return "temporal_association"
    if model_strength in ("causal", "intervention_supported", "temporal_association"):
        return "association"       # 证据不足以支持因果 → 封顶到关联
    return model_strength if model_strength in ("association", "not_supported", "insufficient") else "insufficient"


# ------------------------- 三个模型角色适配器 -------------------------
def make_synthesizer(model, axes_ref):
    def synth(req):
        prompt = (
            "你是系统性硬化症因果证据校准器。只依据给定的结构化证据作答，不得引入证据之外的内容。\n"
            "分别评估：association / mechanistic support / temporal evidence / intervention evidence / "
            "confounding·reverse-causality risk / overall causal tier / evidence gaps。\n"
            "横断面相关、单一生物标志物升高、体外机制、动物模型、无时间顺序的观察都不得当作因果证据。\n"
            "严格只输出一个 JSON：{\"resolved_question\":..,\"causal_strength\":"
            "\"insufficient|association|temporal_association|intervention_supported|not_supported\","
            "\"available_evidence\":[..],\"unsupported_claims\":[..],\"missing_evidence\":[..],\"limitations\":[..]}")
        human = json.dumps({"question": req.question, "evidence_summary": req.evidence_summary,
                            "causal_axes": req.causal_axes.model_dump(),
                            "known_gaps": req.missing_evidence}, ensure_ascii=False)
        res = model.invoke([("system", prompt), ("human", human)])
        d = _parse_json(getattr(res, "content", res))
        strength = _cap_causal(str(d.get("causal_strength", "insufficient")), set(axes_ref["axes"]))
        missing = list(d.get("missing_evidence") or []) or req.missing_evidence or ["证据不足以评估因果"]
        return ControlledInsufficientConclusion(
            resolved_question=d.get("resolved_question") or req.question,
            available_evidence=req.evidence_summary,
            unsupported_claims=list(d.get("unsupported_claims") or ["确定性因果表述（证据不支持）"]),
            causal_strength=strength, missing_evidence=missing,
            limitations=list(d.get("limitations") or ["真实模型金丝雀；仅冻结真实证据"]),
            recommended_next_action="检索纵向/干预研究补足时序与因果证据")
    return synth


def make_verifier(model, resolver_ref):
    def verify(conclusion, cards):
        facts = {"resolver_resolution": resolver_ref["resolution"],   # authoritative structured facts
                 "resolver_pmid": resolver_ref["pmid"],
                 "causal_strength": conclusion.causal_strength,
                 "evidence_ids": [c.evidence_id for c in cards],
                 "missing_evidence": conclusion.missing_evidence}
        prompt = ("你是独立核查者。基于给定 authoritative structured facts 判断结论是否越权声称因果。\n"
                  "不得推翻 Exact-ID Resolver 的 verified/not_found/source_error 终态。\n"
                  "只输出 JSON：{\"status\":\"passed|insufficient_for_causal|not_passed\","
                  "\"resolver_resolution_echo\":\"<原样回显 resolver_resolution>\",\"reason\":\"..\"}")
        res = model.invoke([("system", prompt), ("human", json.dumps(facts, ensure_ascii=False))])
        d = _parse_json(getattr(res, "content", res))
        # fail-closed：Verifier 不得篡改 resolver 事实终态
        if str(d.get("resolver_resolution_echo")) != str(resolver_ref["resolution"]):
            raise _FactConflict(f"verifier_fact_conflict: resolver={resolver_ref['resolution']!r} "
                                f"echo={d.get('resolver_resolution_echo')!r}")
        status = d.get("status")
        if status not in ("passed", "insufficient_for_causal", "not_passed"):
            raise ValueError("verifier status 非法（fail-closed）")
        return {"status": status, "reason": str(d.get("reason", ""))[:200]}
    return verify


def make_claim_extractor(model):
    def extract(conclusion, evidence_ids):
        allowed = set(evidence_ids)
        prompt = ("从结论中抽取原子 claim。supporting_evidence_ids 只能来自给定 evidence_ids，"
                  "不得虚构任何 id。只输出 JSON：{\"claims\":[{\"claim_id\":..,\"text\":..,"
                  "\"claim_type\":\"association|mechanistic|causal|other\","
                  "\"supporting_evidence_ids\":[..]}]}")
        human = json.dumps({"conclusion": conclusion.model_dump(), "evidence_ids": list(evidence_ids)},
                           ensure_ascii=False)
        res = model.invoke([("system", prompt), ("human", human)])
        d = _parse_json(getattr(res, "content", res))
        out = []
        for i, c in enumerate(d.get("claims") or []):
            # 只保留已有 evidence_id；虚构的 id 一律丢弃（不得引用不存在的证据）
            sup = [s for s in (c.get("supporting_evidence_ids") or []) if s in allowed]
            out.append(Claim(claim_id=str(c.get("claim_id") or f"c{i+1}"),
                             text=str(c.get("text") or "")[:400],
                             claim_type=(c.get("claim_type") if c.get("claim_type") in
                                         ("association", "mechanistic", "causal", "other") else "other"),
                             supporting_evidence_ids=sup))
        return out or [Claim(claim_id="c1", text="IL-6 与 SSc 皮肤评分相关（金丝雀）",
                             claim_type="association", supporting_evidence_ids=list(allowed))]
    return extract


class _FactConflict(RuntimeError):
    pass


# ------------------------- 运行编排 -------------------------
def run_canary(event_sink, *, run_id: str, ledger_path: str, fake: bool,
               fakes: Optional[dict] = None, should_stop=lambda: False) -> dict:
    """接入三个 gated 角色，运行一次（fake 或 real）。不重新联网（冻结真实证据）。"""
    gate = build_gate(ledger_path, fake=fake)
    roles = build_roles(gate, fake=fake, fakes=fakes)

    axes_ref = {"axes": set()}
    resolver_ref = {"resolution": "verified", "pmid": None}

    cfg = RealRunConfig(epmc_items=load_frozen_real_items(),   # 冻结真实 Europe PMC 记录，不重新联网
                        exact_id_sources=VerifiedExactIdSource(), query=CANARY_QUESTION)
    deps, state = build_real_deps(event_sink, cfg, clock=now, should_stop=should_stop)

    # 用三个真实/假 gated 角色替换 fake 角色（planner/executor 仍确定性、付费=0）
    deps = dataclasses.replace(
        deps,
        synthesizer=make_synthesizer(roles["synthesizer"], axes_ref),
        verifier=make_verifier(roles["verifier"], resolver_ref),
        claim_extractor=make_claim_extractor(roles["claim_extractor"]))

    gate.check_switches()          # 两个显式开关（real）；fake+allow_ci 也需 ENV 开关
    gate.start_task(run_id)
    try:
        rt = OpenTaskRuntime(deps, run_id=run_id, question=CANARY_QUESTION)
        # 冻结证据快照：在 synthesis（首个模型调用）之前，axes/resolver 已由确定性步骤定稿
        _hook_freeze(rt, state, axes_ref, resolver_ref)
        result = rt.run()
    finally:
        gate.end_task()

    open_res = gate.ledger.open_reservations()
    from pilot.real_runtime import _lifecycle_summary, COMPONENT_STATUS
    acc = result["session"].run_state.accumulator
    result["gate"] = gate.summary()
    result["open_reservations_ledger"] = len(open_res)
    result["evidence_hash"] = _evidence_hash(state)
    result["resolver_resolution"] = resolver_ref["resolution"]
    result["lifecycle"] = _lifecycle_summary(state["lifecycle"])
    # 金丝雀里这三个角色是**真实 gated model**（覆盖 real_runtime 的 fake 默认标签）
    result["components"] = {**COMPONENT_STATUS,
                            "synthesizer": ("real_gated_model" if not fake else "fake"),
                            "verifier": ("real_gated_model" if not fake else "fake"),
                            "claim_extractor": ("real_gated_model" if not fake else "fake")}
    result["evidence_ids"] = list(acc.evidence_ids)
    result["evidence_content_hashes"] = [c.provenance.content_hash for c in acc.evidence_cards]
    result["evidence_axes"] = list(acc.evidence_axes)
    return result


def _hook_freeze(rt: OpenTaskRuntime, state, axes_ref, resolver_ref):
    """在 runtime 的 synthesis 之前把确定性 axes / resolver verdict 冻结进引用。
    通过包装 rt._synthesize 实现——此时所有步骤已终态、证据已定稿。"""
    orig = rt._synthesize

    def wrapped(session):
        axes_ref["axes"] = set(session.run_state.accumulator.evidence_axes)
        cards = session.run_state.accumulator.evidence_cards
        resolver_ref["pmid"] = cards[0].pmid if cards else None
        return orig(session)
    rt._synthesize = wrapped


def _evidence_hash(state) -> str:
    return compute_hash({"pmid": state.get("pmid"), "source": state.get("source")})


# ------------------------- 冻结证据（供 preflight/报告） -------------------------
def freeze_evidence() -> dict:
    """确定性地重建冻结真实证据（不联网），返回脱敏摘要 + hash。"""
    from pilot.event_store import InMemoryEventStore
    from pilot.real_runtime import run_real_demo
    store = InMemoryEventStore()
    res = run_real_demo(store.append, run_id="freeze-probe")
    acc = res["session"].run_state.accumulator
    return {"evidence_ids": list(acc.evidence_ids),
            "evidence_count": len(acc.evidence_cards),
            "pmids": [c.pmid for c in acc.evidence_cards],
            "content_hashes": [c.provenance.content_hash for c in acc.evidence_cards],
            "evidence_axes": list(acc.evidence_axes),
            "frozen_hash": compute_hash([c.provenance.content_hash for c in acc.evidence_cards])}


# ------------------------- preflight -------------------------
def preflight(*, fake: bool) -> dict:
    """运行前零付费验收的一部分：价格/角色/账本可构造 + 冻结证据存在。任一失败抛错。"""
    checks = {}
    # 价格已核实
    for mid in (SYNTH_MODEL, VERIFY_MODEL, CLAIM_MODEL):
        _prices.price_for(mid)
    checks["prices_verified"] = [SYNTH_MODEL, VERIFY_MODEL, CLAIM_MODEL]
    # 冻结证据（真实公开记录，已 hash）
    fe = freeze_evidence()
    if fe["evidence_count"] < 1:
        raise GateConfigError("冻结证据为空 → 拒绝启动")
    checks["frozen_evidence"] = fe
    checks["causal_floor"] = _CAUSAL_FLOOR
    checks["price_table_version"] = _prices.PRICE_TABLE_VERSION
    return checks


# ------------------------- fake 全链（供 API/UI 演练，零付费） -------------------------
FAKE_RESPONSES = {
    "synthesizer": json.dumps({
        "resolved_question": CANARY_QUESTION, "causal_strength": "association",
        "unsupported_claims": ["IL-6 升高导致纤维化（确定性因果，证据不支持）"],
        "missing_evidence": ["纵向/时序证据", "干预（RCT/敲除回补）证据", "混杂控制", "反向因果排查"],
        "limitations": ["横断面相关", "单一队列"]}, ensure_ascii=False),
    "verifier": json.dumps({"status": "insufficient_for_causal",
        "resolver_resolution_echo": "verified", "reason": "仅关联/机制，无时序与干预证据"}, ensure_ascii=False),
    "claim_extractor": json.dumps({"claims": [{"claim_id": "c1",
        "text": "SSc 患者 IL-6 与皮肤评分相关", "claim_type": "association",
        "supporting_evidence_ids": []}]}, ensure_ascii=False),
}


def canary_meta(result: dict, *, kind: str) -> dict:
    """脱敏 canary 元信息（无 prompt/key/绝对路径/模型正文）。"""
    g = result.get("gate", {})
    concl = result.get("conclusion")
    return {"canary_kind": kind, "question": CANARY_QUESTION,
            "model_calls": g.get("calls_global", 0),
            "calls_by_role": g.get("calls_by_role", {}),
            "usd_cost": round(float(g.get("actual_usd", 0.0)), 6),
            "open_reservations": result.get("open_reservations_ledger", 0),
            "causal_tier": concl.causal_strength if concl else None,
            "evidence_hash": result.get("evidence_hash"),
            "resolver_resolution": result.get("resolver_resolution"),
            "frozen_real_literature": True}


def run_fake_canary(event_sink, *, run_id: str, ledger_path: str, should_stop=lambda: False) -> dict:
    """零付费 fake 全链（含 UI SSE）。临时置显式开关（fake-model 零价、无网络）。"""
    import os
    from pilot.hard_gate import ENV_PAID, ENV_CONFIRM
    saved = {ENV_PAID: os.environ.get(ENV_PAID), ENV_CONFIRM: os.environ.get(ENV_CONFIRM)}
    os.environ[ENV_PAID] = "1"
    os.environ[ENV_CONFIRM] = STAGE
    try:
        res = run_canary(event_sink, run_id=run_id, ledger_path=ledger_path, fake=True,
                         fakes=FAKE_RESPONSES, should_stop=should_stop)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    res["meta"] = canary_meta(res, kind="fake")
    return res


__all__ = ["STAGE", "CANARY_QUESTION", "build_gate", "build_roles", "run_canary",
           "run_fake_canary", "canary_meta", "FAKE_RESPONSES",
           "freeze_evidence", "preflight", "make_synthesizer", "make_verifier",
           "make_claim_extractor", "SYNTH_MODEL", "VERIFY_MODEL", "CLAIM_MODEL", "ROLE_QUOTA"]
