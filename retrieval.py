"""
混合检索（步骤1-3）：查询分类 + 医学同义词扩展 + BM25 关键词召回 + RRF 融合。
—— 不用向量、不装 torch。补的是"关键词漏同义词"(如 scarring 搜不到 fibrosis)。
   精确查询(基因/DOI/GSE/rs号)走精确通道，不扩展、不混淆；机制类查询才扩展+混合。
   排序/去重/标准化都由确定性程序做，不交给 LLM。
"""

import re

import numpy as np
from rank_bm25 import BM25Okapi

import data_lake_query as dlq

# ---------- 医学同义词/缩写词典（SSc/风湿/机制，可继续扩） ----------
SYNONYMS = {
    "ssc": ["systemic sclerosis", "scleroderma"],
    "systemic sclerosis": ["scleroderma", "ssc"],
    "scleroderma": ["systemic sclerosis"],
    "sle": ["systemic lupus erythematosus", "lupus"],
    "lupus": ["systemic lupus erythematosus", "sle"],
    "ra": ["rheumatoid arthritis"],
    "sjogren": ["sjögren", "sicca syndrome"],
    "vasculitis": ["anca-associated vasculitis"],
    "fibrosis": ["scarring", "fibrotic", "extracellular matrix deposition",
                 "collagen deposition", "ecm deposition", "tissue remodeling"],
    "scarring": ["fibrosis", "fibrotic"],
    "fibroblast": ["myofibroblast", "fibroblast activation"],
    "vasculopathy": ["vascular injury", "endothelial dysfunction", "vascular damage",
                     "microvascular"],
    "cin": ["chromosomal instability", "aneuploidy", "genomic instability", "micronuclei"],
    "chromosomal instability": ["cin", "aneuploidy", "genomic instability", "micronuclei"],
    "cgas-sting": ["cgas", "sting", "sting1", "cytosolic dna sensing", "cytosolic dna"],
    "sting": ["cgas", "sting1", "cytosolic dna sensing"],
    "interferon": ["ifn", "type i interferon", "interferon signature", "isg"],
    "senescence": ["cellular senescence", "aging", "senescent"],
    "autoantibody": ["autoantibodies", "autoimmune"],
    "biomarker": ["biomarkers", "marker"],
    "single-cell": ["single cell", "scrna-seq", "scrnaseq"],
    # 中文 → 英文（你常用中文提问，但语料是英文，必须跨语言扩展）
    "系统性硬化症": ["systemic sclerosis", "scleroderma", "ssc"],
    "硬皮病": ["scleroderma", "systemic sclerosis"],
    "系统性红斑狼疮": ["systemic lupus erythematosus", "lupus", "sle"],
    "红斑狼疮": ["systemic lupus erythematosus", "lupus"],
    "狼疮": ["lupus", "systemic lupus erythematosus"],
    "类风湿": ["rheumatoid arthritis"],
    "干燥综合征": ["sjogren", "sjögren"],
    "血管炎": ["vasculitis"],
    "肌炎": ["myositis", "dermatomyositis"],
    "染色体不稳定": ["chromosomal instability", "cin", "aneuploidy", "genomic instability", "micronuclei"],
    "基因组不稳定": ["genomic instability", "chromosomal instability"],
    "微核": ["micronuclei", "micronucleus"],
    "纤维化": ["fibrosis", "scarring", "fibrotic", "collagen deposition", "extracellular matrix"],
    "成纤维细胞": ["fibroblast", "myofibroblast"],
    "肌成纤维细胞": ["myofibroblast"],
    "血管病变": ["vasculopathy", "vascular injury", "endothelial dysfunction"],
    "干扰素": ["interferon", "ifn", "type i interferon"],
    "炎症": ["inflammation", "inflammatory"],
    "衰老": ["senescence", "aging"],
    "自身抗体": ["autoantibody", "autoantibodies"],
    "生物标志物": ["biomarker", "biomarkers"],
    "单细胞": ["single-cell", "scrna-seq"],
    "免疫": ["immune", "immune dysregulation"],
    "机制": ["mechanism", "pathogenesis"],
}

