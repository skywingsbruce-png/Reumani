# A1 Executor 空转 —— 根因诊断（A.6.4，零付费调用）

本轮只做**观察与取证**，不实现任何修复。所有实验用 fake 模型 / 真实对象离线检查，
**真实付费调用 = 0**。A1 现场文件全部只读，SHA-256 见 `pilot/round2_results/A1_scene_hashes.json`。

## 0. 现场固定

| 文件 | SHA-256(前16) | 字节 |
|---|---|---|
| `runs/20260721_105710_77ea9469/run.json` | `53a08ea1908fcb16` | 8281 |
| `runs/.../final_answer.md` | `a5dd2425e075ffe3` | 230 |
| `pilot/round2_results/stage1_ledger.jsonl` | `482f9b1e365be79f` | 9935 |
| `stage1_metrics_1784624290.json` | `808d12ae923846df` | 5566 |
| `A1_calls_audit.json` | `1ff8c9859edad84e` | 10846 |
| `SHADOW_PILOT_ROUND2_A1_RESULT.md` | `4c2065f6d46b70ff` | 9368 |
| `SHADOW_PILOT_ROUND2_PROTOCOL.md` | `5d166bce159de665` | 19291 |
| `SHADOW_PILOT_ROUND2_PROTOCOL_V2.md` | `c76f589485e4ebfd` | 8980 |
| `pilot/round2_tasks.py` | `33a5b4bbd9b3d9ed` | 9962 |

有测试 `test_a1_scene_files_unmodified` 持续复核这些 hash。
未脱敏 `run.json` 与原始账本均被 `.gitignore` 覆盖，未入库。

## 1. 证据的边界（必须先说清楚）

**16 次 Executor 响应的正文没有被保存。** `execute()` 抛错，`msgs` 从未返回，
`_save_run` 时 `observations=0`；旧账本只记 token 数与费用。

因此第二部分要求的逐次字段中，**下列一律 `unknown`，不推测**：
`finish_reason` / `response content 类型` / `content 长度` / `tool_calls 是否存在` /
`tool_calls 数量` / `invalid_tool_calls` / `additional_kwargs` / `response_metadata` /
`是否包含工具名文本` / `是否包含 JSON` / `是否包含自然语言"我要调用某工具"` /
`是否实际产生 ToolMessage` / `是否执行工具`。

**可从账本确证的**（每次调用）：`executor_call_index`、`outer_iteration`、
输入 token、输出 token、最坏费用、结算费用、reservation 状态、`is_retry`、`price_config_version`。

## 2. 16 次调用的确切归属

| 事实 | 证据 |
|---|---|
| 16 次全部发生在**外层 iteration 1** | 账本顺序：call#1 planner → call#2–17 executor → call#18 planner |
| 它们是**同一个 `create_agent` 图内部的 ReAct 循环** | 一次 `execute()` 调用内产生；离线复现证实每个工具轮次 = 1 次模型调用 |
| **不是** LangGraph 重试、**不是** parser 纠错、**不是** 外层 iteration | `is_retry` 事件 0 条；外层只跑了 2 轮，每轮 1 次 planner |
| 第 17 次在 provider 之前被拒 | `rejected_before_invoke`: `max_calls_per_role[executor]: 17 > 16`（两次） |
| iteration 2 的 executor **立即被拒** | 每角色计数按**题**累计，非按轮重置 |

输入 token 单调增长 1,407 → 4,848，输出恒为 69–122 —— 与"每轮追加一次工具往返、
模型每轮只吐一个简短 tool_call"的形态一致。

状态转移（离线复现所得）：

```
call → model response(含 tool_calls) → tool node 执行 → 消息追加 → 回到 model
  ↑                                                                     │
  └─────────────────────── 循环边：response.tool_calls 非空 ────────────┘
终止边：response.tool_calls 为空 → END
```

## 3. GatedModel 与工具绑定（§3 十四项）

