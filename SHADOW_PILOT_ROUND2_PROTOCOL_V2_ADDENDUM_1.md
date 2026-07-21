# Shadow Pilot Round 2 — 协议 v2 附录 1（Addendum 1）

**Implementation safety constraints —— 只收紧循环安全边界。**

> 本附录**不修改**：v1 协议正文与 hash、v2 协议正文与 hash、12 道题、评分规则、
> 通过门槛、预算、证据标准、历史 A1 报告、A.6.4 诊断、A1 现场产物。
> 它是**新增文件**，不是对 v2 的原地改写。
>
> 适用范围：Pilot 执行路径（`pilot/`）。生产入口默认行为不受本附录影响。

- 附录版本：**Addendum 1**
- 冻结时间：2026-07-21
- 依附协议：`SHADOW_PILOT_ROUND2_PROTOCOL_V2.md`
  （sha256 `c76f589485e4ebfd728c27b653d2735f3ebd1c6930087c244e4efbdba9d66696`，**未变**）
- 代码基线：A.6.5（dev `03aa6e5` / public `9b31bfa`，CI Run #34 = success 9/9）

---

## 1. 工具轮次上限

| 项 | 值 |
|---|---|
| 每次 Executor 执行的最大工具轮次 | **8** |
| 第 **9** 个工具轮次 | **执行前阻断** |
| 与 v2 的关系 | 严于 v2 的 Executor 模型调用上限 16 |
| v2 的 16 次上限 | **继续保留**，作为外层硬限制 |

三个限制彼此独立、同时生效，不互相替代：

```
工具轮次 ≤ 8        （本附录，最内层）
Executor 模型调用 ≤ 16（v2，外层硬限制）
外层 max_iterations = 2、单题总调用 ≤ 21（v2，最外层）
```

## 2. 重复调用

规范化键：**`tool_name + normalized_arguments_hash`**（参数 JSON 排序后取 SHA-256）。

| 情形 | 处理 |
|---|---|
| 连续第 2 次相同调用 | **warning**（记录，不阻断） |
| 连续第 3 次相同调用 | **执行前阻断** |
| 同一工具、不同参数 | **不视为**相同调用，不触发 |

> 仅凭工具名判重复是错误的 —— 同一工具用不同参数是合理行为。

## 3. 循环检测

- 识别重复的工具调用**模式**（而非单次重复）。
- **A→B→A→B** 模式再次重复时**阻断**。
- 阻断发生在**下一次工具执行之前**。

## 4. 无进展

连续 **3** 轮没有新增以下**确定性**信号时阻断：

- 新 Evidence ID
- 新 dataset ID
- 新 Observation hash
- 新工具结果 hash
- 明确的状态字段变化

> **不得由 LLM 主观判断"是否有进展"。** 进展信号必须是可机器判定的确定性值。

## 5. 失败语义

任一 Guard 触发时：

- `status = failed`
- **旧答案保持 fail-closed**（沿用 v2，不因 Guard 触发而改变裁决方式）
- **不生成科研成功结论**
- Shadow 标记 `not_run_due_to_upstream_failure`
- 生成**脱敏诊断 Manifest**（`manifest_kind = failure_diagnostic`）
- 保存**已发生的** Executor 事件（append-only，异常也不丢）
- 后续 provider 与工具调用**停止**

诊断 Manifest **不是**科研结论 Manifest：`claims` 与 `evidence_cards` 恒为空。

## 6. 可观测性

四种事件必须分别记录，**`selected` 不能证明 `executed`**：

| 事件 | 含义 |
|---|---|
| `selected` | Retriever 允许使用该工具 |
| `requested` | 模型实际产生了 tool_call |
| `executed` | 工具函数实际开始运行 |
| `observed` | 工具结果以 ToolMessage 返回图中 |

轨迹**仅保存**：枚举 / 数量 / 长度 / SHA-256 / 工具名称 / 参数**键名** / 白名单元数据。

轨迹**不保存**：完整 Prompt、完整模型正文、完整工具参数、API key、
Authorization、Cookie、`.env`、患者信息、用户绝对路径。

## 7. 历史可比性声明

> **两次 A1 运行的软件条件不同，必须并列报告，不得互相覆盖。**

| | 第一次 A1 | 下一次 A1 |
|---|---|---|
| 软件条件 | **v2 原始条件** | **v2 + Addendum 1** |
| run_id | `20260721_105710_77ea9469` | 待生成（必须是新的唯一 ID） |
| 结果 | 未完成：Executor 用满 16 次模型调用，无 Manifest | 待运行 |
| 费用 | $0.085541 / 18 次调用 | 待运行 |

规则：

1. 重跑的**目的**是验证修复、并取得 A.6.4 缺失的 Executor 轨迹证据。
2. **不得**用第二次结果覆盖第一次失败记录。
3. 两次的费用、调用次数与结果必须**并列报告**。
4. **不得**挑选较好的一次作为唯一结果。
5. 本附录**不改变**题目、评分、证据标准或预算 —— 只收紧循环安全边界。
6. 因此第二次运行**不构成**对第一次的"重测通过"，只构成一次条件不同的新观测。

## 8. 证据边界（延续，不因附录而改变）

第一次 A1 的 16 次响应正文**未保存**；`tool_calls` 与 `ToolMessage` 仍为 **unknown**；
"模型每轮产生 tool_calls 且工具成功执行"仍然只是**与 LangChain 状态机相符的推断**，
尚未与"`tool_trace` 只有 selected、无 called"统一。本附录带来的可观测性正是为了
在下一次运行中**验证或推翻**该推断 —— 在此之前不得将其写成已证实事实。

## 9. hash 与跨平台

- 本文件的 SHA-256 对 **LF 规范化内容**计算（`\r\n` → `\n`）。
- `.gitattributes` 已把冻结产物标记为 `-text`，保证 Windows 与 Ubuntu 检出字节一致。
- 记录见 `SHADOW_PILOT_ROUND2_PROTOCOL_V2_ADDENDUM_1.sha256`。
- **不修改任何旧 hash 文件。** 附录内容一旦变化，hash 校验测试必须失败。

## 10. 这 12 道题仍然只是开发期 Pilot

不是金标准 benchmark，不得用于公开宣称性能。附录不改变这一点。
