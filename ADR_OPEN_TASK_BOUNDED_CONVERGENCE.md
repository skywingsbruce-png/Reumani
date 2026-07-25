# ADR: 开放式科研任务的有界收敛执行契约

- 状态：**提议（Proposed）** —— A.7.3 只读复盘产出，等待批准后再实现。
- 日期：2026-07-24
- 背景：B1 canary 在开放 ReAct 上不收敛，撞 loop_guard 而 fail-closed（`SHADOW_PILOT_ROUND2_
  B1_CANARY_RESULT.md` + 勘误 `SHADOW_PILOT_ROUND2_B1_AUDIT_ERRATUM.md`）。
- 约束：本 ADR **不改任何生产代码/协议/附录/题目/评分/预算**；仅记录架构决策。零付费。

---

## 1. 当前问题
带 ID 的精确核验任务（A1）已由确定性 exact-ID 路径解决。**开放式机制/因果任务（B1）仍走
ReAct，且不收敛**：Executor 反复检索、把不同结果字节当“进展”，但从不在执行内构建证据、
从不进入综合与核查，最终只能靠 loop_guard 兜底 fail-closed。

## 2. 真实调用链（逐阶段，file:func）

| 阶段 | 位置 | 输入契约 | 输出契约 | 状态存放 | 失败态 | B1 是否进入 | 权威 |
|---|---|---|---|---|---|---|---|
| classifier | `pilot/query_classifier.py:classify` | question | "open"/"exact_id" | — | — | 是（→open） | 分流 |
| planner | `planner.py:make_plan`/`parse_and_validate_plan` | q+allowed_tools | ResearchPlan(schema) | `AgentState.research_plan` | PlanValidationError | 是（2 步计划） | 计划 |
| tool selection | `tool_registry.select_tool_names`/`apply_approvals`（`ssc_a1.run_agent:233-236`） | q | allowed_tools | `AgentState.allowed_tools`/`tool_trace` | — | 是 | 权限 |
| ReAct executor | `ssc_a1.execute:113`→`ssc_skill_agent.build_skill_agent:456`→`create_agent` | plan+tools | (final_text, messages) | 局部 messages | 抛异常→retry | 是（9 次调用） | 执行 |
| tool 生命周期 middleware | `pilot/tool_middleware.py:LifecycleMiddleware.wrap_tool_call` | ToolCallRequest | ToolMessage | `LifecycleReconciler` | fail-closed | 是（8 执行） | 生命周期 |
| ToolMessage→reconciler | `pilot/lifecycle.py:reconcile_messages:98`（经 `exec_wiring.ExecutorHooks.pre_invoke`） | messages | observed + progress 信号 | `reconciler.calls` | INCONSISTENT | 是 | 观测权威 |
| loop guard | `pilot/loop_guard.py:before_tool_round:79`/`record_progress:58` | tool+args / signals | 继续或 LoopGuardTriggered | `guard` | max_tool_rounds/no_progress/repeat/cycle | 是（max_tool_rounds） | 循环安全 |
| evidence building | `shadow.py:build_evidence_cards:112`（**仅在** `shadow.run_shadow:194`） | tool_events | EvidenceCard[] | `AgentState.shadow` | — | **否（未运行）** | 证据构建 |
| verifier | `ssc_a1.verify:138` | answer+cards | VerificationResult | `AgentState.verification_results` | fail-closed | **否** | 最终裁决 |
| claim extractor | `shadow.build_claim_extractor`/`extract_claims:171` | final_text+cards | Claim[] | `shadow.claims` | 结构化错误 | **否** | Claim |
| claim graph | `claim_graph.ClaimEvidenceGraph.adjudicate` | claims+cards | judged | `shadow.claims` | — | **否** | 裁定 |
| shadow | `shadow.run_shadow:194` | messages/events | RunManifest | `AgentState.shadow` | 结构化失败 | **否** | 影子对比 |
| final answer | `ssc_a1.run_agent:286-293` | verify 结果 | 用户可见答案 | `AgentState.final_answer` | no_verification 兜底 | 是（fail-closed） | 呈现 |

**关键结构缺陷**：
- **证据构建只在 `shadow.run_shadow`（execute 之后）** —— ReAct 执行**期间没有** EvidenceCard，
  没有证据累加器；Executor 无法知道自己已经拿到哪些文献。
- **重复实现/权威分裂**：工具事件在 Trace、Lifecycle、`shadow.extract_tool_events` 各有一套；
  `metrics.execution.tool_calls` 从 `state.shadow` 汇总（Shadow 未运行→0），与 Lifecycle=8 冲突。
- 只有 Trace/Lifecycle 里有工具执行数据；`AgentState` 里没有结构化 Observation/EvidenceCard，
  直到 Shadow 才补建——而 B1 从未到 Shadow。

