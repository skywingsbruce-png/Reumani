# Shadow Pilot Round 2 — A1 rerun under v2 + Addendum 1

> **A1-rerun 未完成。** 附录 1 的内层护栏 `max_tool_rounds=8` 先于 v2 的 16 次上限触发，
> Executor 停止，链路未到达 Verifier / Claim extractor / Shadow / 科研 Manifest。
> 触发第五部分停止条件「loop guard 触发」→ **已封存：不修代码、不重跑、不运行 B1。**
>
> **但本次拿到了 A.6.4 缺失的关键证据**，见 §3。
>
> 本文件**不覆盖** `A1-original`。两次并列，见 §7。

## 1. 运行身份与基线

| 项 | 值 |
|---|---|
| 标签 | **A1-rerun-v2-addendum1** |
| run_id | **`20260721_224715_12339e0e`**（全新，未复用 original / preflight） |
| 条件 | v2 + Addendum 1 |
| dev / public HEAD | `549d82c` / `5b4cb7b` |
| CI 基线 | Run #35 success 9/9；pytest 475 passed / 2 deselected |
| v1 hash | `5d166bce…f54f0f22` **未变** |
| v2 hash | `c76f5894…d66696` **未变** |
| Addendum 1 hash | `de3afcdd…ca7f3` **未变** |
| A1-original 现场 9 个文件 | **全部未变** |
| preflight | 19/19 通过 + 附录专项 14/14 通过 |
| 耗时 | 48.0 s（上限 600 s） |

## 2. 四角色调用与费用

| 角色 | 调用 | 输入 | 输出 | 费用 |
|---|---|---|---|---|
| Planner (`claude-opus-4-8`) | **2** | 1,651 | 2,150 | $0.070260 |
| Executor (`deepseek-v4-flash`) | **9** | 17,102 | 778 | $0.002612 |
| Verifier | **0** | — | — | — |
| Claim extractor | **0** | — | — | — |
| **合计** | **11** / 21 | 18,753 | 2,928 | **$0.072872** |

- 单题上限 $1.50 → 余量 **$1.427128**
- `usage_unknown` **0**；`provider_may_have_billed` **0**；**未结算 reservation 0**
- SDK 重试 **0**；Pilot 重试 **0**；`rejected_before_invoke` **0**（本次是护栏在工具轮次层停的，不是预算闸门）
- 所有事件 `price_config_version` = `2026-07-20.1`

## 3. 【关键】四类工具事件 —— A.6.4 证据缺口已解决

```
selected : list_skills, query_data_lake, retrieve_resources, search_evidence, search_literature
requested: search_evidence ×2, search_literature ×7   （共 9 次）
executed : []                                          ← 空
observed : []                                          ← 空
requested_not_executed: 9 项
unauthorized_executed: []      requested_outside_selected: []
```

逐次 Executor 模型响应（脱敏元数据，来自新增轨迹）：

| # | finish_reason | tool_calls | tool_name | content_len | next_node | invalid |
|---|---|---|---|---|---|---|
| 1 | `tool_calls` | 1 | search_literature | 75 | tools | 0 |
| 2 | `tool_calls` | 1 | search_literature | 66 | tools | 0 |
| 3 | `tool_calls` | 1 | search_literature | 33 | tools | 0 |
| 4 | `tool_calls` | 1 | **search_evidence** | 45 | tools | 0 |
| 5 | `tool_calls` | 1 | **search_evidence** | 0 | tools | 0 |
| 6–9 | `tool_calls` | 1 | search_literature | 0 | tools | 0 |

### 对 A.6.4 推断的裁决

A.6.4 的推断是「模型每轮产生 tool_calls **且工具成功执行**」。现在可以拆开判定：

| 分句 | 判定 | 依据 |
|---|---|---|
| 模型每轮产生结构化 tool_calls | ✅ **证实** | 9/9 次 `finish_reason=tool_calls`、`tool_calls_count=1`、`invalid_tool_calls=0`、`next_node=tools` |
| 工具成功执行 | ❌ **未获证实，且轨迹显示相反** | `executed=[]`、`observed=[]`、零 ToolMessage |

这也与 A1-original 的 `tool_trace` 只有 `selected`、没有 `called` **首次统一**了——
之前那处"尚未统一"的矛盾，现在指向同一个事实：**工具没有被执行**。

> ⚠️ **仍未定论的部分（不得越过证据）**：工具是**真的从未运行**，还是**运行了但工具回调未触发**。
> 我做了一次零成本离线探针试图区分，但探针因夹具错误提前中止，**不足以定论**。
> 本轮按第五部分已停止，不再深挖。这是下一步诊断的首要问题。

