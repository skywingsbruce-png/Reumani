"""检索层测试。
- 分类/扩展/RRF 是 unit（纯逻辑，不依赖 data_lake / 模型 / API）。
- 向量索引存在性是 optional_large_data（需本地构建，默认 pytest 跳过）。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import retrieval as R


@pytest.mark.unit
def test_classify_exact_vs_hybrid():
    assert R.classify_query("STING1") == "exact"          # 单基因符号（不依赖 HGNC）
    assert R.classify_query("TP53") == "exact"
    assert R.classify_query("GSE58095 分析") == "exact"    # 含 GSE 号
    assert R.classify_query("10.1038/s41586-020-2612-2") == "exact"  # DOI
    assert R.classify_query("SSc scarring mechanism") == "hybrid"
    assert R.classify_query("染色体不稳定 纤维化") == "hybrid"


@pytest.mark.unit
def test_expand_english_and_chinese():
    exp = R.expand_query("SSc scarring")
    assert "fibrosis" in exp                    # scarring -> fibrosis
    exp_zh = R.expand_query("染色体不稳定 纤维化")
    assert "chromosomal instability" in exp_zh  # 中文 -> 英文
    assert "fibrosis" in exp_zh


@pytest.mark.unit
def test_rrf_fusion_rewards_agreement():
    fused = R._rrf([[5, 1, 2], [5, 3, 4]])
    assert fused[0] == 5


@pytest.mark.unit
def test_exact_query_skips_expansion():
    assert R.expand_query("STING1") == R.expand_query("STING1")  # 稳定
    assert R.classify_query("rs12345") == "exact"


@pytest.mark.optional_large_data
def test_vector_indices_exist():
    import vector_index as vi
    if not (vi.has_index("CIN") and vi.has_index("SSc")):
        pytest.skip("向量索引缺失（optional-large-data，需本地 python vector_index.py 构建）")
    assert vi.has_index("CIN") and vi.has_index("SSc")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except pytest.skip.Exception as e:
                print(f"SKIP {name}: {e}")
            except AssertionError as e:
                print(f"FAIL {name}: {e}")
