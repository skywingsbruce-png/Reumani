"""A.7.5.4.1 —— Canary 证据子集的离线冻结校验（§8）。

只读已冻结产物，不访问网络、不调用付费 LLM。
核心保证：原 v1 证据包未被修改；子集只引用不改写；综述/manual-review/未核验项不得进入核心。
"""
import hashlib
import json
import pathlib
import re

import pytest

pytestmark = pytest.mark.unit

BASE = pathlib.Path(__file__).resolve().parent.parent / "evidence_packs"
SRC = BASE / "ssc_cgas_sting_v1"
SUB = BASE / "ssc_cgas_sting_canary_v1"


def _jl(p):
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _sha_file(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def src_cards():
    return {c["evidence_id"]: c for c in _jl(SRC / "evidence" / "evidence_cards.jsonl")}


@pytest.fixture(scope="module")
def core():
    return _jl(SUB / "canary_evidence_cards.jsonl")


@pytest.fixture(scope="module")
def manifest():
    return json.loads((SUB / "CANARY_INPUT_MANIFEST.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ref():
    return json.loads((SUB / "source_pack_reference.json").read_text(encoding="utf-8"))


# ============================ 1-3 原包不变 / 引用一致 ============================
def test_source_pack_hash_unchanged(ref):
    m = json.loads((SRC / "manifests" / "evidence_pack_manifest.json").read_text(encoding="utf-8"))
    assert ref["source_pack_hash"] == m["pack_hash"]
    assert ref["protocol_hash"] == m["protocol_hash"]
    payload = {k: v for k, v in m.items() if k not in ("pack_hash", "created_at")}
    recomputed = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert recomputed == m["pack_hash"], "v1 pack manifest was modified"


def test_every_selected_card_exists_in_source_pack(core, src_cards):
    for c in core:
        assert c["evidence_id"] in src_cards, c["evidence_id"]


def test_selected_card_hashes_match_originals(core, src_cards):
    for c in core:
        assert c["source_card_content_hash"] == src_cards[c["evidence_id"]]["content_hash"], \
            f"{c['evidence_id']} content hash drifted -> the original card was modified"


def test_subset_does_not_copy_and_edit_original_cards(core):
    """子集只承载引用 + audit overlay，不得携带被改写的原卡正文字段。"""
    forbidden = {"supporting_excerpt", "provenance", "supports_claims", "limitations", "content_hash"}
    for c in core:
        assert not (forbidden & set(c)), f"{c['evidence_id']} carries rewritten original fields"


# ============================ 4-7 核心准入边界 ============================
def test_no_reviews_in_core(core, src_cards):
    for c in core:
        assert src_cards[c["evidence_id"]]["disease_scope"] != "review_navigation"
        assert src_cards[c["evidence_id"]]["relevance_tier"] != "S7"
        assert c["non_evidentiary_context"] is False


def test_context_only_reviews_are_marked_and_bounded():
    ctx = _jl(SUB / "context_only.jsonl")
    assert len(ctx) <= 2
    for c in ctx:
        assert c["non_evidentiary_context"] is True
        assert c["may_be_cited_as_primary_result"] is False
        assert c["affects_causal_tier"] is False


def test_no_metadata_only_in_core(core, src_cards):
    for c in core:
        assert src_cards[c["evidence_id"]]["content_level"] == "abstract"


def test_no_unverified_or_not_found_in_core(core, src_cards):
    ver = {v["candidate_id"]: v for v in _jl(SRC / "verification" / "citation_verification.jsonl")}
    bad_pmids = {v.get("pmid") for v in ver.values()
                 if v["status"] in ("not_found", "mismatch", "suspicious", "retracted") and v.get("pmid")}
    for c in core:
        assert c.get("normalized_pmid") not in bad_pmids, c["evidence_id"]


def test_manual_review_items_never_enter_core(core):
    man = _jl(SUB / "manual_review_assessment.jsonl")
    core_ids = {c.get("normalized_pmid") for c in core} | {c.get("normalized_doi") for c in core}
    for m in man:
        assert m["enters_canary_subset"] is False
        if m.get("pmid"):
            assert m["pmid"] not in core_ids, f"manual-review {m['pmid']} leaked into core"


def test_every_core_card_is_exactly_verified(core, src_cards):
    for c in core:
        vb = src_cards[c["evidence_id"]]["provenance"]["verified_by"]
        assert set(vb) & {"pubmed", "crossref"}, c["evidence_id"]


# ============================ 9-12 科学边界 ============================
def test_subset_contains_contradiction_or_limitation(core, manifest):
    assert manifest["contradiction_or_limitation_count"] >= 1
    assert any(c["contradiction_excerpt"] for c in core), "need at least one negative/contradiction card"


def test_indirect_cards_are_explicitly_marked(core, src_cards):
    for c in core:
        if c["is_indirect"]:
            assert src_cards[c["evidence_id"]]["disease_scope"] == "non_ssc_mechanistic_transfer"
            dns = src_cards[c["evidence_id"]]["does_not_support"]
            assert any("ssc_specific_claims" in s for s in dns), c["evidence_id"]


def test_no_preprint_duplicates_a_published_record(core, src_cards):
    dois = [c.get("normalized_doi") for c in core if c.get("normalized_doi")]
    assert len(dois) == len(set(dois))
    for c in core:
        assert not src_cards[c["evidence_id"]].get("superseded_by")


def test_c3_c4_cards_have_explicit_pathway_perturbation(core):
    """审计后仍为 C3/C4 的卡，必须记录明确的扰动目标。"""
    for c in core:
        if c["audited_causal_tier"] in ("C3", "C4"):
            assert c["perturbation_target"], c["evidence_id"]
            assert not re.fullmatch(r"(associated|activated|increased|expression|correlation)",
                                    str(c["perturbation_target"]), re.I)


def test_downgrades_record_a_reason(core):
    audit = {a["evidence_id"]: a for a in _jl(SUB / "card_audit.jsonl")}
    for c in core:
        if c["audit_verdict"] == "downgrade":
            a = audit[c["evidence_id"]]
            assert a.get("downgrade_reason") or a.get("removed_supports_claims") or a.get("notes"), \
                c["evidence_id"]


def test_animal_evidence_not_labelled_human_direct(core, src_cards):
    for c in core:
        s = src_cards[c["evidence_id"]]
        if s["relevance_tier"] in ("S3", "S5", "S6"):
            assert c["is_indirect"] or s["disease_scope"] != "systemic_sclerosis_direct"


def test_no_claims_are_generated_here(core):
    """本阶段只做审计与选择，不生成任何 Claim。"""
    blob = json.dumps(core, ensure_ascii=False)
    assert '"claims"' not in blob and '"claim_id"' not in blob


def test_direct_human_causal_count_is_zero(manifest):
    assert manifest["direct_human_causal_count"] == 0
    assert manifest["evidence_gap"]["c5_human_interventional"] == 0
    assert "not proof" in manifest["evidence_gap"]["note"].lower()


def test_required_declarations_present(manifest):
    joined = " ".join(manifest["declarations"]).lower()
    for needle in ("abstract-level", "does not prove", "never be extrapolated",
                   "context-only", "downstream verifier"):
        assert needle in joined, needle


# ============================ 14-18 hash / manifest / 安全 ============================
def test_subset_hash_is_deterministic(manifest):
    payload = {k: v for k, v in manifest.items() if k != "subset_hash"}
    recomputed = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert recomputed == manifest["subset_hash"]


def test_manifest_counts_match_files(manifest, core):
    assert len(manifest["selected_card_ids"]) == len(core)
    assert set(manifest["selected_card_ids"]) == {c["evidence_id"] for c in core}
    assert sum(manifest["counts_by_scope"].values()) == len(core)
    assert sum(manifest["counts_by_causal_tier"].values()) == len(core)
    assert manifest["context_only_count"] == len(_jl(SUB / "context_only.jsonl"))
    assert manifest["manual_review_excluded_count"] == len(_jl(SUB / "manual_review_assessment.jsonl"))
    assert manifest["preprint_count"] == sum(1 for c in core if c["publication_status"] == "preprint")


def test_recorded_file_hashes_still_match():
    recorded = {}
    for line in (SUB / "hashes.sha256").read_text(encoding="utf-8").splitlines():
        if line.strip():
            h, n = line.split(None, 1)
            recorded[n.strip()] = h
    for name, h in recorded.items():
        assert _sha_file(SUB / name) == h, f"{name} changed after freezing"


def test_zero_paid_llm_calls(manifest):
    assert manifest["paid_llm_calls"] == 0


def test_no_secrets_or_paths_in_subset():
    bad = re.compile(r"(?<![A-Za-z])sk-[A-Za-z0-9]{16,}|Bearer\s+[A-Za-z0-9]{8,}|Authorization:|"
                     r"Cookie:|[A-Za-z]:\\Users\\|/home/[a-z]+/|api[_-]?key\s*[:=]", re.I)
    for p in SUB.iterdir():
        if p.is_file():
            m = bad.search(p.read_text(encoding="utf-8", errors="replace"))
            assert not m, f"sensitive-looking content in {p.name}: {m.group()[:40]}"


def test_audit_covers_every_source_card(src_cards):
    audit = _jl(SUB / "card_audit.jsonl")
    assert {a["evidence_id"] for a in audit} == set(src_cards)
    for a in audit:
        assert a["verdict"] in ("confirmed", "downgrade", "exclude", "manual_needed")
