"""湿实验知识层 + 实验副驾测试（确定性，不调 API）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lab_knowledge as LK
from experiment_copilot import LabContext, suggest_next, _controls_tips


def test_autoantibody_lookup():
    assert "SRC" in LK.lookup_autoantibody("RNAPolIII") or "肾危象" in LK.lookup_autoantibody("RNAPolIII")
    r = LK.lookup_autoantibody("RA")
    assert "ACPA" in r and "RF" in r          # RA 抗体都在
    assert "anti-dsDNA" in LK.lookup_autoantibody("SLE")


def test_flow_lookup():
    r = LK.lookup_flow("Treg")
    assert "FoxP3" in r and "CD25" in r
    assert "CD34" in LK.lookup_flow("纤维细胞")   # 循环纤维细胞门控


def test_sample_pathway():
    r = LK.sample_pathway("全血")
    assert "PBMC" in r and ("EDTA" in r or "抗凝" in r)


def test_copilot_assembles_relevant_blocks():
    ctx = LabContext(disease="SSc", sample="全血", assay="流式",
                     panel=["CD4", "CD25", "FoxP3", "循环纤维细胞"],
                     hypothesis="外周血纤维细胞升高与mRSS相关")
    out = suggest_next(ctx, with_literature=False)
    assert "样本路径" in out                    # ① 样本路径
    assert "Treg" in out                        # ③ CD25/FoxP3 命中 Treg
    assert "循环纤维细胞" in out                  # panel 命中纤维细胞
    assert "FMO" in out                         # ④ 流式对照提醒


def test_flow_controls_include_intracellular_note():
    tips = _controls_tips(LabContext(disease="SSc", assay="流式"))
    joined = " ".join(tips)
    assert "FMO" in joined and ("BFA" in joined or "转运抑制剂" in joined)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                print(f"FAIL {name}: {e}")
