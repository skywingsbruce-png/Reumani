# A.7.1.1 — 真实来源 Exact-ID Canary

**结论：确定性 Exact-ID Action 在真实公开来源上按契约工作。** 冻结 A1 的两个 ID 全部进入终态
（PMID→`verified`，DOI→`not_found`），EvidenceCard 只对应 verified PMID，
**付费 LLM 调用 = 0**，**账本零变化**，未进入 ReAct / Verifier / Claim / Shadow。

- 只调用免费来源：PubMed(E-utilities)、Europe PMC、Crossref、doi.org
- 不修改代码/协议/附录/Prompt/工具/评分；不运行 A1/B1
- 结构化产物：`pilot/round2_results/A71_real_source_canary.json`、
  `pilot/round2_results/A71_network_error_semantics.json`

---

## 一、基线（全部匹配）

| 项 | 值 |
|---|---|
| public HEAD | `dd04c12` |
| dev HEAD | `ab142c5` |
| CI #43 | 9/9 success |
| pytest | 567 passed / 2 deselected |
| Addendum 2 hash | `b3646d34…a31381` |
| v1 / v2 / Addendum 1 | 未变（`5d166bce` / `c76f5894` / `de3afcdd`） |
| 三次 A1 历史报告 | 未变（`4c2065f6` / `b9c8c50e` / `8812e81`） |
| 账本 | 21686 bytes，reserved=40 / reconciled=40 / open=0，prefix 完好 |
| 工作区 | 干净 |

## 二、LLM 哨兵与调用计数

运行前把 `langchain_openai.ChatOpenAI` 与 `langchain_anthropic.ChatAnthropic` 的
`invoke/ainvoke/stream/astream/batch/abatch` **全部替换为"调用即抛异常并计数"的哨兵**
（类级覆盖，任何已构造/新构造客户端都被拦截）。

| 角色 | 调用数 |
|---|---:|
| Planner | 0 |
| Executor | 0 |
| Verifier | 0 |
| Claim extractor | 0 |
| Shadow 内部 LLM | 0 |
| 任何 LLM（合计） | **0** |

付费账本新增 reservation：**0**（运行前后 21686 bytes / reserved=40 完全一致）。

## 三、冻结 A1 两个 ID —— 逐来源结果

分流：`classify(A1原题) = exact_id`；执行链 = `query classifier → resolve_exact_ids →
HttpSources → EvidenceCard`（与未来 A1 同一路径）。未调用 `search_evidence` / `search_literature`。

### PMID 41657283

| 项 | 值 |
|---|---|
| PubMed 请求 | E-utilities `esummary`（db=pubmed, retmode=json），HTTP 200 |
| PubMed 精确返回 41657283 | **是**（`exact_hit`） |
| Europe PMC 精确返回同一 PMID | **是**（`EXT_ID:41657283 AND SRC:MED`，`exact_hit`） |
| 标题 | From technological iteration to clinical breakthrough: advances of CAR-T cell therapy in autoimmune diseases. |
| 期刊 / 年份 | Annals of medicine / 2026 |
| 该文 DOI（结构化元数据） | 10.1080/07853890.2026.2627057 |
| 元数据规范化 | PMID 规范为裸数字；DOI 小写规范化 |
| 来源冲突 | 无（年份/DOI 一致） |
| `retrieval_status_by_source` | `{pubmed: exact_hit, europepmc: exact_hit}` |
| **`resolution_status`** | **`verified`** |

### DOI 10.1080/03009742.2024.2302553

| 项 | 值 |
|---|---|
| doi.org | handle API，HTTP 200，`responseCode≠1` → **明确 not found**（`zero_hits`） |
| Crossref 精确查询 | `works/{doi}` → **明确无记录**（`zero_hits`） |
| Crossref 返回 DOI 与输入一致性 | 不适用（无记录） |
| Europe PMC（仅补充） | `DOI:"…"` → `zero_hits`（补充，不单独决定） |
| `retrieval_status_by_source` | `{crossref: zero_hits, doi.org: zero_hits, europepmc: zero_hits}` |
| **`resolution_status`** | **`not_found`** |

**关键**：`not_found` 由**两个权威主来源**（doi.org + Crossref）**同时明确无记录**得出，
不是"只有 Europe PMC 无命中"，也不是网络错误。未用相似标题/相似 DOI 替代，未编造标题或年份。

**综合终态**：`all_terminal = true`，counts = `{verified:1, not_found:1, mismatch:0, manual_needed:0}`。

## 四、EvidenceCard

只生成 **1 张**，对应 verified 的 PMID：

