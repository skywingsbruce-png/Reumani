# A.7.5.6.1 — 审批透明度、结构化输出 sizing 与保守费用预留

**真实付费模型调用 = 0。** 本阶段全部离线完成，只用 fake provider 验证；未运行第二次 Canary。

| 项 | 值 |
|---|---|
| 主体提交 | dev `319fefb` / public `774270a` · CI #84 11/11 |
| 缺口补齐 | dev `7f0952e` / public `6639394` · CI #85 11/11 |
| Python | 1166 passed |
| UI | 53 passed |
| 三角色最坏费用 | **$0.136054 ≤ $0.15**（90.7%，余量 $0.013946） |
| 第一次 Canary | 提交、产物、哈希**逐字节未变** |

---

## 1. 修改文件

`pilot/`：`gated_research_executor.py`、`hard_gate.py`、`hitl.py`、`research_contracts.py`、
`research_results.py`、`runtime_events.py`
`tests/`：`test_gated_research_executor.py`
UI：`components/HitlPanel.tsx`、`components/ResearchPanel.tsx`、`store/LabStore.tsx`、
`__tests__/api.test.tsx`、`contracts/reumani-event-v1.json`
文档：2 张审批/全链截图

## 2. Approval 卡的旧问题（第一次 Canary 实证）

- 显示 spec 里的 3 条 `evidence_refs`，**冒充**冻结子集的 6 张核心卡；
- `max_cost_usd` 渲染为 **`0.00`**，而真实闸门预算是 `$0.15`；
- 不展示 subset / source pack / protocol 指纹，也不展示 direct / indirect /
  `direct_human_causal_count`；
- 对每个 research run 都写着「此为零付费测试夹具，不调用真实模型」——在真实付费 Canary 上
  **这句话是假的**。

根因是结构性的：冻结证据在 `validate_evidence` 阶段加载，即**批准之后**，卡片物理上不可能
显示还不存在的事实。

## 3. 真实执行预览的来源

新增 `ResearchExecutionPreview`（`research-execution-preview-v1`），由
**真实 `FrozenEvidenceLoader` + 真实 `HardBudgetGate` + 已注入的三个角色**生成，
绝不来自客户端参数或占位 spec。确定性、零模型调用、零网络、零 `.env`。

`preview_hash` 在批准瞬间被冻结进 `action_hash`；执行前重新求值，**任一字段漂移即拒绝执行，
且发生在第一次 provider 调用之前**。若最坏费用超过任务预算，连审批预览都拒绝生成。

## 4. UI 现在展示的字段

executor id · 6 张核心卡 / 2 张仅背景 · SSc 直接 3 · 非 SSc 间接 3 ·
`direct_human_causal_count = 0` · 因果上限 `preclinical_perturbation_support` ·
subset / source pack / protocol 指纹 · 三角色各 1 次（不可互借）· 总调用上限 3 ·
**真实 Gate 预算 US$0.15000** · 重算最坏费用及其占比 ·
network / planner / code / device 全部禁止 · 预期产物 `research-artifact-v1` ·
证据层级 `abstract_only` · `preview_hash`。

计费提示改以**是否挂了非零预算硬闸门**为判据，而不是证据夹具标志——前端无法分辨 provider
真假，只能多警告，不能少警告。

## 5. Synthesizer schema 压缩

测量发现两处**重复付费**：

1. `authoritative_facts` 本身已含逐卡明细，而证据表又发了一遍同样的 scope/tier/content_level；
2. Verifier 与 Claim extractor 的 Prompt 把全部不可信正文再嵌入一次。

按 A.7.5.5 §6.2/§6.3，Verifier 需要的是每张卡的 scope/tier/content level（权威元数据），
**不是原文**；Claim extractor 只需要 synthesis + verifier + 允许的 evidence id。
因此正文**只发给 Synthesizer**，后续角色改用紧凑权威表 + 上游结构化结果。
Synthesizer prompt **8325 → 4510 字符**，同时缩小了 Prompt 注入面。

结构化输出加硬性上限并**拒绝**超长（不再静默截断，静默截断会把越界输出伪装成合法结果）：

| 字段 | 上限 |
|---|---|
| summary | 320 字符 |
| 单条陈述 | 140 字符 |
| supported / unsupported | 各 4 条 |
| contradictions / evidence_gaps | 各 2 条 |
| limitations | 3 条 |
| citations | 6 个 evidence_id（≤32 字符，只存 id） |

三类证据区分与因果边界全部保留。

## 6. 三个角色的 max_tokens

由「最坏合法 JSON ÷ 2.0 字符每 token」反推，并在构造时由
`assert_max_tokens_sufficient` 断言（该断言当场揪出两个我写错的值）：

| 角色 | 模型 | 最坏合法 JSON | 需要 | max_tokens | 最坏费用 |
|---|---|---|---|---|---|
| synthesizer | claude-opus-4-8 | 3012 字符 | 1506 | **1600** | $0.06981 |
| verifier | claude-opus-4-8 | 2060 字符 | 1030 | **1150** | $0.06492 |
| claim_extractor | deepseek-v4-flash | 4700 字符 | 2350 | **2400** | $0.00132 |

## 7. 截断分类

`OutputTruncated` 是独立于普通 schema 错误的失败类。判据：finish/stop reason 表示长度停止，
或 JSON 解析失败且 usage 已达 max_tokens。

不补 JSON、不重试、不自动提高 max_tokens、不进入 Verifier、fail-closed。
失败 Manifest 记录 role / finish_reason / output_tokens / configured_max_tokens /
`output_truncated=true`，**不保存被截断的输出正文**。
UI 失败卡显示独立的 `output_truncated` 标记与「需人工复核」，同样只显示元数据。

## 8. 旧估算误差

