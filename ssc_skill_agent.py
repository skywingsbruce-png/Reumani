"""
通用科研 Agent（技能驱动版）
—— 这是把 agent 从「写死流程」升级成「读技能→自己干活」的核心。
   工作方式和视频里的「龙虾」一致：
   Agent 软件（本文件） + 大模型算力（DeepSeek/Claude） + Skill（nature-skills 等 SKILL.md）。
   AI 干活前先 list_skills 看有哪些技能，再 read_skill 读手册，然后按手册调用工具执行。
"""

import os
import re
import subprocess
import sys
from pathlib import Path

from langchain_core.tools import tool
from langchain.agents import create_agent as create_react_agent

from ssc_pi_agent import (
    search_literature,
    list_directory_pdfs,
    read_local_pdf,
    judge_llm,          # Claude Opus
    deepseek_llm_pro,   # DeepSeek（省钱）
)
from skill_loader import list_skills_text, read_skill_md
from ssc_evidence import (
    search_with_abstracts,
    make_evidence_cards,
    format_cards,
    verify_claim as _verify_claim,
)
from ssc_resources import retriever as _resource_retriever
from ssc_sandbox import safe_run_python as _safe_run_python
import data_lake_query as _dlq

BASE = Path(__file__).resolve().parent
WORKSPACE = BASE / "agent_workspace"
WORKSPACE.mkdir(exist_ok=True)


# ==========================================
# 技能相关工具
# ==========================================
@tool
def list_skills() -> str:
    """列出所有可用的科研技能（Skill）及其用途。开始任何任务前【必须】先调用它，看看有没有现成技能。"""
    return list_skills_text()


@tool
def read_skill(skill_name: str) -> str:
    """读取某个技能的完整 SKILL.md 操作手册（含工作流、规则、附带脚本清单）。
    选定要用的技能后调用它，然后严格按手册执行。参数为技能名或文件夹名。"""
    return read_skill_md(skill_name)


# ==========================================
# 执行类工具
# ==========================================
# read_file 只允许读这些根目录内的文件（resolve 后仍需落在其中；拒绝任意绝对路径/穿越/.env）
_ALLOWED_READ_ROOTS = [WORKSPACE, BASE / "data_lake", BASE / "skills"]


def _within_allowed(p: Path) -> bool:
    try:
        rp = p.resolve()
    except Exception:
        return False
    if rp.name == ".env" or rp.suffix == ".env":
        return False
    for root in _ALLOWED_READ_ROOTS:
        try:
            rp.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


@tool
def read_file(path: str) -> str:
    """读取【允许目录内】的本地文本/CSV/代码（agent_workspace / data_lake / skills）。返回前 20000 字符。
    只读允许目录：解析后仍须落在允许根内，拒绝任意绝对路径、路径穿越与 .env。"""
    try:
        p = Path(path)
        if not p.is_absolute():
            p = WORKSPACE / path
        if not _within_allowed(p):
            return "[拒绝] 只能读取 agent_workspace / data_lake / skills 目录内的文件（禁止任意绝对路径、.. 穿越、.env）。"
        return p.resolve().read_text(encoding="utf-8", errors="replace")[:20000]
    except Exception as e:
        return f"读取失败：{e}"


@tool
def run_python(code: str) -> str:
    """在【受限沙箱(Level 2,非强隔离)】里执行 Python 代码做数据分析、统计、画图（火山图/森林图等）。
    一任务一独立目录，已装 pandas/numpy/scipy/matplotlib/scanpy 等。
    画图用 matplotlib 并 plt.savefig 存 .png（会自动显示在网页上）。
    受限策略(Level2)：运行时剥离所有 API 密钥；禁止 subprocess/系统命令/删除文件/读取.env/路径穿越；
    仅适用于公开、非敏感数据（患者数据须等 Level 3 容器隔离）。
    返回结构化结果（成功给输出，失败明确报错，不会把失败伪装成正常文本）。"""
    result = _safe_run_python(code)
    return result.as_text()


