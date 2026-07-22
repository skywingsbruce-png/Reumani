# ADR: Exact-ID 确定性执行契约

- 状态：**提议（Proposed）** —— A.7.0 只读复盘产出，等待批准后再进入实现阶段。
- 日期：2026-07-23
- 背景：Shadow Pilot Round 2 A1 三次失败；根因已定位到 Executor 收敛策略。
- 关联：`SHADOW_PILOT_ROUND2_A70_ANALYSIS.md`（证据）、三份 A1 报告、
  `SHADOW_PILOT_ROUND2_A1_RERUN2_RESULT.md`。
- 约束：本 ADR **不改任何生产代码/Prompt/工具/协议**；仅记录架构决策。

---

## 1. 三次 A1 失败证据

| 指标 | A1-original | A1-rerun1 | A1-rerun2 |
|---|---:|---:|---:|
| 完成 | 否 | 否 | 否 |
| 失败点 | executor role cap(16) | loop guard | loop guard(max_tool_rounds=8) |
| 生命周期可观测 | 否 | executed/observed 不可信 0 | **executed=8/observed=8 可信** |
| Executor 调用 | 16 | 9 | 9 |
| 费用 | $0.085541 | $0.072872 | $0.086985 |

三次共性：Executor 在 `search_evidence`/`search_literature` 之间反复检索，拿不到能结束
任务的结果。A.6.6.3 修好了**观测层**（rerun2 逐 tool_call 生命周期可信），从而把根因从
“基础设施不可靠”收敛到“**执行层无确定性完成条件**”。

## 2. 真实工具返回分析（A.7.0 免费直调实测）

- `search_evidence("41657283")` → papers=1（命中 2026 CAR-T 论文），但 `retrieval_status`
  字段**不存在**——即使命中也无 `exact_hit` 信号。
- `search_evidence("PMID:41657283")` / `("<DOI>")` → papers=0，content=`"未检索到相关文献。"`
  （**9 字符** = 观测到的 `result_length=9`）。
- `search_literature("41657283")` → 命中，但返回**纯字符串、无 artifact**，无法进入证据管线。
- EPMC 层：裸 PMID `hitCount=1`；裸/精确 DOI 均 `hitCount=0`（该 DOI 不在 EPMC → **zero_hits
  为正确终态**）。
- `result_length=9` 来自 **API 确实无结果**，非 artifact 丢失/中间件丢包/序列化错误。

## 3. 为什么 ReAct 不适合精确 ID 核验

- 精确 ID 核验是**确定性查表**：每个 ID 只有 exact_hit / zero_hits / tool_error 三种终态，
  无需“探索”。
- ReAct 让 LLM 自由决定“是否再搜一次”，但工具没有给它确定性的终态信号
  （search_evidence 无 `retrieval_status`），于是 LLM 反复改写查询、加 `PMID:` 前缀、换工具，
  破坏精确命中并耗尽工具轮次。
- 开放式探索的自由度在这里是**负资产**：它把一个 O(1) 查表变成不收敛的搜索。

## 4. 确定性状态机（每个提取出的 ID 独立状态）

```
PENDING
  → EXACT_QUERY_SENT          (对该 ID 发出且仅发出一次精确查询)
  → EXACT_HIT | ZERO_HITS | TOOL_ERROR
  → VALIDATED                 (artifact/provenance 校验通过；zero_hits 也是合法 VALIDATED 终态)
  → COMPLETE
```

- 完成条件（任务级）：**所有** ID 进入 VALIDATED 终态，或明确 TOOL_ERROR 并 fail-closed。
- 不由 LLM 决定是否继续；同一 ID 默认**最多一次**精确查询。
- 仅“确定性错误策略”允许**一次非付费重试**（如 HTTP 5xx/超时）；zero_hits **不是**重试理由。
- `zero_hits` 是**合法终态**，不得触发继续搜索。

## 5. 工具权限（exact-ID 任务）

- 允许：`search_evidence`（需补精确 ID 模式 + 状态语义）、ID 规范化 / 引用核验工具。
- 默认不允许：`search_literature`、`retrieve_resources`、`query_data_lake`、语义检索、
  无关技能发现工具。
- 语义补充搜索须作为用户明确要求或独立后续任务，不得替代精确核验。

## 6. 错误语义

