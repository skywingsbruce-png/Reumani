"""
查询本地数据湖（离线、快、可复现）。给 agent 用的查询接口。
数据由 data_lake_build.py 下载缓存到 data_lake/。
"""

import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
LAKE = BASE / "data_lake"


def _load(namespace, name):
    p = LAKE / namespace / f"{name}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def query_disease_targets(disease_keyword: str, top: int = 20) -> str:
    """查某个风湿病的 Open Targets 关联靶点（本地缓存）。"""
    d = _load("open_targets", "rheum_disease_targets")
    if not d:
        return "Open Targets 数据未缓存（运行 python data_lake_build.py opentargets）。"
    kw = disease_keyword.lower()
    out = []
    for name, v in d["data"].items():
        if kw in name.lower() and isinstance(v, dict) and "top_targets" in v:
            tg = v["top_targets"][:top]
            out.append(f"【{name}】(共 {v['total']} 个关联靶点，取 top {len(tg)})\n" +
                       "、".join(f"{t['symbol']}({t['score']})" for t in tg))
    return "\n\n".join(out) if out else f"未找到匹配 '{disease_keyword}' 的疾病。可选：{list(d['data'].keys())}"


def query_gwas(trait_keyword: str, top: int = 30) -> str:
    """查某个性状的 GWAS 关联（风湿/自免子集，本地缓存）。"""
    d = _load("gwas", "rheum_gwas_associations")
    if not d:
        return "GWAS 数据未缓存。"
    kw = trait_keyword.lower()
    rows = [r for r in d["data"] if kw in r["trait"].lower()]
    if not rows:
        return f"未找到匹配 '{trait_keyword}' 的 GWAS 关联。"
    genes = {}
    for r in rows:
        for g in (r.get("gene", "") or "").replace(" ", "").split(","):
            if g:
                genes[g] = genes.get(g, 0) + 1
    topg = sorted(genes.items(), key=lambda x: -x[1])[:top]
    return (f"'{trait_keyword}' 相关 GWAS 关联 {len(rows)} 条，高频关联基因(top {len(topg)})：\n" +
            "、".join(f"{g}({n})" for g, n in topg))


def lookup_gene_set(keyword: str, top: int = 15) -> str:
    """在本地缓存的基因集库里按关键词找通路/基因集（Hallmark/Reactome/GO/KEGG + CIN/免疫精选）。"""
    hits = []
    for ns, files in [("gene_sets", None), ("cin_immune_sets", None)]:
        d = LAKE / ns
        if not d.exists():
            continue
        for f in d.glob("*.json"):
            data = json.loads(f.read_text(encoding="utf-8"))["data"]
            for term in data:
                if keyword.lower() in term.lower():
                    hits.append((f.stem, term, len(data[term])))
    if not hits:
        return f"未找到含 '{keyword}' 的基因集。"
    hits = hits[:top]
    return f"含 '{keyword}' 的基因集(top {len(hits)})：\n" + "\n".join(
        f"- [{lib}] {term}（{n} 基因）" for lib, term, n in hits)


def get_gene_set(term_exact: str) -> str:
    """取某个基因集的完整基因列表（精确匹配 term）。"""
    for ns in ("cin_immune_sets", "gene_sets"):
        d = LAKE / ns
        if not d.exists():
            continue
        for f in d.glob("*.json"):
            data = json.loads(f.read_text(encoding="utf-8"))["data"]
            if term_exact in data:
                return f"{term_exact}（{len(data[term_exact])} 基因）：\n" + ", ".join(data[term_exact])
    return f"未找到基因集 '{term_exact}'。"


def list_ssc_scrna_datasets() -> str:
    """列出本地缓存的 SSc/风湿相关 CELLxGENE 单细胞数据集索引。"""
    d = _load("cellxgene", "ssc_rheum_dataset_index")
    if not d:
        return "CELLxGENE 索引未缓存。"
    rows = d["data"]
    return f"风湿/纤维化相关单细胞数据集 {len(rows)} 个：\n" + "\n".join(
        f"- {r['title'][:70]} | 细胞 {r.get('cell_count')} | 疾病 {r.get('diseases')} | id {r['id']}"
        for r in rows)


