---
name: ssc-scrnaseq
description: >-
  SSc 单细胞 RNA-seq 分析技能（scanpy）：QC、过滤、归一化、HVG、PCA、Leiden 聚类、UMAP、
  每群 marker 识别，并给出 SSc 皮肤/肺常见细胞类型注释提示。SSc 皮肤/肺单细胞是当红方向。
  触发场景：用户要分析单细胞/scRNA-seq、细胞聚类、细胞类型注释、找成纤维细胞亚群、
  "单细胞"、"scRNA"、"scanpy"、"细胞注释"、"亚群"。需要用户提供 h5ad / 10x 数据。
---

# SSc 单细胞分析（scanpy）

SSc 皮肤和肺的单细胞图谱是当前最热的方向（识别致病性成纤维细胞亚群、免疫-基质互作）。
本技能封装标准 scanpy 流程。

## 工作流
1. 拿数据：`.h5ad`、10x `.h5`、或 10x mtx 目录。数据大，放在 agent_workspace。
2. `run_pipeline(input_path)` 一键跑：QC → 过滤 → 归一化 → HVG → PCA → Leiden → UMAP → 每群 marker。
3. 看输出的 marker 表 + UMAP，结合 `marker_annotation_hint()` 给的 SSc 常见 marker 做细胞类型注释。
4. 重点关注成纤维细胞亚群（SFRP2+/PRSS23+ 等），可再对特定亚群做差异/CIN 分析。

## 调用模板（run_python）
```python
import sys
sys.path.insert(0, r"F:\SSC\My_AGI_MrCat\skills\ssc-scrnaseq\scripts")
from scrna import run_pipeline, marker_annotation_hint

print(run_pipeline("skin_scrna.h5ad", out_prefix="ssc_skin", resolution=1.0))
print(marker_annotation_hint())
```

## 参数
- `min_genes=200, min_cells=3, max_pct_mt=20`：QC 阈值
- `n_top_genes=2000, n_pcs=30, resolution=1.0`：HVG 数、PCA 维数、聚类分辨率

## 边界
- 供体级重复很重要：**别把多个样本当成独立细胞直接比**，差异分析要做伪重复(pseudobulk)。
- 自动聚类只是起点，细胞类型注释需要人工结合 marker 和领域知识确认。
- 批次效应大时需先做整合（Harmony/scVI，本技能未内置，可按需扩展）。
