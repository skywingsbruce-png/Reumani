"""评测引擎测试：指标数学正确性 + test 冻结守卫。确定性，不调检索/API。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import eval_harness as E


def test_recall_precision():
    ret = ["a", "b", "c", "d"]
    gold = {"a", "c", "x"}
    assert E.recall_at_k(ret, gold, 4) == 2 / 3        # 命中 a,c，共3个相关
    assert E.precision_at_k(ret, gold, 4) == 2 / 4
    assert E.recall_at_k(ret, set(), 4) is None         # 无金标准→None(不计入)


def test_mrr():
    assert E.mrr(["x", "a", "b"], {"a"}) == 0.5         # 首个相关在第2位
    assert E.mrr(["a"], {"a"}) == 1.0
    assert E.mrr(["x", "y"], {"a"}) == 0.0


def test_ndcg_ideal_is_one():
    # 相关项全排最前 → nDCG=1
    assert round(E.ndcg_at_k(["a", "b", "x"], {"a", "b"}, 3), 6) == 1.0
    # 相关项靠后 → <1
    assert E.ndcg_at_k(["x", "y", "a"], {"a"}, 3) < 1.0


def test_gold_only_counts_relevant_one():
    rows = [{"qid": "q1", "relevant": 1, "pmid": "p1"},
            {"qid": "q1", "relevant": 0, "pmid": "p2"},
            {"qid": "q1", "relevant": None, "pmid": "p3"}]
    # 模拟 _load_gold 的过滤逻辑
    gold = {}
    for r in rows:
        if r.get("relevant") in (1, "1", True):
            gold.setdefault(r["qid"], set()).add(r["pmid"])
    assert gold == {"q1": {"p1"}}


def test_ndcg_graded_rewards_higher_grade_on_top():
    gm = {"a": 3, "b": 1}
    top = E.ndcg_graded(["a", "b", "x"], gm, 3)
    bot = E.ndcg_graded(["x", "b", "a"], gm, 3)
    assert round(top, 3) == 1.0 and bot < top


def test_must_find_recall():
    assert E.must_find_recall(["a", "x"], {"a", "c"}, 10) == 0.5
    assert E.must_find_recall(["a"], set(), 10) is None


def test_graded_gold_sets():
    gmap = {"p1": 3, "p2": 1, "p3": 0, "p4": -1}      # must / acceptable / irrelevant / misleading
    assert E._rel_set(gmap) == {"p1", "p2"}           # grade>0 视为相关
    assert E._must_set(gmap) == {"p1"}                # grade>=3 为 must-find


def test_grade_legend_parses_labels():
    assert E.GRADE["must"] == 3 and E.GRADE["misleading"] == -1 and E.GRADE["acceptable"] == 1


def test_test_split_frozen_guard():
    # 未加 final，score test 必须拒绝（不读检索、直接返回提示）
    r = E.score("test", final=False)
    assert isinstance(r, str) and "冻结" in r


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                print(f"FAIL {name}: {e}")
