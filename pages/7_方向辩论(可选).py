"""
SSc 研究方向智能体 —— 网页版
用法：streamlit run ssc_pi_agent_web.py
"""

import json
import re
from datetime import datetime
from pathlib import Path

import streamlit as st

from ssc_pi_agent import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_MODEL,
    debater_pro,
    debater_con,
    PRO_SYSTEM,
    CON_SYSTEM,
    _invoke,
    judge_agent,
    JUDGE_SYSTEM,
)

# ------------------------------------------------------------------
# 历史记录持久化：每次裁决/追问都会存盘，关掉网站/重启电脑都不会丢
# ------------------------------------------------------------------
CONV_DIR = Path(__file__).resolve().parent / "conversations"
CONV_DIR.mkdir(exist_ok=True)


def slugify(text: str, maxlen: int = 24) -> str:
    text = re.sub(r"[^\w一-鿿]+", "_", text).strip("_")
    return (text[:maxlen] or "session")


def session_file_path(session_id: str) -> Path:
    return CONV_DIR / f"{session_id}.json"


def save_session():
    if not st.session_state.get("session_id"):
        return
    data = {
        "session_id": st.session_state.session_id,
        "created_at": st.session_state.created_at,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "topic": st.session_state.topic,
        "rounds": st.session_state.rounds,
        "feedback": st.session_state.feedback,
        "chat": st.session_state.chat_display,
    }
    with open(session_file_path(st.session_state.session_id), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def list_sessions():
    files = sorted(CONV_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    sessions = []
    for p in files:
        try:
            with open(p, "r", encoding="utf-8") as f:
                sessions.append(json.load(f))
        except Exception:
            continue
    return sessions


def build_judge_history(session: dict) -> list:
    """根据存盘的会话记录，重建能继续对话的消息历史。"""
    transcript = "\n\n".join(
        f"【辩手{r['speaker']} - 第{r['round']}轮】\n{r['text']}" for r in session["rounds"]
    )
    feedback_block = (
        f"\n\n【首席研究员的补充意见，请务必纳入裁决考虑】：\n{session['feedback']}"
        if session.get("feedback") else ""
    )
    user_msg = (
        f"研究问题：{session['topic']}\n\n以下是两位 DeepSeek 辩手的完整辩论记录：\n\n{transcript}"
        f"{feedback_block}\n\n请给出你的裁决报告。"
    )
    history = [("system", JUDGE_SYSTEM), ("user", user_msg)]
    for entry in session.get("chat", []):
        role = "assistant" if entry["role"] == "assistant" else "user"
        history.append((role, entry["text"]))
    return history


def load_session(session: dict):
    st.session_state.session_id = session["session_id"]
    st.session_state.created_at = session["created_at"]
    st.session_state.topic = session["topic"]
    st.session_state.rounds = session["rounds"]
    st.session_state.feedback = session.get("feedback", "")
    st.session_state.chat_display = session.get("chat", [])
    st.session_state.judge_history = build_judge_history(session)
    st.session_state.stage = "judged"

st.set_page_config(page_title="Research Debate", page_icon="🧬", layout="wide")

st.markdown(
    """
    <style>
    .block-container {padding-top: 2rem; padding-bottom: 3rem; max-width: 900px;}
    [data-testid="stChatMessage"] {border-radius: 14px; padding: 0.6rem 0.9rem; margin-bottom: 0.4rem;}
    .speaker-a [data-testid="stChatMessage"] {background: #eef4ff;}
    .speaker-b [data-testid="stChatMessage"] {background: #fff3e8;}
    .judge-msg [data-testid="stChatMessage"] {background: #eafaf1; border: 1px solid #34c38f55;}
    h1 {margin-bottom: 0;}
    </style>
    """,
    unsafe_allow_html=True,
)

from i18n import t, lang_selector
with st.sidebar:
    lang_selector()

st.title("🧬 " + t("研究方向辩论（可选）", "Research Direction Debate (optional)"))
st.caption(t("正方/反方两个 DeepSeek 辩手检索文献辩论 → Claude 裁决。适合『该往哪个方向投入』这类开放决策；日常提问请用首页「研究助手」。",
             "Two DeepSeek debaters (pro/con) search the literature and argue → Claude judges. Best for open 'which direction to invest in' decisions; for everyday questions use the home Assistant."))

# ------------------------------------------------------------------
# session state 初始化
# ------------------------------------------------------------------
defaults = {
    "stage": "idle",          # idle -> debating -> debated -> judging -> judged
    "session_id": "",
    "created_at": "",
    "topic": "",
    "rounds": [],             # [{"round": 1, "speaker": "A"/"B", "text": ...}, ...]
    "feedback": "",
    "judge_history": [],      # 传给 judge_agent 的完整消息历史（含 System/Human/AI/Tool）
    "chat_display": [],       # 仅用于界面展示的 [{"role": "user"/"assistant", "text": ...}]
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v


def reset_all():
    for k, v in defaults.items():
        st.session_state[k] = v() if callable(v) else (v.copy() if isinstance(v, (list, dict)) else v)


# ------------------------------------------------------------------
# 侧边栏
# ------------------------------------------------------------------
with st.sidebar:
    st.subheader("状态")
    if DEEPSEEK_API_KEY:
        st.success(f"DeepSeek 辩手已就绪（{DEEPSEEK_MODEL}）")
    else:
        st.error("未检测到 DEEPSEEK_API_KEY，请先在 .env 中配置")
    st.info("Claude 主脑：claude-opus-4-8")

    st.divider()
    if st.button("🔄 开始新的研究问题", use_container_width=True):
        reset_all()
        st.rerun()

    with st.expander("📚 本地文献库工具"):
        st.caption(r"Claude 裁判可调用工具查阅 F:\SSC\Theo'S Article 下的 PDF 进行交叉印证")

    st.divider()
    st.subheader("📜 历史记录")
    st.caption("每次裁决/追问都会自动存盘，随时可以点开继续讨论")
    past_sessions = list_sessions()
    if not past_sessions:
        st.caption("暂无历史记录")
    else:
        for s in past_sessions[:20]:
            date_str = (s.get("updated_at") or s.get("created_at") or "")[:16].replace("T", " ")
            title = s["topic"][:20] + ("…" if len(s["topic"]) > 20 else "")
            is_current = s["session_id"] == st.session_state.session_id
            label = f"{'👉 ' if is_current else ''}{date_str}\n{title}"
            if st.button(label, key=f"load_{s['session_id']}", use_container_width=True):
                load_session(s)
                st.rerun()


def render_debate_history():
    if not st.session_state.rounds:
        return
    st.subheader(f"📖 研究问题：{st.session_state.topic}")
    for r in st.session_state.rounds:
        avatar = "🅰️" if r["speaker"] == "A" else "🅱️"
        label = "辩手 A（正方）" if r["speaker"] == "A" else "辩手 B（反方）"
        css_class = "speaker-a" if r["speaker"] == "A" else "speaker-b"
        st.markdown(f'<div class="{css_class}">', unsafe_allow_html=True)
        with st.chat_message("assistant", avatar=avatar):
            st.markdown(f"**{label} · 第 {r['round']} 轮**")
            st.markdown(r["text"])
        st.markdown("</div>", unsafe_allow_html=True)


# ------------------------------------------------------------------
# 步骤 1：输入研究问题，开始辩论
# ------------------------------------------------------------------
if st.session_state.stage == "idle":
    with st.form("topic_form"):
        topic_input = st.text_input(
            "研究方向问题",
            placeholder="例如：SSc 早期诊断的生物标志物方向 / 目前最值得投入的治疗靶点",
        )
        submitted = st.form_submit_button(
            "🚀 开始辩论", type="primary", disabled=not DEEPSEEK_API_KEY
        )
    if submitted and topic_input.strip():
        st.session_state.topic = topic_input.strip()
        st.session_state.created_at = datetime.now().isoformat(timespec="seconds")
        st.session_state.session_id = (
            datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + slugify(topic_input.strip())
        )
        st.session_state.stage = "debating"
        st.rerun()

# ------------------------------------------------------------------
# 步骤 2：跑辩论（只在 debating 阶段真正执行一次）
# ------------------------------------------------------------------
if st.session_state.stage == "debating":
    topic = st.session_state.topic
    st.subheader(f"📖 研究问题：{topic}")

    def show_turn(round_no, speaker, text):
        avatar = "🅰️" if speaker == "A" else "🅱️"
        label = "辩手 A（正方）" if speaker == "A" else "辩手 B（反方）"
        with st.chat_message("assistant", avatar=avatar):
            st.markdown(f"**{label} · 第 {round_no} 轮**")
            st.markdown(text)
        st.session_state.rounds.append({"round": round_no, "speaker": speaker, "text": text})

    with st.spinner("🔎 辩手A 正在检索文献并陈述立场..."):
        pro_arg = _invoke(
            debater_pro, PRO_SYSTEM,
            f"研究问题：{topic}\n请提出你认为最值得投入的方向。",
        )
    show_turn(1, "A", pro_arg)

    with st.spinner("🔎 辩手B 正在检索文献并反驳/提出替代方向..."):
        con_arg = _invoke(
            debater_con, CON_SYSTEM,
            f"研究问题：{topic}\n\n辩手A的观点如下：\n{pro_arg}\n\n请挑战辩手A的观点，并提出你的替代或补充方向。",
        )
    show_turn(1, "B", con_arg)

    with st.spinner("🔎 辩手A 第2轮回应..."):
        pro_arg = _invoke(
            debater_pro, PRO_SYSTEM,
            f"研究问题：{topic}\n\n辩手B刚刚提出以下反驳：\n{con_arg}\n\n"
            "请回应辩手B的质疑，必要时补充新的检索证据，坚持或修正你的立场。",
        )
    show_turn(2, "A", pro_arg)

    with st.spinner("🔎 辩手B 第2轮回应..."):
        con_arg = _invoke(
            debater_con, CON_SYSTEM,
            f"研究问题：{topic}\n\n辩手A刚刚回应如下：\n{pro_arg}\n\n请给出你的最终回应。",
        )
    show_turn(2, "B", con_arg)

    st.session_state.stage = "debated"
    st.rerun()

# ------------------------------------------------------------------
# 已经辩论完：始终展示辩论记录
# ------------------------------------------------------------------
if st.session_state.stage in ("debated", "judging", "judged"):
    render_debate_history()

# ------------------------------------------------------------------
# 步骤 3：辩论结束后，先让用户插话，再交给 Claude 裁决
# ------------------------------------------------------------------
if st.session_state.stage == "debated":
    st.divider()
    with st.form("feedback_form"):
        feedback = st.text_area(
            "💭 在裁决前，你有什么想法、实验室实际情况或倾向想让裁判纳入考虑？",
            placeholder="例如：我们实验室没有 CAR-T 的 GMP 条件 / 更偏好能快速转化的方向……（可留空）",
        )
        submitted = st.form_submit_button("⚖️ 提交并让 Claude 主脑裁决", type="primary")
    if submitted:
        st.session_state.feedback = feedback.strip()
        st.session_state.stage = "judging"
        st.rerun()

# ------------------------------------------------------------------
# 步骤 4：Claude 主脑裁决（只在 judging 阶段真正执行一次）
# ------------------------------------------------------------------
if st.session_state.stage == "judging":
    feedback_block = (
        f"\n\n【首席研究员的补充意见，请务必纳入裁决考虑】：\n{st.session_state.feedback}"
        if st.session_state.feedback
        else ""
    )
    transcript = "\n\n".join(
        f"【辩手{r['speaker']} - 第{r['round']}轮】\n{r['text']}" for r in st.session_state.rounds
    )
    user_msg = (
        f"研究问题：{st.session_state.topic}\n\n以下是两位 DeepSeek 辩手的完整辩论记录：\n\n{transcript}"
        f"{feedback_block}\n\n请给出你的裁决报告。"
    )
    with st.spinner("⚖️ Claude 主脑正在审阅辩论记录、交叉印证本地文献库并给出最终裁决..."):
        response = judge_agent.invoke({
            "messages": [
                ("system", JUDGE_SYSTEM),
                ("user", user_msg),
            ]
        })
    st.session_state.judge_history = response["messages"]
    st.session_state.chat_display.append({"role": "assistant", "text": response["messages"][-1].content})
    st.session_state.stage = "judged"
    save_session()
    st.rerun()

# ------------------------------------------------------------------
# 步骤 5：展示裁决 + 后续持续对话
# ------------------------------------------------------------------
if st.session_state.stage == "judged":
    st.divider()
    st.subheader("📋 裁决报告 & 后续讨论")

    for entry in st.session_state.chat_display:
        if entry["role"] == "assistant":
            st.markdown('<div class="judge-msg">', unsafe_allow_html=True)
            with st.chat_message("assistant", avatar="⚖️"):
                st.markdown(entry["text"])
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            with st.chat_message("user", avatar="🧑‍🔬"):
                st.markdown(entry["text"])

    follow_up = st.chat_input("继续和主脑讨论你的看法 / 追问 / 让它设计下一步实验……")
    if follow_up:
        st.session_state.chat_display.append({"role": "user", "text": follow_up})
        st.session_state.judge_history.append(("user", follow_up))
        with st.spinner("⚖️ 主脑正在思考..."):
            response = judge_agent.invoke({"messages": st.session_state.judge_history})
        st.session_state.judge_history = response["messages"]
        st.session_state.chat_display.append({"role": "assistant", "text": response["messages"][-1].content})
        save_session()
        st.rerun()
