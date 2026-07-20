"""Commit A.6 测试：三个技术债——(A) SHA-256 统一 (B) multiplicity 语义 (C) corpus 检索状态语义。
全部 fake/monkeypatch，不调付费 API、不依赖完整 data_lake。"""
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import manifest_safety as MS
import ssc_skill_agent as SK
import tool_envelope as TE

_HEX = set("0123456789abcdef")


def _is_sha256(v):
    return isinstance(v, str) and len(v) == 64 and set(v) <= _HEX


# ============ A. SHA-256 ============
@pytest.mark.unit
def test_compute_hash_is_sha256_full_length():
    h = TE.compute_hash({"a": 1})
    assert _is_sha256(h)
    assert h == hashlib.sha256(TE._blob({"a": 1}).encode("utf-8")).hexdigest()


@pytest.mark.unit
def test_compute_hash_is_stable_and_key_order_independent():
    assert TE.compute_hash({"a": 1, "b": 2}) == TE.compute_hash({"b": 2, "a": 1})
    assert TE.compute_hash({"a": 1}) != TE.compute_hash({"a": 2})


@pytest.mark.unit
def test_toolresult_records_hash_algorithm():
    r = TE.make_toolresult("t", True, {"x": 1}, content_level="metadata_only")
    prov = r["provenance"]
    assert prov["hash_algorithm"] == "sha256"
    assert _is_sha256(prov["content_hash"])


@pytest.mark.unit
def test_toolresult_no_data_no_hash():
    """失败/无 data 时不得凭空造 hash，也不得标注算法。"""
    r = TE.make_toolresult("t", False, None, content_level="metadata_only",
                           error_type="e", error_message="m")
    assert r["provenance"]["content_hash"] is None
    assert r["provenance"]["hash_algorithm"] is None


@pytest.mark.unit
def test_detect_hash_algorithm_recognises_legacy_without_relabelling():
    """旧记录可识别：SHA-1 不得被误标为 SHA-256；旧 16 位截断值 → unknown。"""
    assert TE.detect_hash_algorithm("a" * 64) == "sha256"
    assert TE.detect_hash_algorithm(hashlib.sha1(b"x").hexdigest()) == "sha1"   # 旧记录仍可读
    assert TE.detect_hash_algorithm("a" * 16) == "unknown"                      # legacy 截断值
    assert TE.detect_hash_algorithm("zzzz") == "unknown"
    assert TE.detect_hash_algorithm(None) == "unknown"


@pytest.mark.unit
def test_no_new_sha1_generated_anywhere():
    """新代码不得再生成 SHA-1（源码级：核心 hash 模块不出现 sha1 调用）。"""
    for f in ("tool_envelope.py", "manifest_safety.py"):
        src = (Path(__file__).resolve().parent.parent / f).read_text(encoding="utf-8")
        assert "hashlib.sha1(" not in src, f"{f} 仍在生成 SHA-1"


@pytest.mark.unit
def test_manifest_shrink_and_artifact_ref_use_sha256(tmp_path):
    shrunk = MS._shrink({"big": "x" * 900})["big"]
    assert shrunk["hash_algorithm"] == "sha256" and _is_sha256(shrunk["hash_value"])
    assert shrunk["_len"] == 900 and "sha1" not in shrunk

    p = tmp_path / "a.bin"
    p.write_bytes(b"hello")
    ref = MS.artifact_ref(p)
    assert ref["hash_algorithm"] == "sha256" and ref["hash_value"] == hashlib.sha256(b"hello").hexdigest()
    assert ref["size"] == 5 and ref["inline"] is False and "sha1" not in ref


@pytest.mark.unit
def test_artifact_ref_missing_file_has_no_fake_hash(tmp_path):
    ref = MS.artifact_ref(tmp_path / "nope.bin")
    assert ref["hash_value"] is None and ref["hash_algorithm"] is None


# ============ B. multiplicity 语义 ============
_BASE_REP = {"hypothesis": "A ~ B", "datasets": ["GSE1"], "verdict": "no_detectable_support",
             "signature_overlap": {"shared": [], "n_shared": 0, "jaccard": 0.0},
             "caveats": [], "confounders_not_assessed": [], "n_usable": 1, "n_significant_fdr": 0,
             "leave_one_out_robust": True, "per_dataset": []}

_ROW = {"dataset": "GSE1", "n": 50, "direction": "+", "genes_hit": "A=5/10,B=6/10",
        "pearson_r": 0.2, "p": 0.31, "spearman_rho": 0.2, "ci": [-0.1, 0.4], "null_p": 0.31}


