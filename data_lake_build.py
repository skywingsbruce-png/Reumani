"""
数据湖下载器：把风湿/免疫/CIN 相关、稳定适合缓存的生物医学数据下到本地 data_lake/。
每个源独立、可单独运行、带版本+时间戳，保证可复现。
用法：python data_lake_build.py <源名>   或   python data_lake_build.py all
源名：gene_sets / hgnc / gwas / opentargets / cellxgene / disgenet / cin_sets
"""

import gzip
import io
import json
import sys
from datetime import datetime
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
LAKE = BASE / "data_lake"
LAKE.mkdir(exist_ok=True)


def _save(namespace, name, data, meta=None):
    d = LAKE / namespace
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": name,
        "downloaded_at": datetime.now().isoformat(timespec="seconds"),
        "meta": meta or {},
        "data": data,
    }
    p = d / f"{name}.json"
    text = json.dumps(payload, ensure_ascii=False)
    p.write_text(text, encoding="utf-8")
    return p, len(text)


def _mb(nbytes):
    return f"{nbytes/1024/1024:.1f} MB"


# ---------- 1) 基因集（MSigDB Hallmark / Reactome / GO / KEGG，经 Enrichr，免登录） ----------
def download_gene_sets():
    import gseapy as gp
    libs = [
        "MSigDB_Hallmark_2020",       # 含免疫、DNA修复、G2M、E2F、干扰素、炎症等
        "Reactome_2022",
        "GO_Biological_Process_2021",
        "KEGG_2021_Human",
        "GO_Molecular_Function_2021",
    ]
    ok = []
    for lib in libs:
        try:
            gs = gp.get_library(name=lib, organism="Human")
            p, n = _save("gene_sets", lib, gs, {"n_terms": len(gs), "source": "Enrichr"})
            ok.append(f"  ✅ {lib}: {len(gs)} 个基因集, {_mb(n)}")
        except Exception as e:
            ok.append(f"  ❌ {lib}: {e}")
    return "gene_sets:\n" + "\n".join(ok)


# ---------- 2) CIN / 细胞周期 / DNA损伤 / 免疫 关键基因集（从 Hallmark 抽 + 自带 CIN70/CINSARC） ----------
def download_cin_sets():
    hall_p = LAKE / "gene_sets" / "MSigDB_Hallmark_2020.json"
    curated = {}
    if hall_p.exists():
        hall = json.loads(hall_p.read_text(encoding="utf-8"))["data"]
        def norm(s):
            return "".join(ch for ch in s.upper() if ch.isalnum())
        keys = ["G2MCHECKPOINT", "E2FTARGETS", "DNAREPAIR", "MITOTICSPINDLE",
                "INTERFERONALPHA", "INTERFERONGAMMA",
                "INFLAMMATORYRESPONSE", "IL6JAKSTAT3",
                "TGFBETA", "EPITHELIALMESENCHYMAL", "MYCTARGETS"]
        for term, genes in hall.items():
            nt = norm(term)
            if any(k in nt for k in keys):
                curated[term] = genes
    # 自带 CIN70（读 ssc-cin 技能里的列表）
    cin70_p = BASE / "skills" / "ssc-cin" / "scripts" / "cin70_genes.txt"
    if cin70_p.exists():
        genes = [g.strip() for g in cin70_p.read_text(encoding="utf-8").splitlines()
                 if g.strip() and not g.startswith("#")]
        curated["CIN70_starter"] = genes
    p, n = _save("cin_immune_sets", "cin_immune_curated", curated,
                 {"n_sets": len(curated), "note": "CIN/细胞周期/DNA损伤/免疫/纤维化 关键基因集"})
    return f"cin_sets: ✅ {len(curated)} 个精选基因集, {_mb(n)}"


