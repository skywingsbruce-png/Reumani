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


def test_protocol_lookup_and_caveat():
    import protocols as P
    r = P.lookup_protocol("CENP-B B细胞")
    assert "候选" in r and "确认" in r          # 关键方法学要点：候选≠确认
    assert "ARTISAN" in r and "IMGT" in r        # 10步工具链在
    assert "9%" in r and "55%" in r              # QC 特异性数字保真


def test_cenpb_full_chain_fidelity():
    """锁死 Abdulla 课题链的关键事实，防后续误改。"""
    import protocols as P
    r = P.lookup_protocol("CENP-B ACA B细胞")
    assert "Sortase A" in r and "TAMRA" in r          # sortase 定点标记(非随机化学标记)
    assert "3F3" in r and "9D11" in r                 # RAMOS 阴性对照(ACPA/ATA)
    assert "9%" in r and "25–31%" in r and "55%" in r  # 特异性演进保真
    assert "292" in r and "246" in r                   # 测序统计保真
    assert "RL2790" in r and "IGHV3-72" in r           # 6D6 完整范例
    assert "ABP1" in r and "CBH1" in r and "CBH2" in r  # 微生物同源蛋白
    assert "序列候选库" in r and "SPR/BLI" in r         # 三道边界(不越线)


def test_sam_top1_chain_fidelity():
    """锁死 Sam 课题(TOP1/ATA→ACPA)的关键结论与边界。"""
    import protocols as P
    s = P.lookup_protocol("TOP1 ATA 真菌")
    assert "核蛋白–DNA复合物" in s              # 真实自身抗原可能是核蛋白-DNA复合物
    assert "不等于】已证明真菌感染" in s          # 真菌交叉反应≠证明真菌致病
    assert "临床缓解【不等于】自身免疫反应已经停止" in s
    assert "精氨酸对照" in s and "通透" in s      # 胞内CCP2染色需通透+精氨酸对照


def test_renee_neoepitope_fidelity():
    """锁死 Renee 课题(BCR neoepitope / RATP-Ig)的关键数字与边界。"""
    import protocols as P
    r = P.lookup_protocol("BCR neoepitope RATP-Ig")
    assert "3312" in r and "1581" in r          # 纵向单细胞规模
    assert "446" in r and "322" in r            # RATP-Ig 构建/成功
    assert "只有一半" in r                       # 分选候选约50%为真ACPA
    assert "统一开关" in r                       # W48 非所有ACPA的统一开关
    assert "不是已可用于患者的ACPA疫苗" in r      # 概念验证边界
    assert "没有清晰相关" in r                   # MPO-IgM 与 BVAS 无清晰相关


def test_researcher_map():
    import protocols as P
    t = P.researchers_text()
    assert "Abdulla" in t and "Sam" in t and "Renee" in t
    assert "CENP-B" in t and "TOP1" in t and "ACPA" in t


def test_copilot_surfaces_matching_protocol():
    ctx = LabContext(disease="SSc", assay="流式单细胞分选",
                     panel=["CENP-B", "B细胞"], hypothesis="分选ACA特异B细胞克隆")
    out = suggest_next(ctx, with_literature=False)
    assert "相关成熟协议" in out and "CENP-B" in out


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
