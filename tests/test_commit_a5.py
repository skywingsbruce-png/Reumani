"""Commit A.5 测试：content_and_artifact 端到端 / 三工具结构化 / manifest 安全 / 唯一来源。
全部 fake/monkeypatch，不调付费 API、不依赖完整 data_lake。"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ssc_skill_agent as SK
import shadow as SH
import evidence_build as EB
import manifest_safety as MS
from schemas import AbstractEvidenceCard, AnalysisEvidenceCard

FAKE_PAPERS = [
    {"pmid": "111", "pmcid": "PMC1", "doi": "10.1/x", "title": "Fibroblasts in SSc",
     "authors": "A", "journal": "J", "year": "2022", "pub_type": "",
     "abstract": "Fibroblasts drive dermal fibrosis in systemic sclerosis.",
     "link": "https://pubmed.ncbi.nlm.nih.gov/111/"},
    {"pmid": "222", "pmcid": None, "doi": None, "title": "No abstract paper",
     "authors": "B", "journal": "bioRxiv", "year": "2023", "pub_type": "preprint",
     "abstract": "", "link": "https://doi.org/10.2/y"},
]


# ---- content_and_artifact 端到端（真实 LangChain ToolMessage）----
@pytest.mark.unit
def test_real_langchain_content_and_artifact(monkeypatch):
    monkeypatch.setattr(SK, "search_with_abstracts", lambda q, n=8: FAKE_PAPERS)
    tm = SK.search_evidence.invoke({"type": "tool_call", "name": "search_evidence",
                                    "args": {"query": "ssc", "n": 2}, "id": "c1"})
    assert isinstance(tm.content, str)                       # content 供 LLM
    assert isinstance(tm.artifact, dict) and tm.artifact["schema_version"] == "toolresult-v1"
    assert tm.content != tm.artifact                         # content ≠ artifact
    assert type(tm).__name__ == "ToolMessage"                # 真实 LangChain 消息类型
    events = SH.extract_tool_events([tm])
    assert events[0].get("structured")                       # shadow 优先用结构化 artifact
    cards = SH.build_evidence_cards(events)
    assert any(c.pmid == "111" for c in cards)               # artifact 贯通到 EvidenceCard
    assert all(isinstance(c, AbstractEvidenceCard) for c in cards)   # 由 evidence_build 构建
    # 一路走到 sanitize_manifest：evidence ID 保留、schema 版本存在
    manifest = SH.run_shadow("q", messages=[tm], allowed_tools=["search_evidence"],
                             old_verify={"passed": True},
                             claim_extractor=lambda ft, ids: [{"text": "SSc有成纤维", "claim_type": "existence",
                                                               "supporting_ids": ["111"]}])
    assert manifest["manifest_schema_version"] == "runmanifest-v1"
    assert any(c.get("pmid") == "111" for c in manifest["evidence_cards"])   # sanitized manifest 保留 evidence


@pytest.mark.unit
def test_multi_paper_index_not_misaligned(monkeypatch):
    monkeypatch.setattr(SK, "search_with_abstracts", lambda q, n=8: FAKE_PAPERS)
    tm = SK.search_evidence.invoke({"type": "tool_call", "name": "search_evidence",
                                    "args": {"query": "ssc"}, "id": "c1"})
    cards = SH.build_evidence_cards(SH.extract_tool_events([tm]))
    by = {c.pmid: c for c in cards}
    assert by["111"].title == "Fibroblasts in SSc"           # 不串位


@pytest.mark.unit
def test_excerpt_from_abstract_verbatim(monkeypatch):
    monkeypatch.setattr(SK, "search_with_abstracts", lambda q, n=8: FAKE_PAPERS)
    tm = SK.search_evidence.invoke({"type": "tool_call", "name": "search_evidence", "args": {"query": "x"}, "id": "1"})
    p = tm.artifact["data"]["papers"][0]
    assert p["supporting_excerpt"] in p["abstract"]          # 逐字属于原摘要


@pytest.mark.unit
def test_metadata_only_downgrade_when_no_abstract(monkeypatch):
    monkeypatch.setattr(SK, "search_with_abstracts", lambda q, n=8: [FAKE_PAPERS[1]])
    tm = SK.search_evidence.invoke({"type": "tool_call", "name": "search_evidence", "args": {"query": "x"}, "id": "1"})
    p = tm.artifact["data"]["papers"][0]
    assert p["content_level"] == "metadata_only" and p["supporting_excerpt"] == ""


@pytest.mark.unit
def test_abstract_card_not_full_text(monkeypatch):
    card = EB.abstract_card_from_paper(FAKE_PAPERS[0], tool_name="search_evidence")
    assert card.tier == "abstract" and card.usable_for_key_conclusion()[0] is False


# ---- schema_version / legacy 边界 ----
def _toolmsg(content, artifact):
    return SimpleNamespace(type="tool", tool_call_id="1", name="search_evidence",
                           content=content, artifact=artifact)


@pytest.mark.unit
def test_wrong_schema_version_downgraded():
    ev = SH.extract_tool_events([_toolmsg("txt", {"schema_version": "BAD", "ok": True})])[0]
    assert "structured" not in ev                            # 错版本→降级 legacy
    assert any("schema_version" in w for w in ev["warnings"])


@pytest.mark.unit
def test_plain_string_stays_legacy():
    ev = SH.extract_tool_events([_toolmsg("普通字符串结果", None)])[0]
    assert ev["provenance"]["provenance_quality"] == "legacy_unstructured"


@pytest.mark.unit
def test_fake_json_string_not_upgraded():
    # 伪装成 JSON 的普通字符串（无官方 artifact）→ 不能自动升级
    ev = SH.extract_tool_events([_toolmsg('{"ok":true,"pmid":"1","doi":"10.1/z"}', None)])[0]
    assert "structured" not in ev
    cards = SH.build_evidence_cards([ev])
    assert cards == []                                       # 文本里没有真实 PubMed 链接/PMID标注→无卡


# ---- query_data_lake 五状态 ----
@pytest.mark.unit
def test_data_lake_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(SK, "BASE", tmp_path)                # 无 data_lake 目录
    tm = SK.query_data_lake.invoke({"type": "tool_call", "name": "query_data_lake",
                                    "args": {"kind": "summary"}, "id": "1"})
    assert tm.artifact["ok"] is False and tm.artifact["error_type"] == "data_unavailable"


@pytest.mark.unit
def test_data_lake_zero_hits(monkeypatch):
    # A.6 语义：corpus 的非 ID 查询走排序检索 → 无候选是 zero_candidates（不是 zero_hits）
    monkeypatch.setattr(SK, "_query_data_lake_str", lambda k, q: "未检索到相关文献。")
    tm = SK.query_data_lake.invoke({"type": "tool_call", "name": "query_data_lake",
                                    "args": {"kind": "corpus", "query": "zzz"}, "id": "1"})
    d = tm.artifact["data"]
    assert tm.artifact["ok"] is True and d["retrieval_status"] == "zero_candidates"
    assert d["candidate_count"] == 0                          # 无候选≠该领域无研究


@pytest.mark.unit
def test_data_lake_execution_error(monkeypatch):
    def boom(k, q):
        raise RuntimeError("db error")
    monkeypatch.setattr(SK, "_query_data_lake_str", boom)
    tm = SK.query_data_lake.invoke({"type": "tool_call", "name": "query_data_lake",
                                    "args": {"kind": "corpus", "query": "x"}, "id": "1"})
    assert tm.artifact["ok"] is False and tm.artifact["error_type"] == "query_error"


# ---- triage_hypothesis 结构化 ----
_FAKE_REP = {"hypothesis": "A ~ B", "datasets": ["GSE1", "GSE2"],
             "signature_overlap": {"shared": [], "n_shared": 0, "jaccard": 0.0},
             "verdict": "no_detectable_support", "caveats": [],
             "confounders_not_assessed": ["批次"], "n_usable": 2, "n_significant_fdr": 0,
             "leave_one_out_robust": True,
             "per_dataset": [{"dataset": "GSE1", "n": 50, "direction": "+", "fdr": 0.4, "genes_hit": "A=5/10,B=6/10",
                              "pearson_r": 0.1, "p": 0.4, "spearman_rho": 0.1, "ci": [-0.1, 0.3], "null_p": 0.4},
                             {"dataset": "GSE2", "n": 40, "direction": "-", "fdr": 0.5, "genes_hit": "A=4/10,B=5/10",
                              "pearson_r": -0.1, "p": 0.5, "spearman_rho": -0.1, "ci": [-0.3, 0.1], "null_p": 0.5}]}


@pytest.mark.unit
def test_triage_structured_overlap_and_multiplicity(monkeypatch):
    monkeypatch.setattr("hypothesis_triage.triage", lambda a, b, d: _FAKE_REP)
    monkeypatch.setattr("hypothesis_triage.resolve_signature", lambda x: ["G1", "G2", "G3"])
    tm = SK.triage_hypothesis.invoke({"type": "tool_call", "name": "triage_hypothesis",
                                      "args": {"signature_a": "CIN", "signature_b": "IFN_ISG",
                                               "geo_datasets": "GSE1,GSE2"}, "id": "1"})
    d = tm.artifact["data"]
    assert d["signature_overlap"]["n_shared"] == 0
    assert d["multiplicity_status"] == "adjusted"            # 有 FDR
    assert d["verdict"] in ("supportive_association", "no_detectable_support", "inconsistent", "technically_unresolved")
    assert d["signature_gene_counts"]["A"] == 3
    assert tm.artifact["provenance"]["content_level"] == "computational_analysis"


@pytest.mark.unit
def test_triage_multiplicity_not_adjusted(monkeypatch):
    rep = dict(_FAKE_REP)
    rep["per_dataset"] = [{"dataset": "GSE1", "n": 50, "direction": "+", "genes_hit": "A=5/10,B=6/10",
                           "pearson_r": 0.2, "p": 0.1},
                          {"dataset": "GSE2", "n": 40, "direction": "+", "genes_hit": "A=5/10,B=6/10",
                           "pearson_r": 0.2, "p": 0.1}]  # 无 fdr → not_adjusted
    monkeypatch.setattr("hypothesis_triage.triage", lambda a, b, d: rep)
    monkeypatch.setattr("hypothesis_triage.resolve_signature", lambda x: ["G1", "G2"])
    tm = SK.triage_hypothesis.invoke({"type": "tool_call", "name": "triage_hypothesis",
                                      "args": {"signature_a": "CIN", "signature_b": "IFN_ISG",
                                               "geo_datasets": "GSE1,GSE2"}, "id": "1"})
    assert tm.artifact["data"]["multiplicity_status"] == "not_adjusted"


@pytest.mark.unit
def test_triage_analysis_card_via_shadow():
    ev = {"tool_name": "triage_hypothesis", "arguments": {}, "ok": True, "data": "txt",
          "structured": {"schema_version": "toolresult-v1", "ok": True,
                         "provenance": {"content_level": "computational_analysis", "code_commit": "abc"},
                         "data": {"hypothesis": "A~B", "dataset_ids": ["GSE1"],
                                  "method": "signature correlation", "statistic": "0/2",
                                  "direction": "mixed"}}}
    cards = SH.build_evidence_cards([ev])
    assert len(cards) == 1 and isinstance(cards[0], AnalysisEvidenceCard)


@pytest.mark.unit
def test_tool_failure_no_positive_evidence():
    ev = {"tool_name": "search_evidence", "arguments": {}, "ok": False, "data": None,
          "structured": None}
    assert SH.build_evidence_cards([ev]) == []


# ---- Manifest 安全 ----
@pytest.mark.unit
def test_manifest_redacts_api_key():
    m = MS.sanitize_manifest({"run_id": "r", "note": "key sk-ABCD1234EFGH5678"})
    assert "REDACTED" in m["note"] and "sk-ABCD1234" not in m["note"]


@pytest.mark.unit
def test_manifest_drops_authorization_cookie_env():
    m = MS.sanitize_manifest({"run_id": "r", "authorization": "Bearer x", "cookie": "s=1",
                              "env": {"DEEPSEEK_API_KEY": "sk-xxxxxxxx"}, "random_field": "drop me"})
    assert "authorization" not in m and "cookie" not in m and "env" not in m   # 白名单丢弃
    assert "random_field" not in m


@pytest.mark.unit
def test_manifest_large_field_bounded():
    m = MS.sanitize_manifest({"run_id": "r", "question": "x" * 300000})
    assert len(str(m["question"])) < 6000                    # 大字段被限长，不内联超大内容


@pytest.mark.unit
def test_manifest_keeps_audit_fields_and_version():
    m = MS.sanitize_manifest({"run_id": "r1", "git_commit": "abc", "question": "q",
                              "claims": [{"claim_id": "c"}], "comparison": {"divergence": True}})
    for k in ("run_id", "git_commit", "question", "claims", "comparison", "manifest_schema_version", "phi_warning"):
        assert k in m


@pytest.mark.unit
def test_artifact_ref_path_and_hash(tmp_path):
    f = tmp_path / "big.png"
    f.write_bytes(b"x" * 100)
    ref = MS.artifact_ref(str(f))
    # A.6：hash 字段由 sha1 → hash_value + hash_algorithm(sha256)
    assert ref["inline"] is False and ref["hash_value"] and ref["path"]
    assert ref["hash_algorithm"] == "sha256"


# ---- 并发 / 唯一来源 ----
@pytest.mark.unit
def test_unique_run_ids_no_overwrite():
    import ssc_a1
    ids_set = {ssc_a1._gen_run_id() for _ in range(50)} | {SH._run_id() for _ in range(50)}
    assert len(ids_set) == 100                               # 全唯一，不会同秒覆盖


@pytest.mark.unit
def test_evidence_build_is_sole_constructor():
    # shadow 构建的卡都来自 evidence_build（类型即证据）
    ev = {"tool_name": "search_evidence", "ok": True, "data": "见 https://pubmed.ncbi.nlm.nih.gov/999/",
          "structured": None, "arguments": {}}
    cards = SH.build_evidence_cards([ev])
    assert cards and all(isinstance(c, AbstractEvidenceCard) for c in cards)


if __name__ == "__main__":
    print("用 pytest 运行：pytest tests/test_commit_a5.py -q")
