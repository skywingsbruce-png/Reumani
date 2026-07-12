"""
风湿病疾病注册表 —— 让整套系统从"只做 SSc"泛化到"覆盖所有风湿病"。
新增一个病 = 在这里加一行 + 写一份 <disease>-knowledge SKILL.md（可选）。
语料/GWAS/OpenTargets 的检索词都从这里取，不用改各处代码。
"""

DISEASES = {
    "SSc": {
        "cn": "系统性硬化症",
        "query": '(TITLE_ABS:"systemic sclerosis" OR TITLE_ABS:"scleroderma")',
        "efo_hint": "systemic sclerosis",
        "corpus": True,          # 已建本地语料库
        "knowledge_skill": "ssc-knowledge",
    },
    "SLE": {
        "cn": "系统性红斑狼疮",
        "query": '(TITLE_ABS:"systemic lupus erythematosus" OR TITLE_ABS:"lupus nephritis")',
        "efo_hint": "systemic lupus erythematosus",
        "corpus": False,
        "knowledge_skill": "sle-knowledge",
    },
    "RA": {
        "cn": "类风湿关节炎",
        "query": '(TITLE_ABS:"rheumatoid arthritis")',
        "efo_hint": "rheumatoid arthritis",
        "corpus": False,
        "knowledge_skill": None,
    },
    "SjS": {
        "cn": "干燥综合征",
        "query": '(TITLE_ABS:"Sjogren" OR TITLE_ABS:"Sjögren")',
        "efo_hint": "Sjogren syndrome",
        "corpus": False,
        "knowledge_skill": None,
    },
    "IIM": {
        "cn": "特发性炎性肌病/皮肌炎",
        "query": '(TITLE_ABS:"dermatomyositis" OR TITLE_ABS:"polymyositis" OR TITLE_ABS:"inflammatory myopathy")',
        "efo_hint": "dermatomyositis",
        "corpus": False,
        "knowledge_skill": None,
    },
    "Vasculitis": {
        "cn": "系统性血管炎",
        "query": '(TITLE_ABS:"vasculitis" OR TITLE_ABS:"ANCA")',
        "efo_hint": "systemic vasculitis",
        "corpus": False,
        "knowledge_skill": None,
    },
}


def disease_query(*keys):
    """把若干个病的检索词用 OR 组合，供 corpus/文献检索用。keys 为空则全部。"""
    keys = keys or list(DISEASES)
    parts = [DISEASES[k]["query"] for k in keys if k in DISEASES]
    return "(" + " OR ".join(parts) + ")"


def status_table():
    rows = ["疾病\t中文\t本地语料\t知识技能"]
    for k, v in DISEASES.items():
        rows.append(f"{k}\t{v['cn']}\t{'✅' if v['corpus'] else '—'}\t{v['knowledge_skill'] or '—'}")
    return "\n".join(rows)


if __name__ == "__main__":
    print(status_table())
    print("\n全风湿病组合检索词示例：")
    print(disease_query("SSc", "SLE")[:200])
