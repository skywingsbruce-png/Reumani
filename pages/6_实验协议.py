"""
🧫 湿实验协议（阶段7）—— 只生成结构化协议 + 静态校验 + 人工审批
⚠️ 本页不连接、不驱动任何仪器。设备执行是后期、且必须人工确认后才可能。
"""

import json

import streamlit as st

from ssc_pi_agent import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
from ssc_protocol import generate_protocol, validate_protocol
from i18n import t, lang_selector

st.set_page_config(page_title="Wet-lab Protocol", page_icon="🧫", layout="wide")
st.markdown("<style>.block-container{padding-top:2rem;max-width:950px;}</style>", unsafe_allow_html=True)
with st.sidebar:
    lang_selector()

st.title("🧫 " + t("湿实验协议生成 + 校验", "Wet-lab Protocol — Generate + Validate"))
st.caption(t("把实验想法变成结构化 Protocol IR，做单位/体积/对照/材料静态检查。仅供科研设计参考。",
             "Turns an experiment idea into a structured Protocol IR with static checks (units/volumes/controls/materials). Design aid only."))

st.error(t(
    "⚠️ **安全边界**：本功能**只生成协议、只做静态检查**，"
    "**不连接、不控制任何真实仪器**。任何真实湿实验执行必须由你人工审批、亲自操作。",
    "⚠️ **Safety boundary**: this **only generates protocols and runs static checks** — it **does NOT connect to or control any real instrument**. "
    "Any real wet-lab execution must be approved and performed by you."))

with st.sidebar:
    if DEEPSEEK_API_KEY:
        st.success(f"DeepSeek 已就绪（{DEEPSEEK_MODEL}）")
    else:
        st.error("未检测到 DEEPSEEK_API_KEY")
    model_label = st.radio("生成用模型", ["DeepSeek（省钱）", "Claude Opus（更严谨）"], index=0)
    model = "deepseek" if model_label.startswith("DeepSeek") else "claude"

desc = st.text_area(
    "实验想法",
    placeholder="例如：验证 TGF-β1 诱导 SSc 皮肤成纤维细胞向肌成纤维细胞转化，并检测 α-SMA 表达",
)

if st.button("🧪 生成并校验协议", type="primary", disabled=not (DEEPSEEK_API_KEY and desc.strip())):
    with st.spinner("生成结构化 Protocol IR..."):
        ir = generate_protocol(desc.strip(), model=model)
    passed, issues = validate_protocol(ir)

    if ir.get("error"):
        st.error(ir["error"])
    else:
        st.subheader(f"📋 {ir.get('title','实验协议')}")
        if passed:
            st.success("✅ 静态校验通过（单位/体积/对照/材料/验收/危险齐全）。仍需你人工审批后才能执行。")
        else:
            st.warning("⚠️ 静态校验发现问题，请修订：")
            for x in issues:
                st.markdown(f"- {x}")

        st.markdown(f"**目的**：{ir.get('objective','')}")
        st.markdown("**材料**")
        for m in ir.get("materials", []):
            st.markdown(f"- {m.get('name')}：{m.get('amount')} {m.get('notes','')}")
        st.markdown("**步骤**")
        for i, s in enumerate(ir.get("steps", []), 1):
            extra = []
            if s.get("volume_ul") is not None: extra.append(f"{s['volume_ul']}µL")
            if s.get("temperature_c") is not None: extra.append(f"{s['temperature_c']}°C")
            if s.get("duration_min") is not None: extra.append(f"{s['duration_min']}min")
            st.markdown(f"{i}. [{s.get('operation')}] {s.get('detail','')} {'｜'.join(extra)}")
        st.markdown(f"**对照**：{ir.get('controls')}")
        st.markdown(f"**验收标准**：{ir.get('acceptance_criteria')}")
        st.markdown(f"**危险/生物安全**：{ir.get('hazards')}")

        st.download_button("💾 下载 Protocol IR（JSON）",
                           data=json.dumps(ir, ensure_ascii=False, indent=2),
                           file_name=f"{ir.get('protocol_id','protocol')}.json",
                           mime="application/json")

        st.divider()
        st.info("下一步（后期、且需人工逐步确认）：编译为 Opentrons/PyLabRobot 代码 → 模拟运行 → "
                "人工确认 → 才可能允许设备执行 → 仪器结果回传 SSc-A1。本版止步于'生成+校验'。")
