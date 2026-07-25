# Shadow Pilot Round 2 — Protocol v2 Addendum 3

**开放式科研任务的有界收敛执行契约（Bounded Convergence Contract for Open Scientific Tasks）**

- 版本：Addendum 3
- 依赖：`SHADOW_PILOT_ROUND2_PROTOCOL_V2.md`（不修改）+ Addendum 1（不修改）+ Addendum 2（不修改）
- 冻结日期：2026-07-24
- 来源：A.7.3 复盘（`ADR_OPEN_TASK_BOUNDED_CONVERGENCE.md`）+ A.7.3.1 可执行离线验证
  （`tests/test_open_task_convergence_replay.py`）
- 范围：**仅**适用于 `route=open` 的开放式科研任务；是**更严格的执行约束**，
  **不扩大**权限、预算或调用次数。本附录是规范，不是实现。

本附录冻结“把开放式科研执行从不收敛的自由 ReAct 收敛为有界、可终止、以证据为进展度量”的契约。

---

## 1. 范围与不变量

Addendum 3 只作用于 `route=open`。以下**必须保持不变**：
- exact-ID 路径（Addendum 2）；
- v2 的总模型调用与费用上限（Planner≤2 / Verifier≤2 / Claim≤1 / Executor≤16 / 单题≤21 /
  单题≤$1.50 / Stage≤$3.00 / SDK retries=0）；
- Addendum 1 的外层安全 Guard（工具总轮次≤8、重复/循环/无进展阻断）；
- Addendum 2 的 exact-ID 规则；
- 旧 Verifier 的最终裁决权；Shadow 只记录与对比；
- 题目、评分、证据门槛与历史结果；A1/B1 已冻结现场。

**本附录不扩大任何权限、预算或调用次数。**

## 2. 结构化文献结果硬前置

开放式科研执行**不得**把 legacy 长字符串直接当成 EvidenceAccumulator 的科学证据。
生产实现前必须采用并冻结一种路径：
- **优先**：`search_literature → content_and_artifact → LiteratureRecord[]`；
- 仅在无法直接修改工具时，允许**严格 fail-closed** adapter。

`LiteratureRecord` 最低契约字段：`schema_version, pmid, doi, title, year, journal, abstract,
content_level, study_design, species, longitudinal, interventional, source, query, provenance,
source_ids, content_hash, hash_algorithm`。

规则：
- PMID 与 DOI **至少一个存在**；
- 无法可靠判断的字段置 `unknown/null`；
- **不得由 LLM 猜测**研究设计、物种、样本或全文内容；
- 无 ID / schema 不兼容 / provenance 缺失 → **fail-closed**；
- `metadata_only / abstract / fulltext` 必须明确区分；
- **工具失败、zero_hits、source_error、parse_error 必须分开**；
- `zero_hits` **不得**表述为该领域无研究。

## 3. OpenTaskExecutionContract 状态机

冻结：`plan → collect → normalize → accumulate → assess_step → synthesize → verify → finish`。
**不得跳过 normalize / accumulate / assess_step。**

每次工具返回后，**程序**必须按序执行：
1. 记录 Lifecycle；2. 校验 ToolResult/artifact；3. 标准化 Observation；
4. 构建/更新 EvidenceCard；5. 更新 EvidenceAccumulator；6. 计算 novelty；
7. 检查当前步骤 success criteria；8. 转换 PlanStepState；9. 决定是否允许一次受限补充检索；
10. 所有步骤终态后**强制进入 synthesis**。

**是否继续搜索不能仅由 LLM 决定。**

## 4. PlanStepState

冻结字段：`step_id, objective, allowed_tools, call_budget, attempts, status, observations,
evidence_ids, success_criteria, completion_reason, remaining_gaps`。
状态仅允许：`pending, running, satisfied, insufficient, failed, blocked`。
**所有步骤必须进入终态才能进入 synthesis**；**禁止**由模型自然语言创造新状态。

## 5. Novelty 与科研进展（四级冻结）

1. **transport novelty**：字节/排序/格式/普通文本变化 —— **仅用于审计**；
2. **identifier novelty**：新增可靠 PMID/DOI/GSE 等 ID；
3. **evidence novelty**：新增研究设计/时间方向/物种/干预/反证/证据层级；
4. **decision novelty**：新证据足以改变 Claim 支持等级或因果层级。