旧口径 `len/3`：估 **2512**，实际 **3293**，比值 **1.311**。
真实密度约 **2.29 字符/token**（CJK + 结构化 JSON 比 3 更密），故旧口径必然低估。

## 9. 新估算方法

`2.0 字符/token + 200 token 消息包装开销 + 1.15 安全系数`，计入 system/user/schema/
evidence facts/untrusted excerpts 的完整 payload。

估算口径本身现在**落盘可审计**：`reserved` 记录 `safety_multiplier` / `chars_per_token` /
`message_overhead_tokens`；`reconciled` 记录 `estimated_input_tokens` /
`actual_input_tokens` / `estimation_ratio` / `reserved_cost_usd` / **`under_reserved`**。

## 10–11. 第一次 Canary 回归与新预留

以第一次 Canary 为**永久回归下界**（仅作下界，不写死为通用估算值）：

```
old_estimate_input_tokens = 2512
actual_input_tokens       = 3293
actual_cost               = $0.07043
```

新估算对该冻结 Prompt：**4563 ≥ 3293** ✅ ·
新预留：**$0.08313 ≥ $0.07043** ✅

## 12–13. 三个角色最坏费用是否 ≤ $0.15

**$0.06981 + $0.06492 + $0.00132 = $0.136054 ≤ $0.15 ✅**（预算**未提高**）

关键事实：约束来自**输入**按含 cache creation 的最贵单价计——Opus 的
`cache_write_1h` = **$10/MTok**，而非 $5 基础价。这正是第一次 Canary 的实际计费口径
（$0.07043 − 1500×$25/M ÷ 3293 = $10/MTok），已由测试钉死。
诚实估算下，最初所有候选配置**都超预算**（最低 $0.198）；只有完成上述真实压缩后才落进 $0.15。

## 14–16. fake 验证

- **成功全链**：8 阶段，synthesizer/verifier/claim_extractor = **1/1/1**，artifact = 1，
  审批前 provider 调用 = **0**，open reservations = 0；
  planner / react / resolver / network / code / device 全部 0。
- **截断路径**：仅 **1** 次调用，verifier = 0，claim_extractor = 0，无 Artifact，
  `run_failed` + Manifest 带 `output_truncated`，不重试。
- **预览漂移**：改 subset hash / 预算 / role cap / evidence count → 拒绝执行，provider 调用 = 0。

## 17. 新增测试（共 +14）

执行预览来自真实 loader/gate · 错误 evidence count 回归 · 错误 `0.00` 预算回归 ·
preview hash 稳定且覆盖每个字段 · preview 漂移拒绝 · schema 超长拒绝 · 贴限合法 JSON 接受 ·
max_tokens 覆盖最坏合法输出 · 截断分类 · `finish_reason=max_tokens` · 不补 JSON · 不重试 ·
截断全链 Manifest · 旧估算 fixture · 新估算 ≥ 3293 · 新预留 ≥ $0.07043 ·
cache creation 计价 · 三角色最坏费用 ≤ $0.15 · 超预算 provider 前拒绝 · 账本估算审计字段 ·
UI Approval 字段 · UI 截断失败显示 · 敏感扫描 · 零真实调用哨兵 · 冻结输入 hash。

## 18–23. 全量验证

Python **1166 passed** · UI **53 passed** · typecheck / lint（0 error）/ build 干净 ·
**干净克隆 1163 passed + 1 skipped**，三个哈希与 `subset_id` 可复现 ·
CI **#85 11/11**（windows-latest + ubuntu-latest）· `git diff --check` 干净 ·
敏感扫描无命中 · 证据包与第一次 Canary 产物**逐字节未变** ·
事件契约 safe_payload 88 → 94 键，Python 与 TS 一致 · **真实付费调用 0**。

## 24–25. 提交与回滚

```
dev    319fefb  +  7f0952e
public 774270a  +  6639394
```

回滚：`git revert 7f0952e` / `git revert 319fefb`（public 对应 `6639394` / `774270a`）。

## 26. 已知限制

1. **预算余量仅 9.3%**，且 schema caps 是"very-tight"。四类证据区分保留，但表达空间确实被
   预算压缩了——这是代价，不是免费的。
2. 截断判定依赖 provider 回包的 `finish_reason`/`stop_reason` 或 usage 达到 max_tokens；
   若某 provider 两者都不给，会退回普通 schema 错误分类（**仍然 fail-closed**，只是分类不够精确）。
3. `estimation_ratio` 依赖进程内 `call_uid → 估算值` 映射；跨进程重启后对旧 `call_uid` 结算
   会记为 `null`（不影响预算强制，只影响审计字段）。
4. 估算器是全局的，对非 CJK 任务偏保守（多预留）。

### 过程中发现并修复的两个额外缺陷

- 事件 `safe_payload` 会拒绝任何含 `token` 子串的键（用于拦截 auth token），
  因此 `output_tokens` / `configured_max_tokens` 是非法事件键；已改用
  `output_size` / `configured_output_limit`。该安全规则**未被放宽**。
- 上述拒绝会回滚失败事务，把 run **永远留在 `running`**（fail-open）。
  `_commit_stage_failure` 现在会退回最小载荷再收敛一次，保证 run 始终收敛到 `failed`。

## 27. 是否建议批准第二次且最后一次真实 HITL Canary

**建议批准。** 三个被实证的问题均已修复并有回归测试守住；人在批准时能看到真实证据边界、
每角色输出上限、最坏费用与硬闸门；失败时能一眼分辨截断。

需要你知情的客观事实：预算余量只有 9.3%。若真实输入超出预览估算，硬闸门会在
**provider 调用之前**拒绝，而不是超支。

---

*本阶段结束时未运行第二次 Canary、未调用真实模型、未修改证据包、未迁移 Biomni、
未开始 Commit B。*