@tool(response_format="content_and_artifact")
def triage_hypothesis(signature_a: str, signature_b: str, geo_datasets: str) -> tuple:
    """【假说关联排查（观察性）】给两个基因 signature（内置名如 CIN/cGAS_STING/IFN_ISG/TGF_fibrosis/senescence，
    或逗号分隔的基因列表）和一批 GEO 数据集编号（逗号分隔，如 GSE58095），
    在每个数据集算 signature 关联并做严谨性检查（重叠/命中率/样本量/Pearson+Spearman/FDR/CI/零分布/leave-one-out），
    给四选一结论：supportive_association / no_detectable_support / inconsistent / technically_unresolved。
    ⚠️ 这是关联排查不是因果判定，不用"证明/证伪/杀死"措辞；不能仅凭 p 值就建议放弃湿实验。"""
    from hypothesis_triage import triage, format_report

    def _parse(s):
        s = s.strip()
        if "," in s and " " not in s.strip().rstrip(","):
            parts = [x.strip() for x in s.split(",") if x.strip()]
            # 若像基因列表（多个且非单个内置名）
            return parts if len(parts) > 1 else parts[0]
        return s

    from hypothesis_triage import format_report
    from tool_envelope import make_toolresult
    dsets = [x.strip() for x in geo_datasets.replace(" ", ",").split(",") if x.strip()]
    try:
        rep = triage(_parse(signature_a), _parse(signature_b), dsets)
    except Exception as e:
        art = make_toolresult("triage_hypothesis", False, None, content_level="computational_analysis",
                              error_type="analysis_error", error_message=str(e)[:200],
                              parameters={"signature_a": str(signature_a), "signature_b": str(signature_b),
                                          "datasets": dsets})
        return f"分析失败：{e}", art
    from hypothesis_triage import resolve_signature
    valid = [x for x in rep.get("per_dataset", []) if "error" not in x]
    sig_dirs = {x["direction"] for x in valid if x.get("fdr", 1) < 0.05}
    # 检验族(test family)：本工具每个数据集做 1 次预定义的 signature 对比较
    n_tests = len(valid)
    has_fdr = any(x.get("fdr") is not None for x in valid)
    if n_tests <= 1:
        # 单数据集单比较 → 无多重性可校正；BH 对单个 p 是恒等(q=p)，不得冒称已校正
        multiplicity, adjustment_method, adjusted_q = "not_applicable", None, None
    elif has_fdr:
        multiplicity, adjustment_method = "adjusted", "benjamini-hochberg"
        adjusted_q = [x.get("fdr") for x in valid]
    else:
        multiplicity, adjustment_method, adjusted_q = "not_adjusted", None, None
    test_family = (f"{n_tests} 个数据集 × 1 个预定义 signature 对比较"
                   + ("（单一检验，无需多重校正）" if n_tests <= 1 else ""))
    try:
        ga, gb = resolve_signature(_parse(signature_a)), resolve_signature(_parse(signature_b))
        gene_counts = {"A": len(set(ga)), "B": len(set(gb))}
    except Exception:
        gene_counts = {"A": None, "B": None}
    data = dict(rep)
    data.update({"dataset_ids": rep.get("datasets", []),
                 "signature_a": str(signature_a), "signature_b": str(signature_b),
                 "signature_gene_counts": gene_counts,
                 "genes_hit": [x.get("genes_hit") for x in valid],
                 "sample_counts": [x.get("n") for x in valid],
                 "method": "signature correlation (multi-dataset)",
                 "statistic": f"{rep.get('n_significant_fdr')}/{rep.get('n_usable')} datasets FDR-significant",
                 "direction": (sig_dirs.pop() if len(sig_dirs) == 1 else "mixed"),
                 "multiplicity_status": multiplicity,
                 "adjustment_method": adjustment_method,
                 "test_family": test_family,
                 "test_count": n_tests,
                 "adjusted_q": adjusted_q})
    art = make_toolresult("triage_hypothesis", True, data, content_level="computational_analysis",
                          source="local GEO (offline)", source_ids=rep.get("datasets", []),
                          parameters={"signature_a": str(signature_a), "signature_b": str(signature_b)},
                          warnings=rep.get("caveats", []))
    return format_report(rep), art


