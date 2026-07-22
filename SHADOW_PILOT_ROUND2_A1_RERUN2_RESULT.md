# Shadow Pilot Round 2 — A1-rerun2 结果

**结论：A1-rerun2 未完成（第三次失败）。** 失败模式与 A1-rerun1 相同（Executor 触发
loop guard），但这一次生命周期**完全可观测、逐 tool_call 可信**——这正是 A.6.6.3 要证明的。
按协议 v2 附录与本次指令第八条：第三次仍失败 → **建议暂停 Round 2 并做架构复盘**，
不加高上限、不打补丁重跑、不进行第四次尝试、不运行 B1。

- 运行基线：dev `7199081` / public `fcc8c2f` / CI #40 success 9/9
- run_id：`stage1_A1_17750`（全新，≠ A1-original、≠ A1-rerun1 `stage1_A1_131063`、≠ preflight uuid）
- 真实付费调用：11（planner 2 + executor 9）；费用 **$0.086985**（≤ 单题 $1.50）
- 无 SDK 重试、无 Pilot 重试、无未结算 reservation、无未授权工具执行、无敏感信息泄露、无编造引用

---

## 1. A1-rerun2 是否完成

**否。** Executor 在第 8 个工具轮触发 `loop_guard(max_tool_rounds=8)`，系统 fail-closed 到
“未验证 / 证据不足（no_verification）”，未进入旧 Verifier / Claim extractor / Shadow /
科研 Manifest。生成了**失败诊断 Manifest**（`human_review=true`，`shadow_status=
not_run_due_to_upstream_failure`），未生成科研 Manifest。

Part 七 成功条件逐项核对：

| 条件 | 结果 |
|---|---|
| Planner 成功 | ✅ 2 次调用，结构化计划，3 步 |
| Executor 在限制内正常结束 | ❌ 触发 loop guard（max_tool_rounds=8），非正常收敛 |
| 工具真实执行 | ✅ 8 个 tool_call 真实执行 |
| 生命周期逐 ID 一致 | ❌ 1 项 `requested_not_executed`（第 9 个 tool_call 被护栏拦截，见 §3） |
| PMID exact_hit | ❌ 未在可核验证据中体现（执行器未收敛） |
| DOI zero_hits | ❌ 同上 |
| EvidenceCard 只含有效来源 | —（0 张卡） |
| Verifier 实际运行 | ❌ 0 次 |
| Claim extractor 实际运行 | ❌ 0 次 |
| Shadow 实际运行 | ❌ not_run_due_to_upstream_failure |
| 科研 Manifest 生成 | ❌（改为失败 Manifest） |
| 旧 Verifier 仍控制最终答案 | 部分：兜底“未验证”是旧链 fail-closed 行为，但旧 Verifier 因上游失败未实际运行 |
| 无伪造引用 | ✅ |
| 无敏感信息泄露 | ✅ |
| usage 全部可结算 | ✅ 0 未结算 reservation |
| 总费用 ≤ $1.50 | ✅ $0.086985 |
| 无 SDK 重试 | ✅ retries=0 |

多数条件未满足 → **未完成**。

---

## 2. 四角色调用与费用

| 角色 | 模型 | 调用数 | 上限 |
|---|---|---:|---:|
| Planner | claude-opus-4-8（standard/global） | 2 | 2 |
| Executor | deepseek-v4-flash（thinking disabled） | 9 | 16 |
| Verifier | claude-opus-4-8 | 0 | 2 |
| Claim extractor | deepseek-v4-flash | 0 | 1 |
| **总计** | | **11** | 21 |

- 费用：`actual_usd = $0.086985`，`reserved_open_usd = $0.0`，`committed_usd = $0.086985`
- calls_by_model：`{claude-opus-4-8: 2, deepseek-v4-flash: 9}`
- 0 retries，0 rejected_before_invoke，未触发任何预算硬闸门

---

## 3. 每个 tool_call_id 的四阶段关联

Executor 实际执行 8 个 tool_call，**每一个** `tool_start → tool_returned → observed`
都在同一 `tool_call_id_hash` 下闭合（权威 tool_call_id 来自 middleware
`request.tool_call["id"]`）：

