---
name: ssc-omics
description: >-
  SSc 转录组（bulk RNA-seq / 表达矩阵）分析技能：两组差异表达、通路富集(GO/KEGG/Reactome via Enrichr)、
  从 GEO 下载 SSc 数据集。SSc 皮肤/肺转录组是最高频的研究场景。
  触发场景：用户要做差异基因、DEG、火山图前的差异分析、通路/富集分析、GSEA、GO/KEGG、
  下载 GEO 数据集、"差异表达"、"富集"、"通路分析"、"GEO"。需要用户提供表达矩阵或 GEO 编号。
---

# SSc 组学分析（bulk RNA-seq）

覆盖 SSc 研究最高频的转录组分析：**差异表达 → 通路富集**，以及**从 GEO 拉公开数据**。

## 工作流
1. **拿数据**：用户自己的表达矩阵，或用 `download_geo("GSExxxxx")` 从 GEO 下载 SSc 公开数据集。
2. **确认格式**：用 read_file 看表达矩阵列名（第一列基因，其余样本）和分组表。
3. **差异表达**：`differential_expression(expr, groups, group_col)` → 输出 gene/log2FC/pvalue/padj。
4. **画火山图**：把上一步的 deg.csv 交给 [[ssc-data-figure]] 的 `volcano_plot`。
5. **通路富集**：取显著基因，`enrichment(genes, "KEGG_2021_Human")`（也可用 GO_Biological_Process_2021、Reactome_2022）。
6. **接 CIN**：想看染色体不稳定，转 [[ssc-cin]] 技能算 CIN score。

## 调用模板（run_python）
```python
import sys
sys.path.insert(0, r"F:\SSC\My_AGI_MrCat\skills\ssc-omics\scripts")
from omics import differential_expression, enrichment, download_geo

# 1) 差异表达（dcSSc vs 健康）
print(differential_expression("expr.csv", "groups.csv", "condition",
                              group_a="Healthy", group_b="dcSSc", out_csv="deg.csv"))

# 2) 富集（取显著上调基因）
import pandas as pd
deg = pd.read_csv("deg.csv")
sig = deg[(deg.padj < 0.05) & (deg.log2FC > 1)].gene.tolist()
print(enrichment(sig, "KEGG_2021_Human", out_csv="enrich.csv"))

# 3) 从 GEO 下载
print(download_geo("GSE58095", out_dir="geo_data"))
```

## 数据格式
- **表达矩阵 expr.csv**：第一列基因 symbol，其余列样本，值 = log2(归一化)。
- **分组表 groups.csv**：含 `sample` 列 + 分组列（如 `condition`：Healthy / dcSSc / lcSSc）。

## 说明与边界
- 差异分析用 Welch t 检验 + BH 校正，**适合已归一化的表达值**；
  若是**原始 count**，最规范是 R 里的 DESeq2/edgeR（本技能未封装 R，可让用户提供归一化后矩阵）。
- 富集、GEO 下载**需要联网**。
- 显著基因≠有生物学意义，结论要回到文献和实验验证。
