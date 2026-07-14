"""
实验副驾（Experiment Copilot）——"我此刻在做什么，它马上反应过来给正确湿实验路径"。
输入：一个 LabContext（疾病 / 样本类型 / 实验手段 / 抗体或marker panel / 当前假说）。
输出：结构化"下一步建议"——样本路径骨架 + 相关自身抗体/流式亚群 + 对口文献 + 对照与坑。
默认走【确定性组装】（不烧 API、可测试）；需要润色成一段话时再调 synthesize()（用 LLM）。
"""

from dataclasses import dataclass, field
from typing import List

import lab_knowledge as LK


@dataclass
class LabContext:
    disease: str = ""          # SSc / RA / SLE / SjS / IIM / AAV
    sample: str = ""           # 全血 / 皮肤活检 / 血清 ...
    assay: str = ""            # 流式 / 自身抗体 / scRNA / ELISA ...
    panel: List[str] = field(default_factory=list)   # 抗体名或流式marker，如 ["ACPA","CD4","CD25"]
    hypothesis: str = ""       # 当前想验证的假说，一句话


def _relevant_antibodies(ctx):
    hits = {}
    for token in [ctx.disease] + ctx.panel:
        for k, v in LK.AUTOANTIBODIES.items():
            blob = (k + " " + " ".join(v["aka"]) + " " + v["disease"]).lower()
            if token and token.lower() in blob:
                hits[k] = v
    return hits


def _relevant_flow(ctx):
    hits = {}
    for token in ctx.panel + [ctx.disease]:
        for k, v in LK.FLOW_SUBSETS.items():
            blob = (k + " " + " ".join(v["markers"]) + " " + v["relevance"]).lower()
            if token and token.lower() in blob:
                hits[k] = v
    return hits


def suggest_next(ctx, with_literature=True, top_k=5, full_hybrid=False):
    """确定性组装：把'你在做什么'翻译成结构化的下一步建议。不烧 API。
    full_hybrid=False 时文献走快速通道(BM25+同义词，秒级)；True 时用完整向量+重排序(较慢)。"""
    out = [f"====== 实验副驾建议 ======",
           f"情境：{ctx.disease or '?'} · 样本={ctx.sample or '?'} · 手段={ctx.assay or '?'}"
           + (f" · panel={ctx.panel}" if ctx.panel else "")
           + (f"\n假说：{ctx.hypothesis}" if ctx.hypothesis else "")]

    # 1) 样本路径骨架
    if ctx.sample:
        out.append("\n① 样本路径 & 坑\n" + LK.sample_pathway(ctx.sample))

    # 2) 相关自身抗体
    abs_ = _relevant_antibodies(ctx)
    if abs_:
        out.append("\n② 相关自身抗体（临床解读 / 该测什么）")
        for k, v in list(abs_.items())[:6]:
            out.append(f"- {k}（{'/'.join(v['aka'][:2])}）→ {v['subtype']}；{v['clinical']}；检测：{v['assay']}")

    # 3) 相关流式亚群
    fl = _relevant_flow(ctx)
    if fl:
        out.append("\n③ 相关流式亚群（门控建议）")
        for k, v in list(fl.items())[:6]:
            out.append(f"- {k}：{', '.join(v['markers'])}｜{v['relevance']}")

    # 4) 对照与常识提醒（按手段）
    tips = _controls_tips(ctx)
    if tips:
        out.append("\n④ 对照 & 严谨性提醒\n" + "\n".join(f"- {t}" for t in tips))

    # 4.5) 相关成熟协议（SOP，若情境命中协议库）
    try:
        import protocols as PROT
        ctx_text = " ".join([ctx.disease, ctx.assay, ctx.hypothesis] + ctx.panel)
        phits = PROT.match_protocols(ctx_text)
        # 疾病要对上，避免泛匹配
        phits = [(n, p) for n, p in phits if not ctx.disease or p["disease"].lower() == ctx.disease.lower()
                 or any(k in ctx_text.lower() for k in p["keywords"])]
        if phits:
            out.append("\n⑤ 相关成熟协议（SOP，可直接参考步骤）")
            for n, p in phits[:2]:
                out.append(PROT.format_protocol(n, p, full=True))
    except Exception:
        pass

    # 6) 对口文献（快速通道：BM25+同义词，不加载向量/重排序模型，秒级返回）
    #    完整向量+重排序留给"混合检索"页；这里只需佐证，full_hybrid=True 可切换。
    if with_literature:
        q = " ".join(x for x in [ctx.disease, ctx.assay, ctx.hypothesis] + ctx.panel if x)
        try:
            from retrieval import retrieve_docs
            corpus = ctx.disease if ctx.disease in {"SSc", "SLE", "RA", "CIN"} else "all"
            mode = "hybrid" if full_hybrid else "synonym"
            docs = retrieve_docs(q, corpus=corpus, mode=mode, top_k=top_k)
            lines = ["\n⑥ 对口文献（本地库，带来源）"]
            for r in docs:
                link = (f"https://pubmed.ncbi.nlm.nih.gov/{r['pmid']}/" if r.get("pmid")
                        else (f"https://doi.org/{r['doi']}" if r.get("doi") else ""))
                lines.append(f"- [{r.get('year')}] {r.get('title','')[:85]} | {r.get('journal','')} | {link}")
            out.append("\n".join(lines))
        except Exception as e:
            out.append(f"\n⑤ 文献检索跳过：{e}")

    return "\n".join(out)