| # | tool_name | tool_call_id_hash | requested | executed | tool_returned | observed | failed | ToolMessage | artifact schema | EvidenceCard |
|--:|---|---|:--:|:--:|:--:|:--:|:--:|---|---|:--:|
| 1 | list_skills | `93eaf4a7` | 1 | 1 | 1 | 1 | 0 | success | legacy/none | 否 |
| 2 | search_literature | `39e1c9cc` | 1 | 1 | 1 | 1 | 0 | success | legacy/none | 否 |
| 3 | search_literature | `5c8b1485` | 1 | 1 | 1 | 1 | 0 | success | legacy/none | 否 |
| 4 | search_literature | `be4e605d` | 1 | 1 | 1 | 1 | 0 | success | legacy/none | 否 |
| 5 | search_evidence | `2cf0cf97` | 1 | 1 | 1 | 1 | 0 | success | toolresult-v1 | 否 |
| 6 | search_evidence | `c8cd575f` | 1 | 1 | 1 | 1 | 0 | success | toolresult-v1 | 否 |
| 7 | search_literature | `b6088576` | 1 | 1 | 1 | 1 | 0 | success | legacy/none | 否 |
| 8 | search_literature | `edbb8ab4` | 1 | 1 | 1 | 1 | 0 | success | legacy/none | 否 |
| 9 | search_literature | `1405ef84` | 1 | **0** | 0 | 0 | 0 | —（护栏拦截） | — | 否 |

聚合：`requested=9, executed=8, tool_returned=8, observed=8, failed=0`。

**唯一不一致 `requested_not_executed`（#9）的成因**：Executor 第 9 次模型响应又请求了一个
`search_literature`，但 `loop_guard(max_tool_rounds=8)` 在该 tool_call 执行**之前**触发，
护栏 fail-closed 阻断了这一轮。因此“requested=1 / executed=0”是**护栏正确拦截**的忠实记录，
不是关联丢失。对比 A1-rerun1：那次 `executed/observed` 全为 0 是**观测性缺陷**（工具确实执行了
但旧接线没记录）；本次 8 个已执行 tool_call 全部逐 ID 闭合、可信——A.6.6.3 关联机制得到验证。

EvidenceCard 全部为“否”：执行器在收敛前触发护栏，链路 fail-closed，未进入 Shadow 构卡阶段。

---

## 4. PMID / DOI 结果（诚实陈述）

**未达成可核验的 exact_hit / zero_hits。** Executor 的行为是：反复调用 `search_literature`
（非结构化工具）与 `search_evidence`（结构化工具），返回结果长度极短（`result_length=9`，
多为零命中/空返回，见 trace 的 `result_hash` 反复为 `39cbaa27…`），模型始终**未收敛**成一份
报告 PMID/DOI 标题/年份/命中方式的最终答案，在第 8 轮被护栏中止。

因此：
- 未构造任何 EvidenceCard；
- 未把未命中说成命中、未把排序候选说成精确命中、未编造标题/年份（最终答案为兜底“未验证”文案）；
- 期望的“PMID 41657283 exact_hit / DOI 10.1080/03009742.2024.2302553 zero_hits”这一科学行为
  **本次未被验证**——不是因为检索逻辑错误，而是执行器 agent 未能收敛使用工具。

---

## 5. EvidenceCard 来源

0 张。链路在 Shadow 之前失败，`evidence_from_artifact` 的 artifact 守卫未被触达。
（守卫本身在 A.6.6.3 已由真实 Shadow 调用方接入并通过测试；本次只是未走到该阶段。）

---

## 6. Verifier / Claim / Shadow 是否运行

- 旧 Verifier：**未运行**（0 次）——Executor 上游失败，未进入核查。
- Claim extractor：**未运行**（0 次）。
- Shadow：**未运行**，`shadow_status = not_run_due_to_upstream_failure`。
- old/shadow 结果：均为空（`old_passed=null`，`shadow_passed=null`，`comparison` 未生成）。

---

## 7. old / shadow 结果

不适用——两者都因上游 Executor 失败而未运行。最终对用户可见的答案是旧链 fail-closed 的
“未验证 / 证据不足（no_verification）”兜底文案；用户可见行为未改变，未切换裁决权。

---

## 8. Manifest 与 Trace 安全

- **失败诊断 Manifest**：`A1_rerun2_failure_manifest.json`
  （`manifest_kind=failure_diagnostic`，`primary_failure="执行出错：max_tool_rounds;
  执行出错：tool_lifecycle_inconsistent"`，`human_review=true`，`manifest_failure` 未覆盖
  `primary_failure`）。
