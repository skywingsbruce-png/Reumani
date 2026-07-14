"""
🗂️ 数据对话（Data Chat）—— 上传文件/图片，它保留数据、跨会话记忆，针对这些数据回答。
- 上传 PDF/CSV/Excel/文本 → 抽成文字进上下文；上传图片 → 走 Claude 视觉。
- 对话持久化到 conversations/，刷新/重开不丢。
- 可"教它记住"一条事实/纠正，写入长期记忆(agent_memory)，之后自动注入。
"""

import json
from datetime import datetime
from pathlib import Path

import streamlit as st
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from ssc_pi_agent import DEEPSEEK_API_KEY, deepseek_llm_pro, judge_llm
import agent_memory as MEM
import doc_ingest as DI
from i18n import t, lang_selector, agent_lang_note

BASE = Path(__file__).resolve().parent.parent
CONV_DIR = BASE / "conversations"
CONV_DIR.mkdir(exist_ok=True)
UP_DIR = BASE / "agent_workspace" / "uploads"
UP_DIR.mkdir(parents=True, exist_ok=True)

st.set_page_config(page_title="Data Chat", page_icon="🗂️", layout="centered")
st.markdown("<style>.block-container{padding-top:2rem;max-width:840px;}</style>", unsafe_allow_html=True)
with st.sidebar:
    lang_selector()

# ---- 会话状态 ----
if "dc_id" not in st.session_state:
    st.session_state.dc_id = datetime.now().strftime("%Y%m%d_%H%M%S")
if "dc_msgs" not in st.session_state:
    st.session_state.dc_msgs = []
conv_file = CONV_DIR / f"datachat_{st.session_state.dc_id}.json"


def _save():
    conv_file.write_text(json.dumps(st.session_state.dc_msgs, ensure_ascii=False, indent=2), encoding="utf-8")


st.title("🗂️ " + t("数据对话", "Data Chat"))
st.caption(t("上传你的文件或图片，它会保留这些数据、记住对话，并针对数据回答。图片理解自动用 Claude 视觉。",
             "Upload your files or images; it keeps the data, remembers the conversation, and answers against it. Image understanding uses Claude vision."))

# ---- 侧边栏：模型 / 记忆 / 会话 ----
with st.sidebar:
    st.subheader(t("⚙️ 设置", "⚙️ Settings"))
    model_label = st.radio(t("回答模型", "Model"),
                           [t("DeepSeek（省钱，纯文本）", "DeepSeek (cheap, text)"),
                            t("Claude Opus（更强，支持图片）", "Claude Opus (stronger, vision)")], index=1)
    model = "deepseek" if model_label.startswith("DeepSeek") else "claude"

    st.divider()
    st.subheader("🧠 " + t("长期记忆", "Long-term memory"))
    st.caption(MEM.memory_summary())
    new_mem = st.text_input(t("教它记住一条（事实/你的偏好/纠正）", "Teach it one fact / preference / correction"),
                            key="dc_newmem")
    if st.button(t("💾 记住", "💾 Remember"), use_container_width=True) and new_mem.strip():
        MEM.add_memory(new_mem.strip(), kind="feedback", source="datachat")
        st.session_state.dc_newmem = ""
        st.rerun()

    st.divider()
    if st.button(t("🆕 新对话", "🆕 New chat"), use_container_width=True):
        st.session_state.dc_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        st.session_state.dc_msgs = []
        st.rerun()

# ---- 上传区 ----
ups = st.file_uploader(t("上传文件/图片（可多选：PDF/CSV/Excel/文本/图片）",
                         "Upload files/images (multi: PDF/CSV/Excel/text/image)"),
                       accept_multiple_files=True,
                       type=["pdf", "csv", "tsv", "xlsx", "xls", "txt", "md", "json",
                             "png", "jpg", "jpeg", "webp"])