def _controls_tips(ctx):
    tips = []
    a = (ctx.assay or "").lower()
    if "流式" in ctx.assay or "flow" in a:
        tips += ["设 FMO(荧光减一)对照定门，不要只靠未染管；补偿用单染珠",
                 "活死染料排除死细胞假阳性；胞内因子(IL-17/FoxP3)需刺激(PMA/ion)+蛋白转运抑制剂(BFA/monensin)",
                 "报绝对计数需加计数微球或双平台"]
    if "自身抗体" in ctx.assay or "elisa" in a or "抗体" in ctx.assay:
        tips += ["IIF(HEp-2)先看核型再上特异抗体确认；阳性需滴度",
                 "设阴/阳性血清对照；线性免疫印迹弱带谨慎解读，必要时ELISA复核"]
    if "scrna" in a or "单细胞" in ctx.assay:
        tips += ["受累 vs 未受累/健康对照必须配对；记录解离方案与活率(>80%)",
                 "回归批次；细胞类型注释用 marker 而非仅聚类号"]
    if ctx.disease and not ctx.sample:
        tips.append("先明确样本类型(全血/血清/皮肤)才能定下游路径")
    return tips


def synthesize(ctx, model="deepseek"):
    """可选：把确定性建议 + 你的假说，让 LLM 润色成一段可执行的实验规划（烧少量 API）。"""
    base = suggest_next(ctx, with_literature=True)
    from ssc_pi_agent import deepseek_llm_pro, judge_llm
    llm = judge_llm if model == "claude" else deepseek_llm_pro
    prompt = (
        "你是风湿免疫湿实验方法学顾问。下面是系统根据知识库+文献自动组装的结构化材料。"
        "请据此给出一段【具体、可执行的下一步实验规划】：明确要做的实验、关键对照、"
        "预期读出、以及这一步如何验证/证伪用户的假说。只依据材料，不编造文献。\n\n" + base)
    return llm.invoke(prompt).content


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ctx = LabContext(disease="SSc", sample="全血", assay="流式",
                     panel=["CD4", "CD25", "FoxP3", "循环纤维细胞"],
                     hypothesis="SSc外周血促纤维化单核/纤维细胞比例升高并与mRSS相关")
    print(suggest_next(ctx, with_literature=False))
