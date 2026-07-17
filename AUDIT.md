# Reumani 审计（第一版）— 2026-07

目的：在继续加功能前，按数据治理原则（见 `DATA_GOVERNANCE.md`）审计现状，
按严重度列出问题，给出**分阶段、各自独立 commit** 的修复计划。当前阶段**不训练基础模型**。

## 一、数据资产盘点（对照三类）

| 资产 | 归类 | 现状 |
|---|---|---|
| 基础模型 | 训练数据(1) | **无**（未微调，用现成 DeepSeek/Claude）→ 无权重污染风险 ✅ |
| `data_lake/`（~9.5万摘要 + 基因集/GWAS/… + 向量索引） | 知识库(2) | 有，离线可查 |
| `retrieval_eval.py`（10 查询，程序化银标准） | 评测(3) | **有污染**，见 F1 |
| `ssc_eval_questions.json`（15 知识问答，LLM 裁判） | 评测(3) | 小、手写、无 held-out 划分 |
| `agent_memory`、`protocols.py`、`lab_knowledge.py` | 知识库(2) | 人工策展，带来源与边界 |

## 二、发现（按严重度）

### F1【高】检索基准被污染——用同义词自测自己
`retrieval_eval.py` 的 10 条查询里约 9 条直接由 `retrieval.SYNONYMS` 的词条构成
（如 `SSc scarring`、`系统性硬化症 纤维化`、`染色体不稳定 炎症`、`干扰素signature 硬皮病`）。
即：**评测查询与被评测的同义词词典是同一批人、同时设计的**。
- 后果：报告的"纯关键词 0.79 → +同义词 0.91"**不能**当作泛化能力证据——它只证明"我写的同义词触发了我照着它写的查询"。
- 违反 `DATA_GOVERNANCE.md`"评测查询 ≠ 词典来源"。
- 处置：把 `retrieval_eval.py` 降级为**开发期 sanity check（dev）**，明确标注"非泛化评测"；
  另建**独立 held-out 金标准**（F1 的修复=阶段2）。

### F2【中】相关性是"银标准"，非人工金标准
现基准用"标题/摘要是否含概念组词"判相关，近似但≠人类相关性判断，且与查询用词耦合。
- 处置：阶段2 用**人工标注的 PMID 金标准**（每查询由领域人判定相关文献），程序银标准仅作探索并注明。

### F3【中】知识问答评测集小且无划分
`ssc_eval_questions.json` 仅 15 题、手写、Claude 当裁判（裁判与被测可能共享盲区），无 dev/test 划分。
- 处置：扩充并划 `dev/`（可调）与 `test/`（冻结）；考虑双裁判或人工抽检校准。

### F4【中】超参数无隔离的调参流程
`RRF k=60`、`pool`、`top_k`、重排序模型选择等目前是手工/默认值。虽未在测试集上调过（因当时无独立测试集），
但**没有制度**保证以后不会看着测试分数改它们。
- 处置：所有调参只在 `eval/dev/` 上做；`eval/test/` 只跑一次报告（写进治理规则，已在 `DATA_GOVERNANCE.md`）。

### F7【高·已修】Verifier 核查 fail-open（默认放行）
`ssc_a1.py` 旧 `verify()`：Verifier 输出 JSON 解析失败时返回 `passed=True`（"默认放行"）——
科研核查最该 fail-closed 的地方却 fail-open。
- 处置（已修）：重写为全面 **fail-closed**——无法调用/超时/空输出/JSON解析失败/缺 passed 字段/
  passed 非布尔True("true"字符串不认)/工具执行失败/证据不足，**一律判未通过**；证据不足时结论标记
  "未验证/证据不足"。新增 `VerificationResult` 结构 + `tests/test_verifier_failclosed.py`（9 项，含7个必测场景）。

### F5【中】可靠性：静默兜底 + 无自动化测试
- 多处 `try/except` 吞异常后返回文本（如检索、副驾文献、协议加载），失败被"温柔"掩盖，难以发现回归。
- 27 项测试目前靠人手动跑，无 CI；改动后可能忘跑。
- 处置（阶段1）：给关键路径的兜底加**可见告警/日志**而非静默；加一个一键跑全部测试的脚本（后续可上 CI）。

### F6【低】可复现：语料未快照
`data_lake` 会随每日文献增长，**同一评测在不同日期分数不可比**。当前未记录语料快照。
- 处置：报告评测时记录语料快照 id/日期；重要评测锁定语料版本。

### 正面项 ✅
- 无基础模型训练→无权重污染面。
- 知识库与代码分离；边界结论（如"分选双阳=候选""缓解≠停止"）已用测试锁死。
- 已开源、单条干净历史、零密钥。

## 三、分阶段计划（各自独立 commit/PR，不跳步）

