"""
假说批量筛杀器（通用）：给两个基因 signature（或基因列表）+ 一批 GEO 数据集，
自动在每个数据集上算 signature 打分、做相关检验，汇总判定 假说【活/死/存疑】。
用途：把"CIN↔cGAS-STING""cGAS-STING↔纤维化"这类假说，几分钟在多队列里筛一遍，
     大部分秒杀（免费），活下来的才值得进湿实验。不写死任何具体假说。
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
# GEOparse 惰性导入（见 load_geo_expr）：让纯统计/判定函数在无 GEOparse 的 CI 里也能测。

BASE = Path(__file__).resolve().parent
GEO_DIR = BASE / "data_lake" / "geo_raw"
LAKE = BASE / "data_lake"
GEO_DIR.mkdir(parents=True, exist_ok=True)


# ---------- signature 库 ----------
def _load_cin70():
    p = BASE / "skills" / "ssc-cin" / "scripts" / "cin70_genes.txt"
    if p.exists():
        return [g.strip() for g in p.read_text(encoding="utf-8").splitlines()
                if g.strip() and not g.startswith("#")]
    return []


BUILTIN_SIGS = {
    "CIN": _load_cin70(),
    "cGAS_STING": ["CGAS", "MB21D1", "TMEM173", "STING1", "TBK1", "IKBKE", "IRF3", "IRF7",
                   "IFI16", "DDX58", "IFIH1", "MAVS", "TREX1", "DDX41", "NLRX1", "TRIM56"],
    "IFN_ISG": ["ISG15", "MX1", "MX2", "OAS1", "OAS2", "OAS3", "IFIT1", "IFIT3", "RSAD2",
                "IFI44", "IFI44L", "USP18", "HERC5", "IFI6", "OASL", "STAT1", "IRF7"],
    "TGF_fibrosis": ["COL1A1", "COL1A2", "COL3A1", "ACTA2", "TAGLN", "FN1", "CTGF", "CCN2",
                     "SERPINE1", "TIMP1", "POSTN", "THBS1", "COMP", "TGFB1"],
    "senescence": ["CDKN1A", "CDKN2A", "GLB1", "SERPINE1", "IL6", "CXCL8", "TP53", "LMNB1",
                   "IGFBP3", "MMP3"],
}


def resolve_signature(x):
    """x 可为基因列表，或内置名，或数据湖里的基因集名。返回基因列表。"""
    if isinstance(x, (list, tuple, set)):
        return list(x)
    if x in BUILTIN_SIGS:
        return BUILTIN_SIGS[x]
    for ns in ("cin_immune_sets", "gene_sets"):
        d = LAKE / ns
        if d.exists():
            for f in d.glob("*.json"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))["data"]
                    if x in data:
                        return data[x]
                except Exception:
                    pass
    raise ValueError(f"未知 signature：{x}（可用内置：{list(BUILTIN_SIGS)}，或数据湖基因集名，或直接传基因列表）")


# ---------- GEO 表达加载（缓存 + 探针映射） ----------
_EXPR_CACHE = {}


def load_geo_expr(gse_id):
    if gse_id in _EXPR_CACHE:
        return _EXPR_CACHE[gse_id]
    import GEOparse   # 惰性导入
    gse = GEOparse.get_GEO(geo=gse_id, destdir=str(GEO_DIR), silent=True)
    expr = gse.pivot_samples("VALUE")
    gpl = list(gse.gpls.values())[0].table
    # 找基因符号列
    cand = ["Symbol", "ILMN_Gene", "Gene Symbol", "GENE_SYMBOL", "gene_symbol",
            "GeneSymbol", "Gene symbol"]
    sym_col = next((c for c in gpl.columns if c in cand), None)
    if sym_col is None:
        sym_col = next((c for c in gpl.columns if "symbol" in c.lower()), None)
    if sym_col is None:
        raise ValueError(f"{gse_id} 平台表里找不到基因符号列，列有：{list(gpl.columns)[:8]}")
    id_col = gpl.columns[0]
    probe2sym = dict(zip(gpl[id_col].astype(str), gpl[sym_col].astype(str)))
    expr.index = [probe2sym.get(str(p), "") for p in expr.index]
    expr = expr[(expr.index != "") & (expr.index != "nan")]
    expr = expr.groupby(expr.index).max()
    _EXPR_CACHE[gse_id] = expr
    return expr


def signature_score(expr, genes):
    present = [g for g in set(genes) if g in expr.index]
    if len(present) < 3:
        return None, len(present)
    sub = expr.loc[present]
    z = sub.sub(sub.mean(axis=1), axis=0).div(sub.std(axis=1).replace(0, np.nan), axis=0)
    return z.mean(axis=0, skipna=True), len(present)


# ---------- 严谨性工具（纯函数，可测） ----------
# 判定不再用"证明/证伪/杀死"。四种结论：
VERDICTS = ("supportive_association", "no_detectable_support", "inconsistent", "technically_unresolved")
# 本自动筛查【无法】评估的混杂（必须显式声明，不能假装控制过）
CONFOUNDERS_NOT_ASSESSED = [
    "组织/疾病亚型混杂", "平台与预处理差异", "重复患者/重复样本", "细胞组成混杂",
    "治疗/病程/年龄/性别混杂", "数据集是否真正独立", "独立队列复制状态",
]
WET_LAB_NOTE = {
    "supportive_association": "存在一致的关联信号，值得优先安排【控制混杂的】验证实验。注意：这是关联，不是因果。",
    "no_detectable_support": "未检测到关联支持。⚠️ 不能仅凭此就建议放弃湿实验——本筛查未控制上述混杂、"
                             "无因果设计，仅为初步观察性信号排查。",
    "inconsistent": "跨数据集结果不一致，需更多独立队列 + 敏感性分析后再判断。",
    "technically_unresolved": "技术上无法判定（见 caveats），不构成对假说的任何肯定或否定结论。",
}


def signature_overlap(ga, gb):
    A, B = set(ga), set(gb)
    uni = A | B
    return {"shared": sorted(A & B), "n_shared": len(A & B),
            "jaccard": round(len(A & B) / len(uni), 3) if uni else 0.0}


def fisher_ci(r, n, conf=0.95):
    """相关系数的 Fisher-z 95% 置信区间。"""
    if r is None or n is None or n < 4 or abs(r) >= 1:
        return None
    z = np.arctanh(r)
    se = 1.0 / np.sqrt(n - 3)
    zc = stats.norm.ppf(1 - (1 - conf) / 2)
    return [round(float(np.tanh(z - zc * se)), 3), round(float(np.tanh(z + zc * se)), 3)]


def bh_fdr(pvals):
    """Benjamini-Hochberg FDR 校正。"""
    p = np.asarray(pvals, float)
    n = len(p)
    if n == 0:
        return []
    order = np.argsort(p)
    ranked = p[order] * n / (np.arange(n) + 1)
    adj = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(n)
    out[order] = np.clip(adj, 0, 1)
    return [round(float(x), 4) for x in out]


def assess(valid, overlap, min_n=10, min_hit_frac=0.5, min_replicate_frac=0.5):
    """据每数据集统计 + 严谨性检查，给四选一结论。纯函数，可用合成数据测试。"""
    caveats = []
    # 1) signature 基因重叠 → 自相关伪影
    if overlap["n_shared"] >= 3 or overlap["jaccard"] >= 0.2:
        return {"verdict": "technically_unresolved",
                "caveats": [f"两个 signature 有 {overlap['n_shared']} 个共享基因(自相关伪影风险)"] ,
                "per_dataset": valid}
    # 2) 可用性：样本量 + 基因命中率
    usable = [x for x in valid if x.get("n", 0) >= min_n
              and x.get("hit_frac_a", 0) >= min_hit_frac and x.get("hit_frac_b", 0) >= min_hit_frac]
    if not usable:
        return {"verdict": "technically_unresolved",
                "caveats": ["可用数据集不足：样本量太小或 signature 基因命中率过低"], "per_dataset": valid}
    # 3) 多重检验校正 + 效应量 CI 排除 0（+ 若有 null_p 需通过）
    fdr = bh_fdr([x["p"] for x in usable])
    for x, q in zip(usable, fdr):
        x["fdr"] = q
    sig = []
    for x in usable:
        ci = x.get("ci")
        ci_excl_0 = bool(ci and ci[0] * ci[1] > 0)
        null_ok = (x.get("null_p") is None) or (x["null_p"] < 0.05)
        if x["fdr"] < 0.05 and ci_excl_0 and null_ok:
            sig.append(x)
    dirs = set(x["direction"] for x in sig)
    # 4) leave-one-dataset-out：去掉任一数据集后仍多数显著
    lodo_robust = True
    if len(usable) >= 2:
        for drop in range(len(usable)):
            sub = [x for i, x in enumerate(usable) if i != drop]
            sub_fdr = bh_fdr([x["p"] for x in sub])
            sub_sig = sum(1 for q in sub_fdr if q < 0.05)
            if sub_sig / len(sub) < min_replicate_frac:
                lodo_robust = False
                break

    if not sig:
        verdict = "no_detectable_support"
    elif len(dirs) > 1:
        verdict, caveats = "inconsistent", caveats + ["跨数据集显著方向不一致"]
    elif len(sig) / len(usable) >= min_replicate_frac and lodo_robust:
        verdict = "supportive_association"
    else:
        verdict, caveats = "inconsistent", caveats + ["部分数据集显著、部分不显著，或不通过 leave-one-out"]
    return {"verdict": verdict, "caveats": caveats, "per_dataset": valid,
            "n_usable": len(usable), "n_significant_fdr": len(sig),
            "leave_one_out_robust": lodo_robust}


# ---------- 核心：批量观察性关联排查（非"筛杀"） ----------
def triage(sig_a, sig_b, datasets, n_null=200):
    """在多个 GEO 数据集上检验 signature A 与 B 的关联，做严谨性检查后给四选一结论。
    ⚠️ 这是观察性关联排查，不做因果判断，不用"证明/证伪/杀死"措辞。"""
    ga, gb = resolve_signature(sig_a), resolve_signature(sig_b)
    a_name = sig_a if isinstance(sig_a, str) else "sigA"
    b_name = sig_b if isinstance(sig_b, str) else "sigB"
    overlap = signature_overlap(ga, gb)
    ua, ub = set(ga), set(gb)
    rows = []
    for ds in datasets:
        try:
            expr = load_geo_expr(ds)
            sa, na = signature_score(expr, ga)
            sb, nb = signature_score(expr, gb)
            if sa is None or sb is None:
                rows.append({"dataset": ds, "error": f"signature 命中不足(A={na},B={nb})"})
                continue
            m = pd.DataFrame({"a": sa, "b": sb}).dropna()
            r, p = stats.pearsonr(m["a"], m["b"])
            rho, p2 = stats.spearmanr(m["a"], m["b"])
            null_p = _null_pvalue(expr, na, nb, abs(r), n_null) if n_null else None
            rows.append({"dataset": ds, "n": int(len(m)),
                         "pearson_r": round(float(r), 3), "p": float(p),
                         "spearman_rho": round(float(rho), 3), "p_spearman": float(p2),
                         "ci": fisher_ci(r, len(m)),
                         "hit_frac_a": round(na / max(len(ua), 1), 3),
                         "hit_frac_b": round(nb / max(len(ub), 1), 3),
                         "genes_hit": f"A={na}/{len(ua)},B={nb}/{len(ub)}",
                         "null_p": null_p,
                         "direction": "+" if r > 0 else "-"})
        except Exception as e:
            rows.append({"dataset": ds, "error": str(e)[:150]})

    valid = [x for x in rows if "error" not in x]
    result = assess(valid, overlap)
    return {"hypothesis": f"{a_name} ~ {b_name}（关联排查）", "datasets": datasets,
            "signature_overlap": overlap, "verdict": result["verdict"],
            "caveats": result.get("caveats", []),
            "confounders_not_assessed": CONFOUNDERS_NOT_ASSESSED,
            "wet_lab_note": WET_LAB_NOTE[result["verdict"]],
            "n_usable": result.get("n_usable"), "n_significant_fdr": result.get("n_significant_fdr"),
            "leave_one_out_robust": result.get("leave_one_out_robust"),
            "per_dataset": rows}


def _null_pvalue(expr, na, nb, observed_abs_r, n_perm=200):
    """随机 signature 零分布：同样大小的随机基因集，估计 |r| ≥ 观测值 的经验 p。"""
    genes = list(expr.index)
    if len(genes) < (na + nb) or na < 1 or nb < 1:
        return None
    rng = np.random.RandomState(0)   # 固定种子，可复现（不违反 Date/random 规则：普通脚本）
    hits = 0
    for _ in range(n_perm):
        ra = expr.loc[rng.choice(genes, na, replace=False)].mean(axis=0)
        rb = expr.loc[rng.choice(genes, nb, replace=False)].mean(axis=0)
        mm = pd.DataFrame({"a": ra, "b": rb}).dropna()
        if len(mm) < 4:
            continue
        rr, _ = stats.pearsonr(mm["a"], mm["b"])
        if abs(rr) >= observed_abs_r:
            hits += 1
    return round((hits + 1) / (n_perm + 1), 4)


def format_report(rep):
    v = rep["verdict"]
    lines = [f"# 关联排查：{rep['hypothesis']}", "",
             f"**结论：{v}**（{WET_LAB_NOTE[v]}）", ""]
    if rep.get("signature_overlap", {}).get("n_shared"):
        lines.append(f"⚠️ signature 共享基因：{rep['signature_overlap']['shared']}")
    if rep.get("caveats"):
        lines.append("提醒：" + "；".join(rep["caveats"]))
    lines += ["", "| 数据集 | n | Pearson r [95%CI] | Spearman ρ | p | FDR | null_p | 命中 |",
              "|---|---|---|---|---|---|---|---|"]
    for x in rep["per_dataset"]:
        if "error" in x:
            lines.append(f"| {x['dataset']} | - | {x['error']} | - | - | - | - | ❌ |")
        else:
            ci = x.get("ci")
            lines.append(f"| {x['dataset']} | {x['n']} | {x['pearson_r']} {ci} | {x.get('spearman_rho')} | "
                         f"{x['p']:.1e} | {x.get('fdr','-')} | {x.get('null_p','-')} | {x['genes_hit']} |")
    lines += ["", "未评估的混杂（务必人工把关）：" + "、".join(rep["confounders_not_assessed"])]
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    # demo：用已缓存的 GSE58095 筛两个假说
    print(format_report(triage("CIN", "cGAS_STING", ["GSE58095"])))
    print()
    print(format_report(triage("cGAS_STING", "IFN_ISG", ["GSE58095"])))
