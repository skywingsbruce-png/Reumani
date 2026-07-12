"""
阶段6：Action Discovery Agent（离线读 SSc 论文，发现值得封装成工具的科研动作）。
流程：论文提取 → 标准化 → 语义去重 → 频率与价值评分 → 写入人工审核队列。
⚠️ 安全铁律：只产出"候选动作 + 是否值得封装"，绝不自动实现代码、绝不自动加进正式环境。
   审核、实现、测试、进 SSc-E1 都是人工后续步骤。
用法：python ssc_action_discovery.py [每类篇数]
"""

import json
import re
from datetime import datetime
from pathlib import Path

from ssc_pi_agent import deepseek_llm_pro

BASE = Path(__file__).resolve().parent
CORPUS = BASE / "data_lake" / "ssc_corpus" / "corpus.jsonl"
QUEUE_DIR = BASE / "action_queue"
QUEUE_DIR.mkdir(exist_ok=True)
QUEUE_FILE = QUEUE_DIR / "candidates.json"

CATEGORIES = {
    "综述/高质量原始研究": ["review", "systematic", "consensus", "guideline", "cohort", "randomized"],
    "单细胞/空间组学": ["single-cell", "single cell", "scrna", "spatial transcriptom", "scseq"],
    "纤维化/CIN/DNA损伤": ["fibrosis", "fibroblast", "chromosomal instability", "dna damage",
                        "senescence", "micronuclei", "genomic instability"],
}

# 湿实验模式：挑方法学信息密集的论文，挖"湿实验动作"(流式方案/抗体panel/细胞分离/功能实验/对照)
WET_CATEGORIES = {
    "流式/免疫分型": ["flow cytometry", "immunophenotyp", "facs", "mass cytometry", "cytof",
                  "regulatory t", "th17", "plasmablast", "monocyte subset"],
    "自身抗体/血清学": ["autoantibod", "elisa", "immunoblot", "immunofluorescence", "anca",
                   "anti-centromere", "anti-topoisomerase", "acpa", "serum"],
    "细胞分离/原代培养/功能实验": ["pbmc", "fibroblast culture", "co-culture", "stimulation",
                        "knockdown", "sirna", "crispr", "migration assay", "proliferation assay"],
}


def _load_corpus():
    rows = []
    if CORPUS.exists():
        for line in CORPUS.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def select_papers(n_per_cat=50, mode="dry"):
    """从本地语料库按类别挑论文。mode='dry'挖计算动作，'wet'挖湿实验动作。"""
    corpus = _load_corpus()
    cats = WET_CATEGORIES if mode == "wet" else CATEGORIES
    picked, seen = {}, set()
    for cat, kws in cats.items():
        hits = []
        for r in corpus:
            blob = (r.get("title", "") + " " + r.get("abstract", "") + " " + r.get("pub_type", "")).lower()
            if any(k in blob for k in kws) and r.get("pmid") not in seen:
                hits.append(r)
        hits.sort(key=lambda r: r.get("year", ""), reverse=True)
        take = hits[:n_per_cat]
        for r in take:
            seen.add(r.get("pmid"))
        picked[cat] = take
    return picked


def _parse_json(text):
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        text = m.group(0)
    return json.loads(text)


