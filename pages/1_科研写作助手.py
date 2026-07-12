"""
科研写作助手（网页版第二个页面）
Streamlit 会自动把 pages/ 目录下的脚本变成侧边栏里的多页导航。
入口仍然是 ssc_pi_agent_web.py，启动方式不变。
"""

import streamlit as st

from ssc_pi_agent import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
from ssc_writer import SCENARIOS, retrieve_literature, generate_draft, refine_draft
from i18n import t, lang_selector

st.set_page_config(page_title="Writing Assistant", page_icon="✍️", layout="wide")
with st.sidebar:
    lang_selector()

st.markdown(
    """
    <style>
    .block-container {padding-top: 2rem; max-width: 950px;}
    [data-testid="stChatMessage"] {border-radius: 14px; padding: 0.6rem 0.9rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("✍️ " + t("科研写作助手", "Research Writing Assistant"))
st.caption(t("先真实检索文献 → 再让 AI「只准引用检索到的文献」写作，尽量避免编造引用。",
             "Retrieve real literature first → the AI writes citing only retrieved papers, minimizing fabricated references."))

# ------------------------------------------------------------------
# session state
# ------------------------------------------------------------------
w_defaults = {
    "w_lit": "",          # 检索到的文献列表文本
    "w_topic": "",
    "w_scenario": "文献综述",
    "w_draft": "",        # 当前草稿
    "w_chat": [],         # 润色对话展示 [{"role","text"}]
    "w_model": "deepseek",
}
for k, v in w_defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ------------------------------------------------------------------
# 侧边栏：配置
# ------------------------------------------------------------------
with st.sidebar:
    st.subheader("状态")
    if DEEPSEEK_API_KEY:
        st.success(f"DeepSeek 已就绪（{DEEPSEEK_MODEL}）")
    else:
        st.error("未检测到 DEEPSEEK_API_KEY")
    st.info("Claude 主脑：claude-opus-4-8")

    st.divider()
    model_label = st.radio(
        "写作用哪个模型？",
        ["DeepSeek（省钱，速度快）", "Claude Opus（质量高，费用高）"],
        index=0 if st.session_state.w_model == "deepseek" else 1,
    )
    st.session_state.w_model = "deepseek" if model_label.startswith("DeepSeek") else "claude"

    st.divider()
    if st.button("🔄 清空，重新开始", use_container_width=True):
        for k, v in w_defaults.items():
            st.session_state[k] = v.copy() if isinstance(v, list) else v
        st.rerun()

# ------------------------------------------------------------------
# 第 1 步：输入主题 + 选场景 + 真实检索文献
# ------------------------------------------------------------------
st.subheader("1️⃣ 检索文献")
col1, col2 = st.columns([3, 2])
with col1:
    topic = st.text_input(
        "研究主题 / 关键词",
        value=st.session_state.w_topic,
        placeholder="例如：systemic sclerosis biomarkers / SSc 肺纤维化 发病机制",
    )
with col2:
    scenario = st.selectbox(
        "写作场景", list(SCENARIOS.keys()),
        index=list(SCENARIOS.keys()).index(st.session_state.w_scenario),
    )

colA, colB = st.columns([1, 1])
with colA:
    max_results = st.slider("检索文献数量", 5, 30, 15)
with colB:
    preprints_only = st.checkbox("只看预印本（bioRxiv/medRxiv）", value=False)

if st.button("🔎 检索真实文献", type="primary", disabled=not (DEEPSEEK_API_KEY and topic.strip())):
    st.session_state.w_topic = topic.strip()
    st.session_state.w_scenario = scenario
    with st.spinner("正在从 PubMed / 预印本检索真实文献..."):
        st.session_state.w_lit = retrieve_literature(topic.strip(), max_results, preprints_only)
    st.session_state.w_draft = ""
    st.session_state.w_chat = []
    st.rerun()

if st.session_state.w_lit:
    with st.expander("📚 已检索到的真实文献列表（AI 只能引用这些）", expanded=True):
        st.markdown(st.session_state.w_lit)

# ------------------------------------------------------------------
# 第 2 步：生成草稿
# ------------------------------------------------------------------
if st.session_state.w_lit:
    st.divider()
    st.subheader("2️⃣ 生成草稿")
    extra = st.text_area(
        "额外要求（可留空）",
        placeholder="例如：字数控制在 1500 字内 / 重点写生物标志物那部分 / 用英文写……",
    )
    if st.button(f"🚀 生成「{st.session_state.w_scenario}」草稿", type="primary"):
        with st.spinner("AI 正在基于真实文献撰写..."):
            st.session_state.w_draft = generate_draft(
                st.session_state.w_scenario,
                st.session_state.w_topic,
                st.session_state.w_lit,
                model=st.session_state.w_model,
                extra_requirement=extra,
            )
        st.session_state.w_chat = [{"role": "assistant", "text": st.session_state.w_draft}]
        st.rerun()

# ------------------------------------------------------------------
# 第 3 步：草稿展示 + 下载 + 润色追问
# ------------------------------------------------------------------
if st.session_state.w_draft:
    st.divider()
    st.subheader("3️⃣ 草稿 & 润色")

    st.download_button(
        "💾 下载草稿（.md）",
        data=st.session_state.w_draft,
        file_name=f"{st.session_state.w_scenario}_{st.session_state.w_topic[:20]}.md",
        mime="text/markdown",
    )

    for entry in st.session_state.w_chat:
        avatar = "✍️" if entry["role"] == "assistant" else "🧑‍🔬"
        with st.chat_message(entry["role"], avatar=avatar):
            st.markdown(entry["text"])

    follow = st.chat_input("让 AI 继续改：例如「第二段太啰嗦，精简一半」「把结论改得更有力」……")
    if follow:
        st.session_state.w_chat.append({"role": "user", "text": follow})
        # 把「原始文献 + 当前草稿 + 修改要求」拼成上下文，保证润色时仍然守着真实文献
        history_prompt = (
            "你是医学科研写作导师。下面是真实检索到的文献列表、当前草稿，以及首席研究员的修改要求。\n"
            "请在【只引用列表内文献、不得编造】的前提下，按要求修改草稿，输出修改后的完整版本。\n\n"
            f"【真实文献列表】：\n{st.session_state.w_lit}\n\n"
            f"【当前草稿】：\n{st.session_state.w_draft}\n\n"
            f"【修改要求】：\n{follow}"
        )
        with st.spinner("AI 正在修改..."):
            new_draft = refine_draft(history_prompt, model=st.session_state.w_model)
        st.session_state.w_draft = new_draft
        st.session_state.w_chat.append({"role": "assistant", "text": new_draft})
        st.rerun()
