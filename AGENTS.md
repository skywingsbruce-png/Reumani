# AGENTS.md — SSc 科研智能体项目长期规范

本文件是这个项目（SSc systemic sclerosis 科研智能体，逐步升级为 Biomni 式架构）的长期规则，
供任何在此工作的 AI 编程助手（Codex / Claude 等）遵守。改动代码前先读本文件。

## 术语
- **SSc 始终表示 systemic sclerosis（系统性硬化症）**，不是 single-cell。
- **CIN** = chromosomal instability（染色体不稳定）。

## 迁移原则
- **渐进式迁移**：保留现有 `ssc_pi_agent.py` 的行为和 CLI 入口，不做推倒重来的大重构。
- **不要删除现有功能**，除非用户明确批准。
- 新能力优先以"新增模块 + 接线"的方式加入，保证网站(4-5 页 Streamlit)始终能跑。

## 安全（硬性）
- **禁止读取、输出、提交 `.env` 和任何 API 密钥**。
- `run_python` 必须走安全沙箱 `ssc_sandbox.safe_run_python`（Level 2）：运行时剥离密钥、
  禁止 subprocess/系统命令/删除文件/读 .env。**第一阶段禁止任意 Shell 执行**。
- 原始论文和科研数据**只读**；产出只写入独立工作目录 `agent_workspace/`。
- 真正的强隔离(Level 3, Docker/WSL)后期再上。

## 数据与证据
- **工具必须结构化输入输出**；工具失败**不得伪装成成功文本**（用 `ssc_sandbox.ToolResult`）。
- **每项结论必须保留来源**：DOI、PMID 或本地 PDF 页码。
- 检索优先用本地数据湖（`data_lake/`，可复现），必要时再联网。

## 医学 / 湿实验边界
- 医学和湿实验输出**仅用于科研决策支持**，不替代临床/实验判断。
- **任何真实湿实验执行必须人工审批**；当前不连接真实仪器，湿实验功能只生成结构化协议。

## 工程规范
- 新模块必须有单元测试（`tests/`）。
- 每轮修改后运行测试，并汇报：改了哪些文件、验证结果、遗留问题。
- 每个可运行改动后重启网站确认 HTTP 200（`启动SSc智能体.bat`）。
- 有 git 版本库保底，重要改动前后提交检查点。

## 当前架构状态（2026-07）
- SSc-E1 资源环境：`ssc_resources.py`（ResourceSpec + 40 资源 + Tool Retriever）
- SSc-A1 循环：`ssc_a1.py`（AgentState + Planner-Executor-Verifier + 循环保护）
- 证据层：`ssc_evidence.py`（证据卡片 + Verifier）
- 安全 CodeAct：`ssc_sandbox.py`（Level 2）
- 数据湖：`data_lake/`（文献语料/基因集/HGNC/GWAS/OpenTargets/STRING/CollecTRI…）
- 技能：`skills/`（SSc 专科）+ `nature-skills/`（写作类）
- 正反方辩论降级为可选 Skeptic，不再是主干。

## 尚未做（路线图剩余）
- Action Discovery（从 SSc 论文批量提取科研动作，需人工审核队列，禁止未审代码进正式环境）
- 湿实验 Protocol IR + 静态校验（不连真实设备）
