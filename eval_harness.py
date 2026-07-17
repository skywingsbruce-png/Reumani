"""
金标准评测引擎（阶段2）。严格遵守 DATA_GOVERNANCE.md：
- 相关性只取自【人工】 eval/<split>/labels.jsonl，不用程序化概念匹配。
- pooling：合并多个检索器的 top-k 作候选池给人标注，避免偏向某一系统。
- test 冻结：score test 需 --final，且会记录你动过 test（防止无意中反复看 test 调参）。
用法：
  python eval_harness.py pool  dev            # 生成待标注模板 dev/labels.jsonl.template
  python eval_harness.py score dev            # 用人工标签跑分(keyword/synonym/hybrid)
  python eval_harness.py score test --final   # 最终只跑一次
"""

import json
import math
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
EVAL = BASE / "eval"
POOL_MODES = ["keyword", "synonym", "hybrid"]


def _load_jsonl(path):
    rows = []
    if Path(path).exists():
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            rows.append(json.loads(line))
    return rows


def _pmid(doc):
    return str(doc.get("pmid") or doc.get("doi") or doc.get("title", "")[:60])


def _corpus_snapshot():
    """记录语料规模，供可复现（不同日期语料不同，分数不可跨期直接比较）。"""
    import data_lake_query as dlq
    snap = {}
    for name in ["SSc", "SLE", "RA", "CIN"]:
        try:
            snap[name] = len(dlq._load_corpus(name))
        except Exception:
            snap[name] = None
    return snap


# ---------- pooling：生成待人工标注的候选池 ----------
def build_pool(query, corpus, k=15):
    """合并 keyword/synonym/hybrid 的 top-k → 候选 PMID 池（去重，保留标题）。"""
    from retrieval import retrieve_docs
    seen, pool = set(), []
    for mode in POOL_MODES:
        for d in retrieve_docs(query, corpus=corpus, mode=mode, top_k=k):
            pid = _pmid(d)
            if pid not in seen:
                seen.add(pid)
                pool.append({"pmid": d.get("pmid"), "doi": d.get("doi"),
                             "title": d.get("title", ""), "year": d.get("year")})
    return pool


def make_labeling_sheet(split, k=15):
    """为某个 split 的每条查询建候选池，写出待人工标注模板(relevant=null)。"""
    qs = _load_jsonl(EVAL / split / "queries.jsonl")
    if not qs:
        return f"{split}/queries.jsonl 为空——请先写查询。"
    out = EVAL / split / "labels.jsonl.template"
    n = 0
    with out.open("w", encoding="utf-8") as f:
        f.write(f"# 人工标注：把每行 relevant 填 1(相关)/0(不相关)，另存为 labels.jsonl。语料快照 {_corpus_snapshot()}\n")
        for item in qs:
            pool = build_pool(item["q"], item.get("corpus", "all"), k=k)
            for c in pool:
                f.write(json.dumps({"qid": item["qid"], "q": item["q"],
                                    "pmid": c["pmid"], "title": c["title"][:110],
                                    "relevant": None}, ensure_ascii=False) + "\n")
                n += 1
    return f"{split}: {len(qs)} 查询 → {n} 个候选，写入 {out}（请人工标注 relevant 后另存 labels.jsonl）"


# ---------- 指标（二值相关，标签取自人工） ----------
def recall_at_k(ret, gold, k):
    if not gold:
        return None
    return len(set(ret[:k]) & gold) / len(gold)


def precision_at_k(ret, gold, k):
    return len(set(ret[:k]) & gold) / k if k else 0.0


def mrr(ret, gold):
    for i, p in enumerate(ret):
        if p in gold:
            return 1.0 / (i + 1)
    return 0.0


def ndcg_at_k(ret, gold, k):
    dcg = sum(1.0 / math.log2(i + 2) for i, p in enumerate(ret[:k]) if p in gold)
    ideal = min(len(gold), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal))
    return dcg / idcg if idcg > 0 else 0.0


# 分级相关性：两名专家标注 must(必找)/high(高度相关)/acceptable(可接受)/irrelevant/misleading
GRADE = {"must": 3, "high": 2, "acceptable": 1, "irrelevant": 0, "misleading": -1,
         "3": 3, "2": 2, "1": 1, "0": 0, "-1": -1, 3: 3, 2: 2, 1: 1, 0: 0, -1: -1, True: 1}


def ndcg_graded(ret, grade_map, k):
    """分级 nDCG@k：用相关等级(must=3/high=2/acceptable=1)算增益。"""
    def gain(g):
        return (2 ** max(g, 0) - 1)
    dcg = sum(gain(grade_map.get(p, 0)) / math.log2(i + 2) for i, p in enumerate(ret[:k]))
    ideal = sorted((max(g, 0) for g in grade_map.values()), reverse=True)[:k]
    idcg = sum(gain(g) / math.log2(i + 2) for i, g in enumerate(ideal))
    return round(dcg / idcg, 4) if idcg > 0 else 0.0


