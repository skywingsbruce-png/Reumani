# Shadow Pilot Round 2 — 报告（**Stage 1 中止**，未完成）

> **本轮未完成。** Stage 1 金丝雀第一题（A1）触发第五部分的立即停止条件
> ——「模型调用次数突破硬上限」。按规定：**停止后只报告，不在同一轮中修改代码重跑。**
> B1 未运行，Stage 2 / Stage 3 未运行。
>
> **这不是金标准 benchmark。** 12 道题是开发期 Pilot，不用于公开宣称性能，
> 未进入训练数据、知识库优化或提示词调参。

## 1. 环境、commit、模型版本

| 项 | 值 |
|---|---|
| 代码基线 | Commit A.6：dev `a086585` / public `c675207` |
| 协议冻结提交 | dev `88b380f` / public `b8c7330`（CI Run #22 = success） |
| 运行时 HEAD | 与协议冻结提交一致；系统模块（`ssc_a1` / `ssc_skill_agent` / `shadow` / `verifier`）**未改动** |
| 全量测试 | 218 passed / 2 deselected（A.6 验收态）+ 本轮新增 10 项闸门单测 |
| Planner + Verifier | `claude-opus-4-8`（ChatAnthropic），$5 入 / $25 出 每百万 token |
| Executor + Claim 提取 | `deepseek-chat`（ChatOpenAI → api.deepseek.com），单价为**未核实估计值** |
| `REUMANI_REAL_LLM` | 本轮不适用（Round 2 本就使用真实模型） |
| Adversarial 层 | **仍未运行**（`adversary_searcher=None`）→ 系统不得称为完整"四层核查" |

## 2. 冻结题目与评分规范的 SHA-256

`SHADOW_PILOT_ROUND2_PROTOCOL.md` v1 =
**`5d166bce159de665c4df677aef6765803575a48827afdc5d061cb49ff54f0f22`**

运行前后复核一致，题目与评分规则**未被修改**。

## 3. 实际预算与调用次数

| 项 | 冻结上限 | 实际 |
|---|---|---|
| 总费用 | $25.00 | **$0.2633**（估算） |
| 全局调用 | 200 | **14** |
| 每题调用 | **12** | **A1 用了 14 → 触发停止** |
| 单任务超时 | 600 s | A1 实耗 50.62 s（未超时） |
| 输入 / 输出 token | — | 35,742 / 3,384 |

## 4. 每题逐项结果

| 题 | 状态 |
|---|---|
| A1 | **中止**：达到每题调用上限，指标未采集完整（`results` 为空） |
| B1 | **未运行** |
| A2–A4 / B2–B3 / C1–C3 / D1–D2 | **未运行** |

第 5–10 项（两次运行稳定性、结构化率、Claim–Evidence 关联、引用真实性、因果/外推错误、old/shadow 分歧矩阵）**本轮无数据**——A1 在指标采集之前即被中止。**不做任何推测性填写。**

## 11. 安全与 Manifest 审计

- 落盘文件：`pilot/round2_results/stage1_metrics.json`（825 字节）
- 审计结果：`sk-` 0、`Authorization` 0、`Cookie` 0、`DEEPSEEK_API_KEY` 0、`ANTHROPIC_API_KEY` 0、`.env` 0、用户主目录绝对路径 0、`F:\SSC` 0、`Bearer` 0
- shadow manifest：A1 未走到 manifest 写入即被中止，本轮无 manifest 可审计
- 提交内容不含 key / `.env` / 认证头 / 绝对路径 / 患者数据 / 超大中间文件 / 未脱敏日志

## 12. 失败案例原样记录

**失败 1（触发停止条件）**
```
A1: BudgetExceeded: max_calls_per_task[A1]: 14 > 12
per_task A1: {"calls": 14, "in": 35742, "out": 3384, "usd": 0.26331, "seconds": 50.62}
```

**失败 2（闸门自身缺陷，同轮发现、未修）**
```
Error in BudgetGate.on_llm_end callback: BudgetExceeded('max_calls_per_task[A1]: 13 > 12')
Error in BudgetGate.on_llm_end callback: BudgetExceeded('max_calls_per_task[A1]: 14 > 12')
```
LangChain **捕获并吞掉了 callback 内抛出的异常**，因此第 13、14 次调用在闸门跳闸后**仍然发生**。
计量数学正确、跳闸时机正确，但**中止是"软"的**：只在任务边界真正生效，不能在链路中途硬切断。
→ 当前闸门满足"准确计量 + 任务级停止"，**不满足**"任一调用越限即刻硬中断"。

## 13. 失败归属分类

| 失败 | 归属 | 说明 |
|---|---|---|
| A1 调用数 14 > 12 | **评测设计（我的估算错误）** | 协议 §3.1 只算了 Opus 的 4 次（plan×2 + verify×2），但每题上限 12 是 Opus+DeepSeek 合计。ReAct executor 每轮工具循环都是一次 DeepSeek 调用，2 次迭代很容易到 10 次以上。**不是系统缺陷，不是模型失败。** |
| 闸门软中止 | **工具/harness** | LangChain callback 异常不传播；需要改为在 LLM 包装层前置检查，而非依赖 callback 抛出。 |
| 科学可靠性类失败 | 无数据 | 本轮未产生任何科学结论，不做归因 |

## 14. 是否达到进入下一阶段的门槛

**否 —— 本轮未产生可判定的数据。** 第七部分的 14 项门槛中，除"总费用未突破冻结预算"（✅ $0.26 / $25）外，其余均**无数据**，不做任何"暂时通过"的表述。

## 15. 性质声明

这 12 道题是**开发期 Pilot**，不是金标准 benchmark，不能用于公开宣称性能，也不能与开发测试集或知识库优化混用。本报告记录的是一次**未完成的运行**。

---

## 待你决定的两件事（我未擅自处理）

1. **每题调用上限 12 太低**，与真实链路不符。修改它属于「修改冻结的评测规范」，按协议必须产生 **v2 + 新 SHA-256**，且必须由你批准——我没有改。建议 v2 把每题上限设为 **30**（Opus 仍单独限 6 次/题），总预算硬上限不变。
2. **闸门需要从"软中止"改为"硬中止"**（在模型调用入口前置检查，不依赖 callback 异常传播）。这是 harness 修改，不碰系统模块，但按第五部分规定我没有在同一轮修改重跑。

在你批准之前，我不进入 Stage 1 重跑、不进入 Stage 2/3、不开始 Commit B、不切换最终裁决权。
