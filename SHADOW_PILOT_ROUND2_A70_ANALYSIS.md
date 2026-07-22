# A.7.0 只读架构复盘 — Exact-ID 执行诊断

**性质**：只读分析 + 免费工具直调（Europe PMC HTTP）。**真实付费 LLM 调用 = 0**
（复盘脚本已把 deepseek/claude 客户端 `invoke` 换成抛错，全程只发免费 EPMC 请求）。
不改生产代码/Prompt/工具/协议。三次 A1 现场（报告/Trace/Manifest/账本/协议/历史 commit）
经 hash 与账本前缀核验**未改动**。

原始探针数据：`pilot/round2_results/A70_empirical_probe.json`。

---

## 一、现场保护核验

| 对象 | 状态 |
|---|---|
| 三协议 hash（v1 `5d166bce` / v2 `c76f5894` / Addendum `de3afcdd`） | 未变 |
| 三份 A1 报告（original `4c2065f6` / rerun1 `b9c8c50e` / diagnosis `8812e81`） | 未变 |
| 历史账本前缀（`test_real_a1_original_prefix_intact`） | 通过 |
| 无 tracked 文件被修改（`git status`） | 确认 |
| 历史 commit（`a7d8805` / `7199081` / `a4d1e78`） | 完整 |

---

## 二、A1-rerun2 真实工具序列（脱敏；query 用 arguments_hash 反解，未读任何敏感 run 文件）

A1 只含公开文献标识符，故记录 PMID/DOI；不含系统 Prompt 或其它敏感参数。

| # | tool_call hash | tool | query（反解） | 含PMID | 含DOI | 参数键 | content长度 | artifact | schema | retrieval_status | 终态 | 模型为何继续 |
|--:|---|---|---|:--:|:--:|---|--:|:--:|---|---|---|---|
| 1 | `93eaf4a7` | list_skills | `{}` | — | — | — | 6678 | 否 | legacy | — | — | 技能发现，与精确ID无关 |
| 2 | `39e1c9cc` | search_literature | `41657283` | ✓ | — | max_results,query | **199** | **否** | 无 | 无此语义 | 无 | **命中却只是字符串、无结构化终态** |
| 3 | `5c8b1485` | search_literature | `10.1080/…2302553` | — | ✓ | max_results,query | 9 | 否 | 无 | 无此语义 | 无 | 空返回，无 zero_hits 终态 |
| 4 | `be4e605d` | search_literature | (未反解) | — | — | max_results,query | 9 | 否 | 无 | 无此语义 | 无 | 空返回 |
| 5 | `2cf0cf97` | search_evidence | `10.1080/…2302553` | — | ✓ | n,query | 9 | 是 | toolresult-v1 | **`<absent>`** | 无 | 空返回，且无 zero_hits 状态字段 |
| 6 | `c8cd575f` | search_evidence | (未反解，非裸PMID) | — | — | n,query | 9 | 是 | toolresult-v1 | **`<absent>`** | 无 | 空返回 |
| 7 | `b6088576` | search_literature | (未反解) | — | — | max_results,query | 9 | 否 | 无 | 无此语义 | 无 | 空返回 |
| 8 | `edbb8ab4` | search_literature | (未反解) | — | — | max_results,query | 9 | 否 | 无 | 无此语义 | 无 | 空返回 |
| 9 | `1405ef84` | search_literature | (护栏拦截，未执行) | — | — | — | — | — | — | — | — | loop_guard(max_tool_rounds=8) |

**要点**：8 个已执行 tool_call 中，唯一一次拿到内容的是 #2（search_literature 用裸 PMID → 199
字符，命中那篇 2026 CAR-T 论文），但 search_literature **只返回字符串、无 artifact**，无法进入
证据管线，也没有“exact_hit”终态信号。两次 search_evidence（#5、#6）都**不是**用裸 PMID
调用（#5 用 DOI），全部返回 9 字符空串。→ 结构化管线自始至终没拿到一个命中。

---

## 三、免费工具直调（当前真实行为）

`search_evidence(query, n=8)`（→ `search_with_abstracts` → Europe PMC，无 LLM）：

| query | papers | content长度 | schema | retrieval_status |
|---|--:|--:|---|---|
| `41657283`（裸 PMID） | **1** | 327 | toolresult-v1 | `<absent>` |
| `PMID:41657283` | 0 | 9 | toolresult-v1 | `<absent>` |
| `10.1080/03009742.2024.2302553`（DOI） | 0 | 9 | toolresult-v1 | `<absent>` |
| `doi:10.1080/…` | 0 | 9 | toolresult-v1 | `<absent>` |

`search_literature(query)`（Europe PMC，返回**纯字符串、无 artifact**）：