## 3. 七次文献检索为何没有收敛（只用冻结 B1 Trace）

冻结 Trace（`stage1_B1_executor_trace.jsonl`）事实：

1. 7 次 `search_literature` + 1 次 `query_data_lake`（第 8 轮），第 9 次请求被护栏拦截。
2. 7 次查询 arguments_hash 各不相同 → **模型不断改写关键词**（合理信息增益无法从 hash 判断，
   标 `unknown`；但**每次 query 不同**是确定的）。
3. `search_literature` 全部返回 **legacy 字符串**（`structured:"legacy"`），result_length =
   22906/5188/4458/4216/3196/4462/5205 —— **有真实文献内容（数千字符），但无结构化 artifact**。
4. 因是 legacy，**结果里可能含 PMID/DOI/摘要（文本），但没有结构化 provenance/研究设计字段**
   （具体正文不从 hash 猜，标 `unknown`；但 artifact 缺失是确定的）。
5. **artifact 未进入 ToolMessage**（legacy 工具无 artifact）。
6. **`evidence_build` 未在执行内被调用**（只在 Shadow）。
7. **EvidenceCard=0**：Shadow 未运行；执行内根本没有构卡步骤。
8. **Executor 不知道已获得哪些文献**：无证据累加器，历史结果只是堆在上下文文本里
   （input_tokens 1434→20209，一路膨胀）。
9. **Planner 的 success_criteria 运行时未被检查**：`AgentState` 无逐步状态机，success_criteria
   仅存在于计划文本（`render_plan_text`），程序不校验。
10. **stop_conditions 仅是计划文本**，非程序条件。
11. 第一步（检索）实际被重复执行 7 次（无步骤级预算）。
12. 第二步（corpus 核验）直到第 8 轮才被执行，随即撞满轮数上限。
13. **“不同 result hash”被当有进展**：`lifecycle.py:144-148` 注入 `observed_result:<content_hash>`，
    `loop_guard.record_progress` 见新 hash 即 reset no_progress（`loop_guard.py:62-66`）。7 次不同
    字节 → 7 个“进展”信号 → no_progress 永不触发，只剩 max_tool_rounds 兜底。
14. 疑似**上下文膨胀 / 结构化数据不可见**：20k input tokens 且全为非结构化文本，模型难以判断
    “已足够”（正文不可见，标 `unknown`，但 token 膨胀与 legacy-only 是确定的）。

**根因树**：
- 直接原因：撞 `max_tool_rounds=8`。
- 中层原因：`no_progress` 用 **transport 新颖度**（结果字节 hash）当科研进展 → 永不收敛。
- 深层原因：**执行内无证据模型**（无 EvidenceCard/累加器/步骤状态机），success_criteria/
  stop_conditions 不是程序条件；证据构建被推迟到 execute 之后的 Shadow。
- 底层原因：开放任务把“是否继续搜索”交给 LLM，缺少**有界、可终止、以证据为进展度量**的执行契约。

## 4. OpenTaskExecutionContract（只设计，不实现）

### 4.1 PlanStepState
字段：`step_id, objective, allowed_tools, call_budget, attempts, status, observations,
evidence_ids, success_criteria, completion_reason, remaining_gaps`。
状态：`pending → running → satisfied | insufficient | failed | blocked`。

### 4.2 EvidenceAccumulator（进展的新定义）
- **transport novelty**：结果字节不同（**仅审计**，不重置科研 no_progress）；
- **identifier novelty**：新增 PMID/DOI/GSE；
- **evidence novelty**：新增研究设计/时间方向/物种/干预/反证；
- **decision novelty**：足以改变 Claim 支持等级的新证据。
**只有 identifier/evidence/decision novelty 才能重置科研 no_progress。** 这直接修复 §3.13。

### 4.3 步骤级预算（候选值，待定；给依据与风险）
- 文献检索 ≤ 2 次/步（依据：B1 第 2 次即重复；风险：漏检长尾——用“换来源”而非“换关键词无限重试”缓解）；
- 数据湖查询 ≤ 1 次/步（确定性查表）；
- **同一工具换关键词不得无限重试**：达步骤预算即进入 satisfied/insufficient/failed，禁止空转；
- 数字不在本阶段定死；给候选 + 依据 + 风险，实现时再校准。

### 4.4 确定性状态机
`plan → collect → normalize → accumulate → assess_step → synthesize → verify → finish`。
**禁止模型自行决定是否永远继续搜索。** 每轮工具返回后，**程序**执行：
1) 解析 ToolResult；2) 记录 Observation；3) 构建/更新 EvidenceCard；4) 更新 EvidenceAccumulator；
5) 检查 success_criteria；6) 决定：完成本步 / 一次受限补充检索 / 标 insufficient；
7) 所有步骤终态后**强制进入 synthesis**。

