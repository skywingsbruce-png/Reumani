"""
组学分析工具（bulk RNA-seq / 表达矩阵）：
1) differential_expression —— 两组差异表达（Welch t 检验 + BH 校正，适合已归一化的表达值）
2) enrichment            —— 通路富集（gseapy / Enrichr，需联网）
3) download_geo          —— 从 GEO 下载数据集（GEOparse，需联网）

注：真正的原始 count 差异分析（DESeq2/edgeR）在 R 里最规范；这里用 Welch t 检验做
    log 归一化数据的快速差异分析，输出可直接喂给 ssc-data-figure 画火山图。
"""

from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats


def _bh_fdr(pvals):
    p = np.asarray(pvals, float)
    n = len(p)
    order = np.argsort(p)
    ranked = p[order] * n / (np.arange(n) + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(n)
    out[order] = np.clip(ranked, 0, 1)
    return out


def differential_expression(expr_csv, group_csv, group_col,
                            group_a=None, group_b=None,
                            sample_col="sample", out_csv="deg.csv"):
    """两组差异表达。expr_csv：第一列基因，其余列样本（log 归一化）。
    group_csv：含 sample 列 + 分组列。输出 gene, log2FC, pvalue, padj（可直接画火山图）。"""
    expr = pd.read_csv(expr_csv, index_col=0)
    grp = pd.read_csv(group_csv)
    mapping = dict(zip(grp[sample_col].astype(str), grp[group_col].astype(str)))

    cols = [c for c in expr.columns if str(c) in mapping]
    if not cols:
        raise ValueError("表达矩阵样本名和分组表 sample 列对不上，请检查列名。")
    labels = pd.Series({c: mapping[str(c)] for c in cols})
    levels = sorted(labels.unique())
    if group_a is None or group_b is None:
        if len(levels) != 2:
            raise ValueError(f"分组有 {len(levels)} 个水平：{levels}，请用 group_a/group_b 指定要比较的两组。")
        group_a, group_b = levels[0], levels[1]

    a_cols = labels[labels == group_a].index.tolist()
    b_cols = labels[labels == group_b].index.tolist()
    A = expr[a_cols].values
    B = expr[b_cols].values

    log2fc = np.nanmean(B, axis=1) - np.nanmean(A, axis=1)  # B vs A（正=B 上调）
    t, p = stats.ttest_ind(B, A, axis=1, equal_var=False, nan_policy="omit")
    p = np.where(np.isfinite(p), p, 1.0)
    padj = _bh_fdr(p)

    out = pd.DataFrame({
        "gene": expr.index, "log2FC": log2fc, "pvalue": p, "padj": padj,
    }).sort_values("pvalue")
    out.to_csv(out_csv, index=False)
    n_sig = int(((out["padj"] < 0.05) & (out["log2FC"].abs() >= 1)).sum())
    return (f"差异表达完成：{out_csv}（{group_b} vs {group_a}；"
            f"padj<0.05 且 |log2FC|>=1 的基因 {n_sig} 个）。可用 ssc-data-figure 画火山图。")


def enrichment(genes, gene_sets="KEGG_2021_Human", organism="Human",
               out_csv="enrichment.csv", top=20):
    """通路富集（Enrichr）。genes 可以是基因列表或一个每行一个基因的 txt 路径。需联网。"""
    import gseapy as gp
    if isinstance(genes, str) and Path(genes).exists():
        genes = [g.strip() for g in Path(genes).read_text(encoding="utf-8").splitlines() if g.strip()]
    genes = [g for g in genes if g]
    if len(genes) < 3:
        raise ValueError("富集分析的基因太少（<3）。")
    enr = gp.enrichr(gene_list=genes, gene_sets=gene_sets, organism=organism, outdir=None)
    res = enr.results.sort_values("Adjusted P-value").head(top)
    res.to_csv(out_csv, index=False)
    lines = [f"- {r['Term']} (padj={r['Adjusted P-value']:.2e})" for _, r in res.head(10).iterrows()]
    return f"富集完成：{out_csv}（{gene_sets}）。Top 通路：\n" + "\n".join(lines)


def download_geo(gse_id, out_dir="geo_data"):
    """从 GEO 下载数据集元数据/表达（GEOparse）。需联网。返回样本数和平台信息。"""
    import GEOparse
    Path(out_dir).mkdir(exist_ok=True)
    gse = GEOparse.get_GEO(geo=gse_id, destdir=out_dir, silent=True)
    n = len(gse.gsms)
    title = gse.metadata.get("title", ["?"])[0]
    plats = list(gse.gpls.keys())
    return (f"已下载 {gse_id}：{title}\n样本数 {n}，平台 {plats}。"
            f"数据文件在 {out_dir}/。样本表型在 gse.phenotype_data。")
