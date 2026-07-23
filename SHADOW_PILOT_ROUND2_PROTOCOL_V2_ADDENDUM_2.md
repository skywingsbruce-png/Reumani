# Shadow Pilot Round 2 — Protocol v2 Addendum 2

**Exact-ID 确定性执行契约（Deterministic Exact-ID Resolution）**

- 版本：Addendum 2
- 依赖：`SHADOW_PILOT_ROUND2_PROTOCOL_V2.md`（不修改）+ Addendum 1（不修改）
- 冻结日期：2026-07-23
- 来源：A.7.0 只读架构复盘（`ADR_EXACT_ID_DETERMINISTIC_EXECUTION.md`）
- 范围：**仅**规定 PMID/DOI 精确核验的确定性执行契约。**不改**题目、评分、阈值、预算、
  循环上限，**不改** v1/v2/Addendum 1 及其 hash。机制类/开放式问题继续走原 ReAct 路径。

本附录冻结“把 Exact-ID 精确核验从开放式 ReAct 中拆出、形成确定性可终止零 LLM 依赖科研 Action”
的契约。它是**规范**，不是实现；实现须另行按此契约完成并通过测试。

---

## 1. 目标与边界

- Exact-ID 核验是**确定性查表**：每个标识符的存在性只有有限终态，不需要 LLM 探索。
- 契约保证：**确定性**（程序裁决）、**可终止**（所有 ID 达终态即完成）、**零 LLM 依赖**
  （提取/规范化/查询/裁决/构卡/完成判断全部由程序完成）。
- LLM 只做高层推理（解释 exact_hit/zero_hits、比较证据、生成 Claim、表述不确定性），
  **无权**要求对同一 ID 反复改写查询或发起额外搜索循环。
- 不删除开放式 ReAct；机制类问题仍走原路径。

## 2. 两层状态模型

**来源级** `retrieval_status`（每个 (ID, 来源) 一个）：
- `exact_hit`：该来源精确字段命中该 ID。
- `zero_hits`：该来源精确确认无该记录。
- `source_error`：网络/限流/解析异常，未能确认。
- `not_queried`：未查询该来源。

**综合解析级** `resolution_status`（每个 ID 一个）：
- `verified`、`not_found`、`mismatch`、`manual_needed`。

**铁律**：单一数据库的 `zero_hits` **不得**直接升级为全局 `not_found`。

## 3. 来源路由与判定

### PMID
- 主来源：PubMed / NCBI E-utilities（按 PMID 精确获取）。
- 辅助来源：Europe PMC（按精确 PMID 交叉核验）。
- 判定：
  - PubMed 返回同一 PMID 且元数据有效 → `verified`；
  - PubMed 与 Europe PMC 元数据冲突 → `mismatch`；
  - PubMed 明确无记录且辅助来源也无记录 → `not_found`；
  - 网络错误/限流/解析异常 → `manual_needed` / `source_error`；
  - **不得**用关键词相似文献替代该 PMID。

### DOI
- 主来源：doi.org 解析 + Crossref（按完整 DOI 精确查询）。
- 辅助来源：PubMed / Europe PMC（医学元数据补充与 ID 映射）。
- 判定：
  - doi.org 或 Crossref 精确解析且规范化 DOI 一致 → `verified`；
  - resolver 与 Crossref 均明确 not found → `not_found`；
  - resolver 命中但 Crossref 元数据冲突 → `mismatch` 或 `manual_needed`；
  - **只有** Europe PMC 无命中，**不得**判 `not_found`；
  - 网络错误**不得**判 `zero_hits`；
  - **不得**返回相似 DOI 或相似标题作为 exact hit。

## 4. 确定性状态机（每个 ID）

```
PENDING
→ NORMALIZED
→ PRIMARY_QUERY_SENT
→ PRIMARY_RESULT
→ OPTIONAL_CROSSCHECK
→ VERIFIED | NOT_FOUND | MISMATCH | MANUAL_NEEDED
→ COMPLETE
```

