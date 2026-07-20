# Shadow Pilot Round 1 报告（无付费模型 · 真实外部数据 · Manifest 人工审计）

本轮：运行 + 观察 + 报告。发现 1 个阻断 bug（CI YAML），已最小修复；其余只观察不改。
未修改最终裁决、未开始 Commit B、未加工具/疾病、未训练、未用付费 API。

## 1. commit 与环境
- Commit A.5：dev `d82ad63` / public `47e2967`。
- 本轮 CI 阻断修复：dev `60e92f4` / public `67e20a5`（仅改 `.github/workflows/ci.yml` + `.gitignore`）。
- 试运行环境：Windows + `F:\R`（全依赖）；另用 **只装 requirements-ci 的干净 venv** 复现 CI 依赖场景。
- Claim 提取：`REUMANI_REAL_LLM` **未启用**，用确定性 fake extractor，**零付费调用**。

## 2. CI 证据（关键发现：曾整体失败）
- **CI #18（47e2967）失败**：`.github/workflows/ci.yml` **第 35 行 YAML 语法错误**——流式映射 `with: { python-version: ${{ matrix.python-version }} }` 内未加引号的 `${{ }}` 表达式使 `{{` 破坏流式映射，**整个 workflow 无法解析 → 0 个 job 运行**（这解释了本地全绿而 CI 红）。PyYAML 复现：`line 35 col 34 expected ',' or '}' but got '{'`。
- **修复**：改块式 `with:\n  python-version: ${{ matrix.python-version }}`；PyYAML 校验通过。
- **CI #19（67e20a5）= 成功，2m06s**，9 个 job（含 ubuntu+windows 矩阵）全跑并通过。
- Run URLs：失败 `actions/runs/29699494250`；成功见 `actions`（#19）。
- 本地 CI 复现：干净克隆（无 data_lake/无 .env）+ requirements-ci-only venv → `pytest` **195 passed / 2 deselected / 0 failed**；`pip check`/`compileall`/核心导入均 OK。（说明代码本身无问题，纯 YAML 配置错误。）