_CORPUS_CACHE = {}


def _corpus_files():
    """返回 {库名: jsonl路径}。含 SSc(旧路径) + corpus/ 下的 CIN/SLE/RA 等。"""
    files = {}
    ssc = LAKE / "ssc_corpus" / "corpus.jsonl"
    if ssc.exists():
        files["SSc"] = ssc
    cdir = LAKE / "corpus"
    if cdir.exists():
        for f in cdir.glob("*.jsonl"):
            files[f.stem] = f
    return files


def _load_corpus(name):
    if name not in _CORPUS_CACHE:
        f = _corpus_files().get(name)
        rows = []
        if f:
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
        _CORPUS_CACHE[name] = rows
    return _CORPUS_CACHE[name]


def _corpora_to_search(corpus):
    all_names = list(_corpus_files())
    if not corpus or corpus.lower() == "all":
        return all_names
    for n in all_names:
        if n.lower() == corpus.lower():
            return [n]
    return all_names


def corpus_search(keyword: str, corpus: str = "all", limit: int = 20) -> str:
    """在本地文献库按关键词检索题名/摘要。corpus 可指定 SSc/SLE/RA/CIN，或 all（默认跨库）。离线、快。"""
    names = _corpora_to_search(corpus)
    kws = [k.strip().lower() for k in keyword.split() if k.strip()]
    hits = []
    for name in names:
        for r in _load_corpus(name):
            blob = (r.get("title", "") + " " + r.get("abstract", "")).lower()
            if all(k in blob for k in kws):
                hits.append((name, r))
    hits.sort(key=lambda x: x[1].get("year", ""), reverse=True)
    out = [f"文献库命中 {len(hits)} 篇（关键词：{keyword}，范围：{'/'.join(names)}），最新 {min(limit,len(hits))} 篇："]
    for name, r in hits[:limit]:
        link = f"https://pubmed.ncbi.nlm.nih.gov/{r['pmid']}/" if r.get("pmid") else (f"https://doi.org/{r['doi']}" if r.get("doi") else "")
        out.append(f"- [{name}·{r.get('year')}] {r.get('title','')[:85]} | {r.get('journal','')} | {link}")
    return "\n".join(out)


def corpus_trends(keyword: str, corpus: str = "all") -> str:
    """某主题在文献库里的逐年发文量趋势（离线）。corpus 可指定病种或 all。"""
    names = _corpora_to_search(corpus)
    kws = [k.strip().lower() for k in keyword.split() if k.strip()]
    import collections
    c = collections.Counter()
    for name in names:
        for r in _load_corpus(name):
            blob = (r.get("title", "") + " " + r.get("abstract", "")).lower()
            if all(k in blob for k in kws):
                c[r.get("year", "?")] += 1
    if not c:
        return f"文献库（{'/'.join(names)}）里没有含 '{keyword}' 的文献。"
    lines = [f"'{keyword}' 逐年发文量（{'/'.join(names)}）："]
    for y in sorted(c):
        lines.append(f"  {y}: {c[y]} 篇  {'█'*min(c[y], 40)}")
    return "\n".join(lines)


def corpus_stats() -> str:
    """本地文献库总览。"""
    files = _corpus_files()
    if not files:
        return "尚无文献库。"
    lines = ["📚 本地文献库："]
    for name, f in files.items():
        n = sum(1 for _ in open(f, encoding="utf-8"))
        mb = f.stat().st_size / 1024 / 1024
        lines.append(f"- {name}: {n} 篇, {mb:.0f} MB")
    return "\n".join(lines)


_PPI = None


