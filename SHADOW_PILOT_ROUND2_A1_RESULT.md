# Shadow Pilot Round 2 — A1 金丝雀结果（真实付费，单题单次）

> **A1 未完成，未通过金丝雀门槛。**
> Executor 在第一轮就用满 v2 冻结的每角色上限 16，第 17 次调用在 provider 之前被硬闸门拒绝；
> 两轮 iteration 均因此失败，链路**未到达** Verifier / Claim extractor / Shadow / Manifest。
> 触发第六部分停止条件「调用次数或预算达到硬上限」→ **已封存：不修代码、不重跑、不运行 B1。**
>
> 这不是金标准 benchmark，单题结果不用于宣称整体性能。

## 1. 环境与冻结基线

| 项 | 值 |
|---|---|
| dev HEAD | `86178e7` |
| public HEAD | `a970ac8` |
| CI | Run #29 success 9/9 |
| pytest | 372 passed / 2 deselected |
| v1 协议 hash | `5d166bce159de665c4df677aef6765803575a48827afdc5d061cb49ff54f0f22` |
| v2 协议 hash | `c76f589485e4ebfd728c27b653d2735f3ebd1c6930087c244e4efbdba9d66696` |
| price_config_version | `2026-07-20.1` |
| **正式 run_id** | **`20260721_105710_77ea9469`** |
| preflight run_id（仅检查记录，未复用） | `A1_821d9846b941` |
| 运行耗时 | 60.59 s（上限 600 s） |

19 项 preflight 全部 verified：第 1/4/5 项为 `verified_external`（附外部证据），其余 `verified_automatic`。
12 项角色注入断言全部通过（planner/verifier 为不同 wrapper、同一 Stage 账本、`verifier_call` 默认 None、
run_agent 显式收到两个注入对象、Executor/Claim 走各自角色、Verifier 不走全局 judge_llm 兜底）。

## 2. 调用与费用

| 角色 | 调用 | 输入 token | 输出 token | 实际费用 |
|---|---|---|---|---|
| Planner (`claude-opus-4-8`) | **2** | 1,646 | 2,512 | $0.079260 |
| Executor (`deepseek-v4-flash`) | **16**（用满） | 42,146 | 1,353 | $0.006281 |
| Verifier | **0** | — | — | — |
| Claim extractor | **0** | — | — | — |
| **合计** | **18** / 21 | 43,792 | 3,865 | **$0.085541** |

- 单题上限 $1.50 → **余量 $1.414459**；Stage 上限 $3.00 未逼近。
- `usage_unknown` = **0**；`provider_may_have_billed` = **0**；未结算 reservation = **0**。
- **SDK 自动重试 = 0**；Pilot 自管重试 = 0（`is_retry` 事件 0 条）。
- 所有 18 次调用的 `price_config_version` 一致。
- 硬闸门 `rejected_before_invoke` = **2**，原因均为
  `max_calls_per_role[executor]: 17 > 16` —— **在网络请求之前拒绝**，provider 未被触达。

逐次明细见 `pilot/round2_results/A1_calls_audit.json`（脱敏）。

## 3. 发生了什么（根因）

```
iteration 1: Planner(1) → Executor ReAct 连续 16 次调用 → 第 17 次被闸门拒绝
             → execute() 抛错 → run_agent 捕获 → retry_count=1
iteration 2: Planner(1) → Executor 立即被拒（每角色计数按题累计，已用满）
             → retry_count=2 → 迭代耗尽
最终答案 = "⚠️ 未验证 / 证据不足（no_verification）"（fail-closed，正确）
```

Executor 的 16 次调用输出都很短（69–122 token），输入从 1,407 单调增长到 4,848 ——
典型的 ReAct 循环空转：它在反复发起工具轮次而没有收敛到最终答案。

`state.errors` 两条，均为 `执行出错：max_calls_per_role[executor]: 17 > 16`。

**选中的工具**：`list_skills`, `query_data_lake`, `retrieve_resources`, `search_literature`。
值得注意：确定性选择器**没有选中结构化的 `search_evidence`**，而 A1 恰恰是精确 ID 检索题。
`tool_trace` 只有 4 条 `selected` 事件，没有 `called` 事件。

## 4. 科研输出

| 指标 | 值 |
|---|---|
| 计划是否结构化可执行 | ✅ 是，3 步，通过 schema 校验 |
| 工具调用数（来自 Manifest） | 不可得（Shadow 未运行） |
| 未授权工具执行 | **0** |
| 工具失败数 | 不可得 |
| structured / legacy ToolResult | 不可得（Shadow 未运行） |
| EvidenceCard | **0** |
| Claim | **0** |
| Claim–Evidence 关联率 | 不适用（无 Claim） |
| 无证据强结论 | **0**（最终答案是 fail-closed 的"未验证/证据不足"，未给任何结论） |

> ⚠️ 上表中"不可得"的项**不是 0**，是因为链路没走到 Shadow 采集阶段。不做推测填写。

## 5. 引用核验

A1 的输出**没有产生任何 PMID / DOI**（0 张 EvidenceCard、最终答案无结论），因此**没有引用可核验**，
也就不存在伪造或错配引用（伪造引用被接受 = 0）。

作为后续轮次的基线，用免费权威来源（Europe PMC REST）核对了**冻结题目里**的两个标识符：