def _query_data_lake_str(kind, query):
    corpus, q = "all", query
    m = re.match(r"^\s*(SSc|SLE|RA|CIN)\s*[:：]\s*(.+)$", query, re.IGNORECASE)
    if m:
        corpus, q = m.group(1), m.group(2)
    if kind == "corpus":
        from retrieval import hybrid_search
        return hybrid_search(q, corpus=corpus)
    if kind == "trend":
        return _dlq.corpus_trends(q, corpus=corpus)
    if kind == "targets":
        return _dlq.query_disease_targets(query)
    if kind == "gwas":
        return _dlq.query_gwas(query)
    if kind == "geneset":
        return _dlq.lookup_gene_set(query)
    if kind == "getset":
        return _dlq.get_gene_set(query)
    if kind == "scrna":
        return _dlq.list_ssc_scrna_datasets()
    if kind == "ppi":
        return _dlq.ppi_neighbors(query)
    if kind == "ppi_common":
        return _dlq.ppi_common(query)
    if kind == "tf_targets":
        return _dlq.tf_targets(query)
    if kind == "regulators":
        return _dlq.gene_regulators(query)
    return _dlq.data_lake_summary()


_EMPTY_MARKERS = ["未检索到", "未找到", "没有找到", "命中 0", "0 篇", "为空", "无结果", "找不到"]
_GSE = re.compile(r"^\s*GSE\d{3,7}\s*$", re.I)


def _exact_id(query):
    """查询是否为精确 ID（PMID/DOI/GSE）；是则返回规范化 ID，否则 None。"""
    from ids import valid_pmid, valid_doi
    q = (query or "").strip()
    if valid_pmid(q) or valid_doi(q) or _GSE.match(q):
        return q
    return None


def _retrieval_state(kind, query, result):
    """区分【精确检索】与【排序检索】的状态语义。
    精确ID：命中=exact_hit / 未命中=zero_hits（不把排序候选算作命中）。
    排序检索(corpus 混合检索)：只会给出【相关性未核验的候选】或无候选，
    不设未经评测校准的相关性阈值，也不自称 relevant_hits。"""
    text = result or ""
    empty = (not text.strip()) or any(mk in text for mk in _EMPTY_MARKERS)
    if kind == "corpus":
        ident = _exact_id(query)
        if ident:
            hit = (not empty) and (ident.lower() in text.lower())
            return "exact_id", ("exact_hit" if hit else "zero_hits"), (1 if hit else 0)
        n = None
        m = re.search(r"命中\s*(\d+)\s*篇", text)
        if m:
            n = int(m.group(1))
        elif empty:
            n = 0
        if n == 0 or (n is None and empty):
            return "ranked_relevance", "zero_candidates", 0
        return "ranked_relevance", "candidates_returned_relevance_unverified", n
    # 其余 kind 为确定性查表（非相关性排序）
    return "deterministic_lookup", ("zero_hits" if empty else "exact_hit"), (0 if empty else None)


