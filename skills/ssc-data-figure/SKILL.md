---
name: ssc-data-figure
description: >-
  从整理好的数据（CSV/Excel）生成出版级科研图表——目前支持火山图（差异表达）和森林图（Meta 分析）。
  核心原则：不让 AI 随便画，而是调用封装好的绘图函数 sci_plots.py；数据格式不对就明确报错，绝不糊弄。
  触发场景：用户要画火山图、森林图、做差异表达可视化、Meta 分析森林图、把数据变成论文配图、
  "画图"、"作图"、"出图"、"可视化"、"volcano plot"、"forest plot"。
  前提：用户必须提供格式正确的数据文件；数据不对时先要求用户修正，不要编造数据。
---

# 数据到图表（火山图 / 森林图）

这是「数据 → 图表」自动化技能。你的任务是：确认数据格式 → 调用封装好的绘图函数 → 返回图片路径，
而**不是**自己凭空画或编造数据。

## 工作流

1. **先确认数据**。让用户提供 CSV 文件（或先用 `read_file` 查看列名）。绝不虚构数据。
2. **核对列名是否符合要求**（见下方每种图的格式）。缺列就明确告诉用户要补哪一列，停下等数据。
3. **用 `run_python` 调用 `sci_plots.py` 里的函数**出图，把返回的图片路径告诉用户。
4. 出图后简要说明图里能看出什么（多少上调/下调、效应方向等），但结果解读与结论由用户负责。

## 调用方式（在 run_python 里）

绘图函数在本技能目录的 `scripts/sci_plots.py`。标准调用模板：

```python
import sys
sys.path.insert(0, r"F:\SSC\My_AGI_MrCat\skills\ssc-data-figure\scripts")
from sci_plots import volcano_plot, forest_plot

# 火山图
print(volcano_plot("你的数据.csv", out="volcano.png"))

# 森林图
print(forest_plot("你的数据.csv", out="forest.png"))
```

保存的 png 会出现在工作目录（agent_workspace），网页会自动显示。

## 数据格式要求

### 火山图 volcano_plot
CSV 至少三列（列名可通过参数改）：
- `gene`：基因/特征名称
- `log2FC`：log2 差异倍数
- `pvalue`：p 值（或校正后 p）

可调参数：`lfc_thr`（默认 1.0）、`p_thr`（默认 0.05）、`top_n`（标注前 N 个显著点）。

### 森林图 forest_plot
CSV 至少四列：
- `study`：研究名称
- `effect`：效应值（OR / HR / RR / SMD 等）
- `lower` / `upper`：95% 置信区间下限、上限

效应为比值型（OR/HR/RR）时无效线在 1.0，可设 `logx=True`；差异型（MD/SMD）时设 `null_line=0`。

## 边界

- 只负责「数据 → 图」。真实数据来自用户的实验/统计，不由你产生。
- 图能反映数据，但**是否有统计学意义、生物学意义、能否写进论文由用户判断**。
