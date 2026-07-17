# reumani 包结构迁移（渐进，非一次完成）

目标结构：
```
reumani/
├── core/         # 数据契约 schemas
├── resources/    # E1 资源环境 + 数据湖
├── tools/        # 工具权限层
├── evidence/     # 证据卡 / claim 图 / 文献清洗与分层
├── execution/    # 计划 / 权限执行 / 沙箱 / A1 / 四层 Verifier
├── diseases/
│   ├── systemic_sclerosis/
│   ├── sle/
│   └── rheumatoid_arthritis/
├── evals/        # 评测
├── web/          # 前端
└── tests/
```

## 原则（本轮）
- **不做全量迁移**：现有顶层模块仍是唯一入口，网页/CLI/测试照常可用。
- 本包先提供命名空间 + 兼容 re-export（`reumani.core` → `schemas` 等），不移动文件、不改导入。
- 后续每个子模块【一次一 PR】搬进对应目录，并保留顶层 shim 指回，直到全部迁完再移除 shim。

## 现状映射（顶层 → 目标）
| 顶层现状 | 目标位置 |
|---|---|
| schemas.py | core/ |
| ssc_resources.py, data_lake_query.py | resources/ |
| tool_registry.py | tools/ |
| evidence_build.py, claim_graph.py, lit_ranking.py, lit_cleaning.py | evidence/ |
| planner.py, tool 执行, ssc_sandbox.py, ssc_a1.py, verifier.py | execution/ |
| rheum_config.py, skills/, lab_knowledge.py, protocols.py | diseases/ |
| eval_harness.py, eval/*.md | evals/ |
| ssc_pi_agent_web.py, pages/ | web/ |
