"""
湿实验协议库（结构化 SOP）—— 把成熟流程沉淀成可检索、可复用、带 QC/对照/陷阱 的步骤。
experiment_copilot 会按情境自动带出相关协议；lab_lookup('protocol', ...) 可直接查。
可继续扩：照此结构(steps/tools/readouts/key_caveat/qc/controls/source)加你们实验室自己的 SOP。
"""

# 项目线总览（操纵对象 / 核心操作 / 主要读出）
PROJECT_MAP = [
    ("抗体 N-糖链体外改造", "已纯化抗体", "加糖基转移酶(体外)", "MALDI-TOF-MS(释放糖链)"),
    ("糖基转移酶 KO 细胞平台", "抗体生产细胞", "CRISPR 敲除", "基因测序 + 糖型分析"),
    ("补体 MAC 脂质体实验", "人工脂质膜", "表面接 CCP、加补体", "荧光染料泄漏"),
    ("IgG3 O-糖链研究", "IgG3 铰链区", "位点与糖型解析", "糖肽 LC-MS/MS"),
]
# 可能的主线逻辑（尚未被证明全部打通，也可能是彼此独立的 A/B/C 三块）
PROJECT_LOGIC = ("可能主线：体外给抗体糖链做加法(项目1) → 发现只能加、控制有限 → 建糖基转移酶KO细胞做减法(项目2) "
                 "→ 生产不同N/O糖型IgG3 → 质谱确认糖型与位点(项目4) → 检测糖型是否改变补体激活 → 脂质体泄漏测最终MAC打孔(项目3)。"
                 "判断是否同一项目需看桥接实验：不同糖型IgG3是否用于补体实验、是否测过C1q/C4/C3/C5b-9、脂质体CCP具体是哪种蛋白、"
                 "KO的具体糖基转移酶、KO细胞是否真生产过IgG3、O-糖肽质谱是否完成。")