## 3. 每个真实任务结果
| # | 任务 | tool | structured | ok/status | content_level | EvidenceCard | 关键观察 |
|---|---|---|---|---|---|---|---|
| 1 | SSc+cGAS-STING EPMC 检索 | search_evidence | ✅ | ok | abstract | 4篇 | 真实 PMID `42210736`；excerpt **逐字来自摘要**；⚠️EPMC 按日期排序，返回论文相关性偏低 |
| 2/3 | 已知 PMID/DOI | search_evidence | ✅ | ok | abstract | 3篇 | 真实 PMID(41657283…)/DOI(10.1080/…, 10.1016/…)；preprint flag；retraction=**unknown(诚实)** |
| 4 | data lake 正常命中 | query_data_lake | ✅ | ok/**success** | local_dataset | — | 混合检索命中 |
| 5 | data lake 零命中 | query_data_lake | ✅ | ok/**success** | local_dataset | — | ⚠️corpus 走 hybrid_search **总返回 top-k**，无意义 token 也返回 15 篇 → **zero_hits 未触发**（状态存在但 corpus 通道难触发） |
| 6 | data lake 不可用(模拟) | query_data_lake | ✅ | **ok=False / data_unavailable** | local_dataset | — | 正确区分"不可用"≠"无命中" |
| 7 | 已缓存 GEO 分析 | triage_hypothesis | ✅ | ok | computational_analysis | (→分析卡) | verdict=**no_detectable_support**；overlap n_shared=0；gene_counts A=50/B=16；样本=102；命中 A=49/50,B=12/16；p=0.910, q=0.910；multiplicity=**adjusted** |
| 8 | 因果过度 Claim | (shadow) | — | — | — | 5 | Claim verdict=**not_supported**；**causal overstatement 被抓**；old/shadow **divergence=True** |

## 4–9. 汇总指标
- **structured_tool_rate**：本轮实际调用的 3 类结构化工具（search_evidence/query_data_lake/triage_hypothesis）产生的 tool_events **100% structured**（schema_version=toolresult-v1）。
- **legacy_tool_rate**：本轮结构化工具中 0%。（说明：其余 11 个工具**仍是 legacy** 字符串，未在本轮调用。）
- **EvidenceCard 成功率**：搜索结果每篇真实 PMID → 1 张 AbstractEvidenceCard，共 5 张；excerpt 逐字来自摘要。
- **provenance 完整率**：工具 ToolResult 的 provenance **完整**（source/content_level/source_ids/content_hash/code_commit）；EvidenceCard 的 provenance 有 content_level/source_ids/source，但 `content_hash=null`（卡不做 hash，仅工具结果 hash）。
- **citation failures**：四层 Citation 层 **passed=False**——因摘要卡缺来源定位(low traceability)。这是**预期**（摘要级不能作关键结论），非缺陷。
- **causal overstatement 检测**：✅ 抓到（causal claim + 仅摘要/相关证据 → not_supported）。

## 10. Manifest 安全审计（`pilot_manifest.json`，39316 bytes）
- **禁项全 0**：无 API key(`sk-`)、Authorization、Cookie、Bearer、`DEEPSEEK_API_KEY`、`.env`、用户主目录绝对路径。
- **审计字段全保留**：run_id(带 UUID `..._a156601e`)、manifest_schema_version(`runmanifest-v1`)、phi_warning、tool_events(含结构化 ToolResult)、evidence_cards、claims、old/shadow verifier 结果、comparison、allowed_tools、shadow_status(`ok`)。
- 体积：单个 39KB（< 200KB 上限，未触发压缩）；本轮无被丢弃字段（因无敏感字段可丢）、无 REDACTED 命中（无密钥）、长字段截断阈值 4000 字符生效于超长内容。
- **无误删审计字段**。

## 11. 外部服务错误
- 无。Europe PMC 免费 API 正常返回；GEO GSE58095 已缓存 → 离线分析成功。

## 12. 数据格式异常 / 语义观察（非阻断）
1. **search_evidence 相关性**：查询直传 EPMC 且按日期排序，`SSc cGAS STING` 返回了 palmitoylation/ALS 等**离题近期论文**。excerpt 逐字正确，但**证据相关性差**——属检索相关性问题，非 shadow/结构化缺陷。
2. **query_data_lake corpus 的 zero_hits 难触发**：corpus 走 hybrid_search 总返回 top-k，无意义 token 也返回 15 篇。zero_hits 状态**存在且正确**（对 geneset 等可空 kind 有效），但 corpus 通道基本不产生零命中。
3. **triage 单数据集的 multiplicity**：GSE58095 单数据集，`multiplicity_status="adjusted"` 且 `q=raw p`（BH 对单个 p 值是恒等，q=p 属正确）。但**单数据集标 adjusted 略误导，宜为 not_applicable**。
4. EvidenceCard 的 `content_hash=null`（卡未 hash）。

## 13. 是否发现阻断性 bug
- **是，1 个：CI YAML 语法错误（已最小修复并验证 CI 转绿）。** 除此之外，shadow / 三个结构化工具 / manifest 在真实数据上**未发现阻断性缺陷**。

## 14. 是否建议进入付费 Round 2
**有条件建议**：结构化管道、provenance、Claim 裁决、Manifest 脱敏在真实数据上均表现正确。建议先把以下**小技术债**单独最小 commit 处理，再进付费 Round 2：
- 单数据集 `multiplicity_status` → `not_applicable`；
- `content_hash`/artifact hash **SHA-1 → SHA-256**（本轮仅记录，未改）；
- （可选）search_evidence 相关性排序 / query_data_lake corpus 的 zero_hits 语义。

付费 Round 2 应仍**小规模、可控**：观察真实 LLM 做 Claim 提取时的 old/shadow 分歧率与因果过度检测率，仍不改最终裁决。

---
本轮 hash 算法：`tool_envelope.content_hash` 与 `manifest_safety.artifact_ref` 均为 **SHA-1**（记为技术债，建议后续统一 SHA-256）。

> Historical note: SHA-1 accurately described the Round 1 implementation at that time. Commit A.6 later migrated new records to SHA-256; historical manifests were not rewritten.
