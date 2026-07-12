---
name: ssc-clinical-stats
description: >-
  SSc 临床研究统计技能：生存分析(Kaplan-Meier + logrank, Cox 回归)、logistic/linear 回归、
  Meta 分析合并计算(固定/随机效应 + 异质性 I²)。适合 SSc 队列、随访、预后、系统综述。
  触发场景：用户要做生存分析、KM 曲线、Cox、风险因素回归、OR/HR、Meta 分析合并、
  "生存"、"预后"、"回归"、"Meta"、"合并效应"、"森林图数据"。需要用户提供临床数据表。
---

# SSc 临床统计

覆盖 SSc 临床研究最常用的统计：**生存分析、回归、Meta 分析**。真跑计算，不是只讲方法。

## 工作流
1. 拿到临床数据表（CSV，一行一个患者/研究）。用 read_file 看列名。
2. 按需调用：
   - 预后/死亡/事件 → `km_survival` + `cox_regression`
   - 危险因素/结局关联 → `regression`（logistic 二分类 / linear 连续）
   - 系统综述合并 → `meta_analysis`，再用 [[ssc-data-figure]] 画森林图
3. 报告效应量 + 95%CI + p，并说明样本量和局限。

## 调用模板（run_python）
```python
import sys
sys.path.insert(0, r"F:\SSC\My_AGI_MrCat\skills\ssc-clinical-stats\scripts")
from clin_stats import km_survival, cox_regression, regression, meta_analysis

# 生存
print(km_survival("cohort.csv", "time_months", "death", group_col="subtype", out_png="km.png"))
print(cox_regression("cohort.csv", "time_months", "death", ["age","FVC","antiScl70"]))

# 回归（ILD 危险因素）
print(regression("cohort.csv", "ILD", ["age","antiScl70","dcSSc"], kind="logistic"))

# Meta 分析（给效应+95%CI）
print(meta_analysis("studies.csv", effect_col="OR", lower_col="lower", upper_col="upper", log_scale=True))
```

## 数据格式
- **队列 cohort.csv**：一行一患者，含时间列、事件列(1/0)、协变量列。
- **Meta studies.csv**：一行一研究，含 `study`、`effect`(或 OR/HR/RR)、`lower`/`upper`（或 `se`）。

## 边界
- 生存分析要求事件编码正确（1=事件，0=删失）；Cox 需检验比例风险假设。
- Meta 分析的森林图数据可直接接 forest_plot；异质性大(I²高)时优先看随机效应。
- 统计只给关联，因果和临床意义由研究者判断。