| # | 检查 | 结论 |
|---|---|---|
| 1 | `create_agent()` 收到的是 GatedModel 还是底层客户端 | **GatedModel** |
| 2 | `bind_tools()` 返回对象类型 | **仍是 GatedModel**（内层变 `_ChatModelBinding`） |
| 3 | 绑定后是否仍过硬闸门 | **是**，账本 +1、provider +1 |
| 4 | 工具 schema 是否真传给模型 | **是**，`inner.bound_tools` 收到完整列表 |
| 5 | 传入的工具数量与名称 | A1 实际 4 个：`list_skills` / `query_data_lake` / `retrieve_resources` / `search_literature` |
| 6 | tool_choice | **unknown**（未记录；`_should_bind_tools` 返回 True，走默认） |
| 7 | thinking disabled 是否仍保留工具调用能力 | 无反证；16 次调用形态说明工具轮次在正常发生 |
| 8 | LangChain 是否正确解析 tool_calls | **unknown**（响应未留存） |
| 9 | 是否有 `invalid_tool_calls` 被忽略 | **unknown** |
| 10 | 是否把工具调用写进 content 文本 | **unknown**；但离线证实：真发生这种情况时 ReAct 会**直接结束**，不会空转 |
| 11 | 是否丢失 `bind_tools` / `with_config` 等 Runnable 配置 | ⚠️ **发现逃逸口**，见下 |
| 12 | 绑定后 role 是否仍为 executor | **是** |
| 13 | 绑定后是否仍是同一账本 | **是** |
| 14 | 是否存在绑定后绕过闸门的路径 | ⚠️ **存在**，见下 |

### 发现 ①：`with_config` / `with_retry` 会逃逸闸门

`GatedModel.__getattr__` 把未显式实现的方法透传给底层对象，返回的是**未包装**的 Runnable。
`bind_tools` / `bind` 我做了显式包装，但 `with_config` / `with_retry` 没有。
当前 `create_agent` 路径没有走这两个方法（所以 A1 的 16 次调用确实全部计量到了），
但这是一条**真实存在的绕过路径**。已用 `test_runnable_config_methods_escape_the_gate` 锁定现状。

### 发现 ②：预算闸门是这条链路上**唯一**有效的循环护栏

把角色上限放到 999 后，模型持续要求调工具时，`langchain.agents.create_agent`
在默认配置下**没有**先于闸门触发的递归护栏 —— 一直跑到第 1000 次调用被闸门拒绝。
即：**没有闸门就会无限循环**。（`test_budget_gate_is_the_only_effective_loop_guard`）

## 4. ReAct 为什么没有结束（§4）

离线用**与产品完全相同的入口**（`langchain.agents.create_agent`）复现，逐条排除：

| 假设 | 实验结果 |
|---|---|
| 无 tool_calls 时不结束？ | ❌ 会结束，**1 次调用即 END** |
| 空 content 触发空转？ | ❌ 不会，空 content + 无 tool_calls 仍 END |
| 把普通文本误认成"继续推理"？ | ❌ 不会；工具调用写成文本时直接 END，不产生 ToolMessage |
| parser 错误被吞掉后重试？ | ❌ 未观察到该边 |
| 每个 tool_call 轮次烧 1 次模型调用？ | ✅ **是**，3 轮工具 = 4 次模型调用 |
| 存在递归上限先于闸门触发？ | ❌ 默认配置下没有 |

→ **ReAct 的终止逻辑本身是正确的。** 16 次调用的唯一自洽解释是：
**模型每一轮都返回了 tool_calls，工具每轮都执行了，但始终没有收敛到"无 tool_calls"的终止态。**

`max_iterations=2` 与 16 的关系：**两者不在同一层**。`max_iterations` 只限外层
plan→execute→verify 轮数；ReAct 内部轮数不受它约束（v2 的每角色 16 次是**唯一**约束）。

## 5. 工具路由（§5 十项）

### 发现 ③（根因级）：裸 DOI 提取不到

```python
ids.extract_pmids(A1问题) == ["41657283"]          # PMID 可提取
ids.extract_dois(A1问题)  == []                     # 裸 DOI 提取不到
ids.valid_doi("10.1080/03009742.2024.2302553") is True   # 但校验器认为合法
ids.extract_dois("https://doi.org/10.1080/...") == [...]  # 只有 URL 形式才行
```

`extract_dois()` 只匹配 `_DOI_URL`（`doi\.org/(10\.…)`），**没有裸 DOI 分支**。
**提取器与校验器口径不一致** —— 这是精确 ID 路由失效的直接前置原因。

| # | 检查 | 结论 |
|---|---|---|
| 1 | A1 是否被识别为 exact_id | 部分：PMID 可见，DOI 不可见 |
| 2 | PMID/DOI 是否被 `ids.py` 正确提取 | PMID ✅ / 裸 DOI ❌ |
| 3 | Tool Retriever 为何没把 `search_evidence` 排首位 | 选择器输出不含它（`test_a1_tool_selection_snapshot` 锁定） |
| 4 | `search_literature` 与 `search_evidence` 职责重叠 | 是；前者被选中，后者才是结构化那个 |
| 5 | 精确 ID 是否误走语义检索 | 是 |
| 6 | 权限过滤是否删掉了 search_evidence | ❌ 不是过滤删的，是**从未被选中** |
| 7 | 工具注册是否缺 PMID/DOI 标签 | 待查（未在本轮展开） |
| 8 | Planner 计划是否要求精确核验 | 计划 3 步、schema 合法；正文未留存 |
| 9 | Executor 实际收到哪些工具 | 那 4 个 |
| 10 | 即使 search_evidence 未选中，ReAct 是否仍应正常结束 | **应该**。路由不当只会降低答案质量，**不解释 16 轮空转** |