| query | content长度 | 说明 |
|---|--:|---|
| `41657283` | 199 | 命中（字符串行，无结构化） |
| `PMID:41657283` | 9 | `未检索到相关文献。` |
| DOI / `doi:…` | 9 | `未检索到相关文献。` |

---

## 四、result_length=9 的真实层级（逐层定位，不靠长度猜测）

```
免费文献API原始响应   ← EPMC hitCount=0（关联检索裸DOI/带前缀PMID 确实无结果）
→ search_evidence内部解析   ← papers=[]（忠实反映 0 结果）
→ ToolResult / content_and_artifact   ← content = "未检索到相关文献。"（正好 9 个字符）
→ ToolLifecycleMiddleware   ← 完整记录，无丢失
→ ToolMessage / Agent消息状态   ← observed 正常
```

EPMC 层实测（`pageSize=5`）：

| 查询 | http | hitCount | 结论 |
|---|--:|--:|---|
| relevance(裸 PMID `41657283`) | 200 | **1** | EPMC 自由文本能解析裸 PMID |
| exact(`EXT_ID:41657283 AND SRC:MED`) | 200 | 1 | 精确字段同样命中 |
| relevance(裸 DOI) | 200 | **0** | 该 DOI 不在 EPMC |
| exact(`DOI:"…"`) | 200 | **0** | 精确字段同样 0 → **zero_hits 是正确终态** |

**结论**：`result_length=9` = `"未检索到相关文献。"`（9 字符），来源是 **API 确实无结果**，
**不是** artifact 丢失、proxy/middleware 丢包、序列化错误或参数格式崩溃。artifact 真实存在
（search_evidence 的 schema=toolresult-v1）。

**为什么有效 PMID 没有 exact_hit**：(a) 唯一命中它的调用是 search_literature（无 artifact、
无 exact_hit 语义）；(b) search_evidence 具备 artifact 但**没有** exact_hit/zero_hits 状态字段
（`retrieval_status=<absent>`），且执行器从未用**裸 PMID**调用它（用了 DOI 和其它空返回查询），
还常给 PMID 加 `PMID:` 前缀导致 EPMC 关联检索 0 命中。

**为什么无效 DOI 没有 zero_hits**：search_evidence/search_literature 对空结果只返回
`"未检索到相关文献。"` 字符串，**没有**结构化 `retrieval_status=zero_hits` 终态。系统无法把
“工具返回空”与“已确认 zero_hits 终态”确定性地区分开。

---

## 五、Exact-ID 任务契约审查（当前为何允许失败）

当前架构把 exact-ID 核验当成开放式 ReAct，因此允许：

1. exact-ID 任务选择 `search_literature`（无 artifact 的关联检索工具）；
2. 同一 ID 反复调用、反复改写（#2–#8 多为对同一目标换工具/换措辞）；
3. 工具已返回“可判定为空”的结果后，仍由 LLM 自行决定继续搜索；
4. 对精确 ID 做自由文本/语义扩展（加 `PMID:` 前缀、换关键词），反而破坏精确命中；
5. 由 Executor（LLM）判断任务是否完成——而它没有确定性的完成信号可依据。

根因收敛为一句话：**Exact-ID 核验仍被当成开放式 ReAct 探索任务，缺少确定性的执行与完成条件。**
不是 token 不足，也不是 Claude 调用过多（planner 仅 2、总 11 次调用，费用 $0.086985）。

---

## 六、工具权限收窄评估（exact-ID 任务）

| 工具 | exact-ID 任务 | 理由 |
|---|---|---|
| `search_evidence`（需补精确ID模式+状态语义） | **允许** | 唯一产出结构化 artifact/provenance 的检索工具 |
| ID 规范化 / 引用核验工具 | 允许 | 精确核验必需 |
| `search_literature` | 默认不允许 | 纯字符串、无 artifact、关联检索，误导执行器 |
| `retrieve_resources` / `query_data_lake`（corpus） | 视需要（本地精确查表可用，但非外部核验主路径） | 与外部 ID 核验目标不一致 |
| 语义检索 / 技能发现 | 默认不允许 | 与精确 ID 目标无关，制造探索循环 |

补充语义搜索应作为**用户明确要求**或**后续独立任务**，不得替代精确 ID 核验。

---

## 七、LLM 职责边界（exact-ID 任务）

- **程序负责**（确定性、程序守门）：ID 提取、规范化、工具选择、工具执行、状态判断、
  去重、完成判断、EvidenceCard 构建。
- **LLM 只负责**（高层推理）：解释 exact_hit/zero_hits、比较证据、生成原子 Claim、
  表述不确定性。**不得发起额外搜索循环。**

这与 Biomni 原则一致：资源选择与执行由程序守门，LLM 负责高层推理。

架构决策与最小实现见 `ADR_EXACT_ID_DETERMINISTIC_EXECUTION.md`。
