# A.7.5.6 — 单次真实付费 HITL Canary（一次性，已用尽）

**结论先行：治理与安全机制全部按设计生效；科研产出为零。**
本次运行在 Synthesizer 阶段 **fail-closed 失败**，Verifier 与 Claim extractor **从未被调用**，
未生成 ResearchArtifact，**未产生任何关于 cGAS–STING 与 SSc 的科研结论**。

- Run: `hitl-research-e4cbb903` · Executor: `gated-research-v1`
- 终态：`failed` + `needs_human_review = true`
- 真实付费调用：**1 次**（上限 3）· 实际花费 **$0.07043**（上限 $0.15）
- 本阶段**未修改任何生产代码**；**失败后未修改代码、未重跑**。

---

## 1. HITL 链路（全部通过 UI，未绕过）

| 步骤 | 结果 | 该时刻真实 provider 调用数 |
|---|---|---|
| 创建 Research Run（`gated-research-v1`，固定研究问题） | 201 | 0 |
| UI 显示 Clarification | 是 | **0** |
| 人工在 UI 中选择 `strict_causal`（严格人体因果标准） | 是，未被代码自动填充 | **0** |
| UI 显示 Approval | 是 | **0** |
| 人工点击一次 Approve | 1 次 | 0 |
| Approve 返回 | `running`，**109 ms** | 0 |
| 后台 worker 执行 | 见下 | 1 |

Approval **之前**真实付费调用严格为 **0**，符合 §9.9。

## 2. 阶段执行

```
0  run_created                 8  approval_granted
1  plan_ready                  9  research_stage_started   validate_evidence
2  step_started               10  research_stage_completed validate_evidence
3  clarification_requested    11  research_stage_started   evidence_accumulator
4  clarification_answered     12  research_stage_completed evidence_accumulator
5  step_satisfied             13  research_stage_started   synthesizer
6  step_started               14  research_stage_failed    synthesizer
7  approval_requested         15  run_failed               synthesizer
```
16 个事件，sequence 连续且唯一。未到达：verifier、claim_extractor、claim_graph、shadow、artifact。

## 3. 失败原因（由账本证实，非推测）

```
Synthesizer JSON 解析失败（fail-closed）：Expecting ',' delimiter: line 29 column 4 (char 1782)
```

账本记录：`input_tokens=3293, output_tokens=1500, max_tokens=1500`。
**输出 token 精确等于上限** ⇒ Synthesizer 的结构化 JSON 在对象中途被输出上限截断，
因而无法解析。根因是**运行脚手架的 `max_tokens` 配置过小**（Synthesizer 需同时给出
人体相关性 / SSc 机制 / 非 SSc 间接机制 / 直接人体因果 四层区分 + 不足与矛盾说明，
1500 output tokens 不足），**不是生产代码缺陷**。

Executor 的行为完全正确：宁可失败也不接受半个 JSON，未做任何猜测补全。

## 4. 安全与预算核对

| 项 | 结果 |
|---|---|
| 真实付费调用总数 | 1（≤3）✅ |
| synthesizer / verifier / claim_extractor | 1 / 0 / 0 ✅（失败未级联） |
| 重试次数 | 0 ✅ |
| fallback 调用 | 0 ✅ |
| 第 4 次调用 | 0 ✅ |
| Shadow 模型调用 | 0 ✅ |
| Planner / ReAct / Resolver | 0 ✅ |
| 联网 / 代码执行 / 设备控制 | 0 / 0 / 0 ✅ |
| 账本 reserved / reconciled / **open** | 1 / 1 / **0** ✅ |
| 实际花费 | **$0.07043** ≤ $0.15 ✅ |
| 价格表版本 | `2026-07-20.1`（本轮已对官方页面重新核验：Opus $5/$25 per MTok；DeepSeek-V4-Flash $0.14/$0.28 per M，均未弃用） |
| 冻结证据包运行前后 | 逐字节未变 ✅ |
| 事件中泄漏密钥 / 路径 / traceback / 原始 prompt | 无 ✅ |

预留 $0.06262 < 实际 $0.07043：预留时的输入 token 估计（2512）低于实际（3293）。
硬上限仍然生效（$0.15），但**预留估计偏低**这一点值得记录。

## 5. 已发现的两个真实缺口（未修复，按规则不在本轮改代码）

1. **Approval 卡片信息不足（§9.8 未完全满足）。**
   卡片展示了 executor id、8 个阶段、policy（network / code / device / planner = false、
   `max_model_calls=3`、role_limits 1/1/1、require_approval）、policy_hash、plan_hash，
   但**未展示** subset hash、6 张核心卡、direct=3 / indirect=3、
   `direct_human_causal_count=0`，且 `max_cost_usd` 显示为 `0.00` 而非真实闸门上限 $0.15。
   实际约束在服务端由 `FrozenEvidenceLoader` 与 `HardBudgetGate` 强制执行并确实生效，
   但**人在批准时看不到这些事实**，削弱了知情批准。

2. **Synthesizer `max_tokens` 与所需结构化输出不匹配。**
   本次任务要求四层证据区分 + 不足/矛盾说明，1500 output tokens 会被截断。
   需要更大的输出预算，或改为分段/流式可续的结构化输出。

## 6. 科研结论

**无。** 本次运行未产生任何科研结论，未回答冻结研究问题，
不得据此对「cGAS–STING 激活是否直接导致 SSc 成纤维细胞持续活化」作任何陈述。
冻结证据包本身仍记录 `direct_human_causal_count = 0`。

## 7. 状态

单次授权的真实付费 Canary **已用尽**。按 §15，运行失败后**未修改代码、未重跑**。
是否修复上述两个缺口、是否授权第二次 Canary，均需另行批准。

关联输入哈希见 `A7536_INPUT_HASHES.json`，全量指标见 `A7536_CANARY_METRICS.json`。