def _load_ppi():
    global _PPI
    if _PPI is None:
        d = _load("string_ppi", "human_ppi_hiconf")
        _PPI = d["data"] if d else {}
    return _PPI


def ppi_neighbors(gene: str, top: int = 20) -> str:
    """查某个基因在 STRING 高置信 PPI 网络里的相互作用伙伴（本地）。"""
    ppi = _load_ppi()
    g = gene.strip().upper()
    # 大小写容错
    key = g if g in ppi else next((k for k in ppi if k.upper() == g), None)
    if not key:
        return f"STRING 网络里没有 '{gene}'（可能符号不标准，可先 normalize）。"
    nb = sorted(ppi[key].items(), key=lambda x: -x[1])[:top]
    return f"{key} 的高置信相互作用伙伴(top {len(nb)})：\n" + "、".join(f"{g2}({s})" for g2, s in nb)


def ppi_common(genes, top: int = 20) -> str:
    """查一组基因共同的相互作用伙伴（找机制枢纽 hub）。genes 为列表或逗号分隔。"""
    ppi = _load_ppi()
    if isinstance(genes, str):
        genes = [x.strip() for x in genes.replace(",", " ").split() if x.strip()]
    import collections
    c = collections.Counter()
    for g in genes:
        key = next((k for k in ppi if k.upper() == g.upper()), None)
        if key:
            for nb in ppi[key]:
                c[nb] += 1
    common = [(g, n) for g, n in c.most_common(top) if n >= 2]
    if not common:
        return "没有找到 2 个及以上输入基因共享的相互作用伙伴。"
    return f"输入 {len(genes)} 个基因的共同互作伙伴(被≥2个共享)：\n" + "、".join(f"{g}({n})" for g, n in common)


def tf_targets(tf: str, top: int = 30) -> str:
    """查某个转录因子(TF)在 CollecTRI 里调控的靶基因（+激活/-抑制）。"""
    d = _load("collectri", "tf_target_network")
    if not d:
        return "CollecTRI 未缓存。"
    regs = d["data"]
    key = next((k for k in regs if k.upper() == tf.strip().upper()), None)
    if not key:
        return f"CollecTRI 里没有转录因子 '{tf}'。"
    tgs = list(regs[key].items())[:top]
    return f"{key} 调控的靶基因(top {len(tgs)})：\n" + "、".join(
        f"{g}({'+' if s > 0 else '-'})" for g, s in tgs)


def gene_regulators(gene: str) -> str:
    """查哪些转录因子调控某个基因（反查 CollecTRI）。"""
    d = _load("collectri", "tf_target_network")
    if not d:
        return "CollecTRI 未缓存。"
    regs = d["data"]
    g = gene.strip().upper()
    tfs = [tf for tf, tg in regs.items() if any(t.upper() == g for t in tg)]
    if not tfs:
        return f"CollecTRI 里没有调控 '{gene}' 的转录因子记录。"
    return f"调控 {gene} 的转录因子({len(tfs)} 个)：" + "、".join(tfs[:40])


def data_lake_summary() -> str:
    """数据湖概览：缓存了哪些数据、版本时间。"""
    lines = ["📦 本地数据湖内容："]
    for ns in sorted(LAKE.iterdir()) if LAKE.exists() else []:
        if ns.is_dir():
            for f in ns.glob("*.json"):
                try:
                    meta = json.loads(f.read_text(encoding="utf-8"))
                    m = meta.get("meta", {})
                    lines.append(f"- {ns.name}/{f.stem}（{meta.get('downloaded_at','')[:10]}）{m}")
                except Exception:
                    pass
    return "\n".join(lines) if len(lines) > 1 else "数据湖为空。"


if __name__ == "__main__":
    print(data_lake_summary())
    print("\n--- SSc 靶点 ---")
    print(query_disease_targets("systemic sclerosis", top=10))
    print("\n--- CIN 基因集 ---")
    print(lookup_gene_set("checkpoint"))
