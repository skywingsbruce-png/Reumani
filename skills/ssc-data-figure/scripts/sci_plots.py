"""
可直接调用的科研作图函数（火山图 / 森林图）。
设计原则和视频里一致：AI 不是随便画，而是调用这些封装好的函数；
数据格式不对就报错，绝不糊弄。

用法（在 run_python 里）：
    import sys
    sys.path.insert(0, r"<本文件所在目录>")
    from sci_plots import volcano_plot, forest_plot
    print(volcano_plot("data.csv", out="volcano.png"))
"""

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")  # 无界面后端，服务器/后台也能出图
import matplotlib.pyplot as plt


def volcano_plot(
    csv_path,
    out="volcano.png",
    lfc_col="log2FC",
    p_col="pvalue",
    label_col="gene",
    lfc_thr=1.0,
    p_thr=0.05,
    top_n=10,
    title="Volcano plot",
):
    """火山图。CSV 需含三列：基因名(label_col)、log2 差异倍数(lfc_col)、p 值(p_col)。"""
    df = pd.read_csv(csv_path)
    for c in (lfc_col, p_col):
        if c not in df.columns:
            raise ValueError(
                f"火山图需要列 '{c}'，但 CSV 只有：{list(df.columns)}。"
                f"请确认数据含 {lfc_col}(log2差异倍数) 和 {p_col}(p值)。"
            )
    df = df.dropna(subset=[lfc_col, p_col]).copy()
    df["_neglogp"] = -np.log10(df[p_col].clip(lower=1e-300))

    up = (df[lfc_col] >= lfc_thr) & (df[p_col] < p_thr)
    down = (df[lfc_col] <= -lfc_thr) & (df[p_col] < p_thr)
    ns = ~(up | down)

    fig, ax = plt.subplots(figsize=(6, 5))
    ax.scatter(df.loc[ns, lfc_col], df.loc[ns, "_neglogp"], s=12, c="#c9c9c9", label="NS")
    ax.scatter(df.loc[up, lfc_col], df.loc[up, "_neglogp"], s=14, c="#d62728", label="Up")
    ax.scatter(df.loc[down, lfc_col], df.loc[down, "_neglogp"], s=14, c="#1f77b4", label="Down")
    ax.axhline(-np.log10(p_thr), ls="--", lw=0.8, c="grey")
    ax.axvline(lfc_thr, ls="--", lw=0.8, c="grey")
    ax.axvline(-lfc_thr, ls="--", lw=0.8, c="grey")

    if label_col in df.columns and top_n > 0:
        sig = df[up | down].copy()
        sig["_rank"] = sig["_neglogp"] * sig[lfc_col].abs()
        for _, r in sig.sort_values("_rank", ascending=False).head(top_n).iterrows():
            ax.annotate(str(r[label_col]), (r[lfc_col], r["_neglogp"]),
                        fontsize=7, xytext=(2, 2), textcoords="offset points")

    ax.set_xlabel("log2 Fold Change")
    ax.set_ylabel("-log10(p)")
    ax.set_title(title)
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return f"火山图已保存：{out}（上调 {int(up.sum())}，下调 {int(down.sum())}，无显著 {int(ns.sum())}）"


def forest_plot(
    csv_path,
    out="forest.png",
    study_col="study",
    eff_col="effect",
    low_col="lower",
    high_col="upper",
    xlabel="Effect size (95% CI)",
    null_line=1.0,
    logx=False,
    title="Forest plot",
):
    """森林图（Meta 分析常用）。CSV 需含：研究名、效应值、95%CI 下限、上限。"""
    df = pd.read_csv(csv_path)
    for c in (study_col, eff_col, low_col, high_col):
        if c not in df.columns:
            raise ValueError(
                f"森林图需要列 '{c}'，但 CSV 只有：{list(df.columns)}。"
                f"请确认含 {study_col}, {eff_col}, {low_col}, {high_col} 四列。"
            )
    df = df.dropna(subset=[eff_col, low_col, high_col]).reset_index(drop=True)
    y = np.arange(len(df))[::-1]

    fig, ax = plt.subplots(figsize=(6.5, 0.5 * len(df) + 1.5))
    err_low = (df[eff_col] - df[low_col]).clip(lower=0)
    err_high = (df[high_col] - df[eff_col]).clip(lower=0)
    ax.errorbar(df[eff_col], y, xerr=[err_low, err_high], fmt="s",
                color="#1f77b4", ecolor="#555", capsize=3, ms=6)
    ax.axvline(null_line, ls="--", lw=0.8, c="red")
    if logx:
        ax.set_xscale("log")
    ax.set_yticks(y)
    ax.set_yticklabels(df[study_col].astype(str), fontsize=8)
    ax.set_xlabel(xlabel)
    ax.set_title(title)
    for i, r in df.iterrows():
        ax.text(ax.get_xlim()[1], y[i],
                f"  {r[eff_col]:.2f} [{r[low_col]:.2f}, {r[high_col]:.2f}]",
                va="center", fontsize=7)
    fig.tight_layout()
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return f"森林图已保存：{out}（纳入 {len(df)} 项研究）"
