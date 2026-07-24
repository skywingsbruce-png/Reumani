# Shadow Pilot Round 2 — A1-rerun3-deterministic 结果

**这是 A1 第四次总运行、第三次 rerun，采用确定性 Exact-ID 架构。**

**结论：最小科研闭环在架构层首次完整跑通**（Verifier / Claim / Shadow / 科研 Manifest
全部实际运行，Executor=0，无 ReAct 循环）；**但最终用户答案由旧 Verifier 判为「未验证」**——
旧 Verifier（opus）声称争议 DOI 有效，与确定性解析器的 not_found 相左。经独立多机构复核，
**DOI 确实未注册，解析器正确、旧 Verifier 的断言为无来源支撑的臆断**。按协议：旧 Verifier
判「不通过」不自动等于技术失败，且旧 Verifier 保留最终裁决权——此为合法科研结果，理由已记录。

- run_id：`A1-rerun3-deterministic-3ccaed24`（全新，未复用/覆盖任何历史）
- 运行基线：dev `46a8094` / public `62727a5` / CI #46 9/9
- 真实付费：verifier 1（opus）+ claim_extractor 1（deepseek-flash）；**费用 $0.007876**（≤ $1.50）
- Planner=0、Executor=0、search_literature=0、legacy search_evidence=0、retry=0、open reservation=0

---

## 1. 是否完成最小科研闭环

**技术闭环：完成。** 首次有 A1 运行走完 `Verifier → Claim → Claim-Evidence Graph → Shadow →
科研 Manifest` 全链，且未进入 Planner/ReAct（前三次失败的根因）。逐项：

| 闭环要求 | 结果 |
|---|---|
| all_terminal=true | ✅ |
| verified PMID 构建 EvidenceCard | ✅ |
| not_found DOI 不构卡 | ✅ |
| EvidenceCard 有 64 位 SHA-256 | ✅ `ca9333f5…9271be` |
| 旧 Verifier 真实运行 | ✅ 1 次 |
| Claim extractor 真实运行 | ✅ 1 次（计入 claim_extractor 角色） |
| Claim-Evidence Graph 生成 | ✅ 3 条 judged claim |
| Shadow 真实运行 | ✅ shadow_status=ok |
| 科研 Manifest 生成 | ✅ runmanifest-v1 |
| 无伪造引用 | ✅ |
| 无敏感信息泄露 | ✅ |
| 最终答案由旧 Verifier 决定 | ✅（判 not_passed → 「未验证」） |

**科学层：旧 Verifier 判「未验证」。** 见 §5/§6 的理由与复核。

## 2. 两个 ID 逐来源状态

| ID | 来源 | 状态 | 综合 |
|---|---|---|---|
| PMID `41657283` | PubMed | `exact_hit` | **verified** |
| | Europe PMC | `exact_hit` | |
| DOI `10.1080/03009742.2024.2302553` | Crossref | `zero_hits` | **not_found** |
| | doi.org | `zero_hits` | |
| | Europe PMC（补充） | `zero_hits` | |

DOI 的 `not_found` 来自**三个来源的 zero_hits**（非网络错误），未用相似论文/其它 DOI 替代。

## 3. EvidenceCard

**仅 1 张**，对应 verified 的 PMID：

| 字段 | 值 |
|---|---|
| pmid | 41657283 |
| doi | 10.1080/07853890.2026.2627057（该文自身 DOI，取自 PubMed 结构化 articleids） |
| title | From technological iteration to clinical breakthrough: advances of CAR-T… |
| tier / grade | abstract / 初筛 |
| content_level | metadata_only |
| provenance.source | https://pubmed.ncbi.nlm.nih.gov/41657283/ |
| source_ids | ["41657283"] |
| content_hash | `ca9333f5c43960b11e6c8908d88c540ed6c77fef153ce49e55ed341aec9271be`（64, sha256） |