| 标识符 | Europe PMC 结果 |
|---|---|
| PMID `41657283` | **真实存在**。标题 "From technological iteration to clinical breakthrough: advances of CAR-T cell therapy in autoimmune diseases"，Ann Med，2026，DOI `10.1080/07853890.2026.2627057` |
| DOI `10.1080/03009742.2024.2302553` | **hitCount = 0**，Europe PMC 检索不到 |

→ **观察（不改题）**：A1 的"精确 DOI"子任务在免费索引中可能**没有可检索的标准答案**。
这影响 A1 的可评分性，但按规定**我没有修改冻结题目**，记录在此供你决定。

## 6. Shadow

**Shadow 未运行**（链路未到达）。因此以下全部无数据，不做推测：
old verifier 结果 / shadow verifier 结果 / agreement / divergence / shadow_status /
拒绝理由 / human_review / adversarial_not_run / 错误放行 / 错误拒绝。

**Shadow 异常影响旧答案 = 否**（Shadow 根本没执行；最终答案由旧 verify 的 fail-closed 分支决定）。

## 7. 安全与 Manifest 审计

| 项 | 结果 |
|---|---|
| Manifest 是否生成 | ❌ **否**（链路未到达 Shadow） |
| run_id 唯一 | ✅ `20260721_105710_77ea9469`，与历史无冲突，未复用 preflight 的 id |
| price_config_version 存在 | ✅ 每条账本事件都有 |
| hash 为 SHA-256 / 64 位 | 不适用（无 ToolResult provenance 落盘） |
| API key | ✅ 无 |
| Authorization / Cookie | ✅ 无 |
| `.env` | ✅ 无 |
| 用户主目录绝对路径 | ✅ 无 |
| PHI | ✅ 无 |
| 未脱敏 Prompt | ✅ 账本只存元数据 |
| 未脱敏 `run.json` 入 Git | ✅ 否（`runs/` 已被 .gitignore 覆盖，已用 `git check-ignore` 证明） |
| 原始账本 jsonl 入 Git | ✅ 否（`pilot/round2_results/*_ledger*` 已忽略；提交的是脱敏 JSON） |

对 `stage1_ledger.jsonl` 与 `stage1_metrics_*.json` 的扫描结果均为 **CLEAN**。

## 8. 金丝雀门槛判定

| 门槛 | 结果 |
|---|---|
| 任务正常完成 | ❌ **未完成** |
| Manifest 成功生成 | ❌ **未生成** |
| Shadow 无运行异常 | ⚠️ 未运行（非异常，但无数据） |
| 未授权工具执行 = 0 | ✅ |
| 敏感信息泄露 = 0 | ✅ |
| 伪造或错配引用 = 0 | ✅（无引用产生） |
| provider 重复请求 = 0 | ✅ |
| SDK 自动重试 = 0 | ✅ |
| usage_unknown = 0 | ✅ |
| 总费用 ≤ $1.50 | ✅ $0.0855 |
| 总调用 ≤ 21 | ✅ 18 |
| 模型 ID 与运行模式正确 | ✅ Opus 4.8 standard/global；flash 非思考 |
| relevance_unverified 未被当作确定证据 | ✅（无证据产生） |
| 无证据强结论 = 0 | ✅ |
| 旧答案未受 Shadow 异常影响 | ✅ |

**结论：A1 未通过金丝雀门槛**（前两项硬性未达成）。

## 9. 值得肯定的部分（同样如实记录）

- **硬闸门按设计工作**：第 17 次调用在**网络请求之前**被拒，provider 计数没有增加，
  费用没有失控（$0.0855，远低于 $1.50）。
- **角色分离生效**：账本里 planner 2 次、executor 16 次，标签正确、额度未互借
  —— A.6.3.3 的拆分在真实运行中得到验证。
- **fail-closed 正确**：执行失败没有被伪装成结论，最终答案是"未验证 / 证据不足"。
- **计价正确**：Opus 与 flash 分别按各自单价结算，不再像 A.6.2 那样全按 Opus 计。
- **零 usage 缺失、零可能重复计费、零未结算 reservation。**

## 10. 失败与异常清单（原样）

```
state.errors[0] = 执行出错：max_calls_per_role[executor]: 17 > 16
state.errors[1] = 执行出错：max_calls_per_role[executor]: 17 > 16
ledger rejected_before_invoke = ["max_calls_per_role[executor]: 17 > 16",
                                 "max_calls_per_role[executor]: 17 > 16"]
observations = 0 ; verification_results = 0 ; shadow = {}
```

## 11. 是否建议运行 B1

**不建议现在运行 B1。** 理由：A1 暴露的是**执行层的收敛问题**（ReAct 空转 16 轮未产出），
不是 B1 特有的因果陷阱问题。在 Executor 不能收敛的前提下跑 B1，很可能复现同一失败，
只是再花一次钱。

需要你先决定的两件事（我**没有**擅自处理）：

1. **Executor 为什么空转 16 轮**。可能是工具选择不当（A1 是精确 ID 检索，
   却没选中结构化的 `search_evidence`）、也可能是 ReAct 提示与工具返回的交互问题。
   诊断需要看 Executor 的消息历史 —— 那属于**只读分析**，不需要付费调用。
2. **每角色上限 16 是否合适**。A.6.2 实测的"典型 13 / 最坏 21"是基于 fake 模型的结构模拟，
   真实 Executor 的行为超出了该模型。调整上限属于**修改冻结协议**，必须走 v2.1 + 新 hash + 你批准。

在你批准之前：不重跑 A1、不运行 B1、不改代码、不改协议、不开始 Commit B、不切换最终裁决权。
