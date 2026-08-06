# A.7.5.6.2 — 第二次（也是最后一次）真实付费 HITL Canary

**结论先行：A.7.5.6.1 的三项修复全部按设计生效并被实证；但运行仍然失败，科研产出为零。**

Run `hitl-research-ac76f309` · Executor `gated-research-v1` ·
终态 `failed` + `needs_human_review` · 真实付费调用 **1 次**（上限 3）·
实际花费 **$0.06153**（上限 $0.15）· 两次 Canary 累计 **$0.13196**。

本轮**未修改生产代码**，失败后**未修改代码、未重跑**。

---

## 1. 三项修复的实证结果

| A.7.5.6.1 修复项 | 第一次 Canary | 第二次 Canary | 结论 |
|---|---|---|---|
| Approval 展示真实冻结证据与 Gate 预算 | 3 条 refs / `max_cost_usd=0.00` | 6 核心卡 / 直接 3 / 间接 3 / dhc 0 / 预算 $0.15 / 最坏 $0.136054 | **已修复** |
| 预留不低于实际费用 | 预留 $0.06262 < 实际 $0.07043 | 预留 **$0.06823** ≥ 实际 **$0.06153**，`under_reserved=false` | **已修复** |
| 截断被单独分类 | `ResearchOutputError`「JSON 解析失败」 | `OutputTruncated` + `finish_reason=max_tokens` + `1600/1600` | **已修复** |
| Prompt 压缩 | 真实输入 3293 tokens | 真实输入 **2153** tokens | **生效** |

估算比值 `estimation_ratio = 1.3112`（估 2823 / 实际 2153），方向由「低估」翻转为「高估」，
预留因此充足，并释放了 $0.0067。

## 2. 但运行仍然失败——根因与上次不同

```
research_stage_failed  synthesizer
  error_type = OutputTruncated
  finish_reason = max_tokens
  output_size = 1600 / configured_output_limit = 1600
```

Synthesizer 的输出**再次精确顶到上限**（第一次 1500/1500，这次 1600/1600）。

**真正的根因**：schema 上限约束的是「什么算合法」，**没有任何东西约束模型实际写多长**。
Prompt 告诉了模型 schema，但**没有告诉它每个字段的长度上限**，也没有使用
structured output / tool-use 之类的强制机制。于是模型按自己的习惯写了一篇远超上限的回答，
在 max_tokens 处被切断。

因此 A.7.5.6.1 的 sizing 逻辑（「max_tokens 必须放得下最坏**合法** JSON」）是
**必要但不充分**的：它假设模型会遵守 caps，而模型并不知道 caps 的存在。
把 1500 提到 1600 自然无济于事——模型的自然输出长度远在两者之上。

## 3. 安全与预算核对

| 项 | 结果 |
|---|---|
| 真实付费调用 | **1**（≤3）✅ |
| synthesizer / verifier / claim_extractor | **1 / 0 / 0** ✅（失败未级联） |
| 重试 / fallback / 第 4 次 / Shadow 模型调用 | 0 / 0 / 0 / 0 ✅ |
| Planner / ReAct / Resolver | 0 ✅ |
| 联网 / 代码执行 / 设备控制 | 0 / 0 / 0 ✅ |
| Clarification 前、Approval 前 provider 调用 | **0 / 0** ✅ |
| 人工批准次数 | 1 ✅（返回 `running`，1564 ms，异步） |
| 账本 reserved / reconciled / **open** | 1 / 1 / **0** ✅ |
| 实际花费 | **$0.06153** ≤ $0.15 ✅ |
| 事件 | 16 条，sequence 连续唯一 ✅ |
| 冻结证据包运行前后 | 逐字节未变 ✅ |
| 敏感扫描（key/路径/traceback/原始 prompt/模型正文） | 全部无 ✅ |
| 失败 Manifest | 已生成，`output_truncated=true`，**不含被截断正文** ✅ |
| 成功 Artifact | 0（正确：失败不得产出成功产物）✅ |

## 4. 本轮观察到的一处 UI 不足

审批卡的**逐角色**明细行（`apr-roles`）在 SSE 到达后消失：
`roles` 是嵌套数组，而 `approval_requested` 事件只携带扁平键，
LabStore 重建 `frozen_facts` 时把它丢掉了。

关键数字（core/direct/indirect/dhc/各哈希/预算/最坏费用/`preview_hash`）都是扁平键，
**全部正常显示**；缺的只是「每角色 model 与 max_tokens」这一行。属于展示缺口，不影响任何强制约束。

## 5. 科研结论

**无。** 未回答冻结研究问题，不得据此对
「cGAS–STING 激活是否直接导致 SSc 成纤维细胞持续活化」作任何陈述。
冻结证据包仍记录 `direct_human_causal_count = 0`。

## 6. 状态

两次授权的真实付费 Canary **均已用尽**，累计花费 **$0.13196**。
治理层（HITL、硬闸门、fail-closed、预留、截断分类、审批透明度）已被两次真实付费运行反复验证，
全部生效；未解决的是**模型输出长度控制**这一项，它需要新的设计决定（见下）。

关联：全量指标 `A7562_CANARY_METRICS.json`；第一次 Canary 见 `A7536_CANARY_RESULT.md`
（其提交、产物与哈希本轮逐字节未变）。
