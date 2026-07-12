"""
湿实验知识层（确定性、秒级反应）——"像数据库一样马上反应"的核心。
包含三张策展表：
  1) 风湿病自身抗体 → 疾病/亚型/临床意义/检测方法（ANA/ACPA/ACA/Scl-70/RNAPolIII/ANCA…）
  2) 流式 marker/门控 → 相关细胞亚群（Th17/Treg/Tfh/浆母细胞/单核亚群/纤维细胞…）
  3) 常见样本→实验路径骨架（全血→PBMC→流式/自身抗体，皮肤→scRNA…）
这些是指南级领域知识（非编造文献），供 experiment_copilot 组合成"下一步建议"。
可继续扩；有来源不确定的条目请标 note 待人工核对。
"""

# ============ 1) 自身抗体知识库 ============
# disease: 主关联疾病；subtype: 亚型；clinical: 临床意义/风险；assay: 常用检测
AUTOANTIBODIES = {
    # ---- 系统性硬化症 SSc ----
    "ACA": {"aka": ["anti-centromere", "抗着丝点", "CENP-B"], "disease": "SSc",
            "subtype": "局限型 lcSSc (CREST)", "clinical": "肺动脉高压(PAH)风险高，ILD较少，预后相对好",
            "assay": "间接免疫荧光(HEp-2 着丝点型) / 抗CENP-B ELISA/线性免疫印迹"},
    "Scl-70": {"aka": ["anti-topoisomerase I", "抗拓扑异构酶I", "ATA"], "disease": "SSc",
               "subtype": "弥漫型 dcSSc", "clinical": "间质性肺病(ILD)风险高，皮肤进展快",
               "assay": "ELISA / 免疫印迹 / IIF 核仁+核质型"},
    "anti-RNAPolIII": {"aka": ["anti-RNA polymerase III", "抗RNA聚合酶III", "RP11/RP155"], "disease": "SSc",
                       "subtype": "弥漫型 dcSSc", "clinical": "硬皮病肾危象(SRC)最相关；近期发病肿瘤关联",
                       "assay": "ELISA / 线性免疫印迹"},
    "anti-U3RNP": {"aka": ["fibrillarin", "抗纤维蛋白原"], "disease": "SSc",
                   "subtype": "弥漫型", "clinical": "PAH、心肌受累、肌炎重叠；非裔多见",
                   "assay": "IIF 团块核仁型 / 免疫沉淀"},
    "anti-Th/To": {"aka": ["Th/To"], "disease": "SSc", "subtype": "局限型",
                   "clinical": "ILD、PAH；小血管病", "assay": "免疫沉淀（难普及）"},
    "anti-PM/Scl": {"aka": ["PM-Scl75/100"], "disease": "SSc-肌炎重叠",
                    "subtype": "overlap", "clinical": "肌炎+SSc重叠、ILD、机械手",
                    "assay": "免疫印迹 IIF 核仁均质型"},
    # ---- 类风湿关节炎 RA ----
    "ACPA": {"aka": ["anti-CCP", "抗环瓜氨酸肽", "anti-citrullinated"], "disease": "RA",
             "subtype": "血清阳性RA", "clinical": "高特异，预示侵蚀性/进展；可早于发病数年出现",
             "assay": "anti-CCP2/CCP3 ELISA / CIA"},
    "RF": {"aka": ["rheumatoid factor", "类风湿因子"], "disease": "RA",
           "subtype": "血清阳性RA", "clinical": "敏感但不特异（也见于SjS/感染/健康老人）",
           "assay": "比浊/ELISA（IgM-RF常用）"},
    # ---- 系统性红斑狼疮 SLE ----
    "ANA": {"aka": ["antinuclear antibody", "抗核抗体"], "disease": "SLE/多种结缔组织病",
            "subtype": "筛查", "clinical": "高敏感、低特异，阴性基本排除SLE；需看核型+滴度",
            "assay": "HEp-2 间接免疫荧光（金标准筛查）"},
    "anti-dsDNA": {"aka": ["抗双链DNA"], "disease": "SLE", "subtype": "活动性/肾炎",
                   "clinical": "高特异；与狼疮肾炎和疾病活动度相关，可随病情波动",
                   "assay": "Crithidia IIF / Farr / ELISA"},
    "anti-Sm": {"aka": ["Smith"], "disease": "SLE", "subtype": "特异标志",
                "clinical": "SLE高特异（不随活动波动）", "assay": "免疫印迹/ELISA"},
    "anti-Ro/SSA": {"aka": ["Ro52", "Ro60", "SSA"], "disease": "SjS/SLE",
                    "subtype": "干燥综合征/亚急性皮肤狼疮/新生儿狼疮", "clinical": "母体阳性→胎儿先心传导阻滞风险",
                    "assay": "ELISA/免疫印迹"},
    "anti-La/SSB": {"aka": ["SSB"], "disease": "SjS", "subtype": "干燥综合征",
                    "clinical": "常与Ro共存", "assay": "ELISA/免疫印迹"},
    "aPL": {"aka": ["antiphospholipid", "抗磷脂", "aCL", "anti-β2GPI", "LA", "狼疮抗凝物"],
            "disease": "抗磷脂综合征APS/SLE", "subtype": "血栓/流产",
            "clinical": "动静脉血栓、病态妊娠；需12周复查确认", "assay": "aCL/抗β2GPI ELISA + 狼疮抗凝物功能试验"},
    # ---- 特发性炎性肌病 IIM ----
    "anti-Jo1": {"aka": ["ARS", "抗合成酶"], "disease": "肌炎IIM", "subtype": "抗合成酶综合征",
                 "clinical": "ILD、机械手、关节炎、发热", "assay": "免疫印迹/ELISA"},
    "anti-MDA5": {"aka": ["CADM-140"], "disease": "皮肌炎DM", "subtype": "临床无肌病性DM",
                  "clinical": "快速进展性ILD（RP-ILD）高危，皮肤溃疡", "assay": "免疫印迹/ELISA"},
    "anti-TIF1γ": {"aka": ["TIF1gamma", "p155/140"], "disease": "皮肌炎DM", "subtype": "成人",
                   "clinical": "肿瘤相关肌炎强关联", "assay": "免疫印迹"},
    # ---- ANCA相关血管炎 AAV ----
    "MPO-ANCA": {"aka": ["p-ANCA", "髓过氧化物酶"], "disease": "AAV", "subtype": "MPA/EGPA",
                 "clinical": "显微镜下多血管炎、肾脏受累", "assay": "IIF p型 + MPO ELISA"},
    "PR3-ANCA": {"aka": ["c-ANCA", "蛋白酶3"], "disease": "AAV", "subtype": "GPA(肉芽肿性多血管炎)",
                 "clinical": "上下呼吸道+肾；复发率较高", "assay": "IIF c型 + PR3 ELISA"},
}

