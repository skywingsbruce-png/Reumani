"""
湿实验协议库（结构化 SOP）—— 把成熟流程沉淀成可检索、可复用、带 QC/对照/陷阱 的步骤。
experiment_copilot 会按情境自动带出相关协议；lab_lookup('protocol', ...) 可直接查。
可继续扩：照此结构(steps/tools/readouts/key_caveat/qc/controls/source)加你们实验室自己的 SOP。
"""

# 研究者线路图：三人的自身反应性B细胞课题如何衔接
RESEARCHERS = {
    "Abdulla": {
        "disease": "SSc",
        "antigen": "CENP-B / ACA（抗着丝点）",
        "focus": "怎样【准确找到】CENP-B阳性B细胞、重建ACA抗体，并研究微生物CENP-B同源物(ABP1/CBH1/CBH2)",
        "protocol": "ACA/CENP-B 自身反应性B细胞完整研究链（Abdulla 博士课题）",
    },
    "Sam": {
        "disease": "SSc + RA",
        "antigen": "TOP1 / ATA（抗拓扑异构酶I），延伸到 ACPA",
        "focus": "【最初触发与长期持续】——真菌TOP1分子模拟、TOP1–DNA复合物、缓解后ACPA B细胞仍活化、胞内CCP2染色",
        "protocol": "TOP1/ATA 与 ACPA 自身反应性B细胞：从初始触发到持续活化（Sam 博士课题）",
    },
    "Renee": {
        "disease": "RA（+ MPO-ANCA 支线）",
        "antigen": "ACPA（瓜氨酸化蛋白）",
        "focus": "【把突变BCR变成靶点】——BCR neoepitope 经MHC-I呈递、CD8 T细胞选择性清除；纵向克隆进化；RATP-Ig平台",
        "protocol": "ACPA B细胞克隆进化与 BCR neoepitope 靶向清除（Renee / Target to B-cure）",
    },
}
RESEARCHER_LINKS = ("三人衔接：Sam 已在 TOP1 体系跑通'单细胞BCR→重组抗体→微生物交叉反应'这条路线；"
                    "Abdulla 正把相似思路应用到 CENP-B/ACA（并把探针做到sortase定点双标、特异性9%→55%）；"
                    "Renee 则在 ACPA 体系把这条路线往下游推——不止是重建抗体，而是把克隆特有的突变BCR当作"
                    "pMHC-I 靶点去清除致病B细胞，并用纵向测序回答'靶点稳不稳'。"
                    "共同的方法学骨架：抗原探针→流式单细胞分选→BCR测序(ARTISAN/IMGT/克隆家族)→重组单抗验证。"
                    "共同的边界：探针分选阳性只是候选，必须重组抗体功能验证（Abdulla约55%特异、Renee约50%为真ACPA）。")

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
    "ACA/CENP-B 自身反应性B细胞完整研究链（Abdulla 博士课题）": {
        "disease": "SSc",
        "target": "抗着丝点抗体(ACA) / 识别 CENP-B 的自身反应性 B 细胞",
        "keywords": ["aca", "ata", "抗着丝点", "着丝点", "cenp-b", "cenpb", "top1", "拓扑异构酶",
                     "b细胞", "b cell", "单细胞", "single cell", "克隆", "重组抗体", "分选", "sorting",
                     "bcr", "测序", "artisan", "ramos", "sortase", "imgt", "6d6", "交叉反应", "分子模拟"],
        "goal": ("把'SSc患者血里有ACA'推进到'具体是哪个B细胞、它的重轻链是什么、能否重建成抗体、"
                 "识别CENP-B哪个区域、是否可能由微生物相似抗原触发'。一条从患者样本→抗原探针→单细胞分选→"
                 "BCR测序→重组抗体→微生物交叉反应的完整研究链。"),
        "research_questions": [
            "是哪些B细胞产生ACA？属于IgM/IgG还是IgA？",
            "是否发生克隆扩增和体细胞突变？同一患者是否有多个不同ACA克隆？",
            "这些B细胞最初为什么会识别CENP-B？",
            "微生物蛋白是否与人CENP-B相似，从而触发交叉反应？",
            "（背景：SSc两类主要自身抗体——ACA靶抗原CENP-B；ATA靶抗原TOP1。单纯测血清抗体回答不了以上问题。）",
        ],
        "steps": [
            {"n": 1, "step": "CENP-B 抗原制备与质控",
             "detail": "重组表达→细胞裂解+超声→His-tag亲和纯化→Q柱阴离子交换→S柱阳离子交换→SEC体积排阻→"
                       "SDS-PAGE + anti-His WB确认；比较全长/还原/非还原/TEV切割及不同纯化阶段。CENP-B约80 kDa。"
                       "⚠️纯度与构象至关重要：聚集、游离荧光染料、非特异蛋白都会造成流式假阳性。"},
            {"n": 2, "step": "双色荧光探针（sortase 定点标记）",
             "detail": "早期用普通化学法接PE/AF647——随机标记赖氨酸、可能影响构象、产生背景。改建sortase定点标记："
                       "制备带sortase识别位点的CENP-B→Sortase A定点连接→分别接Cy5与TAMRA→优化CENP-B浓度/酶浓度/"
                       "染料浓度/温度/反应时间→SEC去游离染料。得 CENPB-Cy5 + CENPB-TAMRA。"
                       "同一B细胞须【同时结合两种颜色】才算更可信的CENP-B阳性，可排除只黏一种染料或非特异蛋白的细胞。"},
            {"n": 3, "step": "工程化 RAMOS 细胞验证探针",
             "detail": "把已知抗体的BCR导入RAMOS B细胞系，测试不同浓度CENPB-Cy5/TAMRA。目标：ACA-RAMOS双阳性染色；"
                       "ACPA/ATA对照保持阴性；荧光强度随探针浓度变化；两种探针识别同一批抗原特异性细胞。"
                       "这些RAMOS后来成为患者分选实验的固定阳性对照。"},
            {"n": 4, "step": "患者 PBMC 流式单细胞分选",
             "detail": "门控：活细胞→排除CD3/CD4 T细胞→CD19/CD20阳性B细胞→CENPB-Cy5阳性→CENPB-TAMRA阳性→单细胞分选。"
                       "同时加IgD/IgM/IgG/CD27/CD38/CD10判断亚群：初始B、IgM记忆B、class-switched记忆B、"
                       "double-negative B、浆母细胞样。分到96/384孔板；部分直接裂解测序，部分先培养约14天。"},
            {"n": 5, "step": "培养上清 ELISA 确认是否真产ACA",
             "detail": "流式双阳性仍不等于一定识别CENP-B。培养后检测上清：Total IgG/IgA/IgM ELISA + "
                       "CENP-B特异性IgG/IgA/IgM ELISA；必要时比较TOP1、CCP或其他抗原。"},
            {"n": 6, "step": "BCR 测序与注释",
             "detail": "单细胞RNA提取或直接裂解→cDNA合成→ARTISAN 5′-RACE PCR→分别扩增IgG/IgA/IgM重链及κ/λ轻链→"
                       "凝胶检查与纯化→Sanger测序→IMGT/V-QUEST注释→Change-O/Scoper克隆家族分析。"
                       "IMGT给出：IGHV/IGHD/IGHJ、轻链IGK还是IGL、CDR1/2/3、是否productive/in-frame、"
                       "有无终止密码子、相对生殖系的体细胞突变数。"},
            {"n": 7, "step": "生产重组 ACA 单克隆抗体",
             "detail": "选完整VH/VL配对→序列确认+密码子优化→gBlock或引物设计→In-Fusion克隆→拼到pcDNA3.1重链/轻链载体→"
                       "细菌转化+Mini/Maxi prep→T7/BGH双向Sanger验证→HEK细胞共转染重链与轻链→收集上清并纯化抗体→"
                       "ELISA、Western blot确认CENP-B结合。进展报告先后记录4个、后6个经验证的ACA单抗（含IgG/IgM/IgA）。"},
            {"n": 8, "step": "微生物交叉反应测试（分子模拟假说）",
             "detail": "选裂殖酵母的CENP-B样蛋白 ABP1 / CBH1 / CBH2——与人CENP-B序列一致性不高，但可能保留局部结构或表位。"
                       "对其表达、His纯化、离子交换、SEC，再用1G8/2G4/1F10/6D6等ACA单抗做Western blot。"
                       "若同一患者来源抗体同时识别人CENP-B与微生物同源蛋白，支持'分子模拟/交叉反应'假说。"},
        ],
        "reference_clones": {
            "1G8": "ACA / CENP-B，IgG（阳性）",
            "2G4": "ACA / CENP-B，IgG（阳性）",
            "9G5": "ACA，IgM（阳性）",
            "3F3": "ACPA 对照——不应特异识别CENP-B（阴性对照）",
            "9D11": "ATA / TOP1 对照——不是ACA（阴性对照）",
            "1F10": "ACA 单抗，用于微生物同源蛋白WB",
            "6D6": "最完整范例，见 worked_example",
        },
        "worked_example": {
            "clone": "6D6（来源匿名样本 RL2790）",
            "isotype": "重链 IgA；轻链 Lambda",
            "VH": "IGHV3-72 / IGHD6-13 / IGHJ4；CDR-H3: CARSYSSSWFSPGYW",
            "VL": "IGLV1-44 / IGLJ3；CDR-L3: CAAWDDSLNGRMF",
            "status": "VH与VL均 productive / in-frame",
            "records": "有天然DNA、蛋白序列、密码子优化序列、完整表达载体；原始Sanger .ab1 及 Maxi-prep 后 T7/BGH 验证",
            "completed_chain": "单个B细胞→BCR测序→VH/VL配对→序列优化→表达载体→HEK生产→CENP-B ELISA与Western blot验证",
        },
        "dataset_stats": ("CENP-B候选组：292条重链记录、232条轻链记录；处理后246条独特重链、223条productive重链、"
                          "194个重链克隆家族（其中26个为扩增家族）；约135个productive VH–VL配对细胞，"
                          "约127个无明显多链歧义。"),
        "key_caveat": ("【三道边界，别越线】①流式双阳性=候选，须培养上清ELISA或重组抗体验证才算确认的CENP-B抗体；"
                       "②测序所得是'序列候选库'，不能把全部称为已确认的ACA抗体；"
                       "③微生物同源蛋白Western阳性仍需ELISA、SPR/BLI、突变与表位定位进一步证明交叉反应。"),
        "qc": ("分选特异性演进：第一次实验CENP-B特异性约9% → 经FCS/脱脂奶blocking、严格双阳性gating与探针优化后约25–31% → "
               "更成熟实验约55%（部分记录50–60%）。技术明显进步，但仍说明染色阳性细胞必须ELISA确认。"),
        "accomplishments": [
            "建立可重复的CENP-B纯化流程",
            "制备定点标记的 CENPB-Cy5 / CENPB-TAMRA 双探针",
            "建立 ACA-RAMOS 阳性对照",
            "成功分选并培养CENP-B候选B细胞",
            "分选特异性从约9%提升到约50–60%",
            "获得大批重链与轻链序列，完成IMGT与克隆家族分析",
            "生产并验证多种 IgG/IgM/IgA ACA单克隆抗体（含完整范例6D6）",
            "开始测试微生物CENP-B样蛋白(ABP1/CBH1/CBH2)的交叉反应",
        ],
        "tools": ["重组CENP-B(His/TEV)", "Q/S离子交换 + SEC", "SDS-PAGE/anti-His WB", "Sortase A定点标记(Cy5/TAMRA)",
                  "工程化RAMOS对照", "流式单细胞分选", "单细胞培养+ELISA", "ARTISAN 5′-RACE PCR", "Sanger",
                  "IMGT/V-QUEST", "Change-O/Scoper", "In-Fusion + pcDNA3.1", "HEK共转染", "ELISA/WB"],
        "readouts": ["CENP-B特异性ELISA(总/特异 IgG/IgA/IgM)", "单细胞BCR序列(VH/VL配对)", "克隆家族与扩增",
                     "isotype", "体细胞突变", "重组抗体对人CENP-B与微生物同源蛋白的结合"],
        "source": "用户提供（Abdulla 博士课题）",
    },

    # ============ Sam：从初始触发到持续活化 ============
    "TOP1/ATA 与 ACPA 自身反应性B细胞：从初始触发到持续活化（Sam 博士课题）": {
        "disease": "SSc",
        "target": "SSc 的 TOP1 反应性B细胞(ATA) + RA 的 ACPA B细胞",
        "keywords": ["top1", "ata", "拓扑异构酶", "抗拓扑", "topoisomerase", "真菌", "酵母",
                     "saccharomyces", "分子模拟", "交叉反应", "top1-dna", "核蛋白", "凋亡",
                     "ccp2", "胞内染色", "intracellular", "acpa", "瓜氨酸", "持续活化", "缓解", "复发", "sam"],
        "goal": ("解释自身免疫反应的完整生命周期：自身反应性B细胞从哪里来→什么抗原最初触发它→"
                 "如何克隆扩增与亲和力成熟→为什么疾病控制后它仍然存在。覆盖 SSc(TOP1/ATA) 与 RA(ACPA) 两套体系。"),
        "research_questions": [
            "能不能找到TOP1反应性B细胞？它们处于初始、记忆还是浆母细胞状态？",
            "它们有没有被持续激活？是否存在克隆扩增？同一克隆的不同成员是否共同识别TOP1？",
            "自身反应最初为什么会发生——微生物相似抗原能否触发？",
            "为什么RA临床缓解后ACPA仍长期存在、停药会复发？",
        ],
        "steps": [
            {"n": 1, "step": "SSc 中的 TOP1 反应性B细胞（抗原驱动特征）",
             "detail": "不只看血清有无ATA，而是用单细胞分选+BCR测序找到TOP1反应性B细胞。结果：SSc中TOP1反应并非静态血清抗体，"
                       "患者体内存在正在活化、扩增和进化的TOP1反应性B细胞。可比较IGHV/IGKV/IGLV基因使用、CDR3、体细胞突变、"
                       "同一克隆家族内不同细胞的关系、IgM向IgG或IgA转换——说明ATA应答具典型【抗原驱动】特征。"},
            {"n": 2, "step": "TOP1–DNA 复合物增强部分抗体识别",
             "detail": "TOP1在核内天然与DNA结合。发现对部分抗TOP1单抗，TOP1与DNA形成复合物后识别【增强】。可能解释："
                       "DNA使TOP1构象变化暴露隐藏表位／抗体识别的是TOP1–DNA复合结构／DNA负电荷与抗体表面正电荷辅助／"
                       "抗体同时接触TOP1与DNA／DNA提高TOP1局部聚集或呈递效率。"
                       "→ 真实自身抗原可能不是孤立TOP1蛋白，而是细胞损伤或死亡时释放的【核蛋白–DNA复合物】，"
                       "也解释了凋亡、组织损伤与核物质释放为何可能促进自身免疫。"},
            {"n": 3, "step": "真菌 TOP1 可能触发人的抗TOP1反应（分子模拟）",
             "detail": "部分患者来源抗TOP1克隆抗体可识别真菌抗原（尤其酿酒酵母等真菌的TOP1或相关蛋白）。逻辑链："
                       "B细胞最初识别真菌TOP1→被激活并发生突变→部分克隆逐渐也能识别人TOP1→形成交叉反应→自身免疫持续发展。"},
            {"n": 4, "step": "RA 治疗后 ACPA B细胞仍持续活化",
             "detail": "临床矛盾：炎症与关节症状可被药物控制，但ACPA常长期存在、停药后有些患者复发。发现即使临床控制良好，"
                       "ACPA B细胞仍可能持续活化——仍存在于外周血、保留活化或增殖特征、继续克隆扩增、继续产生ACPA、"
                       "自身反应性BCR库未被完全清除。→ 药物可能主要抑制炎症结果，而没有清除驱动疾病的B细胞克隆；"
                       "自身反应性记忆库仍在、只是暂时被压制，这为复发提供解释。"},
            {"n": 5, "step": "开发细胞内 CCP2 染色方法（抓低表面BCR的细胞）",
             "detail": "ACPA B细胞稀少，且部分活化B细胞或浆母细胞表面BCR表达很低。只染表面易漏掉：表面BCR低的细胞、"
                       "刚被抗原刺激BCR内吞的细胞、浆母细胞样细胞、BCR主要位于胞内合成途径的细胞。方法："
                       "①先做细胞表面标记 ②固定并通透细胞 ③用CCP2或瓜氨酸化探针染【胞内】免疫球蛋白 "
                       "④用对应的【精氨酸对照】排除非特异结合 ⑤结合IgG/CD27/CD38等识别ACPA B细胞。"
                       "提高了对低表面BCR自身反应性B细胞的检出能力。"},
        ],
        "mechanism_chain": ("微生物相似抗原 或 核蛋白–DNA复合物 → 触发自身反应性B细胞 → 克隆扩增和体细胞突变 → "
                            "产生ATA或ACPA → 疾病缓解后仍持续活化 → 形成长期自身免疫记忆和复发基础。"),
        "key_caveat": ("①真菌交叉反应【不等于】已证明真菌感染'导致'SSc；准确结论是：部分患者的克隆抗体能识别真菌抗原，"
                       "为微生物触发假说提供机制证据。②临床缓解【不等于】自身免疫反应已经停止。"),
        "tools": ["患者PBMC分离", "荧光抗原探针", "流式+单细胞分选", "单细胞培养+上清ELISA", "RNA提取/cDNA",
                  "ARTISAN/5′-RACE PCR", "Sanger或高通量BCR测序", "IMGT+克隆家族", "重组单克隆抗体",
                  "TOP1 / TOP1–DNA / 真菌抗原 ELISA", "Western blot", "表面及胞内抗原特异性B细胞染色",
                  "RA临床状态与B细胞表型关联分析"],
        "readouts": ["TOP1反应性B细胞频率与表型(初始/记忆/浆母)", "BCR序列与克隆家族", "体细胞突变与类别转换",
                     "TOP1 vs TOP1–DNA vs 真菌抗原结合", "临床缓解状态下ACPA B细胞活化度"],
        "source": "用户提供（Sam Neppelenbroek 博士论文《Dysregulation of autoreactive B cell responses in autoimmune diseases: from initial triggering to persistent activation》要点；原文PDF在另一台机器，本库依据摘要沉淀）",
    },

    # ============ Renee：把突变BCR变成靶点 ============
    "ACPA B细胞克隆进化与 BCR neoepitope 靶向清除（Renee / Target to B-cure）": {
        "disease": "RA",
        "target": "RA 的 ACPA 自身反应性B细胞及其突变BCR",
        "keywords": ["acpa", "瓜氨酸", "ccp4", "cargp4", "bcr neoepitope", "neoepitope", "mhc-i", "mhc",
                     "cd8", "netmhcpan", "ratp-ig", "k562", "ramos", "纵向", "longitudinal", "克隆进化",
                     "双轻链", "等位基因包容", "allelic inclusion", "w48", "mpo", "anca", "renee", "肽组"],
        "goal": ("研究RA中ACPA自身反应性B细胞如何长期存在、如何克隆进化，并尝试把这些B细胞【自己突变出来的BCR序列】"
                 "变成靶点——让CD8 T细胞经 pMHC-I 选择性清除致病B细胞，而保留绝大多数正常B细胞。(ERC 'Target to B-cure' 核心设想)"),
        "research_questions": [
            "ACPA B细胞的突变BCR能否被降解成短肽并经MHC-I展示，成为克隆特有的'BCR neoepitope'？",
            "能否找到或诱导识别该 肽-MHC 的CD8 T细胞，选择性杀伤ACPA B细胞？",
            "ACPA克隆一年内会不会消失或换克隆——靶点稳不稳？",
            "'分选阳性'的候选细胞里，真正的ACPA有多少？",
        ],
        "core_hypothesis": [
            "1. ACPA B细胞在细胞内合成自身BCR",
            "2. 一部分BCR会被降解成短肽",
            "3. 这些短肽可能通过MHC-I展示在B细胞表面",
            "4. 若短肽含ACPA克隆特有的突变，即成为'BCR neoepitope'",
            "5. 可寻找或诱导识别该肽-MHC复合物的CD8 T细胞",
            "6. 最终有可能选择性杀死ACPA B细胞，保留绝大多数正常B细胞",
        ],
        "steps": [
            {"n": 1, "step": "证明 BCR 肽能被 MHC-I 展示（细胞模型）",
             "detail": "最初直接分析患者来源B细胞的MHC-I肽组：质谱能检测到BCR【恒定区】肽段，但没有可靠检测到【可变区】肽段——"
                       "主要问题很可能是目标细胞太少、患者BCR非常多样、单个克隆信号被稀释。改建更可控的细胞模型："
                       "选已获得重轻链序列的ACPA单抗(如3F3/2G9/7E4)→把完整ACPA-BCR导入Ramos或K562→给K562同时导入对应患者HLA-I→"
                       "用绿色荧光/表面IgG确认BCR表达→分选阳性细胞并扩增到足够数量→纯化MHC-I分子、洗脱结合肽→"
                       "质谱寻找来自ACPA-BCR可变区/突变位点/CDR区的肽段→NetMHCpan预测这些肽与特定HLA的结合能力。"
                       "结果：患者原始B细胞中的检测未成功；但在表达重组BCR的Ramos与K562中确实可检测到BCR来源肽的MHC-I呈递，"
                       "部分候选肽还能产生识别细胞模型的CD8 T细胞反应。"},
            {"n": 2, "step": "纵向追踪 ACPA B细胞一年内的克隆进化",
             "detail": "在基线、约6个月、约12个月采集RA患者样本。流程：含瓜氨酸抗原探针标记候选ACPA B细胞→单细胞分选到96孔板→"
                       "【直接裂解】(避免长期培养造成额外损失)→SMART-Seq逆转录获cDNA→ARTISAN PCR分别扩增IgG/IgA/IgM/κ/λ→"
                       "Sanger测序重轻链→IMGT分析V/D/J、CDR3、productive、体细胞突变→按共同V/J基因与CDR3相似性重建克隆家族→"
                       "比较同一患者不同时间点的克隆组成。结果：ACPA反应【不是完全静止】——有些克隆家族跨多个时间点持续存在；"
                       "同一克隆内部出现不同突变分支和抗体亚型；也会出现后来新进入或扩增的克隆；部分IgM克隆家族也可以很大"
                       "(并非所有ACPA反应都已完全转成IgG/IgA)；一些纵向相关克隆后来经重组单抗确认确有ACPA反应性。"
                       "→ 若未来按BCR表位清除ACPA B细胞，可能需覆盖【多个主要克隆或多个稳定表位】，而非只针对一个序列。"},
            {"n": 3, "step": "'分选阳性' ≠ 真正的 ACPA B细胞",
             "detail": "抗原探针分选可能受非特异结合、链霉亲和素结合、探针聚集或BCR多反应性影响，仅凭被ACPA探针染色不能证明"
                       "其BCR一定识别瓜氨酸抗原。做法：重建候选细胞的完整单克隆抗体，再比较 CCP4等瓜氨酸抗原 / 对应精氨酸对照"
                       "CArgP4 / 其他PTM抗原 / 非相关抗原和探针组分。估计：重组成功的'ACPA分选候选'中，平均约【只有一半】"
                       "最终表现为真正的ACPA。→ 测序库中的候选细胞必须经功能性抗体实验验证。"},
            {"n": 4, "step": "建立快速重组抗体平台 RATP-Ig",
             "detail": "传统方法需把每条重链和轻链分别克隆进表达质粒，通常耗时1–3周；为验证数百个候选BCR，将RATP-Ig改造成"
                       "适配实验室现有ARTISAN PCR产物的工作流：从单细胞获BCR可变区→嵌套PCR加入同源拼接序列→与启动子、"
                       "抗体恒定区和表达元件快速组装(不必逐个建传统完整质粒克隆)→匹配的重轻链转染HEK293F→收集上清、"
                       "检测抗体产量与抗原反应性。已从IgG/κ扩展到IgA1、IgA2、IgM和λ轻链。"},
            {"n": 5, "step": "ACPA 生物学规律",
             "detail": "真正的ACPA通常具有较高的重链与轻链体细胞突变；ACPA可变区经常出现由突变引入的N-糖基化基序；"
                       "高突变和可变区糖基化可帮助富集候选，但【都不是绝对判定标准】；ACPA常表现多反应性，可同时识别"
                       "瓜氨酸化、乙酰化或氨甲酰化抗原；同一克隆家族内不同成员的抗原谱与结合强度可能变化。"
                       "关于'重链框架区W48决定瓜氨酸识别'：其抗体数据中存在【没有W48但仍ACPA阳性】的抗体，也存在"
                       "【含W48但仍能识别氨甲酰化抗原】的抗体——W48可能影响部分抗体结构和反应性，但不能作为所有ACPA的统一开关。"},
            {"n": 6, "step": "最新方向：双轻链与等位基因包容（2025年底起）",
             "detail": "假说：一条κ链与重链组合形成自身反应性BCR；另一条λ链形成弱反应性或非自身反应性BCR；两种BCR共同表达会"
                       "'稀释'表面的自身反应性；细胞因此可能逃过中枢或外周耐受清除；后续环境变化又可能让自身反应性受体产生功能。"
                       "计划：分别重建'同一重链+κ链'与'同一重链+λ链'，比较ACPA等自身抗原结合，并进行胚系回复与广谱抗原筛查。"},
            {"n": 7, "step": "支线：MPO-ANCA 血管炎",
             "detail": "参与MPO特异性BCR测序，并重组抗MPO IgG与五聚体IgM。结果提示MPO反应具多克隆性——既有接近胚系的IgM，"
                       "也有突变抗体；部分抗MPO IgM能引起明显补体沉积。⚠️但患者血清MPO-IgM水平与BVAS疾病活动度没有清晰相关。"},
        ],
        "dataset_stats": ("纵向单细胞BCR(2025汇报)：约3312个单细胞，其中1581个获质量合格重链序列。"
                          "RATP-Ig(2025)：446个抗体构建尝试，322个产生可检测抗体，成功率约72%。"
                          "ACPA分选候选中约50%经重组验证为真ACPA。"),
        "key_caveat": ("①BCR肽MHC-I呈递目前是【机制与概念验证】，不是已可用于患者的ACPA疫苗；"
                       "②分选阳性≠真ACPA（平均约只有一半），候选必须经重组抗体功能验证；"
                       "③高体细胞突变/可变区N-糖基化可富集候选但非绝对判据；"
                       "④W48不能作为所有ACPA的统一开关；"
                       "⑤抗MPO IgM虽可致补体沉积，但血清MPO-IgM与BVAS活动度无清晰相关。"),
        "tools": ["MHC-I肽组质谱(免疫沉淀+洗脱)", "Ramos/K562 BCR转导 + HLA-I导入", "NetMHCpan", "CD8 T细胞反应检测",
                  "瓜氨酸抗原探针(CCP4/CArgP4对照)", "流式单细胞分选(直接裂解)", "SMART-Seq", "ARTISAN PCR",
                  "Sanger", "IMGT + 克隆家族重建", "RATP-Ig 快速组装 + HEK293F", "ELISA(PTM抗原谱)", "补体沉积检测"],
        "readouts": ["MHC-I洗脱肽中的BCR可变区/CDR肽段", "NetMHCpan结合预测", "CD8 T细胞反应",
                     "纵向克隆家族组成与突变分支", "重组抗体的ACPA真伪与PTM交叉谱"],
        "source": "用户提供（Renee 课题 / ERC 'Target to B-cure'，含2025年汇报数据）",
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
        if p.get("research_questions"):
            out.append("研究问题：")
            for q in p["research_questions"]:
                out.append(f"  - {q}")
        if p.get("core_hypothesis"):
            out.append("核心假说：")
            for h in p["core_hypothesis"]:
                out.append(f"  {h}")
        out.append("步骤：")
        for s in p.get("steps", []):
            out.append(f"  {s['n']}. {s['step']} —— {s['detail']}")
        if p.get("reference_clones"):
            out.append("参考克隆/对照面板：")
            for k, v in p["reference_clones"].items():
                out.append(f"  - {k}：{v}")
        if p.get("worked_example"):
            out.append("完整范例：")
            for k, v in p["worked_example"].items():
                out.append(f"  - {k}：{v}")
        if p.get("dataset_stats"):
            out.append(f"数据规模：{p['dataset_stats']}")
        if p.get("mechanism_chain"):
            out.append(f"机制链：{p['mechanism_chain']}")
        if p.get("controls"):
            out.append(f"必要对照：{'、'.join(p['controls'])}")
        if p.get("tools"):
            out.append(f"工具链：{'、'.join(p['tools'])}")
        if p.get("readouts"):
            out.append(f"读出：{'、'.join(p['readouts'])}")
        if p.get("accomplishments"):
            out.append("已完成：" + "；".join(p["accomplishments"]))
    out.append(f"来源：{p.get('source', '')}")
    return "\n".join(out)


def lookup_protocol(query):
    """按关键词/疾病查协议（完整步骤）。"""
    hits = match_protocols(query)
    if not hits:
        return f"未在协议库找到「{query}」。库内：{', '.join(PROTOCOLS)}"
    return "\n\n".join(format_protocol(n, p, full=True) for n, p in hits[:3])


def researchers_text():
    """研究者线路图：谁做什么、如何衔接。"""
    lines = ["【研究者线路图】自身反应性B细胞课题"]
    for who, r in RESEARCHERS.items():
        lines.append(f"- {who}（{r['disease']}）｜抗原：{r['antigen']}\n    侧重：{r['focus']}\n    协议：{r['protocol']}")
    lines.append("\n" + RESEARCHER_LINKS)
    return "\n".join(lines)


def project_map_text():
    """项目线总览表 + 可能的主线逻辑 + 研究者线路图。"""
    lines = ["【糖工程/补体 项目线总览】操纵对象 / 核心操作 / 主要读出"]
    for i, (name, obj, op, ro) in enumerate(PROJECT_MAP, 1):
        lines.append(f"{i}. {name}：{obj} | {op} | {ro}")
    lines.append("关系：项目1、2明显相关；项目4可能是项目2的应用或独立;项目3(补体)可能与抗体项目相连或完全独立。")
    lines.append("主线逻辑：" + PROJECT_LOGIC)
    lines.append("\n" + researchers_text())
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