def _triage(monkeypatch, rows, datasets="GSE1"):
    rep = dict(_BASE_REP)
    rep["per_dataset"] = rows
    rep["n_usable"] = len(rows)
    monkeypatch.setattr("hypothesis_triage.triage", lambda a, b, d: rep)
    monkeypatch.setattr("hypothesis_triage.resolve_signature", lambda x: ["G1", "G2"])
    tm = SK.triage_hypothesis.invoke({"type": "tool_call", "name": "triage_hypothesis",
                                      "args": {"signature_a": "CIN", "signature_b": "IFN_ISG",
                                               "geo_datasets": datasets}, "id": "1"})
    return tm.artifact["data"]


@pytest.mark.unit
def test_single_dataset_single_comparison_is_not_applicable(monkeypatch):
    """单数据集单一预定义比较 → not_applicable，q=None（BH 对单个 p 是恒等，不得冒称已校正）。"""
    d = _triage(monkeypatch, [dict(_ROW, fdr=0.31)])
    assert d["multiplicity_status"] == "not_applicable"
    assert d["adjusted_q"] is None and d["adjustment_method"] is None
    assert d["test_count"] == 1


@pytest.mark.unit
def test_single_dataset_never_reports_q_equal_p(monkeypatch):
    """禁止 q=p：单检验时不得输出任何等于原始 p 的 q。"""
    d = _triage(monkeypatch, [dict(_ROW, fdr=0.31)])
    assert d["adjusted_q"] is None                       # 而不是 [0.31] == [p]


@pytest.mark.unit
def test_multi_test_with_real_correction_is_adjusted(monkeypatch):
    d = _triage(monkeypatch, [dict(_ROW, dataset="GSE1", fdr=0.62),
                              dict(_ROW, dataset="GSE2", fdr=0.62)], datasets="GSE1,GSE2")
    assert d["multiplicity_status"] == "adjusted"
    assert d["adjustment_method"] == "benjamini-hochberg"
    assert d["adjusted_q"] == [0.62, 0.62]               # 真实 q，且与 p(0.31) 不同
    assert d["test_count"] == 2


@pytest.mark.unit
def test_multi_test_without_correction_is_not_adjusted(monkeypatch):
    rows = [{k: v for k, v in dict(_ROW, dataset=n).items()} for n in ("GSE1", "GSE2")]
    d = _triage(monkeypatch, rows, datasets="GSE1,GSE2")
    assert d["multiplicity_status"] == "not_adjusted"
    assert d["adjusted_q"] is None and d["adjustment_method"] is None


@pytest.mark.unit
def test_adjusted_requires_method_and_q(monkeypatch):
    """adjusted 三要素必须同时存在，否则不得称已校正。"""
    d = _triage(monkeypatch, [dict(_ROW, dataset="GSE1", fdr=0.62),
                              dict(_ROW, dataset="GSE2", fdr=0.62)], datasets="GSE1,GSE2")
    if d["multiplicity_status"] == "adjusted":
        assert d["adjustment_method"] and d["adjusted_q"]


@pytest.mark.unit
def test_test_family_recorded(monkeypatch):
    d = _triage(monkeypatch, [dict(_ROW, fdr=0.31)])
    assert isinstance(d["test_family"], str) and d["test_family"]
    assert "1" in d["test_family"]


@pytest.mark.unit
def test_multiplicity_status_is_one_of_three(monkeypatch):
    for rows, ds in (([dict(_ROW, fdr=0.3)], "GSE1"),
                     ([dict(_ROW, dataset="A", fdr=0.6), dict(_ROW, dataset="B", fdr=0.6)], "A,B"),
                     ([dict(_ROW, dataset="A"), dict(_ROW, dataset="B")], "A,B")):
        d = _triage(monkeypatch, rows, datasets=ds)
        assert d["multiplicity_status"] in ("not_applicable", "adjusted", "not_adjusted")


# ============ C. corpus 检索状态语义 ============
def _lake(monkeypatch, text, kind="corpus", query="fibrosis"):
    monkeypatch.setattr(SK, "_query_data_lake_str", lambda k, q: text)
    tm = SK.query_data_lake.invoke({"type": "tool_call", "name": "query_data_lake",
                                    "args": {"kind": kind, "query": query}, "id": "1"})
    return tm