- **Trace**：`A1_rerun2_executor_trace.json`（仅 run_id `stage1_A1_17750` 事件），
  内容仅 **hash + token 计数 + 工具名 + 时间戳**——无 prompt 正文、无工具参数明文、
  无摘要正文、无密钥、无 PHI、无绝对路径。
- 交付产物脱敏核验：5 个 JSON 的绝对路径命中数均为 0；引用核验
  `fabricated_citation_detected=false`。

---

## 9. 三次 A1 并列比较

| 指标 | A1-original | A1-rerun1 | A1-rerun2 |
|---|---:|---:|---:|
| commit（运行基线） | Stage1 冻结 | Addendum1 | dev `7199081` / public `fcc8c2f` |
| run_id | `stage1_A1_…`(原) | `stage1_A1_131063` | `stage1_A1_17750` |
| 完成状态 | 否 | 否 | **否** |
| Planner 调用 | 2 | 2 | 2 |
| Executor 调用 | 16 | 9 | 9 |
| Verifier 调用 | 0 | 0 | 0 |
| Claim 调用 | 0 | 0 | 0 |
| 总调用 | 18 | 11 | 11 |
| 费用 | $0.085541 | $0.072872 | $0.086985 |
| requested | unknown | 9 | 9 |
| executed | unknown | **不可信的 0** | **可信的 8** |
| observed | unknown | **不可信的 0** | **可信的 8** |
| EvidenceCard | 0 | 0 | 0 |
| Shadow | 未运行 | 未运行 | 未运行 |
| Manifest | 无 | 失败诊断 | 失败诊断 |
| 失败原因 | role cap（executor 16） | loop guard | loop guard（max_tool_rounds）+ requested_not_executed |

**软件条件不同，不隐藏任何一次失败：**
- A1-original：executor 打满 16 次角色上限；生命周期不可观测。
- A1-rerun1：loop guard 中止；`executed/observed=0` 是**观测缺陷**（工具执行了但没记录）。
- A1-rerun2：loop guard 中止（同一失败模式），但 A.6.6.3 的权威 tool_call_id 关联让
  `executed=8 / observed=8` **可信**，第 9 个 tool_call 的 `requested_not_executed` 被精确定位为
  护栏拦截。**观测缺陷已修复；暴露出的真问题是 Executor（deepseek-v4-flash）在本题上
  反复检索而不收敛。**

---

## 10. 全部异常

1. `loop_guard_triggered`，reason=`max_tool_rounds`，limit=8（第 8 轮）。
2. 生命周期不一致 1 项：`requested_not_executed`（tool `search_literature`，
   hash `1405ef84`）——护栏在其执行前拦截，属正确 fail-closed，非关联丢失。
3. Executor 行为异常（根因）：反复调用 `search_literature`/`search_evidence`，结果近空、
   不收敛，未产出最终报告。
4. 连带：Verifier / Claim / Shadow 未运行，未生成科研 Manifest（均为上游失败的正确后果）。

无未授权工具执行、无 orphan ToolMessage、无 duplicate_execution、无 trace_incomplete、
无 observation_trace_failed、无未结算 reservation。

---

## 11. commit / CI / 回滚

- 提交（仅脱敏产物，无任何代码/协议/附录/Prompt/工具改动）：
  `pilot(round2): record A1 rerun2 lifecycle-verified result`
- 交付文件：本报告 + `pilot/round2_results/A1_rerun2_{executor_trace,lifecycle_summary,
  failure_manifest,cost_summary,citation_check}.json`。
- CI：推送 public 后等待 CI 全绿。
- 回滚：`git revert <rerun2 commit>`（纯新增文档/产物，无破坏性变更）。

---

## 12. 是否建议运行 B1

**不建议。** 且按第八条：这是第三次 A1 且仍失败 → 正式建议 **暂停 Round 2，进入架构复盘**：

- 观测层已达标（A.6.6.3 让逐 tool_call 生命周期可信），但**执行器 agent 层未过关**：
  deepseek-v4-flash 在 A1 上不能可靠地用结构化 `search_evidence` 精确命中 PMID/排除 DOI 并收敛。
- 复盘应聚焦（不在本次改动）：Executor 的工具选择与收敛策略（为何偏好非结构化
  `search_literature`、为何不用精确 ID 命中后即停）、是否需要更强的 Executor 模型或
  计划-执行协议约束——而**不是**调高 loop guard / role cap 后立即重跑。
- 在完成架构复盘并获批新方案前：不运行 B1、不运行 Stage 2/3、不开始 Commit B、不做第四次 A1。