# ---------- 3) HGNC 基因别名表（升级基因名标准化） ----------
def download_hgnc():
    url = "https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt"
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    lines = r.text.splitlines()
    header = lines[0].split("\t")
    idx = {c: i for i, c in enumerate(header)}
    alias_map = {}
    approved = set()
    for line in lines[1:]:
        cols = line.split("\t")
        if len(cols) <= max(idx.get("symbol", 0), idx.get("alias_symbol", 0), idx.get("prev_symbol", 0)):
            continue
        sym = cols[idx["symbol"]].strip()
        if not sym:
            continue
        approved.add(sym)
        for col in ("alias_symbol", "prev_symbol"):
            if col in idx and idx[col] < len(cols):
                for a in cols[idx[col]].split("|"):
                    a = a.strip()
                    if a and a != sym:
                        alias_map[a.upper()] = sym
    data = {"alias_to_symbol": alias_map, "approved_symbols": sorted(approved)}
    p, n = _save("hgnc", "hgnc_alias_map", data,
                 {"n_approved": len(approved), "n_alias": len(alias_map), "source": "HGNC"})
    return f"hgnc: ✅ {len(approved)} 个官方基因符号, {len(alias_map)} 个别名映射, {_mb(n)}"


# ---------- 4) GWAS Catalog（风湿病子集） ----------
def download_gwas():
    import zipfile
    url = ("https://ftp.ebi.ac.uk/pub/databases/gwas/releases/latest/"
           "gwas-catalog-associations_ontology-annotated-full.zip")
    r = requests.get(url, timeout=600)
    r.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    tsv_name = next(n for n in zf.namelist() if n.endswith(".tsv"))
    text = zf.read(tsv_name).decode("utf-8", errors="replace")
    lines = text.splitlines()
    header = lines[0].split("\t")
    idx = {c: i for i, c in enumerate(header)}
    trait_col = idx.get("DISEASE/TRAIT")
    kws = ["scleroder", "systemic sclerosis", "lupus", "rheumatoid",
           "sjogren", "sjögren", "vasculitis", "myositis", "dermatomyositis",
           "connective tissue", "autoimmune", "ankylosing", "psoriatic arthritis"]
    rows = []
    for line in lines[1:]:
        cols = line.split("\t")
        if trait_col is None or trait_col >= len(cols):
            continue
        trait = cols[trait_col].lower()
        if any(k in trait for k in kws):
            rows.append({
                "trait": cols[trait_col],
                "gene": cols[idx["MAPPED_GENE"]] if "MAPPED_GENE" in idx and idx["MAPPED_GENE"] < len(cols) else "",
                "snp": cols[idx["SNPS"]] if "SNPS" in idx and idx["SNPS"] < len(cols) else "",
                "pvalue": cols[idx["P-VALUE"]] if "P-VALUE" in idx and idx["P-VALUE"] < len(cols) else "",
                "study": cols[idx["STUDY"]] if "STUDY" in idx and idx["STUDY"] < len(cols) else "",
            })
    p, n = _save("gwas", "rheum_gwas_associations", rows,
                 {"n_associations": len(rows), "source": "GWAS Catalog", "filter": "风湿/自身免疫"})
    return f"gwas: ✅ 风湿/自身免疫相关 GWAS 关联 {len(rows)} 条, {_mb(n)}"


