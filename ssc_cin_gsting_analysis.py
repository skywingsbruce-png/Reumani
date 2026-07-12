"""
真实数据分析：在 GEO SSc 皮肤转录组(GSE58095, 102 样本)上检验假说
  CIN(染色体不稳定) ↔ cGAS-STING/干扰素 ↔ 纤维化(皮肤评分 mRSS)
方法：signature 打分(基因跨样本 z-score 再取均值) + 相关/分组比较。
诚实原则：只报真实结果，信号强弱如实说，不为好看改动。
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import stats

import GEOparse

BASE = Path(__file__).resolve().parent
GEO_DIR = BASE / "data_lake" / "geo_raw"
OUT = BASE / "agent_workspace"
OUT.mkdir(exist_ok=True)

# ---- signature 基因集 ----
def load_cin70():
    p = BASE / "skills" / "ssc-cin" / "scripts" / "cin70_genes.txt"
    return [g.strip() for g in p.read_text(encoding="utf-8").splitlines()
            if g.strip() and not g.startswith("#")]

CGAS_STING = ["CGAS", "MB21D1", "TMEM173", "STING1", "TBK1", "IKBKE", "IRF3", "IRF7",
              "IFI16", "DDX58", "IFIH1", "MAVS", "TREX1", "DDX41", "NLRX1", "TRIM56", "CGAS"]
IFN_ISG = ["ISG15", "MX1", "MX2", "OAS1", "OAS2", "OAS3", "IFIT1", "IFIT3", "RSAD2",
           "IFI44", "IFI44L", "USP18", "HERC5", "IFI6", "OASL", "STAT1", "IRF7"]
SIGS = {"CIN": load_cin70(), "cGAS_STING": CGAS_STING, "IFN_ISG": IFN_ISG}


def score(expr, genes):
    present = [g for g in set(genes) if g in expr.index]
    if len(present) < 3:
        return None, present
    sub = expr.loc[present]
    z = sub.sub(sub.mean(axis=1), axis=0).div(sub.std(axis=1).replace(0, np.nan), axis=0)
    return z.mean(axis=0, skipna=True), present


def main():
    print("加载 GSE58095 表达数据...", flush=True)
    gse = GEOparse.get_GEO(geo="GSE58095", destdir=str(GEO_DIR), silent=True)
    # 探针 × 样本 表达矩阵
    expr = gse.pivot_samples("VALUE")
    # 探针 -> 基因 symbol
    gpl = list(gse.gpls.values())[0].table
    sym_col = next((c for c in gpl.columns if c.lower() in ("symbol", "ilmn_gene", "gene_symbol")), None)
    id_col = gpl.columns[0]
    probe2sym = dict(zip(gpl[id_col].astype(str), gpl[sym_col].astype(str)))
    expr.index = [probe2sym.get(str(p), "") for p in expr.index]
    expr = expr[expr.index != ""]
    expr = expr.groupby(expr.index).max()   # 同基因多探针取最大
    print(f"表达矩阵：{expr.shape[0]} 基因 × {expr.shape[1]} 样本", flush=True)

    # 打分
    scores = {}
    for name, genes in SIGS.items():
        s, present = score(expr, genes)
        scores[name] = s
        print(f"  {name} signature: 命中 {len(present)}/{len(set(genes))} 基因", flush=True)
    sdf = pd.DataFrame(scores)

    # 表型
    pheno_rows = {}
    for gsm_name, gsm in gse.gsms.items():
        ch = gsm.metadata.get("characteristics_ch1", [])
        d = {}
        for item in ch:
            if ":" in item:
                k, v = item.split(":", 1)
                d[k.strip().lower()] = v.strip()
        pheno_rows[gsm_name] = d
    pheno = pd.DataFrame(pheno_rows).T
    # 皮肤评分（mRSS）
    skin_col = next((c for c in pheno.columns if "total skin score" in c or "skin score" in c), None)
    sdf["mRSS"] = pd.to_numeric(pheno.get(skin_col), errors="coerce") if skin_col else np.nan
    # 是否 SSc（有皮肤评分/抗体 = 患者；否则可能是对照）
    sdf["is_patient"] = sdf["mRSS"].notna()

    sdf.to_csv(OUT / "gse58095_scores.csv")

    # ---- 相关分析 ----
    report = ["# GSE58095 真实数据：CIN ↔ cGAS-STING ↔ 纤维化\n",
              f"样本数 {len(sdf)}，其中有皮肤评分(患者) {int(sdf['is_patient'].sum())}\n"]

    def corr(a, b, label):
        m = sdf[[a, b]].dropna()
        if len(m) < 5:
            return f"- {label}: 样本不足"
        r, p = stats.pearsonr(m[a], m[b])
        rho, p2 = stats.spearmanr(m[a], m[b])
        return f"- **{label}** (n={len(m)}): Pearson r={r:.3f} (p={p:.1e}); Spearman ρ={rho:.3f} (p={p2:.1e})"

    report.append("## 全样本相关")
    report.append(corr("CIN", "cGAS_STING", "CIN ↔ cGAS-STING"))
    report.append(corr("CIN", "IFN_ISG", "CIN ↔ 干扰素(ISG)"))
    report.append(corr("cGAS_STING", "IFN_ISG", "cGAS-STING ↔ 干扰素"))
    report.append(corr("CIN", "mRSS", "CIN ↔ 皮肤评分 mRSS"))
    report.append(corr("cGAS_STING", "mRSS", "cGAS-STING ↔ 皮肤评分 mRSS"))
    report.append(corr("IFN_ISG", "mRSS", "干扰素 ↔ 皮肤评分 mRSS"))

    # 患者 vs 对照
    if sdf["is_patient"].nunique() == 2:
        report.append("\n## 患者 vs 对照（signature 均值差异）")
        for sig in ["CIN", "cGAS_STING", "IFN_ISG"]:
            a = sdf.loc[sdf.is_patient, sig].dropna()
            b = sdf.loc[~sdf.is_patient, sig].dropna()
            if len(a) >= 3 and len(b) >= 3:
                t, p = stats.mannwhitneyu(a, b)
                report.append(f"- {sig}: 患者 {a.mean():.2f} vs 对照 {b.mean():.2f}, Mann-Whitney p={p:.1e}")

    # ---- 图 ----
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.2))
    for ax, (x, y) in zip(axes, [("CIN", "cGAS_STING"), ("CIN", "IFN_ISG"), ("cGAS_STING", "mRSS")]):
        m = sdf[[x, y]].dropna()
        ax.scatter(m[x], m[y], s=18, alpha=.6, c="#1f77b4")
        if len(m) >= 5:
            r, p = stats.pearsonr(m[x], m[y])
            z = np.polyfit(m[x], m[y], 1)
            xs = np.linspace(m[x].min(), m[x].max(), 20)
            ax.plot(xs, np.polyval(z, xs), "r--", lw=1)
            ax.set_title(f"{x} vs {y}\nr={r:.2f}, p={p:.1e}")
        ax.set_xlabel(x); ax.set_ylabel(y)
    fig.tight_layout()
    fig.savefig(OUT / "gse58095_cin_gsting.png", dpi=200, bbox_inches="tight")
    plt.close(fig)

    report.append(f"\n图已保存：agent_workspace/gse58095_cin_gsting.png")
    text = "\n".join(report)
    (OUT / "gse58095_report.md").write_text(text, encoding="utf-8")
    print("\n" + text)


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
