"""
单细胞 RNA-seq 标准流程（scanpy）。SSc 皮肤/肺单细胞常用。
run_pipeline: QC → 过滤 → 归一化 → HVG → PCA → 近邻 → Leiden 聚类 → UMAP → 每群 marker。
输入 .h5ad 或 10x（mtx 目录 / 10x h5）。输出注释后的 h5ad + marker 表 + UMAP 图。
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import scanpy as sc


def run_pipeline(input_path, out_prefix="scrna",
                 min_genes=200, min_cells=3, max_pct_mt=20,
                 n_top_genes=2000, n_pcs=30, resolution=1.0):
    """跑一遍标准单细胞流程，返回结果摘要。"""
    p = Path(input_path)
    if p.suffix == ".h5ad":
        adata = sc.read_h5ad(p)
    elif p.suffix == ".h5":
        adata = sc.read_10x_h5(p)
    elif p.is_dir():
        adata = sc.read_10x_mtx(p, var_names="gene_symbols")
    else:
        adata = sc.read(p)
    adata.var_names_make_unique()

    # QC
    adata.var["mt"] = adata.var_names.str.upper().str.startswith("MT-")
    sc.pp.calculate_qc_metrics(adata, qc_vars=["mt"], inplace=True, percent_top=None)
    n0 = adata.n_obs
    sc.pp.filter_cells(adata, min_genes=min_genes)
    sc.pp.filter_genes(adata, min_cells=min_cells)
    adata = adata[adata.obs["pct_counts_mt"] < max_pct_mt].copy()

    # 归一化
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)
    adata.raw = adata
    sc.pp.highly_variable_genes(adata, n_top_genes=n_top_genes)
    adata = adata[:, adata.var.highly_variable].copy()
    sc.pp.scale(adata, max_value=10)

    # 降维聚类
    sc.tl.pca(adata, n_comps=n_pcs)
    sc.pp.neighbors(adata, n_neighbors=15, n_pcs=n_pcs)
    sc.tl.leiden(adata, resolution=resolution)
    sc.tl.umap(adata)

    # 每群 marker
    sc.tl.rank_genes_groups(adata, "leiden", method="wilcoxon")
    import pandas as pd
    marker_rows = []
    for grp in adata.obs["leiden"].cat.categories:
        names = adata.uns["rank_genes_groups"]["names"][grp][:10]
        marker_rows.append({"cluster": grp, "top_markers": ", ".join(names)})
    markers = pd.DataFrame(marker_rows)
    marker_csv = f"{out_prefix}_markers.csv"
    markers.to_csv(marker_csv, index=False)

    # UMAP 图
    umap_png = f"{out_prefix}_umap.png"
    sc.pl.umap(adata, color="leiden", show=False, save=None)
    import matplotlib.pyplot as plt
    plt.savefig(umap_png, dpi=200, bbox_inches="tight")
    plt.close()

    out_h5ad = f"{out_prefix}_annotated.h5ad"
    adata.write(out_h5ad)

    return (f"单细胞流程完成：\n- 细胞 {n0} → {adata.n_obs}（过滤后）\n"
            f"- 聚类数 {len(adata.obs['leiden'].cat.categories)}（Leiden res={resolution}）\n"
            f"- marker 表：{marker_csv}\n- UMAP：{umap_png}\n- 注释 h5ad：{out_h5ad}\n"
            f"下一步：结合 marker 做细胞类型注释（如 SSc 成纤维细胞亚群 SFRP2+/PRSS23+ 等）。")


def marker_annotation_hint():
    """SSc 皮肤单细胞常见 marker 提示（供人工/AI 注释参考）。"""
    return (
        "SSc 皮肤/肺单细胞常见细胞类型 marker：\n"
        "- 成纤维细胞：COL1A1, PDGFRA, LUM；致病亚群 SFRP2, PRSS23, WIF1, COMP\n"
        "- 肌成纤维细胞：ACTA2, TAGLN\n"
        "- 内皮：PECAM1, VWF, CDH5\n- 角质细胞：KRT14, KRT5\n"
        "- 巨噬/单核：CD68, LYZ, FCGR3A\n- T 细胞：CD3D, CD3E；B：MS4A1, CD79A\n"
        "- 周细胞：RGS5, PDGFRB"
    )
