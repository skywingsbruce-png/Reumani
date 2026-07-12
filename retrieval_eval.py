"""
检索基准（rigorous benchmark, 步骤对比）——量化混合检索比纯关键词强多少。
对比三档：keyword(纯BM25) / synonym(+同义词扩展) / hybrid(再加向量+重排序)。
相关性用"银标准"：文档需命中每个概念组各一词才算相关(must_all groups)。
指标：precision@10（前10命中相关的比例）+ hit@10（前10是否至少一篇相关）。
纯本地跑，不调外部 API。用法：python retrieval_eval.py
⚠️ v1 是概念覆盖型银标准；正式发表建议再补人工标注的 PMID 金标准集。
"""

import json
import re
from datetime import datetime
from pathlib import Path

import retrieval as R

BASE = Path(__file__).resolve().parent
OUT = BASE / "eval_results"
OUT.mkdir(exist_ok=True)

# 每题：query + 所在库 + 概念组(每组命中一词才算相关) —— 特意覆盖关键词会漏、需要扩展/语义的情形
QUERIES = [
    {"id": 1, "q": "SSc scarring", "corpus": "SSc",
     "groups": [["systemic sclerosis", "scleroderma", "ssc"], ["fibros", "collagen", "extracellular matrix", "ecm"]],
     "note": "scarring 摘要少见，需扩到 fibrosis"},
    {"id": 2, "q": "系统性硬化症 纤维化", "corpus": "SSc",
     "groups": [["systemic sclerosis", "scleroderma"], ["fibros", "collagen"]],
     "note": "中文查询，需跨语言扩展到英文"},
    {"id": 3, "q": "染色体不稳定 炎症", "corpus": "CIN",
     "groups": [["chromosomal instability", "aneuploidy", "genomic instability", "micronuclei"], ["inflamm", "immune", "sting", "interferon"]],
     "note": "中文机制查询"},
    {"id": 4, "q": "cytosolic DNA sensing inflammation", "corpus": "SSc",
     "groups": [["cgas", "sting", "cytosolic dna", "dna sens"], ["inflamm", "interferon", "immune"]],
     "note": "机制概念，语义"},
    {"id": 5, "q": "SSc vasculopathy endothelial", "corpus": "SSc",
     "groups": [["systemic sclerosis", "scleroderma"], ["endothel", "vascular", "vasculopath", "microvascul"]],
     "note": "血管病变"},
    {"id": 6, "q": "干扰素signature 硬皮病", "corpus": "SSc",
     "groups": [["scleroderma", "systemic sclerosis"], ["interferon", "ifn", "isg", "type i"]],
     "note": "中英混合"},
    {"id": 7, "q": "lupus interferon", "corpus": "SLE",
     "groups": [["lupus", "sle", "systemic lupus"], ["interferon", "ifn", "isg"]],
     "note": "SLE库基线"},
    {"id": 8, "q": "rheumatoid arthritis citrullination", "corpus": "RA",
     "groups": [["rheumatoid", "ra "], ["citrullin", "acpa", "ccp"]],
     "note": "RA自身抗原"},
    {"id": 9, "q": "myofibroblast activation TGF beta", "corpus": "SSc",
     "groups": [["myofibroblast", "fibroblast"], ["tgf", "transforming growth factor", "smad"]],
     "note": "成纤维激活"},
    {"id": 10, "q": "senescence fibrosis", "corpus": "SSc",
     "groups": [["senescen", "aging", "senescent"], ["fibros", "collagen"]],
     "note": "衰老与纤维化"},
]


def _text(d):
    return (d.get("title", "") + " " + d.get("abstract", "")).lower()


def _is_relevant(doc, groups):
    t = _text(doc)
    return all(any(term in t for term in grp) for grp in groups)


def score_query(item, mode, k=10):
    docs = R.retrieve_docs(item["q"], corpus=item["corpus"], mode=mode, top_k=k)
    rel = [_is_relevant(d, item["groups"]) for d in docs]
    p_at_k = sum(rel) / k if k else 0.0
    first = next((i for i, r in enumerate(rel) if r), None)
    return {"p@10": round(p_at_k, 3), "hit@10": 1 if any(rel) else 0,
            "first_rel_rank": (first + 1) if first is not None else None,
            "n_returned": len(docs)}


def run(k=10):
    modes = ["keyword", "synonym", "hybrid"]
    rows = []
    agg = {m: {"p": 0.0, "hit": 0} for m in modes}
    for item in QUERIES:
        row = {"id": item["id"], "q": item["q"], "corpus": item["corpus"]}
        for m in modes:
            s = score_query(item, m, k=k)
            row[m] = s
            agg[m]["p"] += s["p@10"]
            agg[m]["hit"] += s["hit@10"]
        rows.append(row)
        print(f"Q{item['id']} [{item['corpus']}] {item['q'][:28]:<28} "
              + " | ".join(f"{m}:P@10={row[m]['p@10']:.2f}" for m in modes), flush=True)
    n = len(QUERIES)
    summary = {m: {"mean_p@10": round(agg[m]["p"] / n, 3), "hit@10_rate": round(agg[m]["hit"] / n, 3)} for m in modes}
    payload = {"run_at": datetime.now().isoformat(timespec="seconds"), "k": k,
               "n_queries": n, "summary": summary, "rows": rows}
    (OUT / "retrieval_benchmark.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n===== 汇总（越高越好）=====")
    for m in modes:
        print(f"  {m:<8}  mean P@10={summary[m]['mean_p@10']:.3f}   hit@10率={summary[m]['hit@10_rate']:.3f}")
    print(f"\n写入 {OUT / 'retrieval_benchmark.json'}")
    return summary


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    run()