- `exact_hit`：EPMC/PubMed/Crossref 精确字段（`EXT_ID:`/`DOI:"..."`）命中且 provenance 可校验。
- `zero_hits`：精确字段确认无该记录（合法终态，非错误、非“该领域无研究”）。
- `tool_error`：HTTP/解析等确定性错误 → 允许一次非付费重试 → 仍失败则 fail-closed，
  记入失败 Manifest，不重跑 A1。

## 7. 对 Verifier / Shadow 的输入

- 状态机为每个 ID 产出确定性结果（exact_hit→结构化 paper；zero_hits→空但已确认）。
- exact_hit 直接构 EvidenceCard（ID 仅来自 artifact provenance，复用 A.6.6.2/.3 守卫）；
  zero_hits 不构卡（复用现有“zero_hits 不构卡”规则）。
- 之后照常进入旧 Verifier / Claim extractor / Shadow —— **裁决权不变**，Shadow 仍只记录对比。

## 8. 精确 ID 检索的确定性微工作流（目标形态）

```
提取 PMID/DOI
→ 每个 ID 调一次 search_evidence（精确模式 EXT_ID/DOI 字段）
→ 得到 exact_hit / zero_hits / tool_error
→ 校验 artifact（schema=toolresult-v1，source_ids 来自 provenance）
→ 所有 ID 均达终态
→ 强制结束 Executor
→ 进入 Verifier / Claim / Shadow
```

LLM 负责规划与解释结果，**不负责决定是否继续重复检索**。

## 9. 向后兼容 / 不影响开放式研究任务

- 仅对**被判定为 exact-ID 类**的任务（question 中含 PMID/DOI/GSE 等精确标识符、
  期望 exact_hit/zero_hits）启用确定性执行契约。
- 机制类 / 开放式研究任务（假设生成、方向辩论、综述）**继续走现有 ReAct**，不受影响。
- 现有 `_exact_id()`（`ssc_skill_agent.py`）已能识别精确 ID，可作为任务分流的判定点。

## 10. 推荐的最小实现（**待批准后**，不在本次执行）

- `search_evidence` 增加**精确 ID 模式**：当 query 为精确 ID 时，用 EPMC 精确字段
  （`EXT_ID:<pmid> AND SRC:MED` / `DOI:"<doi>"`）并在 artifact.data 写入
  `retrieval_status ∈ {exact_hit, zero_hits}`、`retrieval_mode=exact_id`。
  （最小改动、不破坏关联检索路径。）
- 新增确定性执行器 `pilot/exact_id_runner.py`（或等价）：实现第 4 节状态机，
  程序守门工具选择/执行/完成，绕过开放式 ReAct 轮次。
- 任务分流：exact-ID 任务 → 确定性执行器；其余 → 现有 Executor。
- 复用现有：`ids.valid_pmid/valid_doi`、A.6.6.3 生命周期 middleware、evidence_from_artifact
  守卫、失败 Manifest、预算闸门。

## 11. 测试计划（**待批准后**）

- 单元：精确模式 search_evidence 对有效 PMID→exact_hit、无效/缺失 DOI→zero_hits、
  HTTP 错误→tool_error（可用录制的 EPMC 响应，离线、零付费）。
- 状态机：每个 ID 独立终态；zero_hits 不触发重试；tool_error 仅一次非付费重试；
  完成条件当且仅当全部 VALIDATED。
- 端到端（fake LLM + 真实 EPMC 或录制响应）：exact-ID 任务确定性完成 → 进入 Verifier/Claim/
  Shadow → 生成科研 Manifest，且**不**耗尽工具轮次。
- 回归：开放式研究任务仍走 ReAct，行为不变。

## 12. 是否需要新协议版本

- **倾向：需要一个附录（Addendum 2）或 v3**，用于冻结“exact-ID 确定性执行契约”
  （状态机、完成条件、工具权限、错误语义），因为它改变了 A1 类任务的执行模型。
- 但这属于**下一阶段**：须由你批准后再起草，**本次不修改 v2/Addendum 1**。

---

## 决策

**建议**：对 exact-ID 任务，采用第 4 节确定性状态机 + 第 8 节微工作流，由程序守门执行与完成，
LLM 只做高层推理；开放式研究任务保持 ReAct 不变。

**本阶段动作到此为止**：正式暂停 Round 2；不运行第四次 A1、不运行 B1、不提高上限、
不修改 v2/Addendum、不开始 Commit B、不立即改 Prompt、不增加 ReAct 轮次。上述实现/测试/
协议修订均需**单独批准**后另行开展。