@tool(response_format="content_and_artifact")
def query_data_lake(kind: str, query: str = "") -> tuple:
    """查询本地数据湖（离线、可复现）。返回可读结果 + 结构化 artifact(content_level=local_dataset)。
    kind：corpus/trend/targets/gwas/geneset/getset/scrna/ppi/ppi_common/tf_targets/regulators/summary。
    ⚠️ 三者严格区分：【数据湖不可用】(ok=False, data_unavailable)、【精确ID未命中】(zero_hits)、
    【排序检索返回相关性未核验的候选】(candidates_returned_relevance_unverified)。
    候选 ≠ 支持性证据；zero_candidates ≠ 该领域没有研究。"""
    from tool_envelope import make_toolresult
    kind = (kind or "").lower()
    lake = BASE / "data_lake"
    if not lake.exists():
        art = make_toolresult("query_data_lake", False, None, content_level="local_dataset",
                              error_type="data_unavailable",
                              error_message="本地数据湖不存在（≠查询无命中，请勿判为'未发现证据'）",
                              parameters={"kind": kind, "query": query},
                              warnings=["retrieval_status=unavailable"])
        return "【数据湖不可用】未构建 data_lake/，无法判断有无证据。", art
    try:
        result = _query_data_lake_str(kind, query)
    except Exception as e:
        art = make_toolresult("query_data_lake", False, None, content_level="local_dataset",
                              error_type="query_error", error_message=str(e)[:200],
                              parameters={"kind": kind, "query": query},
                              warnings=["retrieval_status=execution_error"])
        return f"查询失败：{e}", art
    snap = _dlq.corpus_stats() if hasattr(_dlq, "corpus_stats") else None
    mode, status, n_cand = _retrieval_state(kind, query, result)
    warnings = []
    if status == "zero_hits":
        warnings.append("零命中：仅表示本地库无该条精确记录，≠该领域没有研究")
    if status == "zero_candidates":
        warnings.append("排序检索无候选：≠该领域没有研究，也≠已证否")
    if status == "candidates_returned_relevance_unverified":
        warnings.append("这些是相关性【未经核验】的排序候选，不得直接当作支持性证据；"
                        "须经人工/全文核验后才可称为相关命中")
    if snap is None:
        warnings.append("数据湖版本/快照缺失，可复现性受限")
    data = {"namespace": kind, "query": query,
            "retrieval_mode": mode, "retrieval_status": status,
            "candidate_count": n_cand, "candidate_scores": "unknown",
            "relevance_verified": False,
            "data_lake_version": str(snap)[:200] if snap else "unknown",
            "source_file": "data_lake", "source_file_hash": None,
            "result_text": result[:4000],
            "n_records": (n_cand if mode != "ranked_relevance" else None),
            "search_parameters": {"kind": kind, "query": query}, "warnings": warnings,
            "content_level": "local_dataset"}
    art = make_toolresult("query_data_lake", True, data, content_level="local_dataset",
                          source="local data_lake", dataset_version=data["data_lake_version"],
                          parameters={"kind": kind, "query": query}, warnings=warnings)
    if status == "candidates_returned_relevance_unverified":
        result = "【排序候选·相关性未核验】以下为检索候选，未经相关性核验，不得直接作为支持性证据：\n" + result
    elif status == "zero_candidates":
        result = "【无候选】本地库未返回候选；这不等于该领域没有研究，也不构成否定证据。\n" + result
    return result, art


@tool
def lab_lookup(kind: str, query: str) -> str:
    """【湿实验知识库·秒查】确定性查询，不烧算力。kind 取值：
    'antibody'(自身抗体→疾病/亚型/临床意义/检测，如 ACPA/ACA/Scl-70/anti-dsDNA/ANCA) /
    'flow'(流式亚群门控→marker，如 Treg/Th17/Tfh/浆母细胞/循环纤维细胞) /
    'sample'(样本类型→实验路径骨架+坑，如 全血/皮肤活检/血清) /
    'protocol'(成熟湿实验SOP完整步骤，如 CENP-B/ACA单B细胞克隆、N-糖酶促改造、糖基转移酶CRISPR-KO、补体MAC脂质体、IgG3 O-糖)。
    query 传 'overview/总览' 可看项目线总览与关系。
    例：lab_lookup('antibody','SSc') / lab_lookup('flow','纤维细胞') / lab_lookup('protocol','CRISPR 糖基转移酶 KO')。"""
    import lab_knowledge as LK
    kind = (kind or "").lower()
    if kind in ("antibody", "autoantibody", "ab"):
        return LK.lookup_autoantibody(query)
    if kind == "flow":
        return LK.lookup_flow(query)
    if kind == "sample":
        return LK.sample_pathway(query)
    if kind in ("protocol", "sop"):
        import protocols as PROT
        if any(w in (query or "").lower() for w in ["overview", "总览", "map", "有哪些", "列表"]):
            return PROT.project_map_text() + "\n\n" + PROT.protocol_summary()
        return PROT.lookup_protocol(query)
    return LK.knowledge_summary()