# ============ 2) 流式 marker / 门控知识库 ============
# markers: 定义该亚群的关键表面/胞内标志；relevance: 在风湿病里的意义
FLOW_SUBSETS = {
    "Th17": {"markers": ["CD3+", "CD4+", "CCR6+", "CD161+", "IL-17A+(胞内)"],
             "relevance": "RA/SSc/银屑病关节炎促炎；IL-17轴"},
    "Treg": {"markers": ["CD3+", "CD4+", "CD25hi", "CD127lo", "FoxP3+(胞内)"],
             "relevance": "免疫耐受；多种自身免疫病功能/数量异常"},
    "Tfh": {"markers": ["CD4+", "CXCR5+", "PD-1+", "ICOS+"],
            "relevance": "辅助B细胞产生自身抗体；SLE/SSc循环Tfh升高"},
    "浆母细胞 Plasmablast": {"markers": ["CD19+/lo", "CD27hi", "CD38hi", "CD20-"],
                          "relevance": "SLE活动度、自身抗体产生"},
    "年龄相关B细胞 ABC": {"markers": ["CD19+", "CD11c+", "T-bet+", "CD21-"],
                     "relevance": "SLE/SSc自身反应性B细胞扩增"},
    "单核细胞亚群": {"markers": ["CD14/CD16: 经典CD14++CD16-, 中间CD14++CD16+, 非经典CD14+CD16++"],
                "relevance": "SSc/RA中间型单核升高，促纤维化/促炎"},
    "循环纤维细胞 Fibrocyte": {"markers": ["CD45+", "CD34+", "Collagen-I+(胞内)", "CXCR4+"],
                          "relevance": "SSc纤维化——外周血纤维前体细胞"},
    "内皮祖细胞 EPC": {"markers": ["CD34+", "CD133+", "VEGFR2/KDR+"],
                   "relevance": "SSc血管病变——修复能力受损"},
    "NK": {"markers": ["CD3-", "CD56+", "CD16+/-"], "relevance": "固有免疫；部分风湿病数量/功能改变"},
}

