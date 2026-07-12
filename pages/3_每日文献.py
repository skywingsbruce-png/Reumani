"""
📰 每日文献 —— 查看「每日文献哨兵」自动生成的 SSc/SLE 新文献报告
"""

from datetime import datetime
from pathlib import Path

import streamlit as st
from i18n import t, lang_selector

BASE = Path(__file__).resolve().parent.parent
REPORT_DIR = BASE / "daily_reports"
WATCH_SCRIPT = BASE / "ssc_daily_watch.py"

st.set_page_config(page_title="Daily Literature", page_icon="📰", layout="wide")
st.markdown("<style>.block-container{padding-top:2rem;max-width:900px;}</style>", unsafe_allow_html=True)
with st.sidebar:
    lang_selector()

st.title("📰 " + t("每日文献哨兵", "Daily Literature Watch"))
st.caption(t("每天自动扫描 SSc + SLE 的新文章，去重后生成报告。",
             "Automatically scans new SSc + SLE papers daily, deduplicates, and generates a report."))

# 报告文件（排除内部的 _seen_ids.json）
reports = sorted(
    [p for p in REPORT_DIR.glob("*.md")],
    key=lambda p: p.stem,
    reverse=True,
) if REPORT_DIR.exists() else []

with st.sidebar:
    st.subheader("状态")
    import subprocess, sys
    task_registered = False
    try:
        r = subprocess.run(
            ["schtasks", "/query", "/tn", "SSc_SLE_DailyLiteratureWatch"],
            capture_output=True, text=True, timeout=8,
        )
        task_registered = (r.returncode == 0)
    except Exception:
        pass
    if task_registered:
        st.success("✅ 定时任务已注册\n每天 9:00 自动运行")
    else:
        st.warning("⚠️ 定时任务尚未注册\n请双击项目里的\n『注册每日文献任务.bat』")

    st.divider()
    if st.button("▶️ 立即手动扫描一次", use_container_width=True):
        with st.spinner("正在扫描 SSc/SLE 新文献..."):
            try:
                subprocess.run([sys.executable, str(WATCH_SCRIPT)],
                               cwd=str(BASE), timeout=120)
                st.success("扫描完成，刷新查看今日报告")
            except Exception as e:
                st.error(f"扫描失败：{e}")
        st.rerun()

    st.divider()
    st.caption(f"📁 报告目录：\n{REPORT_DIR}")

if not reports:
    st.info(
        "还没有报告。两种方式生成：\n\n"
        "1. 双击项目目录里的 **『注册每日文献任务.bat』**（弹 UAC 点「是」），"
        "以后每天 9:00 自动生成；\n"
        "2. 或点左侧 **『立即手动扫描一次』** 现在就跑一次。"
    )
else:
    dates = [p.stem for p in reports]
    pick = st.selectbox("选择日期", dates, index=0)
    chosen = REPORT_DIR / f"{pick}.md"
    st.markdown(chosen.read_text(encoding="utf-8", errors="replace"))

    st.divider()
    st.download_button(
        "💾 下载这份报告（.md）",
        data=chosen.read_bytes(),
        file_name=chosen.name,
        mime="text/markdown",
    )