def must_find_recall(ret, must_set, k):
    if not must_set:
        return None
    return round(len(set(ret[:k]) & must_set) / len(must_set), 3)


def _load_gold(split):
    """qid -> {pmid: grade}。分级标注；grade>0 视为相关，grade>=3 为 must-find。"""
    gold = {}
    for r in _load_jsonl(EVAL / split / "labels.jsonl"):
        g = GRADE.get(r.get("relevant"), 0)
        gold.setdefault(str(r["qid"]), {})[str(r.get("pmid"))] = g
    return gold


def _rel_set(gmap):
    return {p for p, g in gmap.items() if g > 0}


def _must_set(gmap):
    return {p for p, g in gmap.items() if g >= 3}


_EXACT_CATEGORIES = {"exact_id", "gene_symbol"}


def score(split, k=10, final=False, latency_clock=None):
    """报告完整指标（不只 mean P@10）。latency_clock 可注入以便测试；默认用 time.perf_counter。"""
    if split == "test" and not final:
        return ("⛔ test 是冻结集，score test 需加 --final。开发调参请只用 dev。"
                "（这样设计是为防止无意中反复看 test 调参——见 DATA_GOVERNANCE.md）")
    import time
    from retrieval import retrieve_docs, classify_query
    clock = latency_clock or time.perf_counter
    qs = {str(x["qid"]): x for x in _load_jsonl(EVAL / split / "queries.jsonl")}
    gold = _load_gold(split)
    if not gold:
        return f"{split}/labels.jsonl 没有已标注样本——请先完成人工分级标注。"

    agg = {m: {"recall10": [], "recall50": [], "p10": [], "mrr": [], "ndcg": [],
               "must": [], "no_result": 0, "latency": []} for m in POOL_MODES}
    n_misroute, n_q = 0, 0
    for qid, gmap in gold.items():
        item = qs.get(qid)
        if not item:
            continue
        n_q += 1
        rel, must = _rel_set(gmap), _must_set(gmap)
        # 错误路由率（与检索模式无关，按查询类别判一次）
        cat = item.get("category", "")
        expected = "exact" if cat in _EXACT_CATEGORIES else "hybrid"
        if classify_query(item["q"]) != expected:
            n_misroute += 1
        for m in POOL_MODES:
            t0 = clock()
            docs = retrieve_docs(item["q"], corpus=item.get("corpus", "all"), mode=m, top_k=50)
            agg[m]["latency"].append(clock() - t0)
            ret = [_pmid(d) for d in docs]
            if not ret:
                agg[m]["no_result"] += 1
            r10 = recall_at_k(ret, rel, 10)
            r50 = recall_at_k(ret, rel, 50)
            if r10 is not None:
                agg[m]["recall10"].append(r10)
            if r50 is not None:
                agg[m]["recall50"].append(r50)
            agg[m]["p10"].append(precision_at_k(ret, rel, 10))
            agg[m]["mrr"].append(mrr(ret, rel))
            agg[m]["ndcg"].append(ndcg_graded(ret, gmap, 10))
            mf = must_find_recall(ret, must, 10)
            if mf is not None:
                agg[m]["must"].append(mf)

    def _mean(xs):
        return round(sum(xs) / len(xs), 4) if xs else None
    summary = {m: {
        "recall@10": _mean(a["recall10"]), "recall@50": _mean(a["recall50"]),
        "precision@10": _mean(a["p10"]), "mrr": _mean(a["mrr"]),
        "ndcg@10": _mean(a["ndcg"]), "must_find_recall@10": _mean(a["must"]),
        "no_result_rate": round(a["no_result"] / n_q, 3) if n_q else None,
        "mean_latency_s": _mean(a["latency"]),
    } for m, a in agg.items()}
    payload = {"split": split, "n_queries": n_q, "final": final,
               "misroute_rate": round(n_misroute / n_q, 3) if n_q else None,
               "cost": "本地检索，无 API 费用",
               "corpus_snapshot": _corpus_snapshot(),
               "run_at": datetime.now().isoformat(timespec="seconds"), "summary": summary}
    (EVAL / "results").mkdir(exist_ok=True)
    tag = "test_FINAL" if (split == "test" and final) else split
    (EVAL / "results" / f"{tag}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if split == "test" and final:
        (EVAL / "results" / "TEST_WAS_TOUCHED.log").open("a", encoding="utf-8").write(
            f"{payload['run_at']} 跑了 test --final（{n_q}题）\n")
    return payload


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    cmd = sys.argv[1] if len(sys.argv) > 1 else "score"
    split = sys.argv[2] if len(sys.argv) > 2 else "dev"
    final = "--final" in sys.argv
    if cmd == "pool":
        print(make_labeling_sheet(split))
    else:
        r = score(split, final=final)
        print(json.dumps(r, ensure_ascii=False, indent=2) if isinstance(r, dict) else r)
