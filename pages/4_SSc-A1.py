"""
🧪 SSc-A1 —— 计划-执行-验证智能体（Planner → Executor → Verifier 循环）
和"🦞 科研 Agent"(一次调用)不同：这个会先制定计划、执行、再独立验证，不通过就修订重试。
"""

import streamlit as st

from ssc_pi_agent import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
from ssc_a1 import run_agent, RUNS_DIR
from i18n import t, lang_selector

st.set_page_config(page_title="SSc-A1", page_icon="🧪", layout="wide")
st.markdown("<style>.block-container{padding-top:2rem;max-width:950px;}</style>", unsafe_allow_html=True)
with st.sidebar:
    lang_selector()

st.title("🧪 " + t("SSc-A1（计划-执行-验证）", "SSc-A1 (Plan → Execute → Verify)"))
st.caption(t("Planner 制定计划 → Executor 用工具真实执行 → Verifier 独立验证证据；不通过就修订重试（有循环保护，绝不死循环）。",
             "Planner drafts a plan → Executor runs real tools → Verifier independently checks the evidence; revise & retry if it fails (with loop protection, never infinite)."))

with st.sidebar:
    st.subheader(t("状态", "Status"))
    if DEEPSEEK_API_KEY:
        st.success(t(f"DeepSeek 已就绪（{DEEPSEEK_MODEL}）", f"DeepSeek ready ({DEEPSEEK_MODEL})"))
    else:
        st.error(t("未检测到 DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY not found"))
    st.info(t("Planner/Verifier：Claude Opus\nExecutor：可选", "Planner/Verifier: Claude Opus\nExecutor: selectable"))

    exec_label = st.radio(t("Executor 用哪个模型？", "Executor model"),
                          [t("DeepSeek（省钱）", "DeepSeek (cheap)"), t("Claude Opus（更强）", "Claude Opus (stronger)")], index=0)
    exec_model = "deepseek" if exec_label.startswith("DeepSeek") else "claude"
    max_iter = st.slider(t("最大迭代次数（循环保护）", "Max iterations (loop guard)"), 1, 4, 2)

    st.divider()
    st.caption(t(f"📁 运行记录（可复现）：\n{RUNS_DIR}", f"📁 Run records (reproducible):\n{RUNS_DIR}"))
    st.warning(t("⚠️ 一次完整循环会调用多次大模型（计划+执行+验证），比单次问答贵，按需使用。",
                 "⚠️ One full loop makes several LLM calls (plan+execute+verify) — pricier than a single Q&A."))

st.info(t(
    "适合这种任务：\n"
    "- 最近两年是否有证据支持染色体不稳定驱动 SSc 成纤维细胞活化？\n"
    "- SSc-ILD 生物标志物目前证据最强的是哪些？\n"
    "- 帮我规划并执行一个验证 CIN 与 SSc 纤维化关联的分析方案",
    "Good for tasks like:\n"
    "- Is there recent evidence that chromosomal instability drives SSc fibroblast activation?\n"
    "- Which SSc-ILD biomarkers have the strongest current evidence?\n"
    "- Plan and run an analysis testing the CIN–fibrosis link in SSc"))

query = st.text_area(t("研究问题", "Research question"),
                     placeholder=t("输入一个需要规划+执行+验证的 SSc 研究问题……",
                                   "Enter a SSc question that needs plan + execute + verify…"))
constraints = st.text_input(t("约束（可选）", "Constraints (optional)"),
                            placeholder=t("例如：只看近2年 / 实验室没有测序条件 / 只用公开数据",
                                          "e.g. last 2 years only / no sequencing in-house / public data only"))

if st.button("🚀 " + t("运行 SSc-A1", "Run SSc-A1"), type="primary", disabled=not (DEEPSEEK_API_KEY and query.strip())):
    with st.spinner(t("Planner 制定计划 → Executor 执行 → Verifier 验证……（可能要几分钟）",
                      "Planner → Executor → Verifier… (may take a few minutes)")):
        state = run_agent(query.strip(), constraints=constraints.strip(),
                          max_iterations=max_iter, executor_model=exec_model)

    st.divider()
    st.subheader("📋 " + t("最终结论", "Final answer"))
    st.markdown(state.final_answer)

    with st.expander("🔍 " + t("执行过程", "Execution trace"), expanded=False):
        st.markdown("**① " + t("检索到的资源（Tool Retriever 筛选）", "Retrieved resources (Tool Retriever)") + "**")
        st.text(state.selected_resources)
        st.markdown("**② " + t("计划（Planner）", "Plan (Planner)") + "**")
        st.markdown(state.plan)
        st.markdown("**③ " + t("验证结果（Verifier）", "Verification (Verifier)") + "**")
        for i, v in enumerate(state.verification_results, 1):
            ok = t("✅ 通过", "✅ passed") if v.get("passed") else t("❌ 未通过", "❌ failed")
            st.markdown(f"- {t('第', 'round ')}{i} {ok}: {v.get('reason','')} ({t('缺', 'missing')}: {v.get('missing','—')})")
        if state.artifacts:
            st.markdown("**④ " + t("用过的工具", "Tools used") + "**")
            for a in state.artifacts:
                st.caption("、".join(a.get("tools_used", [])) or t("（无工具调用）", "(no tool calls)"))
        if state.errors:
            st.markdown("**" + t("错误", "Errors") + "**")
            for e in state.errors:
                st.error(e)
