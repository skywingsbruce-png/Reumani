"""A.7.5.4 —— SSc–cGAS–STING 证据包的冻结校验（§17）。

**离线**：只读取已冻结的包文件，不访问网络、不调用任何付费 LLM。
真实检索只运行一次并已冻结；CI 用这些脱敏产物做可复现校验。
"""
import hashlib
import json
import os
import pathlib
import re

import pytest

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parent.parent / "evidence_packs" / "ssc_cgas_sting_v1"
BANNED_TEST_IDS = {"1002", "1", "12345", "99999999", "0"}


def _jsonl(rel):
    p = ROOT / rel
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _sha_file(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(65536), b""):
            h.update(c)
    return h.hexdigest()


@pytest.fixture(scope="module")
def manifest():
    return json.loads((ROOT / "manifests" / "evidence_pack_manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def cards():
    return _jsonl("evidence/evidence_cards.jsonl")


# ============================ 协议与查询冻结 ============================
def test_protocol_hash_matches_frozen_value():
    md = ROOT / "protocol" / "SSc_CGAS_STING_EVIDENCE_PROTOCOL_V1.md"
    rec = (ROOT / "protocol" / "SSc_CGAS_STING_EVIDENCE_PROTOCOL_V1.sha256").read_text(
        encoding="utf-8").split()[0]
    assert _sha_file(md) == rec, "protocol file changed without a new hash/V2"


def test_queries_are_frozen_and_hashed():
    qh = json.loads((ROOT / "queries" / "queries_hash.json").read_text(encoding="utf-8"))
    for fn, h in qh["files"].items():
        assert _sha_file(ROOT / "queries" / fn) == h, f"{fn} changed after freezing"
    combined = hashlib.sha256(json.dumps(qh["files"], sort_keys=True).encode()).hexdigest()
    assert combined == qh["queries_hash"]


def test_query_layers_present_and_layer_d_is_indirect_only():
    q = json.loads((ROOT / "queries" / "pubmed_queries.json").read_text(encoding="utf-8"))
    layers = {L["layer"]: L for L in q["layers"]}
    assert set("ABCDE") <= set(layers)
    assert "INDIRECT" in layers["D"]["tier_policy"].upper()
    assert "navigation" in layers["E"]["tier_policy"].lower()


def test_manifest_protocol_and_queries_hash_match(manifest):
    rec = (ROOT / "protocol" / "SSc_CGAS_STING_EVIDENCE_PROTOCOL_V1.sha256").read_text(
        encoding="utf-8").split()[0]
    qh = json.loads((ROOT / "queries" / "queries_hash.json").read_text(encoding="utf-8"))
    assert manifest["protocol_hash"] == rec
    assert manifest["queries_hash"] == qh["queries_hash"]


# ============================ 候选与核验分离 ============================
def test_candidates_are_not_evidence(cards):
    cand = _jsonl("discovery/candidates.jsonl")
    assert len(cand) > len(cards), "candidates must not be promoted 1:1 into evidence"
    ids = {c["evidence_id"] for c in cards}
    assert len(ids) == len(cards)                      # evidence ids unique


def test_only_verified_citations_became_cards(cards):
    ver = {v["candidate_id"]: v for v in _jsonl("verification/citation_verification.jsonl")}
    bad = [v for v in ver.values() if v["status"] in ("not_found", "mismatch", "suspicious", "retracted")]
    banned_pmids = {v.get("pmid") for v in bad if v.get("pmid")}
    for c in cards:
        assert c["normalized_pmid"] not in banned_pmids, c["evidence_id"]


def test_network_faults_never_recorded_as_not_found():
    for v in _jsonl("verification/citation_verification.jsonl"):
        if v["status"] == "not_found":
            assert "unavailable" not in (v.get("reason") or "").lower()
            assert v.get("sources_checked"), "not_found requires a source that actually answered"
    for f in _jsonl("verification/source_failures.jsonl"):
        assert f.get("error"), "source failures must record the error"


def test_every_card_has_a_t1_verification_source(cards):
    for c in cards:
        vb = c["provenance"]["verified_by"]
        assert vb, c["evidence_id"]
        assert set(vb) & {"pubmed", "crossref"}, c["evidence_id"]


# ============================ 身份与真实性 ============================
def test_no_test_or_placeholder_identifiers(cards):
    for c in cards:
        pmid = c.get("normalized_pmid")
        if pmid:
            assert str(pmid) not in BANNED_TEST_IDS, f"banned test PMID in {c['evidence_id']}"
            assert re.fullmatch(r"\d{7,9}", str(pmid)), f"implausible PMID {pmid}"


def test_pmid_1002_absent_everywhere():
    for rel in ("evidence/evidence_cards.jsonl", "normalized/eligible_studies.jsonl"):
        blob = json.dumps(_jsonl(rel), ensure_ascii=False)
        assert '"1002"' not in blob and "PMID:1002" not in blob


def test_identifiers_are_wellformed(cards):
    for c in cards:
        doi = c.get("normalized_doi")
        assert doi or c.get("normalized_pmid"), c["evidence_id"]
        if doi:
            assert doi.startswith("10."), f"malformed DOI {doi}"
            assert " " not in doi


# ============================ 分层与不外推 ============================
def test_direct_ssc_requires_disease_in_title(cards):
    """SSc 直接证据必须标题即为 SSc 研究；仅摘要顺带提及者进入人工复核。"""
    dis = re.compile(r"systemic sclerosis|systemic scleroderma|\bSSc\b|scleroderma", re.I)
    for c in cards:
        if c["disease_scope"] == "systemic_sclerosis_direct":
            assert dis.search(c["title"]), c["evidence_id"]


def test_non_ssc_never_labelled_direct(cards):
    for c in cards:
        if c["disease_scope"] == "non_ssc_mechanistic_transfer":
            assert any("ssc_specific_claims" in s for s in c["does_not_support"]), c["evidence_id"]
            assert c["relevance_tier"] not in ("S1", "S2", "S3")


def test_reviews_are_navigation_only(cards):
    for c in cards:
        if c["relevance_tier"] == "S7" or c["disease_scope"] == "review_navigation":
            assert c["causal_tier"] == "not_applicable", c["evidence_id"]
            assert any("primary_experimental_result" in s for s in c["does_not_support"])


def test_preprints_are_marked_and_not_peer_reviewed(cards):
    for c in cards:
        if c["publication_status"] == "preprint":
            assert c["relevance_tier"] == "S8"
            assert any("peer_reviewed_status" in s for s in c["does_not_support"])


def test_animal_or_invitro_not_presented_as_human_direct(cards):
    for c in cards:
        if c["species"] in ("animal", "cell_line_or_in_vitro"):
            assert c["relevance_tier"] != "S1", c["evidence_id"]


def test_causal_tier_values_and_rules_recorded(cards):
    allowed = {"C0", "C1", "C2", "C3", "C4", "C5", "unknown", "not_applicable"}
    for c in cards:
        assert c["causal_tier"] in allowed, c["evidence_id"]
        assert c["causal_rule"].startswith("R-"), c["evidence_id"]
        assert c["relevance_rule"].startswith("R-"), c["evidence_id"]


def test_unknown_is_preserved_not_guessed(cards):
    for c in cards:
        ss = c["sample_size"]
        assert ss == "unknown" or re.fullmatch(r"\d{1,4}", str(ss)), c["evidence_id"]


def test_metadata_only_records_are_not_cards(cards):
    for c in cards:
        assert c["content_level"] == "abstract", c["evidence_id"]
    reasons = {e.get("reason") for e in _jsonl("normalized/exclusions.jsonl")}
    assert "metadata_only_no_abstract" in reasons


# ============================ 去重 ============================
def test_duplicates_recorded_and_preprint_mapped():
    dups = _jsonl("normalized/duplicates.jsonl")
    assert dups, "duplicate log must not be empty for a multi-source pack"
    links = [d for d in dups if d.get("rule") == "R-DEDUP-preprint-published"]
    for l in links:
        assert l["preprint_evidence_id"] and l["published_evidence_id"]
        assert "NOT counted as separate evidence" in l["action"]


def test_no_duplicate_pmids_or_dois_among_cards(cards):
    pm = [c["normalized_pmid"] for c in cards if c.get("normalized_pmid")]
    do = [c["normalized_doi"] for c in cards if c.get("normalized_doi")]
    assert len(pm) == len(set(pm))
    assert len(do) == len(set(do))


def test_superseded_preprints_are_not_counted(cards):
    for c in cards:
        assert not c.get("superseded_by"), "a superseded preprint must not remain a counted card"


# ============================ 阴性 / 矛盾 / 空白 ============================
def test_contradictory_or_negative_signals_retained(cards, manifest):
    assert manifest["cards_with_contradiction_excerpt"] >= 1
    assert any(c.get("contradiction_excerpt") for c in cards)


def test_evidence_gap_recorded_not_claimed_as_absence(manifest):
    gap = manifest["evidence_gap"]
    assert "NOT proof" in gap["note"]
    assert isinstance(gap["gap_direct_human_causal_evidence"], bool)


def test_exclusions_are_logged_with_reasons():
    ex = _jsonl("normalized/exclusions.jsonl")
    assert ex and all(e.get("reason") for e in ex)


def test_manual_review_items_are_not_cards(cards):
    manual = _jsonl("normalized/manual_review.jsonl")
    ids = {c.get("normalized_pmid") for c in cards}
    for m in manual:
        assert m.get("why_not_automatic"), m
        assert m.get("excluded_from_cards_because")
        if m.get("pmid"):
            assert m["pmid"] not in ids, f"manual-review item {m['pmid']} leaked into cards"


# ============================ hash / manifest 对账 ============================
def test_evidence_card_hash_is_stable_and_excludes_retrieved_at(cards):
    for c in cards:
        payload = {k: v for k, v in c.items()
                   if k not in ("content_hash", "retrieved_at", "previous_version",
                                "superseded_by", "counts_as_independent_evidence")}
        recomputed = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        assert recomputed == c["content_hash"], c["evidence_id"]
        assert c["hash_algorithm"] == "sha256"


def test_manifest_counts_match_actual_files(manifest, cards):
    assert manifest["evidence_card_count"] == len(cards)
    assert manifest["excluded_count"] == len(_jsonl("normalized/exclusions.jsonl"))
    assert manifest["manual_review_count"] == len(_jsonl("normalized/manual_review.jsonl"))
    assert manifest["duplicate_count"] == len(_jsonl("normalized/duplicates.jsonl"))
    assert manifest["direct_ssc_evidence_count"] == sum(
        1 for c in cards if c["disease_scope"] == "systemic_sclerosis_direct")
    assert sum(manifest["counts_by_relevance_tier"].values()) == len(cards)


def test_artifact_hashes_still_match(manifest):
    for rel, h in manifest["artifact_hashes"].items():
        p = ROOT / rel
        if p.exists():
            assert _sha_file(p) == h, f"{rel} changed after freezing"


def test_pack_hash_is_deterministic_and_excludes_created_at(manifest):
    payload = {k: v for k, v in manifest.items() if k not in ("pack_hash", "created_at")}
    recomputed = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert recomputed == manifest["pack_hash"]


# ============================ 零付费 / 敏感信息 ============================
def test_zero_paid_llm_calls_recorded(manifest):
    assert manifest["paid_llm_calls"] == 0


def test_no_llm_generated_fields_and_only_free_sources(manifest, cards):
    assert set(manifest["sources"]) <= {"pubmed", "europe_pmc", "crossref", "doi.org",
                                        "europe_pmc_preprint_channel", "pubmed_central"}
    for c in cards:
        assert set(c["provenance"]["verified_by"]) <= {"pubmed", "crossref"}


def test_no_secrets_paths_or_patient_data_in_pack():
    # NB: `sk-` must not be preceded by a letter, else ordinary words like "risk-associated" match.
    bad = re.compile(r"(?<![A-Za-z])sk-[A-Za-z0-9]{16,}|Bearer\s+[A-Za-z0-9]{8,}|Authorization:|"
                     r"Cookie:|[A-Za-z]:\\Users\\|/home/[a-z]+/|api[_-]?key\s*[:=]", re.I)
    for sub in ("protocol", "queries", "discovery", "verification", "normalized", "evidence", "manifests"):
        d = ROOT / sub
        if not d.exists():
            continue
        for p in d.iterdir():
            if p.is_file():
                txt = p.read_text(encoding="utf-8", errors="replace")
                m = bad.search(txt)
                assert not m, f"sensitive-looking content in {sub}/{p.name}: {m.group()[:40]}"


def test_excerpts_are_short_and_verbatim_sourced(cards):
    for c in cards:
        ex = c.get("supporting_excerpt")
        if ex:
            assert len(ex) <= 260, c["evidence_id"]        # minimal excerpt, not bulk copyright text
            assert c["excerpt_source"] in ("abstract", "full_text")