saved_paths = []
if ups:
    sess_up = UP_DIR / st.session_state.dc_id
    sess_up.mkdir(parents=True, exist_ok=True)
    for uf in ups:
        p = sess_up / uf.name
        if not p.exists():
            p.write_bytes(uf.getbuffer())
        saved_paths.append(str(p))
    imgs = [p for p in saved_paths if DI.is_image(p)]
    docs = [p for p in saved_paths if not DI.is_image(p)]
    st.caption(t(f"已载入 {len(docs)} 个文档、{len(imgs)} 张图片，本轮提问将针对它们。",
                 f"Loaded {len(docs)} docs, {len(imgs)} images — this turn will use them."))

# ---- 历史 ----
for m in st.session_state.dc_msgs:
    with st.chat_message(m["role"], avatar="🗂️" if m["role"] == "assistant" else "🧑‍🔬"):
        st.markdown(m["text"])
        for img in m.get("images", []):
            if Path(img).exists():
                st.image(img, width=280)

# ---- 输入 ----
typed = st.chat_input(t("针对上传的数据提问，或直接聊……", "Ask about the uploaded data, or just chat…"))
if typed:
    if model == "deepseek" and not DEEPSEEK_API_KEY:
        st.error(t("未配置 DEEPSEEK_API_KEY，请切换 Claude 或在 .env 配置。", "No DEEPSEEK_API_KEY — switch to Claude or set .env."))
        st.stop()
    img_paths = [p for p in saved_paths if DI.is_image(p)]
    doc_paths = [p for p in saved_paths if not DI.is_image(p)]
    st.session_state.dc_msgs.append({"role": "user", "text": typed, "images": img_paths})
    with st.chat_message("user", avatar="🧑‍🔬"):
        st.markdown(typed)
        for ip in img_paths:
            st.image(ip, width=280)

    # 组装系统上下文：角色 + 长期记忆 + 上传文档文本
    sys_parts = [
        "你是医学科研数据助手。基于【用户上传的数据】和【长期记忆】回答，只依据给定材料，"
        "缺数据就说明，不编造。涉及文献引用只用真实来源。" + agent_lang_note()]
    mem_txt = MEM.format_for_prompt()
    if mem_txt:
        sys_parts.append(mem_txt)
    if doc_paths:
        blob = "\n\n".join(f"[文件：{Path(p).name}]\n{DI.extract_text(p)}" for p in doc_paths)
        sys_parts.append("【本次上传的文档内容】\n" + blob[:12000])
    system = "\n\n".join(sys_parts)

    # 历史 → 消息；最新一条若带图且用 Claude，则拼视觉块
    msgs = [SystemMessage(content=system)]
    for m in st.session_state.dc_msgs[:-1]:
        msgs.append(HumanMessage(content=m["text"]) if m["role"] == "user" else AIMessage(content=m["text"]))
    if img_paths and model == "claude":
        content = [{"type": "text", "text": typed}]
        for ip in img_paths[:4]:
            content.append({"type": "image_url", "image_url": {"url": DI.encode_image(ip)}})
        msgs.append(HumanMessage(content=content))
    else:
        if img_paths and model == "deepseek":
            st.info(t("DeepSeek 不支持读图，已忽略图片；要看图请切 Claude。",
                      "DeepSeek can't read images; switch to Claude for vision."))
        msgs.append(HumanMessage(content=typed))

    llm = judge_llm if model == "claude" else deepseek_llm_pro
    with st.chat_message("assistant", avatar="🗂️"):
        with st.spinner(t("针对你的数据思考中……", "Reasoning over your data…")):
            answer = llm.invoke(msgs).content
        st.markdown(answer)
    st.session_state.dc_msgs.append({"role": "assistant", "text": answer, "images": []})
    _save()

st.divider()
st.caption(t("💡 想让它长期记住某个结论/纠正，用左侧“教它记住”。对话已存到 conversations/，刷新不丢。",
             "💡 Use “Remember” (left) to persist a conclusion/correction. Chats are saved under conversations/."))
