# Shadow Pilot Round 2 — 运行协议 **v2**（冻结）

> **v2 只修改运行与安全参数。**
> 12 道题、题目文字、最低证据要求、评分规则、硬失败条件、第七部分通过门槛
> **与 v1 逐字一致，一个字都没有改。**
>
> v1 (`SHADOW_PILOT_ROUND2_PROTOCOL.md`, sha256 `5d166bce…f54f0f22`) **原样保留、未被覆盖**。
> 本文件是**新增**的 v2，不是对 v1 的替换。

## 0. 为什么需要 v2（预先声明，不是事后调分）

1. **v1 的每题调用上限 12 是错的。** A.6.2 用真实编排 + 假模型实测：
   最少 **4**、典型 **13**、最坏 **21**。上限 12 落在典型值之下，
   A1 因此在第 14 次调用被中止 —— 这是**运行参数设定错误**，不是模型或系统的科研失败。
2. **v1 的 Budget Gate 只能软中止。** 它靠 `on_llm_end` 回调抛异常，而该回调发生在
   网络请求完成之后，且 LangChain 会捕获并吞掉回调内的异常 → 跳闸既撤不回已发生的调用，
   也拦不住下一次。
3. **A.6.2 已实现调用前硬中止。** `GatedModel` 在进入 `.invoke/.ainvoke/.stream` 之前
   同步完成检查与原子预留，越限时 provider **根本不会被调用**（有测试证明调用计数不增加）。
4. **v2 的性质**：一次**预先声明、版本化**的运行协议修订。
   修订对象只有运行与安全参数（调用上限、token 上限、预算、超时、重试、传输加固）。
   **不修改题目、不修改标准答案、不修改评分规则、不修改通过门槛。**
   本次修订发生在**任何 v2 下的真实付费调用之前**。

## 1. 核实过的模型与价格（v2 §2）

价格表：`pilot/prices.py`，版本 **`2026-07-20.1`**，生效日期 **2026-07-20**，查询日期 **2026-07-20**。
**全部来自官方供应商文档**，未使用记忆、第三方博客或旧报告估算。

### Anthropic — 来源：<https://platform.claude.com/docs/en/about-claude/pricing>

| 模型 ID | 基础输入 | 5m 缓存写 | 1h 缓存写 | 缓存读 | 输出 |
|---|---|---|---|---|---|
| `claude-opus-4-8` | $5.00 / MTok | $6.25 | $10.00 | $0.50 | $25.00 / MTok |

usage 字段：`input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`

### DeepSeek — 来源：<https://api-docs.deepseek.com/quick_start/pricing>

| 模型 ID | 输入(缓存未命中) | 输入(缓存命中) | 输出 | 状态 |
|---|---|---|---|---|
| `deepseek-v4-flash` | $0.14 / MTok | $0.0028 | $0.28 / MTok | **verified** |
| `deepseek-v4-pro` | $0.435 / MTok | $0.003625 | $0.87 / MTok | **verified** |
| `deepseek-chat` | — | — | — | ⚠️ **unverified** |

缓存 usage 字段：`prompt_cache_hit_tokens`, `prompt_cache_miss_tokens`
（来源 <https://api-docs.deepseek.com/guides/kv_cache>）。
`prompt_tokens` / `completion_tokens` 为 OpenAI 兼容惯例，该页未逐字列出，已在价格表中注明。

> ### ⚠️ 阻断项：仓库默认的 `deepseek-chat` 不可用于 v2 运行
> 官方定价页**已不再单列 `deepseek-chat` 的价格**，并公告
> **2026/07/24 15:59 UTC 弃用**（映射到 `deepseek-v4-flash` 的非思考模式）。
> 按 v2 §2「无法从官方来源确认 → 标记 unverified → Pilot fail-closed」：
> `price_for("deepseek-chat")` 抛 `PriceUnverified`，**Pilot 拒绝启动**。
> 运行前必须把 `DEEPSEEK_MODEL` 显式钉到 `deepseek-v4-flash` 或 `deepseek-v4-pro`，
> 并在报告中记录所选 ID。**这个选择需要你确认，我没有替你决定。**

价格表规则：版本号 ✅ / 生效日期 ✅ / 来源 ✅ / 未知模型拒绝 ✅ /
未知 usage 字段拒绝 ✅ / **绝不静默回退为 Opus 或其它模型价格** ✅
（A.6.2 曾把 14 次调用全按 Opus 计价，正是因为旧代码有静默回退；已删除。）

## 2. 传输加固（v2 §3）

Pilot 专用模型实例（`pilot/paid_transport.py`）强制：

| 项 | 值 |
|---|---|
| `max_retries` | **0**（禁止 SDK 在一次 GatedModel 调用内偷发第二次 HTTP 请求） |
| 连接超时 | 10 s |
| 读取超时 | 90 s |
| 单次请求总时限 | 120 s |

**只改 Pilot 专用构造路径**：`build_anthropic()` / `build_deepseek()` 新建实例，
生产模块 `ssc_pi_agent.py` 的默认模型行为**零改动**。

启动前对 **planner / executor / verifier / claim_extractor** 四个角色逐个动态检查，
任一角色出现以下情况即 **拒绝启动**（不降级）：
未经 GatedModel 包装 / `max_retries` ≠ 0 / 无有限 timeout / 无有限 max_tokens /
模型名不可识别 / 价格不可识别。