`search_evidence` 确实已注册且是 `content_and_artifact` 结构化工具（`test_search_evidence_is_registered_and_structured`）——
问题在路由，不在工具缺失。

## 6. A1 题目可评分性（§6）

| 标识符 | Europe PMC | Crossref API | doi.org handle | PubMed E-utilities | 结论 |
|---|---|---|---|---|---|
| PMID `41657283` | hitCount **1** | — | — | **有记录** | **真实存在** |
| DOI `10.1080/03009742.2024.2302553` | hitCount **0** | **HTTP 404** | **HTTP 404** | — | **不可解析** |

PMID 41657283 = "From technological iteration to clinical breakthrough: advances of CAR-T cell
therapy in autoimmune diseases"，Annals of Medicine，2026，DOI `10.1080/07853890.2026.2627057`
（Europe PMC 与 PubMed **两个来源一致**）。

关于那个 DOI：前缀 `10.1080` 是 Taylor & Francis，`03009742` 是 Scandinavian Journal of
Rheumatology 的 ISSN —— 结构上**看起来合理**，但三个独立免费权威来源都解析不到。

**判断（依据冻结协议，不事后改题意）**：A1 的评分规则写的是
「ID↔文献一致=2；**状态标注正确**=2；excerpt 可核验=1」，硬失败是「伪造 PMID/DOI」和
「把 zero_hits 报成命中」。也就是说，**A1 本来就可能在测"系统能否区分一个有效 PMID 和一个
不可解析的 DOI"** —— 对不可解析的 DOI 如实报 `zero_hits` 是**得分行为**，不是失败。
因此：**DOI 不存在不自动使题目失效**，A1 仍然可评分。

## 7. 归属判定（§9）

**多因素，但两者层级不同：**

| 层 | 问题 | 是否解释 16 轮空转 |
|---|---|---|
| **路由**（`ids.extract_dois` 漏裸 DOI → 选错工具） | 真实缺陷 | ❌ **不解释** |
| **模型行为**（每轮都要求调工具、不收敛） | 最可能的直接原因 | ✅ 是唯一自洽解释 |
| **LangChain 适配 / 终止条件** | 离线逐条排除，终止逻辑正确 | ❌ 排除 |
| **GatedModel** | 计量、绑定、role、账本全部正确 | ❌ 排除 |
| **护栏缺失**（无递归上限） | 真实缺陷，放大了后果 | ⚠️ 不是起因，但让空转跑满 16 次 |

**注意**：模型行为这一条是**推断**，不是直证 —— 响应正文没留存。
要坐实它，需要在下一次运行前先加上"保存 Executor 消息历史"的能力，
那属于修复，本轮不做。

## 8. 推荐的最小修复（**本轮不实现**）

按优先级，每条都建议独立最小 commit：

1. **保存 Executor 消息轨迹**（诊断前提）。没有它，下一次失败仍然只能靠推断。
2. **`ids.extract_dois` 增加裸 DOI 分支**，与 `valid_doi` 对齐。纯函数改动，易测。
3. **给 ReAct 加递归/轮次护栏**（例如给 `create_agent` 传 `recursion_limit`），
   让闸门不再是唯一护栏 —— 闸门应是最后一道，不是第一道。
4. **`GatedModel` 显式包装 `with_config` / `with_retry`**，堵掉逃逸口。
5. **路由**：让精确 ID 查询优先选 `search_evidence`。改动面较大，建议最后做且单独评估。

## 9. 是否需要修改 v2 协议

**目前不需要。** 每角色 16 次的上限**没有被证明不合理** —— 16 次没被用在有效工作上，
而是被空转消耗掉了。在修复 1–3 之前提高上限，只会让空转跑得更久、花更多钱。
建议：先修复、再用 fake 模型量出真实收敛所需轮次，然后**才**讨论 v2.1。

## 10. 本轮测试与提交

- 新增离线诊断测试 13 项（`tests/test_a1_diagnosis.py`），全部通过，零 API 调用。
- 修复 `test_preflight_failure_zero_calls_no_artifacts_nonzero_exit`：
  由"目录里永远不存在账本"改为 **delta 断言**（运行前后差集），并新增
  "空目录场景"与"历史账本 hash 不变"两项。既不放宽安全要求，也不要求删除真实运行现场。
- 全量 **386 passed / 2 deselected**。
- **真实付费调用 = 0。**
