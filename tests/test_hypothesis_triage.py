"""筛杀器修正测试：四选一结论(非'证明/证伪/杀死') + 严谨性检查。纯函数，不需 GEO。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hypothesis_triage as HT


def _row(p, r=0.5, n=60, dir_="+", ci=None, hit=0.8, null_p=0.001):
    return {"dataset": "d", "n": n, "p": p, "pearson_r": r, "direction": dir_,
            "ci": ci if ci is not None else [0.2, 0.7], "hit_frac_a": hit, "hit_frac_b": hit,
            "null_p": null_p}


@pytest.mark.unit
def test_no_kill_language():
    for v in HT.VERDICTS:
        note = HT.WET_LAB_NOTE[v]
        assert "杀" not in v and "证明" not in note and "证伪" not in note


@pytest.mark.unit
def test_signature_overlap_flags_unresolved():
    ov = HT.signature_overlap(["A", "B", "C", "D"], ["A", "B", "C", "E"])
    assert ov["n_shared"] == 3
    out = HT.assess([_row(0.001)], ov)
    assert out["verdict"] == "technically_unresolved"      # 基因重叠 → 无法判定


@pytest.mark.unit
def test_fisher_ci_and_bh_fdr():
    ci = HT.fisher_ci(0.5, 60)
    assert ci and ci[0] < 0.5 < ci[1]
    fdr = HT.bh_fdr([0.001, 0.5, 0.5, 0.5])
    assert fdr[0] < 0.05 and all(0 <= q <= 1 for q in fdr)


@pytest.mark.unit
def test_supportive_association():
    ov = HT.signature_overlap(["A", "B"], ["C", "D"])           # 无重叠
    rows = [_row(0.0005, ci=[0.2, 0.7]) for _ in range(3)]
    out = HT.assess(rows, ov)
    assert out["verdict"] == "supportive_association"


@pytest.mark.unit
def test_no_detectable_support():
    ov = HT.signature_overlap(["A", "B"], ["C", "D"])
    rows = [_row(0.6, r=0.05, ci=[-0.2, 0.3], null_p=0.6) for _ in range(3)]  # 不显著
    out = HT.assess(rows, ov)
    assert out["verdict"] == "no_detectable_support"


@pytest.mark.unit
def test_inconsistent_directions():
    ov = HT.signature_overlap(["A", "B"], ["C", "D"])
    rows = [_row(0.001, r=0.5, dir_="+", ci=[0.2, 0.7]),
            _row(0.001, r=-0.5, dir_="-", ci=[-0.7, -0.2])]
    out = HT.assess(rows, ov)
    assert out["verdict"] == "inconsistent"


@pytest.mark.unit
def test_small_n_unresolved():
    ov = HT.signature_overlap(["A", "B"], ["C", "D"])
    out = HT.assess([_row(0.001, n=6)], ov)                    # 样本太小
    assert out["verdict"] == "technically_unresolved"


@pytest.mark.unit
def test_wet_lab_note_never_says_abandon_on_pvalue_alone():
    note = HT.WET_LAB_NOTE["no_detectable_support"]
    assert "不能仅凭" in note and "放弃" in note                # 明确禁止仅凭 p 值放弃湿实验


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                print(f"FAIL {name}: {e}")
