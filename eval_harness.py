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


def _load_gold(split):
    """qid -> set(相关 pmid)。仅取人工 labels.jsonl 里 relevant∈{1,'1',true}。"""
    gold = {}
    for r in _load_jsonl(EVAL / split / "labels.jsonl"):
        if r.get("relevant") in (1, "1", True):
            gold.setdefault(str(r["qid"]), set()).add(str(r.get("pmid")))
    return gold


def score(split, k=10, final=False):
    # test 冻结守卫
    if split == "test" and not final:
        return ("⛔ test 是冻结集，score test 需加 --final。开发调参请只用 dev。"
                "（这样设计是为防止无意中反复看 test 调参——见 DATA_GOVERNANCE.md）")
    from retrieval import retrieve_docs
    qs = {str(x["qid"]): x for x in _load_jsonl(EVAL / split / "queries.jsonl")}
    gold = _load_gold(split)
    if not gold:
        return f"{split}/labels.jsonl 没有已标注(relevant=1)的样本——请先完成人工标注。"
    agg = {m: {"recall": [], "mrr": [], "ndcg": []} for m in POOL_MODES}
    for qid, g in gold.items():
        item = qs.get(qid)
        if not item:
            continue
        for m in POOL_MODES:
            ret = [_pmid(d) for d in retrieve_docs(item["q"], corpus=item.get("corpus", "all"), mode=m, top_k=k)]
            r = recall_at_k(ret, g, k)
            if r is not None:
                agg[m]["recall"].append(r)
            agg[m]["mrr"].append(mrr(ret, g))
            agg[m]["ndcg"].append(ndcg_at_k(ret, g, k))
    def _mean(xs):
        return round(sum(xs) / len(xs), 3) if xs else None
    summary = {m: {f"recall@{k}": _mean(agg[m]["recall"]), "mrr": _mean(agg[m]["mrr"]),
                   f"ndcg@{k}": _mean(agg[m]["ndcg"])} for m in POOL_MODES}
    payload = {"split": split, "k": k, "n_labeled_queries": len(gold), "final": final,
               "corpus_snapshot": _corpus_snapshot(),
               "run_at": datetime.now().isoformat(timespec="seconds"), "summary": summary}
    (EVAL / "results").mkdir(exist_ok=True)
    tag = "test_FINAL" if (split == "test" and final) else split
    (EVAL / "results" / f"{tag}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if split == "test" and final:
        (EVAL / "results" / "TEST_WAS_TOUCHED.log").open("a", encoding="utf-8").write(
            f"{payload['run_at']} 跑了 test --final（{len(gold)}题）\n")
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
