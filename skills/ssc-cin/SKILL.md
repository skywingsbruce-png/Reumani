---
name: ssc-cin
description: >-
  染色体不稳定（CIN, chromosomal instability）在 SSc 中的分析技能——从表达矩阵计算样本级
  CIN signature 打分（CIN70 类），并与临床/纤维化表型（mRSS、FVC、亚型）做相关或分组比较，
  检验「基因组不稳定 → 天然免疫(cGAS-STING) → 纤维化/衰老」这条假说。
  触发场景：用户想在 SSc 组学数据里评估染色体不稳定、CIN score、增殖/有丝分裂 signature、
  DNA 损伤/衰老与纤维化的关系，"CIN"、"染色体不稳定"、"CIN70"、"基因组不稳定"。
  需要用户提供表达矩阵；不编造数据。
---

# SSc × CIN（染色体不稳定）分析

这是本课题组差异化角度的分析技能：在 SSc 的转录组数据里量化**染色体不稳定/增殖 signature**，
并检验它和纤维化、疾病亚型、临床指标的关系。背景见 [[ssc-knowledge]] 第 7 节。

## 为什么做这个
CIN 会导致微核、胞质 DNA、cGAS-STING 天然免疫激活，可能驱动慢性炎症与纤维化；
成纤维细胞衰老、复制应激也与之相关。把 CIN 量化成分数，就能在 SSc 数据里检验这条机制链。

## 工作流
1. 拿到**表达矩阵**（第一列基因 symbol，其余列样本，建议 log 归一化）。用 read_file 查列名确认。
2. 用 run_python 调用 `cin_score.py`：
   - `compute_cin_score(expr_csv)` → 每个样本的 CIN score
   - `correlate_with_phenotype(cin_csv, pheno_csv, "mRSS")` → 与连续表型相关
   - `compare_groups(cin_csv, group_csv, "subtype")` → dcSSc/lcSSc/健康 分组比较
3. 结果可再用 `ssc-data-figure` 画散点/箱线图。
4. 解读时说明：相关≠因果；CIN score 是替代指标，结论需谨慎。

## 调用模板（run_python）
```python
import sys
sys.path.insert(0, r"F:\SSC\My_AGI_MrCat\skills\ssc-cin\scripts")
from cin_score import compute_cin_score, correlate_with_phenotype, compare_groups

print(compute_cin_score("expr.csv", out_csv="cin_score.csv"))
print(correlate_with_phenotype("cin_score.csv", "clinical.csv", "mRSS"))
print(compare_groups("cin_score.csv", "clinical.csv", "subtype"))
```

## 数据格式
- **表达矩阵 expr.csv**：第一列 = 基因 symbol；其余列 = 样本；值为 log2(归一化表达)。
- **临床/表型 clinical.csv**：含 `sample` 列 + 表型列（如 `mRSS`、`FVC`、`subtype`）。

## signature 基因集
默认用 `scripts/cin70_genes.txt`（CIN70/增殖-有丝分裂核心基因起始版）。
⚠️ 正式发表前请用 Carter et al. 2006 原文完整 70 基因列表替换，保证可比性。
也可传入自己的基因集文件：`compute_cin_score("expr.csv", geneset_path="my_genes.txt")`。

## 边界
- 只做「量化 + 关联」，不下因果结论。
- signature 打分是替代指标，真正证明 CIN 需要微核计数、CNV/核型、DNA 损伤标志等实验证据。