| 字段 | 值 |
|---|---|
| evidence_id / pmid | 41657283 |
| doi | 10.1080/07853890.2026.2627057（该文自身 DOI，取自 PubMed 结构化 `articleids`） |
| title | From technological iteration to clinical breakthrough…（结构化响应，非自然语言猜测） |
| tier / evidence_grade | abstract / 初筛 |
| provenance.tool_name | resolve_exact_ids |
| provenance.source | https://pubmed.ncbi.nlm.nih.gov/41657283/ |
| provenance.source_ids | ["41657283"] |
| provenance.content_level | **metadata_only**（准确：本阶段未抓摘要） |
| ExactIdResolution.sha256 | 已复算校验 **一致**（两个 ID 均 match=True） |

`not_found` 的 DOI **未生成任何 EvidenceCard**、未生成相似引用、未编造标题/年份。

## 五、通用 fixture（证明非为 A1 写死）

全部取自公开文献，无患者信息。

| fixture | 输入 | 逐来源 | 终态 | 卡 |
|---|---|---|---|---:|
| 已知有效 PMID | 33301246 | pubmed=exact_hit, europepmc=exact_hit | **verified**（BNT162b2 mRNA Covid-19 Vaccine, 2020） | 1 |
| 已知有效 DOI | 10.1038/s41586-020-2649-2 | crossref=exact_hit, doi.org=exact_hit, europepmc=exact_hit | **verified**（Array programming with NumPy, 2020） | 1 |
| 格式合法但不存在的 DOI | 10.9999/this.doi.does.not.exist.999999 | crossref=zero_hits, doi.org=zero_hits, europepmc=zero_hits | **not_found** | 0 |

实现对任意 PMID/DOI 通用，非针对 A1 写死。

## 六、网络错误语义（模拟 transport 实测，零外网、零 LLM）

来源级：

| 场景 | retrieval_status | error_type | 底层请求数 |
|---|---|---|---:|
| timeout | source_error | `parse_error:Timeout` | 1 |
| DNS/连接失败 | source_error | `parse_error:ConnectionError` | 1 |
| HTTP 429（持续） | source_error | `rate_limited_429` | 2（1 次受控重试） |
| HTTP 429 → 200 | **exact_hit** | — | 2（重试后成功） |
| HTTP 503 | source_error | `http_503` | 2（1 次受控重试） |
| JSON 解析失败 | source_error | `parse_error:ValueError` | 1（不重试，正确） |

解析终态级（PMID 与 DOI 两条路径全覆盖）：**所有错误场景一律 `manual_needed`**，
**没有任何网络错误变成 `not_found`**（实测断言为 True）。

请求次数有界：单来源单 ID 最多 2 次（1 次 + 至多 1 次受控重试），DOI 路径 3 个来源合计
最多 6 次；**无无界重试**。重试为程序控制、非 LLM、且不改变查询内容。

### 发现（不在本阶段修改，留待批准后处理）

1. **timeout / ConnectionError 被标为 `parse_error:*` 且未获得那一次允许的重试。**
   根因：`HttpSources._get` 内层只把 429/5xx 抛为 `_Retryable`，`requests` 的 Timeout/
   ConnectionError 落入 `_with_retry` 的通用 `except Exception` 分支 → 标签失真且不重试。
   **安全性未受影响**（终态仍为 `manual_needed`，绝不 `not_found`），但错误分类不精确、
   瞬时超时少了一次本应允许的重试。建议后续单独提交修正。
2. EvidenceCard 的 `provenance.content_hash` / `hash_algorithm` 为 `None`
   （沿用 `evidence_build.abstract_card_from_paper` 的既有行为）。
   ExactIdResolution 自身的 SHA-256 正常且已复算一致。可作为后续改进项。

## 七、请求次数与耗时

| case | HTTP 请求数 | 耗时 |
|---|---:|---:|
| A1（PMID + DOI） | 5 | 2.532 s |
| 已知有效 PMID | 2 | 0.915 s |
| 已知有效 DOI | 3 | 1.364 s |
| 不存在 DOI | 3 | 1.525 s |

同一 ID 同一来源均只请求一次（无重试场景下），符合"默认只查询一次"。

## 八、终态验收（§7 全部满足）

- 两个 ID 均进入终态 ✅
- `all_terminal = true` ✅
- PMID = `verified` ✅
- DOI = `not_found`（两个权威主来源明确无记录）✅
- EvidenceCard 只对应 verified PMID ✅
- Resolver 无异常 ✅
- 付费 LLM 调用 = 0 ✅
- 账本无变化 ✅

真实来源结果与离线 fake 预期**完全一致**，未做任何迎合测试的修改。
