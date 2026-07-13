"""
🔬 实验副驾（Experiment Copilot）—— "我此刻在做什么，它马上给正确湿实验路径"。
填：疾病 / 样本 / 手段 / 抗体或marker panel / 假说 → 秒出：
样本路径 + 相关自身抗体 + 流式门控 + 关键对照与坑 + 对口文献（带来源）。
"""

import streamlit as st

from experiment_copilot import LabContext, suggest_next, synthesize
from lab_knowledge import knowledge_summary
from i18n import t, lang_selector

st.set_page_config(page_title="Experiment Copilot", page_icon="🔬", layout="wide")
st.markdown("<style>.block-container{padding-top:2rem;max-width:950px;}</style>", unsafe_allow_html=True)
with st.sidebar:
    lang_selector()

st.title("🔬 " + t("实验副驾", "Experiment Copilot"))
st.caption(t("告诉它你此刻在做什么，它秒级组装出下一步湿实验路径：样本处理、相关自身抗体、流式门控、关键对照与坑，并附本地库里对口的文献（带来源）。",
             "Tell it what you're doing now; it instantly assembles the next wet-lab path: sample handling, relevant autoantibodies, flow gating, key controls & pitfalls, plus matching local literature (with sources)."))

with st.sidebar:
    st.subheader(t("知识层", "Knowledge layer"))
    st.info(knowledge_summary())
    st.caption(t("① 确定性组装（免费、秒回）\n② 可选 LLM 润色成一段实验规划（少量算力）",
                 "① Deterministic assembly (free, instant)\n② Optional LLM polish into a plan (small cost)"))

col1, col2 = st.columns(2)
with col1:
    disease = st.selectbox(t("疾病", "Disease"),
                           ["SSc", "RA", "SLE", "SjS", "IIM", "AAV"], index=0)
    sample = st.selectbox(t("样本类型", "Sample"),
                          [t("全血/外周血", "Whole blood/PBMC"), t("血清/血浆", "Serum/plasma"),
                           t("皮肤活检", "Skin biopsy"), t("（不指定）", "(unspecified)")], index=0)
with col2:
    assay = st.text_input(t("实验手段", "Assay"),
                          placeholder=t("流式 / 自身抗体 / scRNA / ELISA …", "flow / autoantibody / scRNA / ELISA …"))
    panel = st.text_input(t("抗体 / marker panel（逗号分隔）", "Antibody / marker panel (comma-separated)"),
                          placeholder="CD4, CD25, FoxP3, 循环纤维细胞")

hypothesis = st.text_area(t("当前假说（一句话，可选）", "Current hypothesis (one line, optional)"),
                          placeholder=t("例如：SSc 外周血促纤维化单核/纤维细胞比例升高并与 mRSS 相关",
                                        "e.g. pro-fibrotic monocytes/fibrocytes are elevated in SSc blood and track with mRSS"))

# 把中文样本标签映射回知识库键
_sample_map = {t("全血/外周血", "Whole blood/PBMC"): "全血", t("血清/血浆", "Serum/plasma"): "血清",
               t("皮肤活检", "Skin biopsy"): "皮肤活检", t("（不指定）", "(unspecified)"): ""}


def _build_ctx():
    panel_list = [x.strip() for x in panel.replace("，", ",").split(",") if x.strip()]
    return LabContext(disease=disease, sample=_sample_map.get(sample, ""),
                      assay=assay.strip(), panel=panel_list, hypothesis=hypothesis.strip())


c1, c2 = st.columns([1, 1])
with c1:
    go = st.button("⚡ " + t("秒出下一步建议", "Instant next-step"), type="primary")
with c2:
    polish = st.button("✍️ " + t("LLM 润色成实验规划", "LLM-polish into a plan"))

_sub = f"{disease} · {_sample_map.get(sample, '')} · {assay}".strip(" ·")

if go:
    with st.spinner(t("组装中（含本地文献检索）……", "Assembling (incl. local literature)…")):
        out = suggest_next(_build_ctx(), with_literature=True, top_k=5)
    st.session_state["copilot_plan"] = out
    st.session_state["copilot_sub"] = _sub
    st.session_state["copilot_kind"] = t("下一步建议", "Next-step suggestion")

if polish:
    from ssc_pi_agent import DEEPSEEK_API_KEY
    if not DEEPSEEK_API_KEY:
        st.error(t("未检测到 DEEPSEEK_API_KEY，无法润色。", "DEEPSEEK_API_KEY not found."))
    else:
        with st.spinner(t("先组装、再让模型润色成可执行规划……", "Assembling, then polishing into an actionable plan…")):
            plan = synthesize(_build_ctx(), model="deepseek")
        st.session_state["copilot_plan"] = plan
        st.session_state["copilot_sub"] = _sub
        st.session_state["copilot_kind"] = t("实验规划（LLM 润色）", "Experiment plan (LLM-polished)")

# 展示结果 + 一键导出 PDF（结果存 session_state，翻页/下载不丢）
if st.session_state.get("copilot_plan"):
    st.subheader("📋 " + st.session_state.get("copilot_kind", ""))
    st.markdown(st.session_state["copilot_plan"])
    try:
        from pdf_export import build_plan_pdf
        pdf_bytes = build_plan_pdf(
            t("实验副驾方案", "Experiment Copilot Plan"),
            st.session_state.get("copilot_sub", ""),
            st.session_state["copilot_plan"])
        st.download_button("📄 " + t("导出 PDF 实验方案", "Export plan as PDF"),
                           data=pdf_bytes, file_name="experiment_plan.pdf", mime="application/pdf")
    except Exception as e:
        st.caption(t(f"PDF 导出不可用：{e}", f"PDF export unavailable: {e}"))

st.divider()
st.caption(t("⚠️ 输出为科研决策支持，非临床/操作规程；对照、伦理与最终判断由研究者负责。知识层可在 lab_knowledge.py 扩成你们实验室自己的 SOP/panel。",
             "⚠️ Decision-support only, not a clinical/operating protocol; controls, ethics and final judgment stay with the researcher. Extend lab_knowledge.py with your lab's own SOPs/panels."))
