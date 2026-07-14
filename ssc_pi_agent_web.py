"""
SSc / Rheumatology Research Assistant —— 首页（提问模式，中英双语）
用法：streamlit run ssc_pi_agent_web.py
"""

import time
from pathlib import Path

import streamlit as st

from ssc_pi_agent import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
from ssc_skill_agent import build_skill_agent, WORKSPACE, skill_system
from skill_loader import discover_skills
from i18n import t, lang_selector, agent_lang_note

st.set_page_config(page_title="Rheumatology Research Assistant", page_icon="🔬", layout="centered")

st.markdown(
    """
    <style>
    .block-container {padding-top: 2.2rem; padding-bottom: 6rem; max-width: 820px;}
    [data-testid="stChatMessage"] {border-radius: 16px; padding: 0.7rem 1rem; margin-bottom: .5rem;
        border: 1px solid rgba(128,128,128,.15);}
    h1 {font-weight: 700; letter-spacing: -.5px;}
    .hero-sub {color: #6b7280; font-size: .95rem; margin-top: -.4rem;}
    .example-chip button {border-radius: 999px !important; font-size: .85rem !important;}
    [data-testid="stChatInput"] textarea {font-size: 1rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

# 语言切换（放最前，后续所有 t() 依赖它）
with st.sidebar:
    lang_selector()

# ------------------------------------------------------------------
# 状态
# ------------------------------------------------------------------
if "model" not in st.session_state:
    st.session_state.model = "deepseek"
if "chat" not in st.session_state:
    st.session_state.chat = []
if "history" not in st.session_state:
    st.session_state.history = []
if "pending" not in st.session_state:
    st.session_state.pending = None

EXAMPLES = [
    t("近两年有没有证据支持染色体不稳定驱动 SSc 成纤维细胞活化？",
      "Is there recent evidence that chromosomal instability drives fibroblast activation in SSc?"),
    t("SSc 皮肤最重要的致病性成纤维细胞亚群有哪些？",
      "What are the key pathogenic fibroblast subsets in SSc skin?"),
    t("查一下 STING1 在 STRING 网络里的互作伙伴，并解读对 SSc 的意义",
      "Find STING1's interaction partners in STRING and interpret their relevance to SSc."),
    t("系统性红斑狼疮(SLE)目前最强的药物靶点是哪些？",
      "What are the strongest drug targets for systemic lupus erythematosus (SLE)?"),
]

# ------------------------------------------------------------------
# 侧边栏
# ------------------------------------------------------------------
with st.sidebar:
    st.subheader(t("⚙️ 设置", "⚙️ Settings"))
    st.caption(("🟢 " if DEEPSEEK_API_KEY else "🔴 ") + t("DeepSeek", "DeepSeek") + f" ({DEEPSEEK_MODEL})")
    st.caption("🟢 Claude Opus")

    label = st.radio(
        t("回答用哪个模型", "Answering model"),
        [t("DeepSeek（省钱，日常够用）", "DeepSeek (cheap, everyday)"),
         t("Claude Opus（更强，难题用）", "Claude Opus (stronger, hard tasks)")],
        index=0 if st.session_state.model == "deepseek" else 1)
    st.session_state.model = "deepseek" if label.startswith("DeepSeek") else "claude"

    st.divider()
    skills = discover_skills()
    st.caption(t(f"🧰 已接入 {len(skills)} 个技能 + 本地数据湖",
                 f"🧰 {len(skills)} skills + local data lake"))
    with st.expander(t("能查什么", "What it can query")):
        st.caption(t("本地文献库 SSc/SLE/RA/CIN(约9万篇) · 基因集/通路 · STRING 互作 · CollecTRI 调控 · "
                     "Open Targets 靶点 · GWAS · HGNC · 假说筛杀器 · 证据卡片+分级",
                     "Local corpora SSc/SLE/RA/CIN (~95k abstracts) · gene sets/pathways · STRING PPI · "
                     "CollecTRI regulons · Open Targets · GWAS · HGNC · hypothesis triage · evidence cards"))

    st.divider()
    if st.button(t("🗑️ 清空对话", "🗑️ Clear chat"), use_container_width=True):
        st.session_state.chat = []
        st.session_state.history = []
        st.rerun()
    st.caption(t("👉 复杂课题走 SSc-A1深度分析；定方向用 方向辩论；其它页在左侧导航。",
                 "👉 Deep tasks → SSc-A1; direction → Debate; other tools in the left nav."))

# ------------------------------------------------------------------
# 头部
# ------------------------------------------------------------------
st.markdown("# 🔬 " + t("SSc / 风湿病 研究助手", "SSc / Rheumatology Research Assistant"))
st.markdown(
    '<p class="hero-sub">' + t(
        "问一个研究问题，它会自己读技能、查真实文献和数据、必要时写代码分析，给出带证据的回答。",
        "Ask a research question — it reads skills, queries real literature and data, runs code when needed, and answers with evidence.") +
    "</p>", unsafe_allow_html=True,
)


def render_trace(messages):
    steps = []
    for m in messages:
        tcs = getattr(m, "tool_calls", None)
        if tcs:
            for tc in tcs:
                name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                steps.append(name)
    return steps


def render_images(since_ts):
    imgs = []
    for p in sorted(WORKSPACE.glob("*")):
        if p.suffix.lower() in (".png", ".jpg", ".jpeg") and p.stat().st_mtime >= since_ts - 1 \
                and not p.name.startswith("_"):
            imgs.append(p)
    return imgs

# ------------------------------------------------------------------
# 空状态：示例问题
# ------------------------------------------------------------------
if not st.session_state.chat:
    st.write("")
    st.caption(t("试试这些（点一下直接问）：", "Try one (click to ask):"))
    cols = st.columns(2)
    for i, ex in enumerate(EXAMPLES):
        with cols[i % 2]:
            st.markdown('<div class="example-chip">', unsafe_allow_html=True)
            if st.button(ex, key=f"ex{i}", use_container_width=True):
                st.session_state.pending = ex
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)

# ------------------------------------------------------------------
# 历史对话
# ------------------------------------------------------------------
for entry in st.session_state.chat:
    avatar = "🔬" if entry["role"] == "assistant" else "🧑‍🔬"
    with st.chat_message(entry["role"], avatar=avatar):
        st.markdown(entry["text"])
        for img in entry.get("images", []):
            if Path(img).exists():
                st.image(img)
        if entry.get("tools"):
            with st.expander(t("🔍 它用了哪些工具/技能", "🔍 Tools / skills used")):
                st.caption("、".join(entry["tools"]))

# ------------------------------------------------------------------
# 输入
# ------------------------------------------------------------------
typed = st.chat_input(t("问一个 SSc / 风湿病研究问题……", "Ask a SSc / rheumatology research question…"))
question = st.session_state.pending or typed
st.session_state.pending = None

if question:
    if not DEEPSEEK_API_KEY and st.session_state.model == "deepseek":
        st.error(t("未配置 DEEPSEEK_API_KEY，请在 .env 填入，或在左侧切换到 Claude。",
                   "DEEPSEEK_API_KEY not set — add it to .env, or switch to Claude in the sidebar."))
        st.stop()

    st.session_state.chat.append({"role": "user", "text": question})
    with st.chat_message("user", avatar="🧑‍🔬"):
        st.markdown(question)

    if not st.session_state.history:
        st.session_state.history.append(("system", skill_system(agent_lang_note())))
    st.session_state.history.append(("user", question))

    agent = build_skill_agent(st.session_state.model)
    since = time.time()
    with st.chat_message("assistant", avatar="🔬"):
        with st.spinner(t("正在查文献 / 分析 / 组织回答……",
                          "Querying literature / analyzing / composing…")):
            result = agent.invoke({"messages": st.session_state.history})
        msgs = result["messages"]
        st.session_state.history = msgs
        answer = msgs[-1].content if hasattr(msgs[-1], "content") else str(msgs[-1])
        tools = render_trace(msgs)
        imgs = [str(p) for p in render_images(since)]

        st.markdown(answer)
        for img in imgs:
            st.image(img)
        if tools:
            with st.expander(t("🔍 它用了哪些工具/技能", "🔍 Tools / skills used")):
                st.caption("、".join(tools))

    st.session_state.chat.append({"role": "assistant", "text": answer,
                                  "tools": tools, "images": imgs})