| 阶段 | 内容 | 对应发现 | 状态 |
|---|---|---|---|
| 0 | 数据治理规则 + 本审计 | — | ✅ |
| P0-2 | 干净克隆全测通过 + CI（基因路由去数据依赖/占位key/测试分级/最小fixture/GitHub Actions） | F5,F6 | ✅ 42→50 passed |
| P0-3 | 统一核心数据契约 `schemas.py`（Pydantic v2，11模型+Provenance+Artifact，严格校验；失败不可伪装成功） | — | ✅ 已接线：ToolResult(沙箱)、VerificationResult(ssc_a1)、ResourceSpec(ssc_resources 43资源)。待迁：EvidenceCard(需LLM输出重整)、AgentState(随结构化Planner一并收敛) |
| 工具权限 | Tool Retriever 从"推荐"升级为"权限控制"：`tool_registry.py`（确定性选工具→resolve校验真实存在→高风险未批准物理排除→PermissionedToolset做权限+参数schema+审批+trace）；Executor 只拿授权工具；未知名报错；8 项权限绕过测试 | — | ✅ |
| 结构化Planner | `planner.py`：Planner 产出经 schema 验证的 ResearchPlan（每步 success_criteria 必填、tool_name 必须∈allowed_tools、maximum_retries 程序封顶、LLM 不能自行扩权）；解析/验证失败即停，绝不自由文本降级；7 项测试 | — | ✅ |
| 分层证据卡 | 三层证据卡 Abstract/FullText/Analysis（全部保留 provenance）+ 科学诚信规则：无 excerpt 不能作关键结论、缺定位标低可追溯、摘要卡只初筛、动物/体外/相关性硬性告诫、预印本标记、撤稿不作正向、更正记版本、缺失写"未报告"不猜测；`evidence_build.py` 构造器；12 项测试 | — | ✅（取代之前 deferred 的 EvidenceCard 迁移）|
| 文献分层使用 | 全量保存不删；`LiteratureQuality` 质量标签 + `lit_ranking.py` A–F 分级（据研究设计，**不用影响因子**）+ 按任务动态选择（临床/预后/机制/可行性/探索）+ 透明多因子排序（质量×相关性×直接性×可重复性×可追溯性×任务契合，非单一不透明总分）；撤稿移出结论排序但保留数据；6 项测试 | — | ✅ |
| Claim–Evidence Graph | `claim_graph.py`：答案拆成原子 Claim，每个按【自己的证据要求】单独裁决（6 种 verdict）；相关≠因果、动物/体外≠临床、缺证据→insufficient；证据要求不能互相替代；未解析证据 id 单列；需人工复核自动标记；10 项测试（含四示例 Claim） | — | ✅ |
| 四层 Verifier | `verifier.py`：Schema(工具状态/字段)+Citation(PMID/DOI格式+来源定位+抓伪造DOI)+Claim-Evidence(复用claim_graph)+Adversarial(不读Planner推理,重生成反证检索式,第二LLM非金标准)；整体 fail-closed；关键结论/未跑反证→human_review；15 项测试 | — | ✅ |
| 修正筛杀器 | `hypothesis_triage.py`：结论改 supportive_association/no_detectable_support/inconsistent/technically_unresolved（去掉证明/证伪/杀死）；加 signature重叠/命中率/样本量/Pearson+Spearman/BH-FDR/Fisher CI/随机null分布/leave-one-out；显式声明未评估的混杂；禁止仅凭 p<0.05+过半显著就建议放弃湿实验；8 项测试 | — | ✅ |
| 十.1 三分数据 | knowledge_base(=data_lake)/dev_set(eval/dev)/held_out_test_set(eval/test 冻结)；反污染禁令写入 `DATA_GOVERNANCE.md`+`eval/README.md` | F1 | ✅ |
| 十.2 Retrieval-Eval | 查询分层(exact_id/gene/mechanism/phenotype/treatment/omics/counterevidence+中英混合)+两名专家分级标注(must/high/acceptable/irrelevant/misleading)+完整指标(Recall@10/@50、P@10、nDCG@10分级、MRR、must-find召回、无结果率、错误路由率、延迟、成本，非仅mean P@10)；4 项指标测试 | F1-F4 | ✅ 框架就绪待标注 |
| 十.3 Agent-Eval | `eval/agent_eval.md`：12 项(工具选择/计划可执行/参数/真实成功/真实引用/Claim支持/主动反证/相关升因果/正确停止/3次稳定性/成本/人工修正)+硬红线 | — | ✅ 规范 |
| 十.4 Safety-Eval | 强化沙箱(路径穿越/绝对敏感路径/pathlib删除/socket/动态导入/eval/ctypes/os.spawn)+资源耗尽超时；非沙箱层：恶意CSV不执行、提示注入检测(工具返回/记忆 `safety.py`)、伪造DOI、Verifier非法结构；14 项对抗测试 + `eval/safety_eval.md`(诚实记录Level-2局限) | 安全 | ✅ |
| 十.5 Scientific-Eval | `eval/scientific_eval.md`：未参与开发的GEO/预注册/固定代码再开测试数据/专家盲评/独立队列复制/记录批次混杂多重检验/区分探索性与验证性 | — | ✅ 规范 |
| 十一 文献清洗 | `lit_cleaning.py` 四层：确定性检查(PMID/DOI/去重/撤稿更正/类型/人-动物-细胞/全文)→LLM只做抽取→程序规则(动物≠临床/横断面≠因果/无样本量不编造/摘要≠全文证据/预印本必标/无定位≠关键证据)→人工审核优先级(临床/新发现/共识冲突/两模型不一致/低置信/进know-how-protocol-benchmark)；不让单一LLM独断质量；11 项测试 | — | ✅ |
| 1 | 可靠性：兜底告警化 + 一键测试脚本 + 语料快照工具 | F5,F6 | 待做 |
| 2 | **金标准评测**：建 `eval/dev` `eval/test`(冻结)，人工 PMID 相关性；旧基准降级为 dev sanity | F1,F2,F3,F4 | 🔧 框架就绪，待人工标注（eval_harness.py + eval/；旧 retrieval_eval.py 已加降级横幅） |
| 3 | 文献与证据分层：用 LLM 给文献打质量分→分层，比较"少而净 vs 多而噪"（用户实战教训） | — | 待做 |
| 4 | 结构化工具编排 | — | 待做 |
| 5 | 强沙箱（Level 3 隔离）与可复现 | — | 待做 |
| 6 | 独立科学验证 | — | 待做 |
| 7 | （最后才考虑）训练 | — | 不启动 |

**推进原则**：先审计（本文件）→ 再按上表逐阶段独立提交。每阶段结束更新本文件状态。