def extract_actions_batch(papers, model="deepseek"):
    """一次给几篇论文，让 LLM 提取可复用的科研动作候选。返回 list[dict]。"""
    llm = deepseek_llm_pro  # 省钱，用 DeepSeek
    listing = ""
    for i, p in enumerate(papers):
        listing += f"\n[{i}] PMID={p.get('pmid')} 标题：{p.get('title')}\n摘要：{p.get('abstract','')[:1200]}\n"
    prompt = (
        "你是科研方法学分析员。下面是若干 SSc 论文的标题+摘要。请从中提取【可复用、可自动化的科研动作】"
        "（比如'皮肤 scRNA-seq 成纤维细胞亚聚类'、'差异表达+通路富集'、'CIN signature 打分'），"
        "只依据摘要，不编造。输出 JSON 数组，每个动作一个对象：\n"
        "{\"task_name\":\"动作名\",\"input_types\":[\"输入数据类型\"],\"output_types\":[\"输出\"],"
        "\"software\":[\"软件/包\"],\"databases\":[\"数据库\"],\"key_parameters\":\"关键参数\","
        "\"validation_method\":\"验证方法\",\"worth_implementing\":true/false,"
        "\"reason\":\"是否值得封装成工具的理由\",\"source_pmid\":\"来源PMID\"}\n"
        "只提取真正需要领域知识、可复用、易出错、值得封装的动作；几行普通代码能搞定的不用提。"
        "只输出 JSON 数组。\n" + listing
    )
    try:
        return _parse_json(llm.invoke(prompt).content)
    except Exception as e:
        return [{"task_name": "解析失败", "error": str(e)}]


def extract_wet_actions_batch(papers, model="deepseek"):
    """挖【湿实验动作】：从摘要提取可复用的湿实验方案骨架（样本→处理→读出→对照）。"""
    llm = deepseek_llm_pro
    listing = ""
    for i, p in enumerate(papers):
        listing += f"\n[{i}] PMID={p.get('pmid')} 标题：{p.get('title')}\n摘要：{p.get('abstract','')[:1200]}\n"
    prompt = (
        "你是风湿免疫湿实验方法学分析员。下面是若干论文标题+摘要。请提取【可复用的湿实验动作/方案骨架】"
        "（如'外周血PBMC流式检测Treg(CD4+CD25+FoxP3+)'、'血清抗Scl-70 ELISA'、"
        "'SSc成纤维细胞TGF-β刺激+αSMA检测'），只依据摘要，不编造。输出 JSON 数组，每个动作一对象：\n"
        "{\"task_name\":\"动作名\",\"sample\":\"样本类型\",\"technique\":\"技术(流式/ELISA/培养…)\","
        "\"markers_or_targets\":[\"抗体/marker/靶点\"],\"readout\":\"读出指标\","
        "\"controls\":\"关键对照\",\"reagents\":[\"关键试剂\"],\"worth_curating\":true/false,"
        "\"reason\":\"是否值得纳入协议库的理由\",\"source_pmid\":\"来源PMID\"}\n"
        "只提取真正需要领域知识、易出错、值得沉淀的方案。只输出 JSON 数组。\n" + listing
    )
    try:
        return _parse_json(llm.invoke(prompt).content)
    except Exception as e:
        return [{"task_name": "解析失败", "error": str(e)}]


def _norm(s):
    return re.sub(r"[^a-z0-9一-鿿]+", "", (s or "").lower())


def standardize_and_dedup(candidates):
    """标准化 + 语义去重（按动作名 token 归并），并累计出现频次。"""
    groups = {}
    for c in candidates:
        if not c.get("task_name") or c.get("task_name") == "解析失败":
            continue
        key = _norm(c["task_name"])[:40]
        # 找已有相近组（token 重叠）
        # 近似语义去重（字符/token 重叠）——第一版简单方法，embedding 语义去重为后期升级
        matched = None
        for gk in groups:
            a, b = set(key), set(gk)
            if a and b and len(a & b) / min(len(a), len(b)) > 0.6:
                matched = gk
                break
        gk = matched or key
        g = groups.setdefault(gk, {"task_name": c["task_name"], "count": 0,
                                    "software": set(), "databases": set(),
                                    "input_types": set(), "output_types": set(),
                                    "worth_votes": 0, "sources": [], "reasons": []})
        g["count"] += 1
        g["software"].update(c.get("software", []) or [])
        g["databases"].update(c.get("databases", []) or [])
        g["input_types"].update(c.get("input_types", []) or [])
        g["output_types"].update(c.get("output_types", []) or [])
        if c.get("worth_implementing"):
            g["worth_votes"] += 1
        if c.get("source_pmid"):
            g["sources"].append(str(c["source_pmid"]))
        if c.get("reason"):
            g["reasons"].append(c["reason"])
    # 转成可序列化 + 评分（频率 × 是否值得）
    out = []
    for g in groups.values():
        score = g["count"] * 2 + g["worth_votes"] * 3
        out.append({
            "task_name": g["task_name"], "count": g["count"],
            "worth_votes": g["worth_votes"], "score": score,
            "software": sorted(g["software"]), "databases": sorted(g["databases"]),
            "input_types": sorted(g["input_types"]), "output_types": sorted(g["output_types"]),
            "sources": g["sources"][:10], "reason_sample": g["reasons"][:2],
            "status": "pending_review",   # ⚠️ 待人工审核，未实现
        })
    out.sort(key=lambda x: -x["score"])
    return out


