"""Tool Retriever 单元测试（含负面测试：无关资源不应被选中）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ssc_resources import retriever, registry


def test_registry_loaded():
    assert len(registry.all()) >= 20


def test_cin_query_selects_evidence_not_scrna():
    picked = [s.name for s in retriever.retrieve(
        "最近两年是否有证据支持染色体不稳定驱动SSc成纤维细胞活化", top_k=10)]
    # 正面：文献/证据类应被选中
    assert any(n in picked for n in ("search_europe_pmc", "extract_evidence_card", "cin_score"))
    # 负面：单细胞聚类不该出现
    assert "scrna_pipeline" not in picked


def test_scrna_query_selects_scrna():
    picked = [s.name for s in retriever.retrieve(
        "SSc 皮肤单细胞 scRNA 聚类 细胞注释 h5ad", top_k=10)]
    assert "scrna_pipeline" in picked


def test_clinical_query_selects_stats():
    picked = [s.name for s in retriever.retrieve(
        "SSc 队列 生存分析 Cox 预后", top_k=10)]
    assert any(n in picked for n in ("survival_analysis", "meta_analysis"))


if __name__ == "__main__":
    fns = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}")
    print(f"\n{passed}/{len(fns)} 通过")