@tool
def experiment_next_step(disease: str, sample: str = "", assay: str = "",
                         panel: str = "", hypothesis: str = "") -> str:
    """【实验副驾】告诉它你此刻在做什么，它给出结构化的下一步湿实验建议：
    样本路径+相关自身抗体+流式门控+关键对照与坑+对口文献(带来源)。
    disease: SSc/RA/SLE/SjS/IIM/AAV；sample: 全血/皮肤活检/血清；assay: 流式/自身抗体/scRNA/ELISA；
    panel: 逗号分隔的抗体或marker(如 'CD4,CD25,FoxP3')；hypothesis: 你想验证的假说一句话。"""
    from experiment_copilot import LabContext, suggest_next
    panel_list = [x.strip() for x in re.split(r"[,，]", panel) if x.strip()]
    ctx = LabContext(disease=disease, sample=sample, assay=assay, panel=panel_list, hypothesis=hypothesis)
    return suggest_next(ctx, with_literature=True, top_k=5)


@tool
def retrieve_resources(query: str) -> str:
    """【资源检索 / SSc-E1】根据研究问题，从资源环境里选出相关的工具、数据库、软件和 know-how
    （而不是把所有资源都塞进来）。规划任务时先调用它，看看有哪些对口资源可用。"""
    return _resource_retriever.bundle_text(query, top_k=12)


def _paper_struct(p, query):
    pt = (p.get("pub_type", "") or "")
    jr = (p.get("journal", "") or "")
    preprint = ("preprint" in (pt + jr).lower()
                or any(x in (p.get("link", "") or "").lower() for x in ["biorxiv", "medrxiv"]))
    abstract = (p.get("abstract") or "")
    has_abs = bool(abstract.strip())
    return {"pmid": p.get("pmid"), "pmcid": p.get("pmcid"), "doi": p.get("doi"),
            "title": p.get("title"), "authors": p.get("authors"), "journal": jr,
            "year": p.get("year"), "publication_type": pt,
            "abstract": abstract[:2000],
            "supporting_excerpt": abstract[:400] if has_abs else "",   # 真实摘要逐字片段，不LLM改写
            "source_database": "Europe PMC", "query": query,
            "content_level": "abstract" if has_abs else "metadata_only",  # 无摘要→降级
            "preprint": preprint, "retraction_status": "unknown", "link": p.get("link")}


@tool(response_format="content_and_artifact")
def search_evidence(query: str, n: int = 8) -> tuple:
    """【两阶段证据检索】检索文献并连摘要一起抓回（Europe PMC）。返回给 LLM 的可读摘要 + 供 Verifier/Shadow
    读取的结构化 artifact（每篇 PMID/DOI/title/journal/year/abstract/真实摘要片段/preprint/来源）。
    评估证据时用这个（而非 search_literature 只给标题）。摘要级证据 content_level=abstract。"""
    from tool_envelope import make_toolresult
    try:
        papers = [_paper_struct(p, query) for p in search_with_abstracts(query, n=n)]
    except Exception as e:
        art = make_toolresult("search_evidence", False, None, content_level="abstract",
                              error_type="search_error", error_message=str(e)[:200])
        return f"检索失败：{e}", art
    src_ids = [str(p["pmid"] or p["doi"]) for p in papers if (p["pmid"] or p["doi"])]
    art = make_toolresult("search_evidence", True, {"papers": papers, "query": query},
                          content_level="abstract", source="Europe PMC", source_ids=src_ids,
                          warnings=(["无结果"] if not papers else []))
    content = "\n".join(
        f"- [{p['year']}] {p['title'][:90]} | {p['journal']} | PMID:{p['pmid']} | 摘要:{p['abstract'][:200]}"
        for p in papers) or "未检索到相关文献。"
    return content, art


@tool
def verify_claim(claim: str, query: str) -> str:
    """【证据验证】针对某个论断，检索相关文献证据卡片，核对该论断是否真被支持、
    有没有过度解读（把小样本/动物实验/横断面当成强因果）。用于给结论做独立把关。"""
    papers = search_with_abstracts(query, n=8)
    cards = make_evidence_cards(papers, model="deepseek")
    return _verify_claim(claim, cards, model="claude")


