# Pilot 运行配置说明（Commit A.6.3.1）

本文件是**运行配置**，不是协议。协议 v1 / v2 正文与 hash **均未修改**。

## 是否需要 v2.1？——不需要

| v2 原文 | A.6.3.1 的处理 | 关系 |
|---|---|---|
| 「Executor 模型必须钉到 `deepseek-v4-flash` 或 `-pro`，**这个选择需要你确认**」 | 钉死 `deepseek-v4-flash`，明确禁用 `-pro` | **收窄**（v2 留的选择被作出），非冲突 |
| v2 未提 thinking 模式 | 显式 `thinking=disabled` | **新增**，v2 无相反表述 |
| v2 未提计费模式细节 | 锁定标准速度 / global / 非 Batch / 第一方 API | **新增**，与 v2 §2 的价格表口径一致 |
| v2 分角色上限 2/2/1/16、总 21 | 未改 | 一致 |
| v2 预算 $1.50 / $3.00 / $25.00 | 未改 | 一致 |
| 12 道题、评分、门槛 | 未改 | 一致 |

结论：v2 正文没有任何一句因本次改动而变成假话 → **不新建 v2.1**，v2 hash 保持
`c76f589485e4ebfd728c27b653d2735f3ebd1c6930087c244e4efbdba9d66696`。

## 1. DeepSeek 模型钉死为 `deepseek-v4-flash`

两个 DeepSeek 角色（**Executor**、**Claim extractor**）统一使用 `deepseek-v4-flash`。

理由：
1. **官方声明 `deepseek-chat` 对应 `deepseek-v4-flash` 的非思考模式** —— 所以钉到 flash
   是**兼容迁移**，保持与此前运行行为一致；
2. **不是**因为评测结果不好而换更强模型 —— 恰恰相反，本次**明确不用** `deepseek-v4-pro`；
3. Flash 足以承担本 Pilot 的工具编排与结构化抽取任务；
4. Flash 价格低于 Pro（具体单价见 `pilot/prices.py`，本文件不复制价格数字）；
5. `deepseek-chat` 于 2026/07/24 15:59 UTC 弃用，且官方定价页已不单列其价格 →
   在价格表中标为 `unverified`，任何调用直接 fail-closed。

## 2. 显式关闭 thinking

DeepSeek V4 **默认 thinking enabled**，因此 Pilot 不依赖默认值，显式传：

```python
extra_body={"thinking": {"type": "disabled"}}
```

启动前对 **Executor** 与 **Claim extractor** 分别动态检查：
model ID 必须精确等于 `deepseek-v4-flash`；`thinking` 必须存在且为 `disabled`；
未设置 / 读不到 / 值不对 → fail-closed；不得设置 `reasoning_effort`；
非思考模式下不产生也不依赖 `reasoning_content`。

## 3. 价格单一来源

`pilot/prices.py` 是**唯一**价格权威（含 version / 生效日期 / 查询日期 / 官方 URL）。
`hard_gate` 不再持有任何单价常量，只通过公开接口
（`price_for` / `worst_case_usd` / `actual_usd` / `table_meta`）查询。
旧的软闸门 `pilot/budget_gate.py` 曾内嵌**第三份**价格表（含未核实的 deepseek-chat 估价），
已于本次**退役**（保留文件与 git 历史，构造即抛错）。
每条账本事件与运行产物都写入 `price_config_version`。

历史账本与历史价格记录**未删除、未改写**。

## 4. Anthropic 计费模式（resolved）

| 项 | 解析值 |
|---|---|
| platform | `anthropic_first_party` |
| resolved_speed | `standard`（禁止 Fast Mode） |
| resolved_inference_geo | `global`（禁止 US-only，其有 1.1x 乘数） |
| batch | `false`（禁止 Batch API 折扣路径） |

客户端没有显式 `speed` / `inference_geo` 字段时，接受「参数不存在 = 官方默认」，
但必须验证确实**没有** fast 或 US-only 参数，并把上表的 resolved 值记入运行配置。
端点非 `api.anthropic.com`（Bedrock / Vertex / Foundry）→ 拒绝启动。