# ---------- 5) Open Targets（风湿病 → 关联靶点） ----------
def download_opentargets():
    url = "https://api.platform.opentargets.org/api/v4/graphql"
    disease_names = ["systemic sclerosis", "systemic lupus erythematosus",
                     "rheumatoid arthritis", "Sjogren syndrome",
                     "dermatomyositis", "systemic vasculitis"]
    search_q = """query($q:String!){ search(queryString:$q, entityNames:["disease"]){ hits{ id name } } }"""
    assoc_q = """query($id:String!){ disease(efoId:$id){ name
      associatedTargets(page:{index:0,size:100}){ count rows{ score
        target{ approvedSymbol approvedName } } } } }"""
    out = {}
    for name in disease_names:
        try:
            s = requests.post(url, json={"query": search_q, "variables": {"q": name}}, timeout=60)
            hits = s.json()["data"]["search"]["hits"]
            # 取名字完全匹配的第一个，否则取第一个
            did = next((h["id"] for h in hits if h["name"].lower() == name.lower()), hits[0]["id"])
            r = requests.post(url, json={"query": assoc_q, "variables": {"id": did}}, timeout=60)
            at = r.json()["data"]["disease"]["associatedTargets"]
            out[name] = {"disease_id": did, "total": at["count"],
                         "top_targets": [{"symbol": x["target"]["approvedSymbol"],
                                          "name": x["target"]["approvedName"],
                                          "score": round(x["score"], 4)} for x in at["rows"]]}
        except Exception as e:
            out[name] = {"error": str(e)}
    total = sum(len(v.get("top_targets", [])) for v in out.values() if isinstance(v, dict))
    p, n = _save("open_targets", "rheum_disease_targets", out,
                 {"n_targets": total, "source": "Open Targets", "diseases": disease_names})
    return f"opentargets: ✅ {len(disease_names)} 个风湿病，缓存 top 关联靶点共 {total} 条, {_mb(n)}"


# ---------- 6) CELLxGENE 数据集索引（SSc 相关，只存元数据不下全量） ----------
def download_cellxgene():
    url = "https://api.cellxgene.cziscience.com/curation/v1/datasets"
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    datasets = r.json()
    kws = ["scleroder", "systemic sclerosis", "lupus", "rheumatoid", "fibrosis", "autoimmune"]
    hits = []
    for d in datasets:
        blob = json.dumps(d, ensure_ascii=False).lower()
        if any(k in blob for k in kws):
            hits.append({
                "title": d.get("title", ""),
                "id": d.get("dataset_id", ""),
                "cell_count": d.get("cell_count"),
                "collection_id": d.get("collection_id", ""),
                "diseases": [x.get("label") for x in d.get("disease", [])],
                "tissues": [x.get("label") for x in d.get("tissue", [])],
            })
    p, n = _save("cellxgene", "ssc_rheum_dataset_index", hits,
                 {"n_datasets": len(hits), "source": "CELLxGENE Discover", "note": "仅元数据索引"})
    return f"cellxgene: ✅ 风湿/纤维化相关单细胞数据集索引 {len(hits)} 个, {_mb(n)}"


# ---------- 7) DisGeNET（可能需授权，best-effort） ----------
def download_disgenet():
    url = "https://www.disgenet.org/static/disgenet_ap1/files/downloads/curated_gene_disease_associations.tsv.gz"
    try:
        r = requests.get(url, timeout=180)
        r.raise_for_status()
        text = gzip.decompress(r.content).decode("utf-8", errors="replace")
        lines = text.splitlines()
        header = lines[0].split("\t")
        idx = {c: i for i, c in enumerate(header)}
        dcol = idx.get("diseaseName")
        kws = ["scleroder", "lupus", "rheumatoid", "sjogren", "autoimmune", "myositis"]
        rows = []
        for line in lines[1:]:
            cols = line.split("\t")
            if dcol is None or dcol >= len(cols):
                continue
            if any(k in cols[dcol].lower() for k in kws):
                rows.append({"gene": cols[idx.get("geneSymbol", 1)], "disease": cols[dcol],
                             "score": cols[idx.get("score", -1)] if "score" in idx else ""})
        p, n = _save("disgenet", "rheum_gene_disease", rows,
                     {"n": len(rows), "source": "DisGeNET"})
        return f"disgenet: ✅ 风湿病基因-疾病关联 {len(rows)} 条, {_mb(n)}"
    except Exception as e:
        return f"disgenet: ❌ 下载失败（可能已改为需注册/API key）：{e}"


