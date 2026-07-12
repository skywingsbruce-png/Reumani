"""
极简双语助手：t(中文, English) 根据当前语言返回对应字符串。
语言存在 st.session_state['lang']（'zh' 默认 / 'en'），跨页面共享。
"""

import streamlit as st


def get_lang():
    return st.session_state.get("lang", "zh")


def t(zh, en):
    return en if get_lang() == "en" else zh


def lang_selector(sidebar=True):
    """在侧边栏渲染语言切换。返回当前 lang。"""
    box = st.sidebar if sidebar else st
    cur = st.session_state.get("lang", "zh")
    choice = box.radio("🌐 Language / 语言", ["中文", "English"],
                       index=0 if cur == "zh" else 1, horizontal=True, key="_lang_widget")
    st.session_state.lang = "en" if choice == "English" else "zh"
    return st.session_state.lang


def agent_lang_note():
    """给 agent 系统提示追加的语言指令。"""
    if get_lang() == "en":
        return "\n\nIMPORTANT: The user is working in English. Respond entirely in English."
    return ""