## 3. 分角色调用上限（v2 §4）

依据 A.6.2 实测（最少 4 / 典型 13 / 最坏 21）：

| 角色 | 每题上限 |
|---|---|
| Planner | **2** |
| Verifier | **2** |
| Claim extractor | **1** |
| Executor | **16** |
| **单题总逻辑调用** | **21** |
| SDK 自动重试 | **0** |
| Pilot 自管重试 | **0** |
| 最大外层 iteration | **2** |

**任何角色不得借用其它角色的剩余额度**（`max_calls_per_role` 按角色独立计数，按题重置；
未配置上限的角色直接拒绝）。

**21 是硬上限，不是目标值。** 报告中，任何高于典型值 13 的调用数都必须
作为**效率异常**记录并归因，不得因为"没超 21"就当作正常。

## 4. token 与费用（v2 §5）

### 分角色输出 token 上限

| 角色 | `max_tokens` |
|---|---|
| Planner | 2,000 |
| Executor | 3,000 |
| Verifier | 2,000 |
| Claim extractor | 2,000 |

（`ChatAnthropic` 与 `ChatOpenAI` 的参数名均为 `max_tokens`，已在构造处显式传入并有测试验证。）

### 最坏费用公式

```
worst_case_usd(call) = est_input_tokens / 1e6 × 最贵输入单价
                     + max_tokens        / 1e6 × 输出单价
```
- 最贵输入单价：取该模型输入侧所有档位中最贵一档
  （Opus 4.8 = 1h 缓存写 $10.00；DeepSeek = 缓存未命中价）—— 保守方向。
- `est_input_tokens` 按字符数 / 3 估算（比常见的 /4 更保守）。

### 单题最坏费用上界（以 `deepseek-v4-flash` 为 Executor）

```
Planner  : 2 × (est_in×$10/M + 2000×$25/M)  = 2 × ($0.05/M·est + $0.0500)
Verifier : 2 × (est_in×$10/M + 2000×$25/M)  = 同上
Claim    : 1 × (est_in×$0.14/M + 2000×$0.28/M) ≈ $0.00056 + 极小
Executor : 16 × (est_in×$0.14/M + 3000×$0.28/M) ≈ 16 × $0.00084 ≈ $0.0134
```
Opus 侧 4 次输出封顶 = 4 × 2000 × $25/M = **$0.20**；
输入侧按每次 30k token 估：4 × 30000 × $10/M = **$1.20**。
→ **单题最坏 ≈ $1.42**，落在候选上限 $1.50 之内。

### 冻结的预算与时限

| 项 | 值 |
|---|---|
| 单题预算上限 | **$1.50** |
| Stage 1（两题）预算上限 | **$3.00** |
| Round 2 总预算 | **$25.00** |
| 单题运行时间 | **600 s** |
| Stage 1 总运行时间 | **1,500 s** |

**这些值不得机械套用**：若「当前已消费 + 本次最坏费用」无法落在上限内，
Pilot 必须**报告计算结果并停止**，由你决定是否调整。
**Pilot 不得自行提高预算。**

## 5. 超时与取消语义（v2 §6）

| 情形 | 记账状态 | 是否可能已计费 |
|---|---|---|
| 1 调用前已超时/越限 | `rejected_before_request` | **否**（请求未发出） |
| 2 provider request timeout | `provider_may_have_billed` | **是** |
| 3 用户取消 | `user_cancelled` | 视发出与否；后续调用一律拒绝 |
| 4 usage 未知 | `usage_unknown` | 保留**最坏费用** reservation |
| 5 网络错误 | `network_error_no_auto_retry` | 保守视为可能已计费；**不自动重试** |
| 6 任务级超时 | 任务标记 stopped | 之后**所有角色**调用一律拒绝 |

**绝不把 provider timeout 写成「确认未计费」。** 只有第 1 类才允许判定为未计费。

## 6. 与 v1 的唯一差异清单

| 项 | v1 | v2 |
|---|---|---|
| 每题调用上限 | 12（单一总数） | 分角色 2/2/1/16，总 21 |
| 中止方式 | callback 软中止 | 调用前硬中止 |
| SDK 重试 | 未设置（默认 2） | **0** |
| timeout | 未设置 | 连接 10s / 读取 90s / 总 120s |
| `max_tokens` | 未设置 | 分角色 2000/3000/2000/2000 |
| 价格来源 | 估算，未核实；未知模型静默回退 Opus | 官方核实、版本化；未知即拒绝 |
| Executor 模型 | `deepseek-chat` | **必须钉到 `deepseek-v4-flash`/`-pro`** |
| 单题预算 | 无 | $1.50 |
| Stage 1 预算 | 无 | $3.00 |
| Stage 1 总时限 | 无 | 1,500 s |
| **题目 / 评分 / 门槛** | — | **完全未改** |

## 7. 版本与冻结 hash

- 版本：**v2**；冻结时间：2026-07-20
- 代码基线：Commit A.6.2（dev `f0a8d5f` / public `32a2e04`）
- v1 hash（未变）：`5d166bce159de665c4df677aef6765803575a48827afdc5d061cb49ff54f0f22`
- 本文件 SHA-256：见 `SHADOW_PILOT_ROUND2_PROTOCOL_V2.sha256`
- 这 12 道题仍然只是**开发期 Pilot**，不是金标准 benchmark，不得用于公开宣称性能。