## 4. PMID / DOI 结果

| 检查项 | 结果 |
|---|---|
| A1 路由为 `exact_id` | ✅（pmids=`['41657283']`，dois=`['10.1080/03009742.2024.2302553']`） |
| `search_evidence` 进入允许工具集 | ✅（5 个工具中含它） |
| 模型确实请求了 `search_evidence` | ✅ 2 次（第 4、5 轮） |
| PMID 走精确查询 → `exact_hit` | ❌ **未发生**：工具未执行，无检索状态 |
| DOI 走精确查询 → `zero_hits` | ❌ **未发生**：同上 |
| 用语义候选冒充 DOI 命中 | ✅ **未发生**（没有任何候选被产出） |
| `zero_hits` 被说成"该领域没有研究" | ✅ **未发生**（无输出结论） |

**路由修复本身生效了**（A1-original 时 `search_evidence` 根本不在工具集里，本次在，且被模型请求）。
但因为工具未执行，精确 ID 的命中/未命中语义**这一轮仍未被验证**。

## 5. Loop guard

| 项 | 值 |
|---|---|
| tool_rounds | **8** / 上限 8 |
| distinct_signatures | 8（8 个签名互不相同） |
| 重复调用 warning | **无**（参数每轮不同，未误判） |
| cycle 阻断 | **未触发** |
| no_progress | **未触发**，`rounds_without_progress=0`，`progress_signal_count=9` |
| 触发项 | `loop_guard_triggered: max_tool_rounds (limit=8)`，两次（每个外层 iteration 各一次） |

护栏按设计工作：在**第 9 个工具轮次执行前**阻断，Executor 调用停在 9 次（A1-original 是 16 次）。

> **我自己实现的一处弱点（如实记录）**：`no_progress` 用的进展信号是**请求调用的签名**，
> 参数每轮不同就算"有进展"，所以 9 轮下来 `progress_signal_count=9`、从未触发 —— 
> 而实际上**一个工具结果都没有**，是彻底的零进展。
> 进展信号应当基于**工具结果 hash / Evidence ID**（即执行侧），而不是请求侧。
> 这是附录 §4 定义与我实现之间的偏差，需要修正（本轮不改）。

## 6. 科研输出

| 指标 | 值 |
|---|---|
| Planner 计划 | ✅ 结构化、3 步、通过 schema 校验 |
| structured / legacy ToolResult | 0 / 0（无工具执行） |
| EvidenceCard | **0** |
| Claim | **0** |
| Claim–Evidence 关联率 | 不适用 |
| 无证据强结论 | **0** |
| 未授权工具执行 | **0** |
| 最终答案 | `⚠️ 未验证 / 证据不足（no_verification）` —— fail-closed 正确 |

## 7. old / shadow

Shadow **未运行**（链路未到达）。`old_passed` / `shadow_passed` / `agree` / `divergence` /
`shadow_status` / `adversarial_not_run` / `human_review` 全部**无数据**，不做推测。
**Shadow 异常影响旧答案 = 否**（Shadow 未执行；最终答案由旧 verify 的 fail-closed 分支决定）。

## 8. 引用真实性

本次**未产生任何 PMID / DOI 引用**（0 张 EvidenceCard、最终答案无结论），
因此**无引用可核验**，伪造引用 = **0**。

冻结题目里两个标识符的权威核验结论沿用 A.6.4（本轮未重复调用）：
PMID `41657283` 真实存在（Europe PMC + PubMed 两来源一致）；
DOI `10.1080/03009742.2024.2302553` 在 Europe PMC / Crossref / doi.org handle **三处均不可解析**。

## 9. Manifest 与 Trace 安全

| 项 | 结果 |
|---|---|
| 科研结论 Manifest | **未生成**（链路未到达 Shadow） |
| **失败诊断 Manifest** | ✅ **已生成**（`manifest_kind=failure_diagnostic`，`shadow_status=not_run_due_to_upstream_failure`，`claims`/`evidence_cards` 恒空） |
| Trace 文件 | ✅ `stage1_A1_executor_trace.jsonl`，7,550 字节，10 条事件，SHA-256 `95cd4f442066e712…` |
| run_id 唯一 | ✅ 与 original / preflight 均不同 |
| price_config_version | ✅ 每条账本事件都有 |
| API key / Authorization / Cookie / `.env` / 绝对路径 / PHI | ✅ 审计产物与 Trace 扫描 **全部 CLEAN** |
| 完整 Prompt / 正文 / 参数 | ✅ 未保存（只有长度、SHA-256、工具名、参数键名、白名单元数据） |
| 未脱敏 `run.json` / 原始账本入 Git | ✅ 否（`.gitignore` 覆盖） |