**只有 identifier / evidence / decision novelty 可重置 scientific no_progress。**
**transport novelty 绝不能单独重置 scientific no_progress。**
重复 EvidenceCard 必须按稳定 ID/hash 去重。

## 6. 步骤级预算（仅冻结下一次 B1 验证的保守值）

- 文献检索步骤：**最多 2 次**工具执行；
- 数据湖查询步骤：**最多 1 次**工具执行；
- 同一工具**不得**通过不断改写关键词绕过步骤预算；
- 步骤达预算后必须进入 `satisfied/insufficient/failed/blocked`，**不得**保持 `running` 继续请求工具；
- Addendum 1 的工具总轮次 8 仍作外层上限；v2 的模型与费用上限不变；
- **步骤预算不得跨步骤借用。**

**这些数字只冻结下一次 B1 Pilot 行为，不宣称是所有疾病/工具的永久最佳值；后续调整必须生成新协议版本与新 hash。**

## 7. 机器可判定的 success criteria

Planner 的自由文本 `success_criteria` **不能**直接成为程序条件。冻结最小确定性判据：
至少获得规定数量的有效 EvidenceCard / EvidenceCard 满足所需 content_level / 研究设计字段可用 /
目标证据轴是否存在 / 是否出现反证 / 是否达到步骤调用预算 / 是否仍存在明确 missing_evidence。
自由文本标准可保留用于解释，但**不得单独控制状态转换**。

## 8. 受控 insufficient 结论（Controlled insufficient conclusion）

即使证据不足，也必须产生**非空结构化结果**：`resolved_question, available_evidence,
unsupported_claims, causal_strength, missing_evidence, limitations, recommended_next_action`。

规则：
- EvidenceCard=0 时 `missing_evidence` **必须非空**；
- **禁止出现“还缺：[]”**；
- `zero_hits` 必须写成“当前来源未命中”，**不得**写成“没有研究”；
- insufficient **仍必须进入** Verifier、Claim extraction、Claim Graph、Shadow；
- **不允许**因证据不足而跳过核查链。

## 9. 因果校准协议

机制/因果任务的 synthesis 必须分别记录：`association, temporal evidence, dose-response,
intervention evidence, genetic/instrumental evidence, mechanistic plausibility, reverse causation,
confounding, clinical evidence`。

- **缺少时间方向与干预证据时，不得输出确定性因果结论；**
- 横断面相关最多支持 `association`，除非存在独立、更高层证据；
- 机制合理性**不得**自动升级为人类临床因果证据。

## 10. 遥测单一权威

冻结权威来源：
- **Lifecycle**：requested/executed/tool_returned/observed/failed；
- **EvidenceAccumulator**：EvidenceCard、dedup 与 scientific progress；
- **Gate**：模型调用/token/费用/重试/reservation；
- **Stage counters**：synthesize/verifier/claim/claim_graph/shadow 调用次数。

**Final RunMetrics 只能从上述对象汇总**；**不得**继续从 `state.shadow.tool_events` 单独推算
`metrics.execution.tool_calls`。若不同权威对象矛盾 → `status=failed`、`telemetry_conflict=true`、
`human_review=true`，**不得**发布成功科研结论。

## 11. 失败语义

冻结失败类型：`structured_result_invalid, evidence_normalization_failed, step_budget_exhausted,
scientific_no_progress, synthesis_failed, verification_failed, telemetry_conflict`。
失败必须保存：`primary_failure, failure_stage, completed_steps, evidence_cards 已有数量,
remaining_gaps, lifecycle counts, gate summary, human_review`。
**后续 Manifest 写入错误不得覆盖 primary_failure。**

## 12. 历史可比性

必须明确声明：
- 原 B1 canary 在**自由 ReAct** 条件下失败（安全终止、科研未完成）；
- 下一次 B1 将运行在 **v2 + Addendum 1/2/3** 条件下；
- **两次不是相同软件条件**；
- 新结果**不得覆盖**原 B1；
- 必须**并列报告**调用、费用、工具轮次、EvidenceCard、Claim、Verifier、Shadow 与最终结论；
- 新运行**不得**表述为原运行的“重测通过”。

---

**本附录仅为规范，不含实现。不改 v1/v2/Addendum 1/2 及其 hash，不改题目/评分/证据门槛/预算/上限。
第一个生产实现提交须按 ADR 的 11 步顺序、经单独批准后进行。**
