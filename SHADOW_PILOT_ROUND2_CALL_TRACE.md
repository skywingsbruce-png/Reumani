# Round 2 · A1 调用图还原（只读分析）

本文件只记录**脱敏元数据**：无完整 Prompt、无密钥、无认证头、无绝对路径。

每个字段标注证据等级：
- **[账本]** = `pilot/round2_results/stage1_metrics.json` 或 stderr 实测；
- **[代码]** = 从已验证的运行路径推导（读源码确认，非猜测）；
- **[实测]** = 真实编排 + 假模型跑出来的经验值（`tests/test_hard_gate_agent_chain.py`）；
- **unknown** = 证据不足，**不猜**。

## 0. 为什么很多字段是 unknown

旧 `BudgetGate` 只累加**总量**，不写逐次调用记录；A1 在 `_save_run()` 之前就抛异常，
因此 **没有 run 目录、没有 run.json、没有 shadow manifest**。
可用证据只有：总调用数、总 token、总耗时、总费用、两条 stderr。
→ 逐次的 provider / model / 阶段 / iteration / 起止时间**无法还原**，一律标 unknown。
这正是本次 A.6.2 要引入逐次账本的原因。

## 1. 账本实测总量 [账本]

| 项 | 值 |
|---|---|
| 计数到的调用 | **14** |
| 输入 token | 35,742 |
| 输出 token | 3,384 |
| 记账费用 | $0.26331 |
| 任务耗时 | 50.62 s（未超 600 s） |
| 跳闸点 | 第 13 次越限，第 14 次再次越限 |

### 1.1 一个可证明的计价错误 [账本 + 算术]

```
35,742 / 1e6 × $5.00(Opus in)  = $0.178710
 3,384 / 1e6 × $25.00(Opus out) = $0.084600
                          合计  = $0.263310   ← 与账本 $0.26331 完全相等
```
**结论：旧闸门把全部 14 次调用都按 Opus 单价计了**，包括本应是 DeepSeek 的 Executor 调用。
原因：`_extract_usage()` 未能从 DeepSeek 响应中取到可匹配的 `model_name`，
`price_for()` 于是回退到 `_DEFAULT`（假设 Opus）。
影响：**费用被高估（保守，安全方向）**，但**逐模型归因完全丢失**。
A.6.2 已改为：未知模型 / 未知价格 → `GateConfigError` 拒绝调用，不再静默回退。

## 2. 逐次调用表（14 次）

| call_index | provider | model | Agent 阶段 | iteration | 产生工具调用 | 是否重试 | 是否计费 | 开始 | 结束 |
|---|---|---|---|---|---|---|---|---|---|
| 1–12 | unknown | unknown | unknown | unknown | unknown | unknown | **是** [代码] | unknown | unknown |
| 13 | unknown | unknown | unknown | unknown | unknown | unknown | **是** [账本] | unknown | unknown |
| 14 | unknown | unknown | unknown | unknown | unknown | unknown | **是** [账本] | unknown | unknown |

**为什么 1–12 也确定计费**：`on_llm_end` 是**响应返回之后**才触发的回调，14 次全部触发过
（其中 13、14 两次在回调内抛错并被框架吞掉）→ 14 次都拿到了供应商响应 → 14 次都已计费。[代码 + 账本]

**为什么第 12、13、14 次仍被执行**：
- 第 12 次：**当时尚未越限**（上限 12，第 12 次是合法的最后一次）。[代码]
- 第 13、14 次：闸门确实在 `on_llm_end` 里抛了 `BudgetExceeded`，但**该回调发生在网络请求完成之后**，
  且 **LangChain 捕获并吞掉了 callback 内的异常**（stderr 原文：
  `Error in BudgetGate.on_llm_end callback: BudgetExceeded('max_calls_per_task[A1]: 13 > 12')`）。
  因此第 13 次的跳闸既不能撤销第 13 次（已发生、已计费），也不能阻止第 14 次。[账本]

## 3. 八个重点问题

**Q1 `max_iterations=2` 为什么产生 14 次调用？**
因为 **iteration ≠ LLM 调用**。`max_iterations` 只限制"计划→执行→核查"的**外层轮数**，
不限制每轮内部的 LLM 次数。Executor 是 `create_react_agent(deepseek_llm_pro, tools)`（LangGraph ReAct），
**每一轮工具循环都是一次独立 LLM 调用**，轮数由模型自己决定，不受 `max_iterations` 约束。[代码]

**Q2 一次 iteration 实际包含多少个 LLM invoke？**
`1 (Planner) + N (Executor ReAct) + 1 (Verifier)`，其中 N = 工具循环轮数 + 1 次收尾。
N 在代码里**没有上限**。[代码] 实测典型 N≈4 → 每轮约 6 次。[实测]

**Q3 callback 按什么计数？**
按**逻辑 LLM run 完成**计数（`on_llm_end` 每个 run 触发一次）。
它**不**按 HTTP 请求计数、**不**按流式事件计数、**也不**为 SDK 层重试单独触发。
→ SDK 内部重试产生的额外网络请求**已计费但闸门看不见**，计数天然偏低。[代码]

**Q4 是否存在框架自动重试？**
**存在可能性且未被禁用**：`judge_llm`(ChatAnthropic) 与 `deepseek_llm_pro`(ChatOpenAI)
**都没有显式设置 `max_retries`** → 走 SDK 默认值（Anthropic / OpenAI SDK 均为 2 次）。[代码]
A1 运行中**是否真的发生过重试 = unknown**（旧闸门对此不可见）。

**Q5 Planner / Verifier 是否各运行两次？**
如果两轮 iteration 都跑满，则各 2 次（共 4 次 Opus）。[代码]
**A1 是否真的跑满两轮 = unknown** —— 没有 run.json，`retry_count` 未落盘。

**Q6 Claim extraction 是否单独计费？**
**是**，它是 shadow 链里一次独立的 DeepSeek 调用（`default_claim_extractor`）。[代码]
**A1 是否执行到这一步 = unknown**；从中止点看很可能未执行（shadow 在 verify 之后才跑）。

**Q7 第 13、14 次是否已被供应商计费？**
**是。** 依据同 §2：`on_llm_end` 只在响应返回后触发，这两次都触发了。[账本]

**Q8 A1 正常完成的最少 / 典型 / 最坏调用数？** [实测]

| | 结构 | 次数 |
|---|---|---|
| 最少 | 1 轮 × (1 plan + 1 exec + 1 verify) + 1 claim | **4** |
| 典型 | 2 轮 × (1 plan + 4 exec + 1 verify) + 1 claim | **13** |
| 最坏 | 2 轮 × (1 plan + 8 exec + 1 verify) + 1 claim | **21** |

（由 `test_measure_min_typical_worst_call_counts` 用真实编排 + 假模型测得。）

**这就是 A1 中止的结构性原因：冻结的每题上限 12 落在"典型 13"之下。**
实测 A1 用了 14 次，与"典型"区间吻合。
→ **上限 12 本身不合理，但本轮不改**（第七部分要求）；改动须走协议 v2。

## 4. 与冻结协议的一致性

- 协议 `SHADOW_PILOT_ROUND2_PROTOCOL.md` v1 hash **未变**：
  `5d166bce159de665c4df677aef6765803575a48827afdc5d061cb49ff54f0f22`
- 12 道题、评分标准、通过门槛、每题上限 12 **均未修改**
- Stage 1 失败记录 `SHADOW_PILOT_ROUND2_REPORT.md` **未覆盖、未删除、未重写**
- 本次分析**零真实付费调用**