PROTOCOLS = {
    # ============ 免疫学 / SSc ============
    "ACA-CENP-B 特异性B细胞单细胞克隆与重组抗体验证": {
        "disease": "SSc",
        "target": "抗着丝点抗体(ACA) / 识别 CENP-B 的 B 细胞",
        "keywords": ["aca", "抗着丝点", "着丝点", "cenp-b", "cenpb", "b细胞", "b cell",
                     "单细胞", "single cell", "克隆", "重组抗体", "分选", "sorting", "bcr", "测序", "artisan"],
        "goal": ("从SSc患者血分选真正结合CENP-B的单个B细胞→单细胞培养/测序→构建重组抗体验证，"
                 "解析ACA的isotype、克隆扩增、体细胞突变与潜在微生物交叉反应。重点是这条【分选→克隆→测序→重组验证】的途径。"),
        "steps": [
            {"n": 1, "step": "确认血清ACA阳性", "detail": "选血清抗着丝点抗体阳性的SSc患者作来源。"},
            {"n": 2, "step": "双色CENP-B荧光探针", "detail": "重组CENP-B偶联两种荧光染料，只把两色同时阳性者判为真正结合CENP-B。"},
            {"n": 3, "step": "流式单细胞分选", "detail": "把CENP-B double-positive单个B细胞分选到96/384孔板(每孔一个)。"},
            {"n": 4, "step": "单细胞培养+上清验证", "detail": "培养后取上清检测是否真结合CENP-B(初筛特异性)。"},
            {"n": 5, "step": "RNA/cDNA + ARTISAN PCR + Sanger", "detail": "对上清阳性细胞扩增Ig基因，测序得重链、κ或λ轻链序列。"},
            {"n": 6, "step": "IMGT注释", "detail": "注释V/D/J、CDR3、productive状态与体细胞突变。"},
            {"n": 7, "step": "克隆家族聚类", "detail": "Change-O/Scoper聚类判断同一克隆家族(克隆扩增)。"},
            {"n": 8, "step": "选代表链构建重组抗体", "detail": "选代表性重+轻链配对表达重组抗体(克隆名如1G8/2G4等只是示例，重在方法)。"},
            {"n": 9, "step": "HEK表达+结合验证", "detail": "HEK表达后用ELISA/WB验证是否识别人CENP-B或微生物CENP-B类似蛋白。"},
            {"n": 10, "step": "解析ACA反应特征", "detail": "综合isotype、克隆扩增、体细胞突变率、潜在微生物交叉反应。"},
        ],
        "key_caveat": "【候选≠确认】CENP-B染色阳性只是候选B细胞；须经培养上清ELISA或重组抗体验证后才算确认的CENP-B抗体。",
        "qc": "早期分选背景高、特异性约9%；改用双色探针+blocking+更严格gating后，培养上清CENP-B特异性提升到约55%。",
        "tools": ["双色CENP-B荧光探针", "流式单细胞分选", "ARTISAN PCR", "Sanger", "IMGT", "Change-O/Scoper", "HEK表达", "ELISA/WB"],
        "readouts": ["单细胞BCR序列(重/轻链)", "克隆家族", "isotype", "体细胞突变", "重组抗体结合特异性"],
        "source": "用户提供（Abdulla 课题）",
    },

    # ============ 项目1：抗体 N-糖链体外酶促改造 ============
    "抗体 N-糖链体外酶促改造(加法)": {
        "disease": "抗体糖工程",
        "target": "已纯化抗体 Fc 的 N-糖链",
        "keywords": ["n-糖", "n-glycan", "糖基转移酶", "gnt", "gntiii", "b4galt", "st6gal", "唾液酸",
                     "sialic", "maldi", "pngase", "糖工程", "glycan", "糖型", "半乳糖", "galactos"],
        "goal": "对已纯化抗体Fc的N-糖链用糖基转移酶在体外定向改造(加bisecting GlcNAc/半乳糖/α2,6-唾液酸)，释放糖链后MALDI-TOF-MS验证糖型推进。属纯化蛋白后的体外糖工程，不改生产细胞基因。",
        "steps": [
            {"n": 1, "step": "起始抗体", "detail": "已表达纯化的抗体，Fc N-糖初始糖型不均一(无/单/双半乳糖、不同岩藻糖化、少量唾液酸化、其他分支)。"},
            {"n": 2, "step": "体外加糖基转移酶", "detail": "GnTIII(+bisecting GlcNAc, 供体UDP-GlcNAc)/B4GalT1(末端GlcNAc→加Gal, UDP-Gal)/ST6GalT1(末端Gal→加α2,6-Neu5Ac, CMP-Neu5Ac)。链式:GlcNAc→Gal→Neu5Ac-Gal；双分支都唾液酸化=双唾液酸化。"},
            {"n": 3, "step": "PNGase F释放N-糖", "detail": "切蛋白与N-糖连接，释放游离糖链后再检测(说明验证的是N-糖，非IgG3铰链O-糖)。"},
            {"n": 4, "step": "唾液酸乙酯化衍生", "detail": "稳定唾液酸、提高含唾液酸糖链检测、减少假性去唾液酸峰，部分方案可区分连接形式。"},
            {"n": 5, "step": "HILIC纯化", "detail": "富集亲水糖链、洗掉盐和疏水杂质、降低MALDI离子抑制。"},
            {"n": 6, "step": "MALDI-TOF-MS检测", "detail": "糖链+基质结晶→激光解吸离子化→按质荷比得谱；实测峰对理论糖链质量，双唾液酸化峰出现/增强=改造成功。"},
        ],
        "key_caveat": "单独用释放糖链MALDI【只能定性】(only allows qualitative mass spec)——不能证明糖链原位点、精确定量比例、唾液酸连接位置唯一性、是否同质量异构体、是否改变抗体功能。",
        "qc": "ST6GalT1处理后唾液酸化糖型增加可支持改造；结论限于'样品出现与目标组成相符的N-糖链'。",
        "tools": ["GnTIII/B4GalT1/ST6GalT1 + 糖供体", "PNGase F", "唾液酸乙酯化", "HILIC", "MALDI-TOF-MS"],
        "readouts": ["释放N-糖链的MALDI-TOF-MS谱(定性)"],
        "source": "用户提供（幻灯片项目一）",
    },

    # ============ 项目2：糖基转移酶 KO 细胞平台 ============
    "糖基转移酶 CRISPR-KO 细胞平台(减法)": {
        "disease": "抗体糖工程",
        "target": "抗体生产细胞的糖基转移酶基因",
        "keywords": ["crispr", "cas9", "grna", "ko", "敲除", "knockout", "糖基转移酶", "细胞系", "clone",
                     "单细胞克隆", "rfp", "糖型", "糖组", "转染", "igg1", "igg3", "fc"],
        "goal": "用CRISPR敲除生产细胞的糖基转移酶，建单细胞克隆KO细胞系→生产不同糖型糖蛋白(IgG1/IgG3/Fc)→比较结构与功能。直接改细胞糖链合成能力(做减法)。",
        "steps": [
            {"n": 1, "step": "选敲除基因", "detail": "取决于想让糖链停在哪一步(前体→中间体A→B→成熟，敲哪一步)。敲某酶后其底物积累、后续糖型减少/消失。"},
            {"n": 2, "step": "设计gRNA", "detail": "靶关键外显子、易移码、切割效率、脱靶、避免识别相似基因；至少准备2条独立gRNA比较。"},
            {"n": 3, "step": "克隆CRISPR质粒", "detail": "含gRNA/Cas9/RFP/Kan抗性。Kan抗性用于【细菌阶段】(转大肠杆菌→卡那筛选→挑菌落→扩增→测序确认gRNA插入)，通常不用于筛哺乳细胞。"},
            {"n": 4, "step": "转染生产细胞", "detail": "细胞瞬时表达RFP+Cas9+gRNA→靶位DNA双链断裂→NHEJ修复产生indel→移码+提前终止使酶失活。"},
            {"n": 5, "step": "流式分选RFP+", "detail": "RFP=质粒进过细胞，≠一定敲除。Bulk sort富集群体(混基因型，快看整体);Single-cell sort建单细胞克隆(定义清楚的稳定细胞系)。"},
            {"n": 6, "step": "96孔单细胞克隆培养", "detail": "坑:部分单细胞不存活/孔内混入多细胞/克隆生长速度不一/等位基因突变不同。留一部分作种子细胞防丢。"},
            {"n": 7, "step": "提基因组DNA测序", "detail": "PCR扩Cas9靶位→Sanger或扩增子深测→查插入/缺失/移码；目标双等位失活。混合Sanger峰≠稳定KO(可能等位不同/非单细胞来源/≥3拷贝/群体混杂)。"},
            {"n": 8, "step": "验证酶真失活(最重要=糖型验证)", "detail": "查mRNA↓/蛋白消失/酶活消失/生长正常/目标糖型改变。糖型验证法:释放糖MALDI、LC-MS糖组、糖肽LC-MS/MS、凝集素结合、特异糖型抗体、HPLC/CE。"},
            {"n": 9, "step": "用KO细胞生产糖蛋白", "detail": "表达IgG1/IgG3/Fc等，不同KO产不同糖型，建'糖型生产面板'。"},
            {"n": 10, "step": "比较结构与功能", "detail": "产量/纯度聚集/热稳定/FcR结合/C1q结合/补体激活/ADCC-CDC/半衰期受体/蛋白酶敏感——把KO↔糖型↔功能真正连起来。"},
        ],
        "key_caveat": "RFP阳性只代表质粒进入，不代表基因已敲除；混合Sanger峰≠稳定KO，需双等位失活 + 蛋白/酶活/糖型三层验证。",
        "qc": "可靠项目用≥2条gRNA；须单细胞克隆得到定义清楚的细胞系；糖型改变是KO成功的最终证据。",
        "tools": ["CRISPR/Cas9质粒(gRNA/RFP/Kan)", "转染", "流式分选", "Sanger/扩增子深测", "糖组/糖肽MS", "凝集素/HPLC/CE"],
        "readouts": ["基因型(测序)", "mRNA/蛋白/酶活", "糖型(糖组/糖肽MS)", "功能面板(FcR/C1q/CDC/ADCC等)"],
        "source": "用户提供（幻灯片项目二）",
    },

    # ============ 项目3：补体 MAC 脂质体打孔 ============
    "补体 MAC 脂质体打孔功能实验": {
        "disease": "补体功能",
        "target": "人工脂质膜(脂质体) + 终末补体 MAC(C5b-9)",
        "keywords": ["补体", "complement", "mac", "c5b-9", "c5b9", "脂质体", "liposome", "ccp", "dbco",
                     "点击化学", "spaac", "sulforhodamine", "泄漏", "leakage", "膜穿孔", "c1q"],
        "goal": "构建人工脂质膜作为终末补体MAC(C5b-9)打孔的功能检测平台，用包裹染料泄漏定量膜穿孔；可比较CCP修饰的保护作用，或作统一平台测不同抗体/蛋白/血清的MAC形成能力。",
        "steps": [
            {"n": 1, "step": "配脂质", "detail": "DMPC+DMPG(成双层)+胆固醇(调稳定)+DBCO脂质(点击把手)，溶于氯仿/甲醇。"},
            {"n": 2, "step": "氮气吹干成脂质薄膜", "detail": "除净有机溶剂，残留会影响脂质体稳定性和后面的补体实验。"},
            {"n": 3, "step": "高浓度Sulforhodamine B水化", "detail": "自组装成囊泡并包裹染料;高浓度内部自淬灭→初始荧光低。"},
            {"n": 4, "step": "超声减小粒径", "detail": "使颗粒变小、分布集中;要更精确粒径可用挤出膜(图中未显示)。"},
            {"n": 5, "step": "体积排阻纯化", "detail": "分离包裹染料的脂质体与外部游离染料，保证后续荧光来自膜泄漏而非游离染料背景。"},
            {"n": 6, "step": "CCP-azide点击接表面", "detail": "脂质体DBCO + CCP-azide 无铜应变促进叠氮-炔点击(SPAAC)→脂质体-CCP定向固定;接后应再纯化去游离CCP。CCP很可能是含complement control protein模块的补体调控蛋白(全名不确定)。"},
            {"n": 7, "step": "加补体体系", "detail": "加补体成分或血清;级联顺利则末端形成C5b-9(MAC)插入脂质膜。"},
            {"n": 8, "step": "MAC打孔→染料释放", "detail": "打孔后Sulforhodamine B泄漏→稀释→解除自淬灭→荧光增强(膜穿孔间接读出)。归一化:泄漏率=(F_sample−F_bg)/(F_detergent−F_bg)×100%，去污剂完全裂解=最大释放。"},
            {"n": 9, "step": "在问什么", "detail": "①比较不同CCP修饰脂质体的染料释放→哪个CCP更抑制MAC;②脂质体作统一终末补体检测平台，测不同抗体/蛋白/血清的MAC形成能力。"},
        ],
        "key_caveat": "只看到脂质体形成和染料包封，不能宣称已验证MAC打孔——需完整对照。",
        "controls": ["无补体", "热灭活血清", "无CCP脂质体", "不含DBCO脂质体", "不带azide的CCP",
                     "补体阳性条件", "去污剂完全裂解", "相同脂质浓度与粒径", "CCP表面密度测定"],
        "tools": ["DMPC/DMPG/胆固醇/DBCO脂质", "Sulforhodamine B", "超声/挤出", "体积排阻柱", "SPAAC点击化学", "荧光读板"],
        "readouts": ["荧光染料(Sulforhodamine B)泄漏率(%)"],
        "source": "用户提供（幻灯片项目三）",
    },

    # ============ 项目4：IgG3 O-糖链研究 ============
    "IgG3 铰链区 O-糖链解析": {
        "disease": "抗体糖工程",
        "target": "IgG3 铰链区 Ser/Thr 上的 O-糖链",
        "keywords": ["igg3", "o-糖", "o-glycan", "o糖", "铰链", "hinge", "糖肽", "glycopeptide",
                     "ser/thr", "etd", "ethcd", "占有率", "occupancy"],
        "goal": "解析IgG3长铰链区Ser/Thr上尚未充分探索的O-糖链——位点、结构、占有率与生物学作用。目前更像研究动机，而非已完成结果。",
        "steps": [
            {"n": 1, "step": "为什么研究IgG3", "detail": "IgG3铰链区很长、富含Ser/Thr，O-糖常接在这些残基。提出IgG3存在未充分探索的O-糖、可能只部分分子携带、位点/结构/功能不清。'10% of IgG3 carry O-glycans'需澄清具体所指(10%分子/10%样品/某位点占有率/某文献条件)。"},
            {"n": 2, "step": "正确解析O-糖路线", "detail": "PNGase F【不能】释放O-糖，不能沿用N-糖MALDI流程。路线:纯化IgG3→蛋白酶切成糖肽→富集O-糖肽→LC-MS/MS→用ETD/EThcD碎裂保留糖修饰→定位Ser/Thr→分析各位点糖型与占有率→外切糖苷酶/标准品辅助确认→位点突变或糖基转移酶KO做因果验证。铰链重复性高、糖肽长、异质性大，位点定位不易。"},
            {"n": 3, "step": "与KO平台的关系", "detail": "野生型 vs 不同糖基转移酶KO细胞表达IgG3→比较O-糖肽质谱→看哪个糖型消失/积累→推断对应酶参与哪一步。IgG3 O-糖可能是KO平台建成后的重点应用。"},
        ],
        "key_caveat": "PNGase F不释放O-糖;GnTIII/B4GalT1/ST6GalT1/PNGase F主要对应N-糖，不能直接当IgG3 O-糖研究结果。",
        "tools": ["蛋白酶(生成糖肽)", "O-糖肽富集", "LC-MS/MS", "ETD/EThcD碎裂", "外切糖苷酶/标准品", "位点突变/糖基转移酶KO"],
        "readouts": ["O-糖肽LC-MS/MS(ETD/EThcD)", "Ser/Thr位点定位", "各位点糖型与占有率"],
        "source": "用户提供（幻灯片项目四，研究动机为主）",
    },
}