### 4.5 受控 insufficient 结论（禁止空白/“还缺：[]”）
即使证据不足也必须产出结构化结果：`resolved_question, available_evidence, unsupported_claims,
causal_strength, missing_evidence, limitations, recommended_next_action`。修复 §1 勘误 #6。

### 4.6 因果校准契约
综合阶段必须区分：`association / temporal_evidence / dose_response / intervention_evidence /
genetic_instrumental / mechanistic_plausibility / reverse_causation / confounding / clinical_evidence`。
**缺时间方向或干预证据 → 不得输出确定性因果结论。**

### 4.7 遥测单一权威（修复 §1 勘误 #5）
- Lifecycle 权威 `requested/executed/returned/observed`；
- EvidenceAccumulator 权威 evidence progress；
- Gate 权威模型调用与费用；
- **Final RunMetrics 只从上述权威对象汇总，不自行重复计数**。
- 迁移：**淘汰** `metrics.execution.tool_calls`（从 `state.shadow` 汇总的旧口径），改由 Lifecycle
  提供 `tool_calls`，消除“Lifecycle=8 / metrics=0”冲突。

### 4.8 结构化文献工具前置条件（A.7.3.1 补充，**硬前置**）
**开放任务不能在 legacy `search_literature` 字符串上直接建立 EvidenceAccumulator。** 生产实现前
必须二选一（否则 EvidenceAccumulator/构卡缺少可靠字段，novelty 分级失去依据）：

- **方案 A**：把 `search_literature` 改为 `content_and_artifact`，返回结构化文献记录；或
- **方案 B**：建立**严格、可验证、fail-closed** 的 `LiteratureToolResultAdapter`（离线重放已用
  `literature_adapter` 演示 fail-closed 语义）。

结构化文献记录（`litrec-v1`）至少含：`pmid/doi`、`title/year/journal`、`abstract 或
content_level`、`study_design`、`species`、`longitudinal/interventional 标记`、`source`、`query`、
`provenance`、`content_hash`、`schema_version`。**无法可靠提取的字段必须置 `unknown`，
不得由 LLM 猜测**；整条无法解析时 fail-closed（不构卡、不猜 ID）。

> A.7.3.1 离线重放的 `literature_adapter` 对非 `litrec-v1` / 无 ID 的输入抛 `LiteratureParseError`，
> 是该前置条件的可执行验证锚点。

## 5. 三方案比较

| 维度 | A 纯 ReAct 只改 Prompt | B ReAct + Step Controller + EvidenceAccumulator | C 确定性工作流替代开放 ReAct |
|---|---|---|---|
| 科研可靠性 | 低（仍靠 LLM 自控收敛） | 中-高（程序守门进展/预算） | 高（程序编排，LLM 只推理） |
| 可解释性 | 低 | 高（逐步状态+证据轨迹） | 高 |
| 工具扩展性 | 高 | 高 | 中（需为每类收集器建契约） |
| 成本 | 不可控（易空转） | 可控（步骤预算） | 最低且稳定 |
| 实现复杂度 | 最低 | 中 | 中-高 |
| 对现有代码侵入 | 最小 | 中（新增 Step Controller，复用现有 middleware/gate/shadow） | 大（新执行器 + 分流） |
| 与 Biomni 相似度 | 低 | 中 | 高（资源/执行程序守门，LLM 高层推理） |
| SSc/Rheum 专科适用 | 差（因果题易空转/过度表述） | 好 | 好 |

**推荐：方案 B（ReAct + Step Controller + EvidenceAccumulator）。** 理由：既保留 ReAct 对开放
问题的灵活性，又用**程序**把“进展/预算/收敛/构卡”从 LLM 手里收回，直接修复 no_progress 误判与
执行内无证据模型两个根因；复用现有 middleware/lifecycle/gate/shadow，侵入可控。方案 C 更彻底但
侵入大、灵活性下降；方案 A 不触根因。**不默认“更自由=更好”。** 若后续开放任务证明仍不稳定，
再向 C 迁移。

## 6. 离线状态机重放结果（A.7.3.1：可执行 + 自动化测试）
**可执行重放源码 + 自动化测试**：`tests/test_open_task_convergence_replay.py`（离线，无模型/网络，
所有 fixture 明确标 FAKE，不导入生产执行链），产物 `pilot/round2_results/B1_state_machine_replay.json`
由该模块**运行时生成**（非预生成）。10 个 pytest 全通过，真实演示：

