# MrCat — 风湿免疫科研智能体 / A Biomedical Research Agent for Rheumatology

[![CI](https://github.com/skywingsbruce-png/Reumani/actions/workflows/ci.yml/badge.svg)](https://github.com/skywingsbruce-png/Reumani/actions/workflows/ci.yml)

> 一个受 [Biomni](https://github.com/snap-stanford/Biomni) 启发、面向**系统性硬化症 (SSc) 及风湿免疫疾病**的研究型 AI Agent：
> 规划 → 检索 → 执行(安全沙箱) → 证据核查 → 假说批量筛杀，全部在本地可复现的数据湖上运行。
>
> A research agent for **systemic sclerosis (SSc) and rheumatic/immune diseases**, inspired by Biomni.
> It plans, retrieves, runs code in a sandbox, verifies claims against literature, and screens hypotheses
> against real public omics data — reproducibly, on a local data lake.

**用途 / Scope.** 仅用于**科研决策支持**，不替代临床或实验判断。所有真实湿实验需人工审批。
For research decision-support only; not a medical device. Wet-lab steps require human approval.

---

## 为什么做这个 / Motivation

通用大模型能聊文献，但做不了可复现的科研：它会编造引用、无法在真实数据上验证假说、检索靠关键词漏掉同义词。
本项目把一个 LLM 包进一套**确定性的科研骨架**里 —— 检索、排序、去重、证据核查、假说筛杀都由程序把关，
LLM 只负责它擅长的推理与写作。目标：用 *agent(方法)* 在真实公开数据上做出可发表的 *发现(科学)*。

## 架构 / Architecture

```
                         用户问题 (中/英)
                              │
              ┌───────────────▼────────────────┐
              │  SSc-A1  规划器→执行器→核查器循环   │  ssc_a1.py
              │  (Planner → Executor → Verifier) │  (含循环保护/重试)
              └───────┬─────────────────┬────────┘
                      │                 │
        ┌─────────────▼──────┐   ┌──────▼─────────────────────┐
        │ SSc-E1 资源环境      │   │ 混合检索 Hybrid Retrieval    │  retrieval.py
        │ + Tool Retriever    │   │ 精确/机制路由(程序判)         │  vector_index.py
        │ ssc_resources.py    │   │ BM25 + 同义词扩展 + 向量      │
        │ (40 资源)           │   │ → RRF 融合 → 交叉编码重排序    │
        └─────────────────────┘   └──────┬─────────────────────┘
                      │                 │
        ┌─────────────▼─────────────────▼────────────────────┐
        │            本地数据湖 Data Lake (离线可复现)          │  data_lake/
        │  文献语料 SSc/SLE/RA/CIN(~9.5万摘要) · 基因集 · HGNC   │
        │  GWAS · Open Targets · STRING PPI · CollecTRI TF     │
        └─────────────────────────────────────────────────────┘
                      │                 │
        ┌─────────────▼──────┐   ┌──────▼──────────────┐
        │ 安全 CodeAct 沙箱   │   │ 证据层 + Verifier    │  ssc_evidence.py
        │ ssc_sandbox.py      │   │ 结构化证据卡片        │
        │ (剥离密钥/禁系统调用) │   │ 核对是否过度解读       │
        └─────────────────────┘   └─────────────────────┘
                      │
        ┌─────────────▼──────────────┐   ┌────────────────────────┐
        │ 假说批量筛杀器               │   │ 技能库 Skills           │  skills/
        │ hypothesis_triage.py       │   │ (SSc 专科 + 写作类)      │
        │ 在多个 GEO 数据集上打分判活杀 │   └────────────────────────┘
        └────────────────────────────┘
```

## 核心特性 / Highlights

- **混合检索，不把排序交给 LLM。** 查询先被**确定性程序**分流：基因符号/DOI/GSE 号走精确通道；
  机制类问题走 `BM25 + 医学同义词扩展(含中→英、HGNC 基因旧名) + dense 向量 → RRF 融合 → cross-encoder 重排序`。
  例：搜 `SSc scarring` 会自动扩到 `fibrosis`，返回机制论文而不是"整形疤痕"噪音。
- **本地数据湖，可复现。** ~9.5 万篇 SSc/SLE/RA/CIN 摘要 + 基因集/GWAS/Open Targets/STRING/CollecTRI，
  优先离线检索，结论都带 DOI/PMID 来源。
- **假说批量筛杀器。** 给两个基因 signature，在多个真实 GEO 数据集上算相关性，判"存活/被杀/存疑"，
  免费筛掉大部分假说，只留值得做湿实验的。
- **安全 CodeAct 沙箱 (Level 2)。** 运行时剥离所有 API 密钥，禁 subprocess/系统命令/删文件/读 `.env`；
  工具失败返回结构化错误，绝不伪装成正常文本。
- **证据核查。** 两阶段证据卡片(研究类型/样本量/局限/证据强度) + Verifier，专抓"把小样本/横断面当强因果"的过度解读。
- **双语 Web 界面。** Streamlit 多页应用，中英文切换。
- **模型分工。** DeepSeek 负责便宜的检索/初筛，Claude Opus 负责核查/判断。

## 目录结构 / Layout

| 文件 | 作用 |
|---|---|
| `ssc_a1.py` | A1 主循环：规划器→执行器→核查器 |
| `ssc_resources.py` | E1 资源环境 + Tool Retriever（40 资源） |
| `retrieval.py` | 混合检索：分类/同义词扩展/BM25/RRF |
| `vector_index.py` | dense 向量索引 + cross-encoder 重排序 |
| `data_lake_query.py` / `data_lake_build.py` | 数据湖查询 / 构建 |
| `corpus_build.py` / `ssc_corpus_build.py` | 文献语料构建（Europe PMC） |
| `hypothesis_triage.py` | 假说批量筛杀器（GEO signature 相关性） |
| `ssc_sandbox.py` | 安全 CodeAct 沙箱（Level 2） |
| `ssc_evidence.py` | 证据卡片 + Verifier |
| `ssc_skill_agent.py` | 技能驱动的通用科研 Agent |
| `ssc_pi_agent_web.py` + `pages/` | 双语 Streamlit Web 应用 |
| `skills/` | SSc 专科技能（SKILL.md + 脚本） |
| `tests/` | 单元测试 |

## 安装 / Install

> ⚠️ **关键：`numpy` 必须 `<2`。** 否则 scanpy/numba/statsmodels 会崩。requirements 已锁定。

```bash
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -r requirements.txt
```

向量检索(步骤4-5)需要 `torch` + `sentence-transformers`（CPU 版即可）：
```bash
pip install rank-bm25 sentence-transformers "numpy<2" "scipy<1.14"
```

## 配置密钥 / API keys

复制 `.env.example` 为 `.env`，填入你自己的 key（`.env` 已被 `.gitignore` 忽略，**永不入库**）：
```
DEEPSEEK_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
```

## 运行 / Run

```bash
# Web 应用（双语）
streamlit run ssc_pi_agent_web.py        # 或双击 启动SSc智能体.bat

# 构建向量索引（首次，按需；属 optional-large-data）
python vector_index.py CIN SSc

# 跑测试（unit + integration，无需 data_lake / API key）
pytest
# 本地跑需要大数据/向量索引的测试：
pytest -m optional_large_data
```

## 数据来源 / Data sources

Europe PMC · GEO (NCBI) · HGNC · GWAS Catalog (EBI) · Open Targets · STRING · CollecTRI/OmniPath ·
Enrichr gene sets。本仓库**不包含**下载的数据（体积大且各有许可），用 `data_lake_build.py` / `corpus_build.py` 在本地重建。

## 安全与边界 / Safety

- 禁止读取/输出/提交 `.env` 与任何密钥；`run_python` 强制走沙箱。
- 原始论文与科研数据**只读**；产出只写 `agent_workspace/`。
- 医学/湿实验输出仅供科研决策支持；真实湿实验必须人工审批。

## 状态 / Status

已完成：E1 资源环境、A1 循环、混合检索(BM25+同义词+向量+重排序)、数据湖、安全沙箱、证据层、假说筛杀器、双语 Web。
路线图剩余：Action Discovery（论文→科研动作，需人工审核队列）、湿实验 Protocol IR 静态校验。详见 `AGENTS.md`。

## 引用 / Citation

如果本项目对你的研究有帮助，欢迎引用（论文准备中 / manuscript in preparation）。

## 许可 / License

MIT（见 `LICENSE`）。
