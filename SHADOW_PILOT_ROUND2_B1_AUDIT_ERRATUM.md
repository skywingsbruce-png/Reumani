# B1 canary 审计勘误（A.7.3 §1）

本勘误**追加**修正 `SHADOW_PILOT_ROUND2_B1_CANARY_RESULT.md` 的结论口径。
**不改写、不删除原始 B1 报告**；原报告保持冻结，本文件为独立审计追补。零付费、只读分析。

| # | 原报告措辞 | 更正后的准确措辞 |
|---|---|---|
| 1 | “工程上技术完成（fail-closed）” | **不应称“工程技术完成”**。准确口径：**安全终止成功，科研任务未完成**。系统正确地在护栏处 fail-closed，但未交付任何科研结论，不构成“完成”。 |
| 2 | “没有因果过度表述” | 没有因果过度表述**是因为系统未作答**（fail-closed 于产出结论之前），**不代表因果校准能力通过**。因果校准**未被真正测试**。 |
| 3 | 报告列出 Verifier/Claim/Shadow 未运行 | 明确重申：**old Verifier、Claim extractor、Claim-Evidence Graph、Shadow 均未实际运行**。B1 从未触达综合与核查阶段。 |
| 4 | preflight “四角色真实全链” | preflight 的真实计量只覆盖 **planner / executor / claim_extractor**（fake open 链在 require_evidence 处短路，**verifier 未实际被调用**）。**不得声称四角色真实全链已完成。** 正式 B1 运行中 verifier 调用数亦为 0。 |
| 5 | 生命周期与 metrics 并列 | **遥测口径冲突**：Lifecycle/Trace 记录 **8 次工具执行**，但旧 `metrics.execution.tool_calls=0`（因该字段从 `state.shadow.tool_events` 汇总，而 Shadow 未运行）。二者矛盾，需统一遥测权威（见 ADR §Telemetry authority）。 |
| 6 | 最终答案“还缺：[]” | EvidenceCard=0 时最终答案出现“还缺：[]”属**错误失败语义**：`verification_results` 为空 → fail-closed 文案取 `last.get("missing",[])` 得空列表，误示“没有缺口”。应输出结构化 missing_evidence，禁止“还缺：[]”。 |
| 7 | loop_guard no_progress=0 视为“持续有进展” | `no_progress` 把**每个不同 result hash**（transport 新颖度）当成进展（`pilot/lifecycle.py:144-148` 注入 `observed_result:<hash>`）。**不能代表新增科学证据**——7 次不同关键词检索各得不同字节，均被记为“进展”，故 no_progress 永不触发。 |

**结论修正（权威口径）**：B1 = **安全终止成功 / 科研任务未完成 / 因果校准未被测试**。
根因：开放式 ReAct 把“不同工具结果 hash”当进展，却**未在执行内把结果转成 EvidenceCard、
未维护证据累加器、未进入综合与核查**。详见 `ADR_OPEN_TASK_BOUNDED_CONVERGENCE.md`。