@pytest.mark.unit
def test_exact_pmid_hit(monkeypatch):
    tm = _lake(monkeypatch, "命中 1 篇：\n- [SSc·2022] X | J | https://pubmed.ncbi.nlm.nih.gov/12345678/",
               query="12345678")
    d = tm.artifact["data"]
    assert d["retrieval_mode"] == "exact_id" and d["retrieval_status"] == "exact_hit"
    assert d["candidate_count"] == 1


@pytest.mark.unit
def test_exact_pmid_miss_is_zero_hits_not_candidates(monkeypatch):
    """精确ID未命中：即使排序检索返回了其它候选，也不得算作命中。"""
    tm = _lake(monkeypatch, "命中 15 篇：\n- [SSc·2022] 无关论文 | J | https://pubmed.ncbi.nlm.nih.gov/999/",
               query="12345678")
    d = tm.artifact["data"]
    assert d["retrieval_mode"] == "exact_id" and d["retrieval_status"] == "zero_hits"
    assert d["candidate_count"] == 0


@pytest.mark.unit
def test_exact_doi_hit(monkeypatch):
    tm = _lake(monkeypatch, "命中 1 篇：\n- X | https://doi.org/10.1080/abc", query="10.1080/abc")
    assert tm.artifact["data"]["retrieval_status"] == "exact_hit"


@pytest.mark.unit
def test_ranked_candidates_are_relevance_unverified(monkeypatch):
    """混合检索返回 top-k → 只能称候选，不得称相关命中，也不得自动成为支持性证据。"""
    tm = _lake(monkeypatch, "【混合检索：BM25】路由=hybrid，命中 15 篇（范围 SSc）：\n- a\n- b")
    d = tm.artifact["data"]
    assert d["retrieval_mode"] == "ranked_relevance"
    assert d["retrieval_status"] == "candidates_returned_relevance_unverified"
    assert d["candidate_count"] == 15
    assert d["relevance_verified"] is False
    assert d["n_records"] is None                       # 候选不是记录命中数
    assert any("未经核验" in w for w in d["warnings"])
    assert "相关性未核验" in tm.content                  # 下游/模型必须看到该限定


@pytest.mark.unit
def test_zero_candidates_is_not_absence_of_research(monkeypatch):
    tm = _lake(monkeypatch, "【混合检索：BM25】命中 0 篇（范围 SSc）：", query="zzqqxx")
    d = tm.artifact["data"]
    assert d["retrieval_status"] == "zero_candidates" and d["candidate_count"] == 0
    assert any("没有研究" in w for w in d["warnings"])
    assert "不等于该领域没有研究" in tm.content


@pytest.mark.unit
def test_candidate_scores_reported_as_unknown(monkeypatch):
    """检索层不暴露分数 → 必须诚实记为 unknown，不得伪造分数或阈值。"""
    tm = _lake(monkeypatch, "命中 3 篇：\n- a\n- b\n- c")
    assert tm.artifact["data"]["candidate_scores"] == "unknown"


@pytest.mark.unit
def test_unavailable_and_execution_error_labelled(monkeypatch, tmp_path):
    monkeypatch.setattr(SK, "BASE", tmp_path)
    tm = SK.query_data_lake.invoke({"type": "tool_call", "name": "query_data_lake",
                                    "args": {"kind": "corpus", "query": "x"}, "id": "1"})
    assert tm.artifact["ok"] is False and tm.artifact["error_type"] == "data_unavailable"
    assert "retrieval_status=unavailable" in tm.artifact["warnings"]

    monkeypatch.undo()

    def boom(k, q):
        raise RuntimeError("db")
    monkeypatch.setattr(SK, "_query_data_lake_str", boom)
    tm2 = SK.query_data_lake.invoke({"type": "tool_call", "name": "query_data_lake",
                                     "args": {"kind": "corpus", "query": "x"}, "id": "2"})
    assert tm2.artifact["ok"] is False and "retrieval_status=execution_error" in tm2.artifact["warnings"]


@pytest.mark.unit
def test_non_corpus_kind_is_deterministic_lookup(monkeypatch):
    tm = _lake(monkeypatch, "IFN_ISG: ISG15, MX1", kind="geneset", query="IFN_ISG")
    d = tm.artifact["data"]
    assert d["retrieval_mode"] == "deterministic_lookup" and d["retrieval_status"] == "exact_hit"

    tm2 = _lake(monkeypatch, "未找到该基因集。", kind="geneset", query="NOPE")
    assert tm2.artifact["data"]["retrieval_status"] == "zero_hits"
