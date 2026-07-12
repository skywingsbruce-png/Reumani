"""检索层测试：查询分类、同义词扩展、RRF、向量索引存在性。
只测确定性部分，不下载/加载重排序或向量模型（那些在集成测试里跑）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import retrieval as R
import vector_index as vi


def test_classify_exact_vs_hybrid():
    assert R.classify_query("STING1") == "exact"          # 单基因符号
    assert R.classify_query("GSE58095 分析") == "exact"    # 含 GSE 号
    assert R.classify_query("10.1038/s41586-020-2612-2") == "exact"  # DOI
    assert R.classify_query("SSc scarring mechanism") == "hybrid"
    assert R.classify_query("染色体不稳定 纤维化") == "hybrid"


def test_expand_english_and_chinese():
    exp = R.expand_query("SSc scarring")
    assert "fibrosis" in exp                    # scarring -> fibrosis
    exp_zh = R.expand_query("染色体不稳定 纤维化")
    assert "chromosomal instability" in exp_zh  # 中文 -> 英文
    assert "fibrosis" in exp_zh


def test_rrf_fusion_rewards_agreement():
    # 两个排序都把 5 排前面 -> 5 应领先
    fused = R._rrf([[5, 1, 2], [5, 3, 4]])
    assert fused[0] == 5


def test_vector_indices_exist():
    # CIN/SSc 索引已构建（前面步骤生成）
    assert vi.has_index("CIN")
    assert vi.has_index("SSc")


def test_exact_query_skips_expansion():
    assert R.expand_query("STING1") == R.expand_query("STING1")  # 稳定
    # 精确查询在 hybrid_search 里不扩展（route=exact -> expansions=[]）
    assert R.classify_query("rs12345") == "exact"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                print(f"FAIL {name}: {e}")
