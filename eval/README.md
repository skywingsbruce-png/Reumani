# eval/ — 金标准评测（阶段2）

衡量检索/系统效果的**唯一可信来源**。严格遵守 `../DATA_GOVERNANCE.md`。

## 为什么重建（见 ../AUDIT.md F1）
旧的 `retrieval_eval.py` 查询几乎全部由 `retrieval.SYNONYMS` 的词条构成——**用同义词自测自己**，
其数字不能作为泛化证据。本目录用【人工判定相关性 + 与词典无关的查询 + 冻结 test 集】重建。

## 三条铁律
1. **相关性人工判定**：金标准 = 人看候选文献判 相关/不相关（0/1），**不用**"标题含某词"这种程序化银标准。
2. **查询与词典解耦**：查询是自然语言研究问题，**不得**照着 `SYNONYMS` 的键来写。
3. **test 冻结**：`test/` 只在最终报告时跑**一次**。开发期只用 `dev/` 调参。看过 test 的多轮迭代 = test 已烧毁，须换新集。

## 目录
```
eval/
  dev/   queries.jsonl   labels.jsonl        ← 可反复用于调参
  test/  queries.jsonl   labels.jsonl        ← 冻结，最终只跑一次
  results/                                    ← 跑分结果（带语料快照）
```

## 标注流程（人工，一次性）
1. 写查询：`dev/queries.jsonl` / `test/queries.jsonl`，每行 `{"qid","q","corpus"}`。
   - test 查询请【独立编写】，不要复用 dev、不要参考 SYNONYMS。
2. 生成候选池（**pooling**，合并多个检索器的 top-k，避免偏向某一系统）：
   `python eval_harness.py pool dev`
   → 产出 `dev/labels.jsonl.template`（每行一个候选：qid/pmid/title/relevant=null）。
3. **人工标注**：把 template 里每个候选的 `relevant` 填 1(相关)/0(不相关)，另存为 `dev/labels.jsonl`。
4. 跑分：`python eval_harness.py score dev`（对比 keyword / synonym / hybrid，报 recall@k、MRR、nDCG@k）。
5. 最终（谨慎）：`python eval_harness.py score test --final`（有冻结守卫，会记录你动过 test）。

## 指标
recall@k、precision@k、MRR、nDCG@k（二值相关）。相关性只取自人工 `labels.jsonl`。

## 现状
dev 已有 8 条自然语言种子查询，**金标准标签待人工填写**。test 查询请你独立编写后再 pool+标注。
在标签填好前，任何"检索效果数字"都不成立。