任务完成条件：
- 所有 ID 都进入终态；
- 终态由**程序**判断；
- `zero_hits` 是来源状态，**不**自动触发语义搜索；
- LLM 无权要求对同一 ID 反复改写查询；
- 同一来源同一 ID 默认**只查询一次**；
- 仅网络错误允许**程序控制的一次退避重试**（非 LLM 调用，且不得改变查询内容）。

## 5. 结构化契约

`ExactIdResolution`（单 ID）字段：`original_input`、`normalized_id`、`id_type`、
`source_results`、`retrieval_status_by_source`、`resolution_status`、`canonical_title`、
`canonical_doi`、`canonical_pmid`、`journal`、`year`、`provenance`、`content_level`、
`warnings`、`error_type`。

`ExactIdBatchResult`（任务级）字段：`ids`、`all_terminal`、`verified_count`、
`not_found_count`、`mismatch_count`、`manual_needed_count`、`evidence_cards`、
`completion_reason`。

两者必须带：`schema_version`、`SHA-256`、查询时间、来源、标准化 ID、原始 ID。

## 6. 确定性 Action

独立 Action（如 `resolve_exact_ids(query_or_ids)`）由**程序**负责：ID 提取、规范化、去重、
来源选择、精确查询、交叉核验、状态裁决、EvidenceCard 构建、完成判断、Trace 与 Manifest。
**LLM 不参与上述任何步骤。**

Exact-ID 任务进入：`query classifier → exact_id action → structured observations →
Verifier / Claim / Shadow`；**不得**进入开放式 ReAct Executor。

## 7. EvidenceCard 规则

- 只为 `verified` 结果构建 EvidenceCard；
- `not_found` / `manual_needed` / `source_error` / `zero_hits`：不构卡；
- `mismatch`：不构卡，且 `human_review=true`；
- PMID/DOI 必须来自**结构化来源响应**（provenance），不得从自然语言 content 提取或猜测；
- 多来源元数据保留各自 provenance；冲突不得静默选一个。

## 8. 任务分流

- 查询中含可提取 PMID/DOI 且任务目标是核验/获取这些 ID → exact_id action；
- 同时含机制探索问题时，先完成 exact-ID 子任务，再由独立开放研究子任务处理；
- exact-ID 结果**不得**被语义搜索覆盖；
- 分类不确定时 fail-closed 或要求人工确认；
- query classifier 必须有确定性测试。

## 9. 错误与安全语义

必须区分：明确无记录 / 来源不可用 / HTTP 超时 / 429 限流 / 解析失败 / 元数据冲突 / 无效格式。

禁止：
- 网络失败 → `not_found`；
- Europe PMC 无结果 → 判 DOI 不存在；
- Crossref 无结果 → 自动生成相似引用；
- 标题相似 → `exact_hit`；
- 工具错误 → 空的“成功”结果。

## 10. 对既有系统的影响

- 仅对**被判定为 exact-ID 类**的任务启用本契约；机制类/开放式任务不受影响，继续 ReAct。
- 不改变旧 Verifier 的最终裁决权；Shadow 仍只记录与对比。
- 不改题目、评分、阈值、预算、循环上限；不改 v1/v2/Addendum 1。
- A1 属 exact-ID 类：PMID 41657283 → PubMed 精确验证 → verified；
  DOI 10.1080/03009742.2024.2302553 → doi.org + Crossref 明确无记录 → not_found；
  Europe PMC 仅作补充；两 ID 均达终态；EvidenceCard 只含有效 PMID；不调用 search_literature；
  不进入 ReAct 循环；Executor 模型调用为 0；其后 Verifier/Claim/Shadow 可运行。
  **此为期望行为，非对具体 ID 写死**；实现须用额外 PMID 与不存在 DOI fixture 证明通用性。
