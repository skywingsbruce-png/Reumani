"""
CIN（染色体不稳定）signature 打分工具。
思路（标准做法）：对表达矩阵按基因做 z-score，再对 signature 基因取平均，
得到每个样本的 CIN score；可进一步与临床/纤维化表型做相关或分组比较。

表达矩阵格式：CSV，第一列是基因 symbol，其余列是样本（建议已 log 归一化）。
"""

from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).resolve().parent
DEFAULT_GENESET = HERE / "cin70_genes.txt"


def load_geneset(path=None):
    p = Path(path) if path else DEFAULT_GENESET
    genes = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            genes.append(line)
    return genes


def compute_cin_score(expr_csv, out_csv="cin_score.csv", geneset_path=None):
    """返回每个样本的 CIN score。expr_csv 第一列基因名，其余列为样本。"""
    df = pd.read_csv(expr_csv, index_col=0)
    genes = load_geneset(geneset_path)
    present = [g for g in genes if g in df.index]
    missing = [g for g in genes if g not in df.index]
    if len(present) < 5:
        raise ValueError(
            f"表达矩阵里只匹配到 {len(present)} 个 signature 基因，太少。"
            f"请确认第一列是基因 symbol。匹配到：{present}"
        )
    sub = df.loc[present]
    # 每个基因跨样本 z-score
    z = sub.sub(sub.mean(axis=1), axis=0).div(sub.std(axis=1).replace(0, np.nan), axis=0)
    score = z.mean(axis=0, skipna=True)
    out = pd.DataFrame({"sample": score.index, "CIN_score": score.values})
    out.to_csv(out_csv, index=False)
    return (
        f"CIN score 已保存：{out_csv}\n"
        f"使用 signature 基因 {len(present)}/{len(genes)}（缺失 {len(missing)} 个：{missing[:8]}{'...' if len(missing)>8 else ''}）\n"
        f"样本数 {len(score)}，CIN score 范围 [{score.min():.2f}, {score.max():.2f}]"
    )


def correlate_with_phenotype(cin_csv, pheno_csv, pheno_col, sample_col="sample"):
    """把 CIN score 和某个连续表型（如 mRSS、FVC）做相关。两个 CSV 用 sample 列对齐。"""
    cin = pd.read_csv(cin_csv)
    ph = pd.read_csv(pheno_csv)
    m = cin.merge(ph, left_on="sample", right_on=sample_col, how="inner")
    x = m["CIN_score"].astype(float)
    y = pd.to_numeric(m[pheno_col], errors="coerce")
    ok = x.notna() & y.notna()
    r, p = stats.pearsonr(x[ok], y[ok])
    rho, p2 = stats.spearmanr(x[ok], y[ok])
    return (
        f"CIN score vs {pheno_col}（n={ok.sum()}）：\n"
        f"Pearson r={r:.3f} (p={p:.2e})；Spearman rho={rho:.3f} (p={p2:.2e})"
    )


def compare_groups(cin_csv, group_csv, group_col, sample_col="sample"):
    """按分组（如 dcSSc vs lcSSc、SSc vs 健康）比较 CIN score。"""
    cin = pd.read_csv(cin_csv)
    g = pd.read_csv(group_csv)
    m = cin.merge(g, left_on="sample", right_on=sample_col, how="inner")
    groups = [grp["CIN_score"].astype(float).values for _, grp in m.groupby(group_col)]
    names = list(m.groupby(group_col).groups.keys())
    if len(groups) == 2:
        t, p = stats.mannwhitneyu(groups[0], groups[1], alternative="two-sided")
        means = {n: float(np.mean(v)) for n, v in zip(names, groups)}
        return f"分组 {names}：均值 {means}，Mann-Whitney p={p:.2e}"
    else:
        h, p = stats.kruskal(*groups)
        means = {n: float(np.mean(v)) for n, v in zip(names, groups)}
        return f"分组 {names}：均值 {means}，Kruskal-Wallis p={p:.2e}"