- **两次** `search_literature`：第 1 次结构化命中（identifier+evidence+decision novelty →
  scientific_progress=true）；第 2 次同证据、transport 排序不同（transport_novelty=true，
  identifier/evidence/decision=false，**scientific_progress=false**）；`attempts=2`，**不允许第三次**；
  timeline 明确含两次文献检索；正向字段 `transport_only_does_not_reset_scientific_progress=true`。
- **四级 novelty 语义**：新 PMID→identifier；同 PMID 不同字节→仅 transport；同 PMID 新增纵向设计→
  evidence；新增干预证据→decision（causal_tier 升到 intervention_supported）。
- **legacy 无法解析 → fail-closed**（`literature_adapter` 抛 `LiteratureParseError`）。
- data lake `zero_hits`→insufficient 且标注“≠该领域无研究”；受控 insufficient 结论**非空**、
  `missing_evidence` 非空、**不出现“还缺：[]”**。
- **五个 fake 阶段真实被调用**（CallCounter）：`synthesize→verify→claim_extract→claim_graph→shadow`
  顺序正确、各 call_count=1；Verifier **看得到 EvidenceCard** 并判 `insufficient_for_causal`；
  Claim 只引用已有 evidence_id；Shadow **不新建 EvidenceCard**。
- **遥测单一权威一致**：Lifecycle `requested=executed=tool_returned=observed=3`、`failed=0`；
  `run_metrics.tool_calls` 取自 Lifecycle、`evidence_cards` 取自 Accumulator、`stage_calls` 取自
  CallCounter、`timeline_len=3` 三者一致。

**这只是架构模拟，不代表 B1 已通过。**

## 7. 决策、迁移、测试、风险

**决策**：采用方案 B 的 OpenTaskExecutionContract；exact-ID 任务保持现状；机制类走带 Step
Controller 的 ReAct；旧 Verifier 裁决权不变；Shadow 仍只记录对比。

**修正后的实现顺序（A.7.3.1；每步独立、可回滚，均需单独批准，本轮不实现）**：
1. OpenTask / PlanStep / EvidenceAccumulator **schema 契约**（纯数据契约 + 单测）。
2. `search_literature` **结构化 artifact（方案 A）或严格 adapter（方案 B）**（见 §4.8 硬前置）。
3. **EvidenceAccumulator 与 novelty 分级**（transport/identifier/evidence/decision）。
4. **执行内 EvidenceCard 构建**（每轮工具返回即构卡/更新累加器，复用 evidence_build）。
5. **Step Controller、步骤预算和终态转换**（PlanStepState 状态机）。
6. **synthesis 与 controlled insufficient conclusion**（修复“还缺：[]”）。
7. **因果校准检查**（association/temporal/intervention/… 分级，缺时序/干预不升因果）。
8. **遥测单一权威**（淘汰 `metrics.execution.tool_calls`，改由 Lifecycle 汇总）。
9. **fake 开放任务完整端到端验收**（离线，到 synthesize→verify→claim→claim_graph→shadow）。
10. **起草并冻结 Addendum 3**。
11. **经批准后才允许一次真实 B1 重跑。**

**关键顺序约束（A.7.3.1）**：**不要先修改 `no_progress` 再补证据结构**。在缺少结构化证据
（步骤 2-4）之前，系统无法判断 scientific progress，单独改 `no_progress`（旧步骤 2）会让守卫
失去可靠的进展依据。因此 novelty/no_progress 的修正必须**排在结构化文献工具 + 执行内构卡 +
EvidenceAccumulator 之后**（即步骤 3 内含 novelty 分级，依赖步骤 2 的结构化记录）。

**测试计划**（零付费、离线/fake）：novelty 分级；no_progress 只被 evidence novelty 重置；步骤预算
达上限即终态；受控 insufficient 非空且无“还缺：[]”；因果题缺时序/干预→association 不升因果；
zero_hits≠无研究；遥测三源一致；开放任务端到端 fake 链到 synthesis/Verifier/Claim/Shadow；
exact-ID 与既有 A1/B1 现场不回归。

**风险与回滚边界**：每步纯新增或局部替换，独立 `git revert`；步骤预算过紧可能漏检——用“换来源”
而非“换词无限重试”缓解，数字待真实校准；不改协议题目/评分/预算。

**向后兼容**：exact-ID 路径、旧 Verifier 裁决权、Shadow 只记录、A1 全部历史现场**保持不变**；
仅开放任务的执行层增加程序守门。

**是否需要新协议附录**：**倾向需要 Addendum 3**冻结“开放任务有界收敛执行契约”（状态机/进展定义/
步骤预算/受控 insufficient/因果校准/遥测权威）——但属下一阶段，**本轮不起草、不修改 v2/Addendum 1/2**。

---
**本轮仅提交：ADR + 审计勘误 + 零付费离线分析产物。不实现推荐架构，不运行 B1，不开始 Commit B。**