## 10. 并列比较（**不得互相覆盖**）

| 指标 | A1-original | A1-rerun-v2-addendum1 |
|---|---:|---:|
| 条件 | v2 原始 | v2 + Addendum 1 |
| run_id | `20260721_105710_77ea9469` | `20260721_224715_12339e0e` |
| 完成状态 | 未完成 | 未完成 |
| Planner 调用 | 2 | 2 |
| Executor 调用 | 16 | **9** |
| 工具轮次 | unknown | **8**（上限 8） |
| Verifier 调用 | 0 | 0 |
| Claim 调用 | 0 | 0 |
| 总调用 | 18 | **11** |
| 费用 | $0.085541 | **$0.072872** |
| requested 工具 | unknown | **search_evidence ×2, search_literature ×7** |
| executed 工具 | unknown | **[]** |
| observed 工具 | unknown | **[]** |
| EvidenceCard | 0 | 0 |
| Claim | 0 | 0 |
| 科研 Manifest | 无 | 无 |
| **失败诊断 Manifest** | 无 | **有** |
| **Executor 轨迹** | 无 | **有（逐次）** |
| Shadow | 未运行 | 未运行 |
| 失败原因 | Executor **角色上限 16** | **loop_guard: max_tool_rounds 8** |

两次**都未完成**，但失败点前移、成本下降、且第二次**产出了第一次完全缺失的证据**。
按附录 §7：这**不构成**对第一次的"重测通过"，只是一次条件不同的新观测。

## 11. 成功标准判定

第六部分 13 项中：

- ❌ Executor 在限制内终止（被护栏阻断，非自然终止）
- ❌ 精确 PMID/DOI 结果正确区分（工具未执行）
- ❌ Verifier 运行 / ❌ Claim extractor 运行 / ❌ Shadow 运行 / ❌ 科研 Manifest 生成
- ✅ Planner 完成
- ✅ 未授权工具执行 0 / ✅ 伪造引用 0 / ✅ 敏感信息泄露 0
- ✅ 总费用 $0.0729 ≤ $1.50 / ✅ 所有 reservation 已结算 / ✅ Shadow 未改变旧裁决权

→ **A1-rerun 未达成完成标准。**

## 12. 全部 warning 与 failure（原样）

```
run.json errors: ["执行出错：max_tool_rounds", "执行出错：max_tool_rounds"]
loop_guard_triggered ×2: {reason: "max_tool_rounds", limit: 8, round: 8}
repeat_warning: 无        cycle_warning: 无        no_progress: 未触发
rejected_before_invoke: []   usage_unknown: 0   provider_may_have_billed: 0
open_reservations: 0
```

## 13. 现场完整性的一处必须说明

运行后复核 A1-original 现场时，**`pilot/round2_results/stage1_ledger.jsonl` 的整文件
SHA-256 变了** —— 因为 rerun 与 original 共用同一个 **append-only** 账本，rerun 的事件
被追加在后面（历史轮次明确要求"不删除历史账本"）。

**A1-original 的账本内容逐字节完好**，已证明：

```
账本前 38 行（= original 结束时的全部内容）
  SHA-256 = 482f9b1e365be79f9ca82dc34cb8c593dfeea10cf61ba7ae5203314bd0ec8353
  大小     = 9935 字节
与 A1_scene_hashes.json 记录的 original 整文件 hash 与大小 **完全一致**。
```

其余 8 个现场文件 hash **全部未变**。

由此导致 **2 项测试在本机失败**（`test_a1_scene_files_unmodified`、
`test_a1_scene_untouched_by_addendum`）：它们比对**整文件** hash，不理解 append-only 共享账本。
本机 **473 passed / 2 failed**。CI 干净检出里账本被 `.gitignore` 排除、文件不存在，
两项测试跳过该文件，因此 **CI 预期为绿**。

本轮不允许改代码，故**未修复**。建议下一个最小 commit 把现场校验改为
"**前缀不变 + 只允许追加**"，而不是整文件 hash 相等。

## 14. 性质声明

即便完成也只代表 A1 金丝雀完成，不代表 Reumani 整体性能达标。这 12 道题仍是开发期 Pilot，
不是金标准 benchmark，不得用于公开宣称性能。
