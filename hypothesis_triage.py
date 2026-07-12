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

import GEOparse

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


# ---------- 核心：批量筛杀 ----------
def triage(sig_a, sig_b, datasets, alpha=0.05, min_replicate_frac=0.5):
    """在多个 GEO 数据集上检验 signature A 与 B 的相关性，判定假说活/死。"""
    ga, gb = resolve_signature(sig_a), resolve_signature(sig_b)
    a_name = sig_a if isinstance(sig_a, str) else "sigA"
    b_name = sig_b if isinstance(sig_b, str) else "sigB"
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
            rows.append({"dataset": ds, "n": len(m), "pearson_r": round(r, 3),
                         "p": p, "spearman_rho": round(rho, 3), "p_spearman": p2,
                         "significant": bool(p < alpha),
                         "direction": "+" if r > 0 else "-",
                         "genes_hit": f"A={na},B={nb}"})
        except Exception as e:
            rows.append({"dataset": ds, "error": str(e)[:150]})

    valid = [x for x in rows if "error" not in x]
    n_sig = sum(1 for x in valid if x["significant"])
    dirs = set(x["direction"] for x in valid if x["significant"])
    if not valid:
        verdict = "无法判定（全部数据集加载失败）"
    elif n_sig / len(valid) >= min_replicate_frac and len(dirs) == 1:
        verdict = f"🟢 假说存活：{n_sig}/{len(valid)} 个数据集显著且方向一致({dirs.pop()})——值得进一步验证"
    elif n_sig == 0:
        verdict = f"🔴 假说被杀：0/{len(valid)} 个数据集有显著相关——不建议投入湿实验"
    else:
        verdict = f"🟡 存疑：{n_sig}/{len(valid)} 显著（方向 {dirs or '不一致'}）——需更多数据集或换角度"

    report = {"hypothesis": f"{a_name} ↔ {b_name}", "datasets": datasets,
              "verdict": verdict, "per_dataset": rows}
    return report


def format_report(rep):
    lines = [f"# 假说筛杀：{rep['hypothesis']}", "", f"**判定：{rep['verdict']}**", "", "| 数据集 | n | Pearson r | p | 显著 |", "|---|---|---|---|---|"]
    for x in rep["per_dataset"]:
        if "error" in x:
            lines.append(f"| {x['dataset']} | - | - | {x['error']} | ❌ |")
        else:
            lines.append(f"| {x['dataset']} | {x['n']} | {x['pearson_r']} | {x['p']:.1e} | {'✅' if x['significant'] else '—'} |")
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