SKILL_AGENT_TOOLS = [
    retrieve_resources,
    lab_lookup,
    experiment_next_step,
    triage_hypothesis,
    query_data_lake,
    list_skills,
    read_skill,
    search_literature,
    search_evidence,
    verify_claim,
    list_directory_pdfs,
    read_local_pdf,
    read_file,
    run_python,
]

SKILL_AGENT_SYSTEM = (
    "你是一位医学科研 Agent，采用【技能驱动】的工作方式（就像一位会查操作手册再动手的科研助理）。\n\n"
    "你的标准流程：\n"
    "0. 接到任务后，先调用 retrieve_resources 看看资源环境(SSc-E1)里有哪些对口的工具/数据库/软件/know-how。\n"
    "1. 再调用 list_skills 查看有哪些现成技能（Skill）。\n"
    "2. 选定最相关的技能，用 read_skill 读取它的完整操作手册（SKILL.md），严格按里面的工作流和规则执行。\n"
    "   —— 有的技能手册会指向它目录下的脚本，你可以用 read_file 查看、用 run_python 调用。\n"
    "3. 按需调用工具：\n"
    "   - 快速查文献用 search_literature；\n"
    "   - 需要评估证据强度/样本量/研究设计时，用 search_evidence（两阶段证据卡片，别只看标题）；\n"
    "   - 给结论把关、核对是否过度解读，用 verify_claim；\n"
    "   - 读本地 PDF 用 read_local_pdf / list_directory_pdfs；\n"
    "   - 数据分析和画图用 run_python 写并运行 Python 代码。\n"
    "4. 如果没有完全对口的技能，就用最接近的技能的方法论，结合你的判断完成，并说明你是怎么做的。\n\n"
    "铁律：\n"
    "- 涉及文献引用时，只引用 search_literature 真实检索到的文献，严禁编造文献 / DOI / 作者。\n"
    "- 涉及数据时，只用用户提供的真实数据，严禁编造数据。缺数据就停下来要。\n"
    "- 真实世界的数据采集、实验执行、创新性判断、结果核查、伦理与署名责任属于人类，"
    "你可以给建议和草稿，但不要替用户拍板这些结论。\n"
    "- 全程用中文与用户交流（写英文论文段落等除外）。"
)


def skill_system(extra: str = "") -> str:
    """组装完整系统提示 = 基础人设 + 【实时读取的长期记忆】 + 额外(如语言指令)。
    记忆每次调用现读，用户随时纠正随时生效；这是 agent 跨会话"学习"的落点。"""
    parts = [SKILL_AGENT_SYSTEM]
    try:
        from agent_memory import format_for_prompt
        mem = format_for_prompt()
        if mem:
            parts.append(mem)
    except Exception:
        pass
    if extra:
        parts.append(extra)
    return "\n\n".join(parts)


def _tools_by_name():
    return {t.name: t for t in SKILL_AGENT_TOOLS}


def build_skill_agent(model: str = "deepseek", allowed_tools=None):
    """model: 'deepseek'(省钱) 或 'claude'。
    allowed_tools=None 时给全部工具（向后兼容，供网页问答用）；
    传入工具名列表时【只】把这些工具交给 Executor —— 未授权/未知的工具物理上无法被调用。"""
    llm = judge_llm if model == "claude" else deepseek_llm_pro
    if allowed_tools is None:
        return create_react_agent(llm, SKILL_AGENT_TOOLS)
    from tool_registry import resolve
    resolve(allowed_tools)                       # 未知工具名 → 抛错，绝不静默忽略
    m = _tools_by_name()
    tools = [m[n] for n in allowed_tools if n in m]
    return create_react_agent(llm, tools)


# 便捷默认实例
skill_agent = build_skill_agent("deepseek")


if __name__ == "__main__":
    # 自检：只打印技能清单，不消耗 API
    print("发现的技能：\n")
    print(list_skills_text())