# ---------- 8) STRING 人类蛋白互作网络（高置信子集，做机制网络分析） ----------
def download_string():
    import gzip as _gz
    info_url = "https://stringdb-downloads.org/download/protein.info.v12.0/9606.protein.info.v12.0.txt.gz"
    links_url = "https://stringdb-downloads.org/download/protein.links.v12.0/9606.protein.links.v12.0.txt.gz"

    # 1) ENSP -> 基因symbol 映射
    r = requests.get(info_url, timeout=180)
    r.raise_for_status()
    id2sym = {}
    txt = _gz.decompress(r.content).decode("utf-8", errors="replace")
    for line in txt.splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) >= 2:
            id2sym[cols[0]] = cols[1]

    # 2) 流式读 links，只留高置信(>=700)，转成 symbol 邻接表
    adj = {}
    kept = 0
    with requests.get(links_url, stream=True, timeout=600) as resp:
        resp.raise_for_status()
        gz = _gz.GzipFile(fileobj=resp.raw)
        gz.readline()  # header
        for raw in gz:
            a, b, score = raw.decode("utf-8", "replace").split()
            if int(score) < 700:
                continue
            sa, sb = id2sym.get(a), id2sym.get(b)
            if not sa or not sb:
                continue
            adj.setdefault(sa, {})[sb] = int(score)
            kept += 1
    # 每个基因只留 top 30 邻居，控制体积
    trimmed = {g: dict(sorted(nb.items(), key=lambda x: -x[1])[:30]) for g, nb in adj.items()}
    p, n = _save("string_ppi", "human_ppi_hiconf", trimmed,
                 {"n_genes": len(trimmed), "n_edges_kept": kept, "cutoff": 700,
                  "top_neighbors": 30, "source": "STRING v12.0"})
    return f"string: ✅ 人类高置信 PPI 网络 {len(trimmed)} 个基因、{kept} 条边(截前每基因top30), {_mb(n)}"


# ---------- 9) CollecTRI 转录因子-靶基因调控网络（做 TF 活性推断） ----------
def download_collectri():
    url = "https://omnipathdb.org/interactions"
    params = {"datasets": "collectri", "format": "tsv",
              "fields": "sources", "organisms": "9606", "genesymbols": "yes"}
    r = requests.get(url, params=params, timeout=180)
    r.raise_for_status()
    lines = r.text.splitlines()
    if not lines or "\t" not in lines[0]:
        raise RuntimeError("OmniPath 返回异常")
    header = lines[0].split("\t")
    idx = {c: i for i, c in enumerate(header)}
    # 用基因名列（genesymbol）
    sc = idx.get("source_genesymbol", idx.get("source"))
    tc = idx.get("target_genesymbol", idx.get("target"))
    stim = idx.get("is_stimulation")
    regs = {}
    for line in lines[1:]:
        cols = line.split("\t")
        if sc is None or tc is None or max(sc, tc) >= len(cols):
            continue
        tf, tg = cols[sc], cols[tc]
        sign = 1
        if stim is not None and stim < len(cols):
            sign = 1 if cols[stim] in ("1", "True", "true") else -1
        regs.setdefault(tf, {})[tg] = sign
    p, n = _save("collectri", "tf_target_network", regs,
                 {"n_tf": len(regs), "n_edges": sum(len(v) for v in regs.values()),
                  "source": "CollecTRI/OmniPath"})
    return f"collectri: ✅ TF-靶基因调控网络 {len(regs)} 个转录因子, {_mb(n)}"


SOURCES = {
    "gene_sets": download_gene_sets,
    "string": download_string,
    "collectri": download_collectri,
    "cin_sets": download_cin_sets,
    "hgnc": download_hgnc,
    "gwas": download_gwas,
    "opentargets": download_opentargets,
    "cellxgene": download_cellxgene,
    "disgenet": download_disgenet,
}


def main(which):
    if which == "all":
        order = ["gene_sets", "cin_sets", "hgnc", "gwas", "opentargets", "cellxgene", "disgenet"]
    else:
        order = [which]
    for s in order:
        fn = SOURCES.get(s)
        if not fn:
            print(f"未知源：{s}")
            continue
        try:
            print(fn())
        except Exception as e:
            print(f"{s}: ❌ {e}")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main(which)