def standardize_wet(candidates):
    """湿实验候选：按动作名去重、保留湿实验字段、worth_curating 票数排序。"""
    groups = {}
    for c in candidates:
        name = c.get("task_name")
        if not name or name == "解析失败":
            continue
        key = _norm(name)[:40]
        g = groups.setdefault(key, {"task_name": name, "count": 0, "worth_votes": 0,
                                    "sample": set(), "technique": set(), "markers_or_targets": set(),
                                    "readout": set(), "controls": set(), "reagents": set(),
                                    "sources": [], "status": "pending_review"})
        g["count"] += 1
        if c.get("worth_curating"):
            g["worth_votes"] += 1
        for f in ["sample", "technique", "readout", "controls"]:
            if c.get(f):
                g[f].add(str(c[f]))
        for f in ["markers_or_targets", "reagents"]:
            g[f].update(c.get(f, []) or [])
        if c.get("source_pmid"):
            g["sources"].append(str(c["source_pmid"]))
    out = []
    for g in groups.values():
        for f in ["sample", "technique", "markers_or_targets", "readout", "controls", "reagents"]:
            g[f] = sorted(g[f])
        g["score"] = g["count"] * 2 + g["worth_votes"] * 3
        g["sources"] = g["sources"][:10]
        out.append(g)
    out.sort(key=lambda x: -x["score"])
    return out


def run_discovery(n_per_cat=50, batch_size=5, max_papers=None, mode="dry"):
    """mode='dry' 挖计算动作；'wet' 挖湿实验方案（写入独立队列）。"""
    picked = select_papers(n_per_cat, mode=mode)
    papers = [p for lst in picked.values() for p in lst]
    if max_papers:
        papers = papers[:max_papers]
    extractor = extract_wet_actions_batch if mode == "wet" else extract_actions_batch
    print(f"[{mode}] 选出 {len(papers)} 篇论文，开始提取动作...", flush=True)
    all_cands = []
    for i in range(0, len(papers), batch_size):
        batch = papers[i:i + batch_size]
        all_cands.extend(extractor(batch))
        print(f"  已处理 {min(i+batch_size, len(papers))}/{len(papers)}，累计候选 {len(all_cands)}", flush=True)
    ranked = standardize_wet(all_cands) if mode == "wet" else standardize_and_dedup(all_cands)
    queue_file = QUEUE_DIR / (f"wet_candidates.json" if mode == "wet" else "candidates.json")
    payload = {"built_at": datetime.now().isoformat(timespec="seconds"), "mode": mode,
               "n_papers": len(papers), "n_raw_candidates": len(all_cands),
               "n_deduped": len(ranked), "candidates": ranked}
    queue_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return f"[{mode}] 动作发现完成：{len(papers)} 篇 → {len(all_cands)} 原始候选 → {len(ranked)} 去重后，写入人工审核队列 {queue_file}"


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    # 用法：python ssc_action_discovery.py [每类篇数] [dry|wet]
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    mode = sys.argv[2] if len(sys.argv) > 2 else "dry"
    print(run_discovery(n_per_cat=n, mode=mode))
