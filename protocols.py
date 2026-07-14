"""
湿实验协议库（结构化 SOP）—— 把成熟的实验流程沉淀成可检索、可复用、带 QC 与坑的步骤。
第一条：Abdulla 的 SSc 抗着丝点抗体(ACA)/CENP-B 特异性 B 细胞单细胞克隆流程（用户提供）。
可继续扩：照此结构加你们实验室自己的 SOP。experiment_copilot 会按情境自动带出相关协议。
"""

PROTOCOLS = {
    "ACA-CENP-B单B细胞克隆与重组抗体验证": {
        "disease": "SSc",
        "target": "抗着丝点抗体(ACA) / 识别 CENP-B 的 B 细胞",
        "keywords": ["aca", "抗着丝点", "着丝点", "cenp-b", "cenpb", "b细胞", "b cell",
                     "单细胞", "single cell", "克隆", "重组抗体", "分选", "sorting", "bcr", "测序"],
        "goal": ("从 SSc 患者血液中分选真正结合 CENP-B 的单个 B 细胞 → 单细胞培养/测序 → 构建重组抗体验证，"
                 "最终解析 ACA 反应的 isotype、克隆扩增、体细胞突变与潜在微生物交叉反应。"),
        "logic": ("SSc 患者血里有 ACA，但不知是哪批 B 细胞产生。用双色荧光标记的 CENP-B 同时标记"
                  "真正结合 CENP-B 的 B 细胞，分选到单细胞后克隆、测序、重组表达并验证。"),
        "steps": [
            {"n": 1, "step": "确认血清 ACA 阳性", "detail": "选取血清抗着丝点抗体阳性的 SSc 患者作为来源。"},
            {"n": 2, "step": "制备并荧光标记 CENP-B 探针",
             "detail": "重组 CENP-B 抗原分别偶联两种荧光染料，做成【双颜色探针】，只把两色同时阳性的 B 细胞判为真正结合 CENP-B。"},
            {"n": 3, "step": "流式单细胞分选",
             "detail": "流式把 CENP-B double-positive 的单个 B 细胞分选到 96/384 孔板（每孔一个细胞）。"},
            {"n": 4, "step": "单细胞培养 + 上清验证",
             "detail": "培养分选出的单 B 细胞，取培养上清检测是否真的结合 CENP-B（初筛特异性）。"},
            {"n": 5, "step": "RNA/cDNA + ARTISAN PCR + Sanger 测序",
             "detail": "对上清阳性细胞提 RNA、逆转录 cDNA，用 ARTISAN PCR 扩增免疫球蛋白基因，Sanger 测序获得重链、κ 或 λ 轻链序列。"},
            {"n": 6, "step": "IMGT 注释",
             "detail": "用 IMGT 注释 V/D/J 基因、CDR3、productive 状态与体细胞突变。"},
            {"n": 7, "step": "克隆家族聚类",
             "detail": "用 Change-O / Scoper 聚类，判断哪些细胞属于同一克隆家族（克隆扩增分析）。"},
            {"n": 8, "step": "选代表链构建重组抗体",
             "detail": "从各克隆选代表性重链+轻链配对，构建重组抗体（例：1G8、2G4、3G2、6D6）。"},
            {"n": 9, "step": "HEK 表达 + 结合验证",
             "detail": "在 HEK 细胞表达重组抗体，用 ELISA / Western blot 验证是否识别人 CENP-B 或微生物 CENP-B 类似蛋白。"},
            {"n": 10, "step": "解析 ACA 反应特征",
             "detail": "综合分析 isotype、克隆扩增、体细胞突变率及潜在微生物交叉反应。"},
        ],
        "key_caveat": ("【候选 ≠ 确认】“CENP-B 染色阳性”只是候选 B 细胞；只有经培养上清 ELISA 或重组抗体验证后，"
                       "才能称为确认的 CENP-B 抗体。"),
        "qc": ("特异性把控：早期 CENP-B 分选背景高、特异性仅约 9%；改用【双颜色探针 + blocking + 更严格 gating】后，"
               "培养上清的 CENP-B 特异性提升到约 55%。"),
        "tools": ["双色 CENP-B 荧光探针", "流式单细胞分选", "ARTISAN PCR", "Sanger", "IMGT", "Change-O/Scoper", "HEK 表达", "ELISA/WB"],
        "readouts": ["单细胞 BCR 序列(重/轻链)", "克隆家族", "isotype", "体细胞突变", "重组抗体结合特异性"],
        "example_clones": ["1G8", "2G4", "3G2", "6D6"],
        "source": "用户提供（Abdulla 课题）",
    },
}


def match_protocols(text):
    """按疾病/关键词匹配相关协议，返回 [(name, proto)]。"""
    t = (text or "").lower()
    hits = []
    for name, p in PROTOCOLS.items():
        blob = (name + " " + p["disease"] + " " + p["target"] + " " + " ".join(p["keywords"])).lower()
        if any(k in t for k in p["keywords"]) or p["disease"].lower() in t or any(w in blob for w in t.split()):
            hits.append((name, p))
    return hits


def format_protocol(name, p, full=True):
    out = [f"【协议】{name}",
           f"目标：{p['goal']}",
           f"⚠️ {p['key_caveat']}",
           f"🎯 QC：{p['qc']}"]
    if full:
        out.append("步骤：")
        for s in p["steps"]:
            out.append(f"  {s['n']}. {s['step']} —— {s['detail']}")
        out.append(f"工具链：{'、'.join(p['tools'])}")
        out.append(f"读出：{'、'.join(p['readouts'])}")
        if p.get("example_clones"):
            out.append(f"示例克隆：{'、'.join(p['example_clones'])}")
    out.append(f"来源：{p['source']}")
    return "\n".join(out)


def lookup_protocol(query):
    """按关键词/疾病查协议（完整步骤）。"""
    hits = match_protocols(query)
    if not hits:
        return f"未在协议库找到「{query}」。库内：{', '.join(PROTOCOLS)}"
    return "\n\n".join(format_protocol(n, p, full=True) for n, p in hits[:3])


def protocol_summary():
    return f"协议库：{len(PROTOCOLS)} 条 —— " + "；".join(f"{n}({p['disease']})" for n, p in PROTOCOLS.items())


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(protocol_summary())
    print("\n" + lookup_protocol("CENP-B B细胞"))
