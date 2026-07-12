"""
🔬 Action Discovery 审核队列（阶段6）
从 SSc 论文自动发现的"可封装科研动作"候选，进人工审核。
⚠️ 审核通过 ≠ 自动实现。实现工具是审核后的人工步骤，绝不自动加进正式环境。
"""

import json
from pathlib import Path

import streamlit as st
from i18n import t, lang_selector

BASE = Path(__file__).resolve().parent.parent
QUEUE = BASE / "action_queue" / "candidates.json"

st.set_page_config(page_title="Action Discovery", page_icon="🔬", layout="wide")
st.markdown("<style>.block-container{padding-top:2rem;max-width:1000px;}</style>", unsafe_allow_html=True)
with st.sidebar:
    lang_selector()

st.title("🔬 " + t("Action Discovery 审核队列", "Action Discovery — Review Queue"))
st.caption(t("从本地 SSc 论文语料自动提取的'可封装科研动作'候选，按频率×价值排序，供你人工审核。",
             "Reusable research actions auto-mined from the SSc corpus, ranked by frequency×value, for human review."))

st.warning(t(
    "⚠️ **安全铁律**：审核'通过'只是标记这个动作值得实现，**不会自动生成或运行任何代码**。"
    "实现工具 → 测试 → 进 SSc-E1，都是审核之后的人工步骤。",
    "⚠️ **Safety rule**: 'Approve' only flags an action as worth implementing — **no code is generated or run automatically**. "
    "Implement → test → add to SSc-E1 are all manual steps done after review."))

if not QUEUE.exists():
    st.info("审核队列为空。在终端运行 `python ssc_action_discovery.py 50` 生成候选"
            "（会读本地语料库、调用 DeepSeek 提取，约 150 篇；也可先小批测试 `max_papers`）。")
    st.stop()

data = json.loads(QUEUE.read_text(encoding="utf-8"))
cands = data["candidates"]

col1, col2, col3, col4 = st.columns(4)
col1.metric("论文数", data.get("n_papers", "?"))
col2.metric("原始候选", data.get("n_raw_candidates", "?"))
col3.metric("去重后", data.get("n_deduped", len(cands)))
col4.metric("待审核", sum(1 for c in cands if c.get("status") == "pending_review"))

flt = st.radio("筛选", ["全部", "待审核", "已通过", "已拒绝"], horizontal=True)
status_map = {"待审核": "pending_review", "已通过": "approved", "已拒绝": "rejected"}


def save():
    QUEUE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


for i, c in enumerate(cands):
    if flt != "全部" and c.get("status") != status_map.get(flt):
        continue
    badge = {"pending_review": "🕓 待审核", "approved": "✅ 已通过", "rejected": "❌ 已拒绝"}.get(c.get("status"), "")
    with st.container(border=True):
        st.markdown(f"**[score {c['score']}] {c['task_name']}**　{badge}")
        cc1, cc2 = st.columns(2)
        cc1.markdown(f"- 输入：{c.get('input_types')}\n- 输出：{c.get('output_types')}")
        cc2.markdown(f"- 软件：{c.get('software')}\n- 数据库：{c.get('databases')}")
        st.caption(f"出现频次 {c.get('count')}｜值得封装票 {c.get('worth_votes')}｜来源PMID {c.get('sources')}")
        if c.get("reason_sample"):
            st.caption("理由样例：" + "；".join(c["reason_sample"]))
        b1, b2, _ = st.columns([1, 1, 4])
        if b1.button("✅ 通过", key=f"ap{i}"):
            c["status"] = "approved"; save(); st.rerun()
        if b2.button("❌ 拒绝", key=f"rj{i}"):
            c["status"] = "rejected"; save(); st.rerun()

approved = [c["task_name"] for c in cands if c.get("status") == "approved"]
if approved:
    st.divider()
    st.subheader("✅ 已通过、等待人工实现的动作")
    for a in approved:
        st.markdown(f"- {a}")
    st.caption("下一步（人工）：为这些动作写工具 + 单元测试 → 注册进 ssc_resources.py 的 SSc-E1。")