_TOKEN = re.compile(r"[a-z0-9][a-z0-9\-]*")
_EXACT_ID = re.compile(r"\bGSE\d+\b|\brs\d+\b|10\.\d{4,}/\S+|\bPMID[:\s]*\d+", re.IGNORECASE)


def _tokenize(text):
    return _TOKEN.findall((text or "").lower())


# ---------- HGNC 基因别名反查（symbol -> 旧名/别名） ----------
_HGNC_REV = None
_HGNC_APPROVED = None


def _hgnc():
    global _HGNC_REV, _HGNC_APPROVED
    if _HGNC_REV is None:
        d = dlq._load("hgnc", "hgnc_alias_map")
        _HGNC_REV, _HGNC_APPROVED = {}, set()
        if d:
            _HGNC_APPROVED = set(d["data"].get("approved_symbols", []))
            for alias, sym in d["data"]["alias_to_symbol"].items():
                a = alias.strip().strip('"\'')
                if a:
                    _HGNC_REV.setdefault(sym.upper(), []).append(a)
    return _HGNC_REV, _HGNC_APPROVED


# 确定性基因识别：不依赖 data_lake/HGNC（干净克隆也能正确路由）。
# 内置常用基因种子（本项目相关）+ 含数字的大写符号模式；HGNC 若存在仅作增强。
COMMON_GENES = {
    "STING1", "TMEM173", "CGAS", "MB21D1", "TP53", "MYC", "IL6", "IL8", "CXCL8", "CXCL4",
    "PIGR", "TOP1", "CENPB", "IFNB1", "IFNA1", "STAT1", "STAT3", "IRF3", "IRF7", "TGFB1",
    "SMAD3", "ACTA2", "COL1A1", "SFRP2", "PDGFRB", "TLR7", "TLR8", "TLR9", "FOXP3", "CD19",
}
_GENE_NUM = re.compile(r"^[A-Z][A-Z0-9]*[0-9][A-Z0-9]*$")   # 含数字的大写符号，如 STING1/TP53/IL6


def _looks_like_gene(t):
    if t != t.upper():          # 必须原样即为大写
        return False
    tu = t.upper()
    if tu in COMMON_GENES or _GENE_NUM.match(tu):
        return True
    _, approved = _hgnc()       # HGNC 存在则增强，不存在也不影响上面两条
    return tu in approved


def classify_query(q):
    """精确(基因/ID) vs 混合(机制/概念)。确定性，不依赖本地数据。"""
    if _EXACT_ID.search(q):
        return "exact"
    toks = re.findall(r"[A-Za-z0-9]{2,8}", q)
    gene_like = [t for t in toks if _looks_like_gene(t)]
    if gene_like and len(toks) <= 3:      # 短查询且主要是基因符号 → 精确
        return "exact"
    return "hybrid"


def expand_query(q):
    """医学同义词 + HGNC 基因旧名扩展。返回扩展词列表。"""
    ql = q.lower()
    exp = set()
    for term, syns in SYNONYMS.items():
        if term in ql:
            exp.update(syns)
    rev, approved = _hgnc()
    for t in re.findall(r"[A-Za-z0-9]{2,8}", q):
        if t.upper() in approved:
            for a in rev.get(t.upper(), [])[:5]:
                exp.add(a.lower())
    return sorted(exp)


# ---------- BM25 索引（按库缓存） ----------
_BM25 = {}


def _get_bm25(name):
    if name not in _BM25:
        docs = dlq._load_corpus(name)
        tokenized = [_tokenize(d.get("title", "") + " " + d.get("abstract", "")) for d in docs]
        _BM25[name] = (BM25Okapi(tokenized) if tokenized else None, docs)
    return _BM25[name]


def _rrf(rank_lists, k=60):
    """Reciprocal Rank Fusion。"""
    scores = {}
    for rl in rank_lists:
        for rank, idx in enumerate(rl):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores, key=lambda i: -scores[i])