（该 hash 与 A.7.1.1 canary 一致 → 相同元数据跨运行稳定。）not_found 的 DOI **未构卡**，
未从自然语言 content 猜测 ID。

## 4. 四角色调用次数

| 角色 | 模型 | 次数 | 上限 |
|---|---|---:|---:|
| Planner | — | **0** | 2 |
| Executor | — | **0** | 16 |
| Verifier | claude-opus-4-8 | 1 | 2 |
| Claim extractor | deepseek-v4-flash | 1 | 1 |

`calls_by_role = {verifier: 1, claim_extractor: 1}`（Claim 计入 claim_extractor，**未**计入
executor——A.7.1.3 修复生效）。`calls_by_model = {claude-opus-4-8: 1, deepseek-v4-flash: 1}`。

## 5. Verifier / Claim / Shadow 结果

- **旧 Verifier（1 次，opus）**：`not_passed`。理由（原文）：
  「第二条记录 DOI 10.1080/03009742.2024.2302553 被报告为 not_found，但该 DOI 实际对应有效
  期刊文章，研究问题要求的标题、年份、期刊未能提供，未真正回答。」
  missing：要求核实该 DOI 是否确实无法检索到而非工具遗漏。
  → 旧 Verifier 保留裁决权，最终用户答案为「未验证 / 证据不足（not_passed）」。
- **Claim extractor（1 次，deepseek-flash）**：从 verified PMID 提取 3 条原子 Claim
  （CAR-T 在自身免疫病的进展/临床突破/技术迭代），claim_extraction_error=None。
- **Claim-Evidence Graph**：3 条 Claim 的 verdict 均 `not_supported`——因为证据卡为
  metadata_only（无 supporting_excerpt），不足以支撑关键结论。这是保守、正确的判定。
- **Shadow**：运行，shadow_status=ok，生成 runmanifest-v1 科研 Manifest，
  shadow_verifier `not_passed`（human_review=true）。

## 6. old / shadow 分歧

**无分歧。** `comparison = {old_passed: False, shadow_passed: False, agree: True,
divergence: False}`——旧 Verifier 与四层 Shadow Verifier **一致判 not_passed**。

**关于 Verifier 与解析器的争议（重要，已独立复核）**：旧 Verifier 断言争议 DOI「实际对应
有效期刊文章」。为避免掩盖可能的解析器错误，做了**独立零 LLM 多机构复核**
（`A1_rerun3_doi_recheck.json`）：

| 复核方式 | 结果 |
|---|---|
| doi.org handle API | HTTP 404，responseCode=**100（handle not found）** |
| doi.org 直接解析 | HTTP 404，未跳转（停留在 doi.org） |
| Crossref works/{doi} | HTTP 404 |
| DataCite dois/{doi} | HTTP 404 |
| Europe PMC 精确 DOI | hitCount=0 |
| 对照（已知有效 DOI 10.1038/s41586-020-2649-2） | handle responseCode=**1（正常解析）** |

结论：**该 DOI 在所有注册机构均未注册**，对照 DOI 正常解析（证明方法有效）。
→ **确定性解析器的 not_found 正确**；旧 Verifier 的「有效」为**无来源支撑的臆断**（正是本架构
要防止的 LLM 幻觉）。旧 Verifier 因此错误地拒绝了一个正确答案——但它保留裁决权，行为合法，
理由如实记录。

## 7. token 与费用

| 项 | 值 |
|---|---|
| actual_usd | **$0.007876** |
| reserved_open_usd | $0.0 |
| committed_usd | $0.007876 |
| retries | 0 |
| usage_unknown | 0 |
| open reservation | 0（全部 reconciled） |
| 账本 | 21686 → 22755 bytes；reserved 40→42，reconciled 40→42，open=0 |

远低于单题预算 $1.50。SDK/LLM 重试均为 0。

## 8. Manifest 与安全

