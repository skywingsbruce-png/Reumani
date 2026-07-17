# Scientific-Eval（十.5）— 科学有效性评测规范

目的：确保 Reumani 的"发现"经得起真实科研标准，而不是自证。执行以流程纪律为主。

## 铁律
1. **用未参与开发的 GEO 数据**：验证队列不得在 dev 阶段被看过/调过参（与 `data_lake` 快照分离记录）。
2. **预注册分析**：在打开验证数据【之前】写死假设、signature、统计方法、阈值（存 `eval/preregistration/<date>.md`）。
3. **固定代码后再打开测试数据**：记录 code_commit；分析脚本冻结后才 touch 验证数据。
4. **与生物信息专家盲法比较**：同一问题，Agent 与专家各自独立产出，第三方盲评。
5. **独立队列复制**：关键结论需在 ≥1 个独立队列复现（不同平台/中心/亚型）。
6. **记录批次、混杂与多重检验**：批次效应、细胞组成、临床混杂、FDR 全部报告（对齐 `hypothesis_triage` 的严谨性检查）。
7. **区分探索性与验证性**：探索(hypothesis-generating) 与 验证(confirmatory) 明确标注，探索性结果不得当结论。

## 与系统的对接
- 每个"发现"必须落成分层证据卡 + Claim–Evidence Graph 裁决（关联/因果/临床分开），并带 provenance(code_commit/dataset_version)。
- 临床相关结论一律 `human_review_required=True`。

## 产出
一份可复现报告：预注册链接、code_commit、语料/数据快照、批次与混杂记录、探索性 vs 验证性标注、专家盲评结果。