# ============ 3) 样本 → 实验路径骨架 ============
SAMPLE_PATHWAYS = {
    "全血/外周血": {
        "isolate": "Ficoll密度梯度分离PBMC（或全血直接染色/裂红）",
        "downstream": ["流式分型(T/B/单核/NK亚群)", "血清分离→自身抗体谱/细胞因子", "PBMC→scRNA-seq/刺激实验", "DNA/RNA提取"],
        "pitfalls": "抗凝管选择(EDTA vs 肝素 vs ACD)影响下游；PBMC需及时分离避免活性下降；胞内因子需刺激+蛋白转运抑制剂",
    },
    "皮肤活检": {
        "isolate": "打孔活检→酶消化出单细胞 或 冰冻/石蜡切片",
        "downstream": ["scRNA-seq/空间转录组", "IHC/IF(αSMA/COL1/CD分子)", "成纤维细胞原代培养"],
        "pitfalls": "取材部位(受累 vs 未受累)必须配对记录；mRSS评分定位；消化过度损伤细胞",
    },
    "血清/血浆": {
        "isolate": "促凝管离心取血清 / 抗凝取血浆",
        "downstream": ["自身抗体(ELISA/免疫印迹/IIF)", "细胞因子(Luminex/ELISA)", "cfDNA/自身抗原"],
        "pitfalls": "反复冻融降解；溶血影响部分指标",
    },
}


# ============ 查询接口（给 agent 和 copilot 用） ============
def _match(table, query):
    q = (query or "").lower().strip()
    hits = []
    for key, v in table.items():
        blob = (key + " " + " ".join(str(x) for x in v.get("aka", [])) + " "
                + str(v.get("disease", "")) + " " + str(v.get("relevance", ""))).lower()
        if q in blob or blob.find(q) >= 0 or q in key.lower():
            hits.append((key, v))
    return hits


def lookup_autoantibody(query):
    """按抗体名/疾病/别名查自身抗体临床意义与检测方法。"""
    hits = _match(AUTOANTIBODIES, query)
    if not hits:
        return f"未在自身抗体库找到「{query}」。库内：{', '.join(AUTOANTIBODIES)}"
    out = [f"【自身抗体】匹配 {len(hits)} 条："]
    for k, v in hits[:8]:
        out.append(f"- {k}（{'/'.join(v['aka'][:3])}）→ {v['disease']} · {v['subtype']}\n"
                   f"    临床：{v['clinical']}\n    检测：{v['assay']}")
    return "\n".join(out)


def lookup_flow(query):
    """按细胞亚群名/marker/疾病查流式门控与意义。"""
    hits = _match(FLOW_SUBSETS, query)
    if not hits:
        return f"未在流式库找到「{query}」。库内：{', '.join(FLOW_SUBSETS)}"
    out = [f"【流式亚群】匹配 {len(hits)} 条："]
    for k, v in hits[:8]:
        out.append(f"- {k}：门控 {', '.join(v['markers'])}\n    意义：{v['relevance']}")
    return "\n".join(out)


def sample_pathway(sample):
    """按样本类型给实验路径骨架 + 常见坑。"""
    for k, v in SAMPLE_PATHWAYS.items():
        if sample and (sample in k or k in sample or any(t in k for t in sample.split())):
            return (f"【{k} 实验路径】\n分离：{v['isolate']}\n下游：{'、'.join(v['downstream'])}\n⚠️坑：{v['pitfalls']}")
    return f"样本类型「{sample}」暂无骨架。库内：{', '.join(SAMPLE_PATHWAYS)}"


def knowledge_summary():
    return (f"湿实验知识层：自身抗体 {len(AUTOANTIBODIES)} 条、流式亚群 {len(FLOW_SUBSETS)} 条、"
            f"样本路径 {len(SAMPLE_PATHWAYS)} 类。")


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(knowledge_summary())
    print("\n" + lookup_autoantibody("SSc"))
    print("\n" + lookup_flow("纤维细胞"))
    print("\n" + sample_pathway("全血"))