- 科研 Manifest：runmanifest-v1，run_id=`A1-rerun3-deterministic-3ccaed24`。
- 交付均脱敏（敏感信息扫描 0 命中）：无 API key/.env/认证头/完整 prompt/原始 HTTP 响应/
  绝对路径/原始共享账本/未脱敏 run 文件。原始 run 落地文件仅本地保存、**不提交**。

## 9. 四次 A1 并列比较

| 指标 | Original | Rerun1 | Rerun2 | Rerun3 |
|---|---:|---:|---:|---:|
| 架构 | ReAct | ReAct+Guard | ReAct+可信 Trace | **Deterministic** |
| Planner | 2 | 2 | 2 | **0** |
| Executor | 16 | 9 | 9 | **0** |
| Verifier | 0 | 0 | 0 | **1** |
| Claim | 0 | 0 | 0 | **1** |
| EvidenceCard | 0 | 0 | 0 | **1（verified PMID）** |
| Shadow | 未运行 | 未运行 | 未运行 | **运行（ok）** |
| 科研 Manifest | 无 | 失败诊断 | 失败诊断 | **生成** |
| 费用 | $0.085541 | $0.072872 | $0.086985 | **$0.007876** |
| 失败点/结果 | role cap，失败 | loop guard，失败 | loop guard，失败 | **闭环完成；旧 Verifier 判未验证** |

**声明**：四次软件条件不同，不隐藏前三次失败，**不把 rerun3 说成同条件的性能提升**。
前三次是 ReAct 架构在 exact-ID 上不收敛（Executor 耗尽轮次，从未到 Verifier/Claim/Shadow）；
rerun3 是**新的确定性架构**，首次跑通完整验证/证据链。rerun3 的低费用是因为绕开了
Planner/Executor 的多轮 LLM，不能与前三次直接比"性能"。

## 10. 全部异常

1. 旧 Verifier 判 `not_passed`（**非停止条件、非技术失败**）：因其断言争议 DOI 有效——
   经独立复核证否，属旧 Verifier 的 LLM 幻觉；解析器正确。
2. 3 条 Claim 均 `not_supported`：metadata_only 证据卡无 supporting_excerpt，保守正确。

无以下任一停止条件：Planner/Executor 误调用、search_literature/旧 search_evidence 调用、
Claim 记为 Executor、Claim 绕过 Gate、网络错误判 not_found、not_found 构卡、content ID 进卡、
usage 缺失、预算越界、开放 reservation、协议 hash 变化、历史结果被覆盖、敏感信息泄露。

## 11. commit / CI / 回滚

- 提交（仅脱敏产物）：`pilot(round2): record deterministic A1 rerun3 result`
- 交付：本报告 + `pilot/round2_results/A1_rerun3_{result_sanitized,doi_recheck}.json`
  + 两次 preassert 记录（已在库）。
- 回滚：`git revert <rerun3 commit>`（纯新增文档/产物，无代码改动）。

## 12. 是否建议进入 B1

**暂不建议自动进入 B1。** rerun3 在技术架构上是成功的（确定性闭环首次跑通、根因消除、
Executor=0、费用极低、账本干净），但暴露出一个**新的、独立的问题值得先处理**：
**旧 Verifier（LLM）会以无来源支撑的断言否决确定性解析器的正确结果**（本次把正确的 not_found
判成"该 DOI 有效"而拒绝）。这不是本次要修的范围，但会系统性影响 exact-ID 任务的最终裁决质量。

建议下一步（**需你单独批准**，非本次执行）：评估是否给旧 Verifier 提供
"结构化解析结果 + 权威来源状态"作为**只读事实上下文**（不改其裁决权、不改 Prompt 科学逻辑），
使其不必凭参数化记忆臆断 ID 存在性；或明确 exact-ID 任务下"解析器权威来源判定优先于
Verifier 对 ID 存在性的主观断言"。在此评估完成前，不进入 B1、不开始 Commit B、不再跑 A1。
