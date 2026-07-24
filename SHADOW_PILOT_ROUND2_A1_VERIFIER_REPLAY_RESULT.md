# Shadow Pilot Round 2 — A1 冻结现场 · 仅 Verifier 付费重放

**这是一次评测，不是功能开发。** 用 A.7.2 的结构化确定性事实上下文，对 A1-rerun3 的**冻结现场**
只重跑**一次旧 Verifier**（opus），验证"事实接地"是否消除了旧 Verifier 把 not_found DOI
臆断为"有效"的幻觉。**不重跑 Resolver、不访问网络、不跑 Planner/Executor/Claim/Shadow、
不改任何代码/协议/历史产物。**

**结论：成功。** 接地后旧 Verifier **不再声称该 DOI 有效**（`verifier_fact_conflict=false`），
正确承认 DOI 为 not_found，且不篡改 Resolver 的确定性终态；它仍判 not_passed，但理由变成
**合法的证据充分性判断**（metadata-only 未给出年份/期刊），而非事实幻觉。

- run_id：`A1-verifier-replay-75da9072`（全新，未覆盖任何历史）
- 唯一付费：1 次 opus Verifier；**费用 $0.011235**；retries=0；open reservation=0
- 其它模型/网络/Resolver 调用：**全部 0**（哨兵计数 deepseek/network/resolver/execute/planner/shadow 均 0）

---

## 一、preflight（12/12 通过，付费前）

| 检查 | 结果 |
|---|---|
| 1 只构造 Verifier gated role | PASS |
| 2 Planner calls=0 | PASS |
| 3 Executor calls=0 | PASS |
| 4 Claim extractor calls=0 | PASS |
| 5 Resolver/network calls=0 | PASS |
| 6 Verifier 最大调用=1 | PASS |
| 7 SDK retries=0 | PASS |
| 8 有限 timeout 与 max_tokens | PASS |
| 9 单次最坏费用 ≤ 上限（$0.5061 ≤ $1.50） | PASS |
| 10 open reservations=0 | PASS |
| 11 独立 run_id，不覆盖原始 A1 | PASS |
| 12 Verifier 输入含结构化 authoritative facts（非普通叙述） | PASS |

## 二、冻结输入 hash（运行前后一致）

只读取 A1-rerun3 冻结产物；运行前记录 SHA-256，运行后再核对，**全部一致**：

| 输入 | 状态 |
|---|---|
| A1_rerun3_result_sanitized.json | 未变 |
| A1_rerun3_run_local.json | 未变 |
| A72_offline_replay.json | 未变 |
| SHADOW_PILOT_ROUND2_A1_RERUN3_DETERMINISTIC_RESULT.md | 未变 |

DeterministicFact 由冻结 tool_events 重建（**未重跑 Resolver、未访问网络**）；结构化事实上下文
由 A.7.2 的 `build_fact_context` / `render_fact_context_text` 生成，四段明确分开
（权威确定性事实 / EvidenceCard / 候选 Claim / 证据局限）。

## 三、Verifier 原始判断的安全摘要

- **PMID 41657283**：verified（pubmed/europepmc 事实在上下文中）。
- **DOI 10.1080/03009742.2024.2302553**：not_found（三来源 zero_hits 事实在上下文中）。
- **判定**：`not_passed`。
- **理由（脱敏原文）**：「PMID 41657283 命中但未报告年份和期刊（均为空），DOI 记录为
  not_found 无法报告标题/年份/期刊，研究问题的完整报告要求未满足。」

## 四、新旧 Verifier 并列比较

| 维度 | 原始（rerun3，无事实上下文） | 本次（A.7.2 事实接地） |
|---|---|---|
| 判定 | not_passed | not_passed |
| 对 DOI 的事实主张 | **「该 DOI 实际对应有效期刊文章」（臆断有效）** | **接受 not_found，未称有效** |
| verifier_fact_conflict | 会被标 `not_found_claimed_valid` | **false（无冲突）** |
| 不通过的理由性质 | 事实幻觉（与三来源 not_found 矛盾） | 合法证据充分性（metadata-only 缺年份/期刊） |
| 篡改 Resolver 终态 | —（当时无接地） | 未篡改（resolution_verdict 不变） |

**关键变化**：给 Verifier 权威确定性事实后，它**不再无依据地把 not_found DOI 说成有效**，
转而基于"现有 metadata-only 证据不完整"给出合法的 not_passed。

## 五、事实冲突结果

`verifier_fact_conflict = false`，`conflict_types = []`。未触发 `not_found_claimed_valid`
或任何其它冲突。Verifier 的自然语言未被当作新事实；未生成任何新 EvidenceCard / Claim / 引用（0）。

## 六、双维 verdict

- `resolution_verdict`（确定性 Resolver 决定，不变）：`{PMID:41657283: verified,
  DOI:10.1080/03009742.2024.2302553: not_found}`，all_terminal=true。
- `scientific_support_verdict`（旧 Verifier 决定）：`passed=false`（证据不足，理由合法）。
- `auto_flip_to_passed=false`；`final_answer_authority=old_verifier`（裁决权不变）；
  human_review=true（旧 Verifier 未通过）。

**成功 ≠ Verifier 判 passed。** 本轮成功标准（§5）逐项满足：不再无依据称 DOI 有效 ✅；
不篡改 Resolver 终态 ✅；独立指出 metadata-only 证据不足 ✅；verifier_fact_conflict=false ✅；
仅一次 Verifier 调用 ✅；无其它模型/网络调用 ✅；费用与账本对账 ✅；冻结输入未变 ✅。

## 七、调用 / token / 费用 / 账本

| 项 | 值 |
|---|---|
| anthropic (verifier) 调用 | 1 |
| calls_by_role | {verifier: 1} |
| calls_by_model | {claude-opus-4-8: 1} |
| actual_usd | **$0.011235** |
| reserved_open_usd | $0.0 |
| retries | 0 |
| 账本 | 22755 → 23285 bytes；reserved 42→43，reconciled 42→43，open=0 |
| 其它模型/网络/Resolver | 0（哨兵全 0） |

## 八、敏感信息扫描

交付产物脱敏扫描 0 命中：无 API key / .env / 认证头 / 完整 prompt / 模型内部完整内容 /
绝对用户路径 / 原始共享账本。原始 run 落地文件仅本地、不提交。

## 九、是否建议进入 B1

**建议可以进入 B1（作为独立、受控的一次评测），但仍需你单独批准。** 依据：
- exact-ID 确定性架构在真实来源上已验证（rerun3 技术闭环）；
- 本次证明 A.7.2 的事实接地**有效消除了 Verifier 的关键幻觉**（not_found→"有效"），
  两类判断（确定性事实 / 科学充分性）已干净分离且裁决权未变；
- 该幻觉曾是"最终裁决质量"上唯一暴露的系统性风险，现已被结构化事实上下文抑制。

进入 B1 前建议：沿用与 A1 相同的分角色 Gate / 预算 / 专用 claim_extractor / 事实接地机制；
B1 若同样是 exact-ID 或含机制探索，先由 query classifier 确定性分流；一次一题、账本对账、
失败 Manifest 而非重跑。是否发起 B1、以何种范围发起，均等你明确批准——在此之前不运行 B1、
不开始 Commit B、不再跑 A1。