def retrieve_docs(query, corpus="all", mode="hybrid", top_k=15, pool=50):
    """返回排序后的文档列表(dict)，供 benchmark 打分。
    mode: 'keyword'(纯BM25原查询) / 'synonym'(BM25+同义词扩展) / 'hybrid'(再加向量+重排序)。"""
    names = dlq._corpora_to_search(corpus)
    route = classify_query(query)
    expansions = expand_query(query) if (route == "hybrid" and mode in ("synonym", "hybrid")) else []
    exp_query = query + " " + " ".join(expansions)
    vi = None
    if mode == "hybrid":
        try:
            import vector_index as vi
        except Exception:
            vi = None

    fused = []
    for name in names:
        bm25, docs = _get_bm25(name)
        if bm25 is None:
            continue
        rankings = [list(np.argsort(bm25.get_scores(_tokenize(query)))[::-1][:pool])]
        if expansions:
            rankings.append(list(np.argsort(bm25.get_scores(_tokenize(exp_query)))[::-1][:pool]))
        if mode == "hybrid" and vi is not None and route == "hybrid" and vi.has_index(name):
            dr = vi.dense_rank(exp_query, name, top_k=pool)
            if dr is not None:
                rankings.append(dr)
        for i in _rrf(rankings)[:pool]:
            fused.append((name, docs[i]))
    if mode == "hybrid" and vi is not None and route == "hybrid" and fused:
        try:
            fused = vi.rerank(exp_query, fused[: max(30, top_k * 2)], top_k=top_k)
        except Exception:
            pass
    return [d for _, d in fused[:top_k]]


def hybrid_search(query, corpus="all", top_k=15, pool=50, use_dense=True, use_rerank=True):
    """混合检索主流程：分类 → 扩展 → BM25 + 向量 → RRF 融合 → cross-encoder 重排序。
    精确查询(基因/DOI)走 BM25 精确通道，不扩展、不用向量。"""
    names = dlq._corpora_to_search(corpus)
    route = classify_query(query)
    expansions = expand_query(query) if route == "hybrid" else []
    exp_query = query + " " + " ".join(expansions)

    # 向量/重排序只对机制类查询用；精确查询保持精确
    dense_on = use_dense and route == "hybrid"
    vi = None
    if dense_on or use_rerank:
        try:
            import vector_index as vi
        except Exception:
            vi = None

    fused_hits = []
    for name in names:
        bm25, docs = _get_bm25(name)
        if bm25 is None:
            continue
        rankings = [list(np.argsort(bm25.get_scores(_tokenize(query)))[::-1][:pool])]
        if expansions:
            rankings.append(list(np.argsort(bm25.get_scores(_tokenize(exp_query)))[::-1][:pool]))
        if dense_on and vi is not None and vi.has_index(name):
            dr = vi.dense_rank(exp_query, name, top_k=pool)
            if dr is not None:
                rankings.append(dr)
        for i in _rrf(rankings)[:pool]:
            fused_hits.append((name, docs[i]))

    # cross-encoder 重排序（对融合后的候选池）
    reranked = False
    if use_rerank and route == "hybrid" and vi is not None and fused_hits:
        try:
            fused_hits = vi.rerank(exp_query, fused_hits[: max(30, top_k * 2)], top_k=top_k)
            reranked = True
        except Exception:
            pass
    hits = fused_hits[:top_k]

    channels = ["BM25"]
    if expansions:
        channels.append("同义词扩展")
    if dense_on and vi is not None:
        channels.append("向量")
    if reranked:
        channels.append("重排序")
    if route == "exact":
        route_note = "（精确通道，不扩展）"
    elif expansions:
        route_note = f"，扩展：{expansions[:8]}"
    else:
        route_note = "（语义通道，无同义词命中）"
    header = (f"【混合检索：{'+'.join(channels)}】路由={route}"
              + route_note
              + f"，命中 {len(hits)} 篇（范围 {'/'.join(names)}）：")
    out = [header]
    for name, r in hits:
        link = f"https://pubmed.ncbi.nlm.nih.gov/{r['pmid']}/" if r.get("pmid") else (f"https://doi.org/{r['doi']}" if r.get("doi") else "")
        out.append(f"- [{name}·{r.get('year')}] {r.get('title','')[:85]} | {r.get('journal','')} | {link}")
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("分类测试：")
    for q in ["STING1", "GSE58095 分析", "SSc scarring mechanism", "染色体不稳定 纤维化"]:
        print(f"  '{q}' -> {classify_query(q)} | 扩展 {expand_query(q)[:5]}")