def match_protocols(text):
    """按疾病/关键词匹配相关协议（须命中 keyword 或 disease，避免泛匹配）。返回 [(name, proto)]。"""
    t = (text or "").lower()
    if not t.strip():
        return []
    hits = []
    for name, p in PROTOCOLS.items():
        if any(k in t for k in p["keywords"]) or p["disease"].lower() in t or name.lower() in t:
            hits.append((name, p))
    return hits


def format_protocol(name, p, full=True):
    out = [f"【协议】{name}", f"目标：{p.get('goal', '')}"]
    if p.get("key_caveat"):
        out.append(f"⚠️ {p['key_caveat']}")
    if p.get("qc"):
        out.append(f"🎯 QC：{p['qc']}")
    if full:
        out.append("步骤：")
        for s in p.get("steps", []):
            out.append(f"  {s['n']}. {s['step']} —— {s['detail']}")
        if p.get("controls"):
            out.append(f"必要对照：{'、'.join(p['controls'])}")
        if p.get("tools"):
            out.append(f"工具链：{'、'.join(p['tools'])}")
        if p.get("readouts"):
            out.append(f"读出：{'、'.join(p['readouts'])}")
    out.append(f"来源：{p.get('source', '')}")
    return "\n".join(out)


def lookup_protocol(query):
    """按关键词/疾病查协议（完整步骤）。"""
    hits = match_protocols(query)
    if not hits:
        return f"未在协议库找到「{query}」。库内：{', '.join(PROTOCOLS)}"
    return "\n\n".join(format_protocol(n, p, full=True) for n, p in hits[:3])


def project_map_text():
    """项目线总览表 + 可能的主线逻辑。"""
    lines = ["【项目线总览】操纵对象 / 核心操作 / 主要读出"]
    for i, (name, obj, op, ro) in enumerate(PROJECT_MAP, 1):
        lines.append(f"{i}. {name}：{obj} | {op} | {ro}")
    lines.append("关系：项目1、2明显相关；项目4可能是项目2的应用或独立;项目3(补体)可能与抗体项目相连或完全独立。")
    lines.append("主线逻辑：" + PROJECT_LOGIC)
    return "\n".join(lines)


def protocol_summary():
    return f"协议库：{len(PROTOCOLS)} 条 —— " + "；".join(f"{n}({p['disease']})" for n, p in PROTOCOLS.items())


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(protocol_summary())
    print("\n" + project_map_text())
    print("\n" + lookup_protocol("CRISPR 糖基转移酶 KO"))
