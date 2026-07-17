"""
SSc-E1：科研资源环境（Biomni-E1 的 SSc 版）。
包含：
  - ResourceSpec：每个资源（工具/数据库/软件/know-how）的结构化定义
  - ResourceRegistry：资源注册表
  - ResourceRetriever：Tool Retriever —— 根据问题选出相关资源，而不是把所有工具塞给模型
  - DataLake：数据湖框架 —— 本地快照 + 版本/时间戳，保证可复现
第一版约 20 个资源；已实现的指向真实函数，未实现的标 implemented=False（诚实占位）。
"""

import json
import re
from datetime import datetime
from pathlib import Path

from schemas import ResourceSpec   # 统一契约（P0-3）：ResourceSpec 定义收敛到 schemas.py

BASE = Path(__file__).resolve().parent
DATA_LAKE_DIR = BASE / "data_lake"
DATA_LAKE_DIR.mkdir(exist_ok=True)


class ResourceRegistry:
    def __init__(self):
        self._items = {}

    def register(self, spec: ResourceSpec):
        self._items[spec.name] = spec

    def all(self):
        return list(self._items.values())

    def get(self, name):
        return self._items.get(name)

    def by_kind(self, kind):
        return [s for s in self._items.values() if s.kind == kind]

    def to_json(self, path=None):
        data = [s.to_dict() for s in self._items.values()]
        text = json.dumps(data, ensure_ascii=False, indent=2)
        if path:
            Path(path).write_text(text, encoding="utf-8")
        return text


# 任务类型 → 触发关键词（阶段3：任务类型匹配）
TASK_KEYWORDS = {
    "literature": ["文献", "检索", "查文献", "论文", "literature", "search", "paper", "证据", "evidence", "支持"],
    "evidence": ["证据", "样本量", "研究设计", "证据强度", "过度解读", "evidence", "grade", "验证", "verify", "靠谱"],
    "gene": ["基因", "gene", "symbol", "标准化", "别名"],
    "enrichment": ["通路", "富集", "pathway", "enrichment", "reactome", "kegg", "go ", "gsea"],
    "differential_expression": ["差异表达", "差异基因", "deg", "differential", "火山图", "volcano"],
    "clustering": ["单细胞", "scrna", "聚类", "细胞类型", "亚群", "cluster", "annotation", "scanpy"],
    "cin": ["染色体不稳定", "cin", "基因组不稳定", "微核", "chromosomal instability"],
    "clinical": ["生存", "队列", "回归", "预后", "survival", "cox", "cohort", "meta", "随访"],
    "figure": ["画图", "作图", "出图", "figure", "plot", "森林图", "火山图"],
    "dataset": ["数据集", "geo", "cellxgene", "dataset", "下载"],
}


# ==========================================
# Tool Retriever：根据问题选相关资源
# ==========================================
class ResourceRetriever:
    def __init__(self, registry: ResourceRegistry):
        self.registry = registry

    @staticmethod
    def _tokens(text):
        text = (text or "").lower()
        latin = re.findall(r"[a-z0-9]{2,}", text)
        cjk = re.findall(r"[一-鿿]{2,}", text)
        return set(latin) | set(cjk)

    def _infer_tasks(self, query):
        ql = (query or "").lower()
        active = set()
        for task, kws in TASK_KEYWORDS.items():
            if any(kw.strip().lower() in ql for kw in kws):
                active.add(task)
        return active

    def _infer_input_types(self, query):
        ql = (query or "").lower()
        it = set()
        for token, t in [("csv", "csv"), ("表达矩阵", "expr_matrix"), ("h5ad", "h5ad"),
                         ("10x", "10x"), ("基因列表", "gene_list"), ("pdf", "pdf"),
                         ("pmid", "pmid"), ("doi", "doi"), ("geo", "geo_id")]:
            if token in ql:
                it.add(t)
        return it

    def retrieve(self, query, top_k=8, kinds=None):
        q = self._tokens(query)
        active_tasks = self._infer_tasks(query)
        active_inputs = self._infer_input_types(query)
        broad = {"ssc", "systemic", "sclerosis", "硬化", "scleroderma", "硬皮病"}
        scored = []
        for s in self.registry.all():
            if kinds and s.kind not in kinds:
                continue
            hay = f"{s.name} {s.description} {' '.join(s.domains)} {s.kind}".lower()
            hay_tokens = self._tokens(hay)
            # 1) 关键词匹配
            score = len(q & hay_tokens)
            for token in q:
                if token in hay:
                    score += 1
            # 2) 领域标签匹配
            if q & broad and ("systemic-sclerosis" in s.domains or "ssc" in hay):
                score += 1
            # 3) 任务类型匹配（阶段3 新增，权重高）
            score += 3 * len(active_tasks & set(s.task_types))
            # 4) 输入格式匹配（阶段3 新增）
            score += 2 * len(active_inputs & set(s.input_types))
            if score > 0:
                scored.append((score, s))
        scored.sort(key=lambda x: (-x[0], not x[1].implemented))
        return [s for _, s in scored[:top_k]]

    def bundle_text(self, query, top_k=10):
        """把选中的资源分类列成文本，喂给 agent 当'可用资源清单'。"""
        picked = self.retrieve(query, top_k=top_k)
        if not picked:
            return "（未匹配到相关资源。）"
        groups = {"tool": [], "database": [], "software": [], "knowhow": []}
        for s in picked:
            tag = "" if s.implemented else "（未实现·占位）"
            groups.get(s.kind, groups["tool"]).append(f"- {s.name}{tag}: {s.description}")
        out = []
        titles = {"tool": "工具 Tools", "database": "数据库 Databases",
                  "software": "软件 Software", "knowhow": "Know-how"}
        for k, title in titles.items():
            if groups[k]:
                out.append(f"【{title}】\n" + "\n".join(groups[k]))
        return "\n\n".join(out)


# ==========================================
# 数据湖框架（本地快照 + 版本 + 时间戳）
# ==========================================
class DataLake:
    """轻量数据湖：处理过的结果本地缓存，保证'同一查询半年后重跑数据源不变'。"""

    def __init__(self, root=DATA_LAKE_DIR):
        self.root = Path(root)
        self.root.mkdir(exist_ok=True)

    def _key(self, namespace, query):
        safe = re.sub(r"[^\w一-鿿]+", "_", query.lower())[:60]
        d = self.root / namespace
        d.mkdir(exist_ok=True)
        return d / f"{safe}.json"

    def get(self, namespace, query):
        p = self._key(namespace, query)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def put(self, namespace, query, data):
        p = self._key(namespace, query)
        payload = {
            "namespace": namespace,
            "query": query,
            "cached_at": datetime.now().isoformat(timespec="seconds"),
            "data": data,
        }
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(p)

    def status(self):
        lines = []
        for ns_dir in sorted(self.root.iterdir()):
            if ns_dir.is_dir():
                n = len(list(ns_dir.glob("*.json")))
                lines.append(f"- {ns_dir.name}: {n} 条缓存")
        return "\n".join(lines) if lines else "（数据湖为空，目前只缓存处理过的证据卡片。）"


# ==========================================
# 种子资源（第一版 ~20 个）
# ==========================================
def build_registry() -> ResourceRegistry:
    reg = ResourceRegistry()
    SSC = ["systemic-sclerosis"]

    # ---- 工具（已实现，指向真实函数）----
    # (name, domains, description, callable_path, task_types, input_types)
    tools_impl = [
        ("search_europe_pmc", SSC + ["literature"], "检索 SSc 相关文献返回结构化元数据（Europe PMC/PubMed）",
         "ssc_pi_agent:search_literature", ["literature"], ["text"]),
        ("fetch_article_abstract", SSC + ["literature"], "获取文献摘要（Europe PMC resultType=core）",
         "ssc_evidence:search_with_abstracts", ["literature"], ["text", "pmid", "doi"]),
        ("deduplicate_articles", SSC + ["literature"], "按 PMID/DOI 去重文献列表",
         "ssc_resources:deduplicate_articles", ["literature"], ["pmid", "doi"]),
        ("extract_evidence_card", SSC + ["literature", "evidence"], "从摘要提取结构化证据卡片（研究类型/样本量/证据强度）",
         "ssc_evidence:make_evidence_cards", ["literature", "evidence"], ["text"]),
        ("grade_evidence", SSC + ["evidence"], "评估研究设计、样本量、证据等级",
         "ssc_evidence:make_evidence_cards", ["evidence"], ["text"]),
        ("verify_claim", SSC + ["evidence"], "核对某论断是否被文献支持、有没有过度解读",
         "ssc_evidence:verify_claim", ["evidence", "literature"], ["text"]),
        ("normalize_gene_symbols", SSC + ["omics", "gene"], "基因名标准化（大小写/常见别名映射，基础版）",
         "ssc_resources:standardize_genes", ["gene"], ["gene_list"]),
        ("search_reactome", SSC + ["omics", "pathway"], "Reactome 通路富集/查询（经 Enrichr Reactome_2022）",
         "skills/ssc-omics/scripts/omics.py:enrichment", ["enrichment"], ["gene_list"]),
        ("pathway_enrichment", SSC + ["omics", "pathway"], "GO/KEGG/Reactome 通路富集（Enrichr）",
         "skills/ssc-omics/scripts/omics.py:enrichment", ["enrichment"], ["gene_list"]),
        ("differential_expression", SSC + ["omics", "transcriptomics"], "两组差异表达（Welch+BH），输出可画火山图",
         "skills/ssc-omics/scripts/omics.py:differential_expression", ["differential_expression"], ["csv", "expr_matrix"]),
        ("geo_search", SSC + ["omics", "dataset"], "检索/下载 GEO 数据集",
         "skills/ssc-omics/scripts/omics.py:download_geo", ["dataset"], ["geo_id"]),
        ("cin_score", SSC + ["cin", "omics"], "CIN 染色体不稳定 signature 打分 + 与表型关联",
         "skills/ssc-cin/scripts/cin_score.py:compute_cin_score", ["cin", "differential_expression"], ["csv", "expr_matrix"]),
        ("scrna_pipeline", SSC + ["single-cell"], "单细胞 scanpy 标准流程（聚类/注释/marker）",
         "skills/ssc-scrnaseq/scripts/scrna.py:run_pipeline", ["clustering"], ["h5ad", "10x"]),
        ("survival_analysis", SSC + ["clinical"], "生存分析 KM+Cox",
         "skills/ssc-clinical-stats/scripts/clin_stats.py:km_survival", ["clinical"], ["csv"]),
        ("meta_analysis", SSC + ["clinical", "evidence"], "Meta 分析合并（固定/随机效应）",
         "skills/ssc-clinical-stats/scripts/clin_stats.py:meta_analysis", ["clinical", "evidence"], ["csv"]),
        ("make_figure", SSC + ["figure"], "出版级作图：火山图/森林图",
         "skills/ssc-data-figure/scripts/sci_plots.py", ["figure"], ["csv"]),
    ]
    for name, dom, desc, path, tasks, inputs in tools_impl:
        reg.register(ResourceSpec(name=name, kind="tool", domains=dom, description=desc,
                                  callable_path=path, implemented=True,
                                  task_types=tasks, input_types=inputs))

    # ---- 工具（已注册，未实现，诚实占位）----
    tools_planned = [
        ("cellxgene_search", SSC + ["single-cell", "dataset"], "检索 CELLxGENE SSc 单细胞数据集"),
        ("open_targets_query", SSC + ["gene", "drug"], "查询 Open Targets 的 SSc 基因-疾病-药靶证据"),
        ("clinvar_query", SSC + ["gene", "variant"], "查询 ClinVar 变异-临床意义"),
        ("hpa_query", SSC + ["gene", "protein"], "查询 Human Protein Atlas 组织/细胞表达"),
        ("ssc_gene_cell_pathway_summary", SSC + ["integration"], "汇总某基因在 SSc 的细胞-通路-证据（聚合器）"),
    ]
    for name, dom, desc in tools_planned:
        reg.register(ResourceSpec(name=name, kind="tool", domains=dom, description=desc,
                                  implemented=False, notes="第一版仅注册，待实现"))

    # ---- 数据库（注册为资源；已缓存到数据湖的标注版本）----
    def _lake_date(namespace, fname):
        p = DATA_LAKE_DIR / namespace / f"{fname}.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8")).get("downloaded_at", "")[:10]
            except Exception:
                return ""
        return ""
    dbs = [
        ("PubMed/EuropePMC", "文献", "已接入（实时+证据卡片缓存）", True, ""),
        ("GEO", "转录组数据集", "已接入检索", True, ""),
        ("CELLxGENE", "单细胞数据集", "已缓存 SSc/风湿数据集索引", True, _lake_date("cellxgene", "ssc_rheum_dataset_index")),
        ("Reactome", "通路", "已缓存 Reactome_2022 基因集", True, _lake_date("gene_sets", "Reactome_2022")),
        ("Gene Ontology", "基因本体/通路", "已缓存 GO_BP/MF 基因集", True, _lake_date("gene_sets", "GO_Biological_Process_2021")),
        ("Open Targets", "基因-疾病-药靶", "已缓存 6 个风湿病靶点", True, _lake_date("open_targets", "rheum_disease_targets")),
        ("GWAS Catalog", "遗传关联", "已缓存风湿/自免子集 7000+条", True, _lake_date("gwas", "rheum_gwas_associations")),
        ("HGNC", "基因符号/别名", "已缓存 59k+ 别名映射", True, _lake_date("hgnc", "hgnc_alias_map")),
        ("ClinVar", "变异-临床意义", "待接入", False, ""),
        ("Human Protein Atlas", "蛋白/组织表达", "待接入", False, ""),
    ]
    for name, dom, note, impl, ver in dbs:
        reg.register(ResourceSpec(name=name, kind="database", domains=SSC + [dom],
                                  description=f"{dom}数据库", implemented=impl, notes=note,
                                  data_version=ver, data_updated_at=ver))

    # ---- 软件（仅注册信息，不必全装）----
    for sw, dom in [("pandas", "数据"), ("scipy", "统计"), ("statsmodels", "统计"),
                    ("scanpy", "单细胞"), ("scvi-tools", "单细胞整合"),
                    ("gseapy", "富集"), ("decoupler", "通路活性"), ("Biopython", "序列/数据库")]:
        installed = _is_installed(sw)
        reg.register(ResourceSpec(name=sw, kind="software", domains=SSC + [dom],
                                  description=f"{dom}软件包",
                                  implemented=installed,
                                  notes="已安装" if installed else "未安装（用时再装）"))

    # ---- Know-how ----
    kh_dir = BASE / "knowhow"
    for fname, name, desc, tasks in [
        ("ssc_overview.md", "SSc研究领域概览", "SSc 三大轴、亚型、当前热点方向（含 CIN 角度）",
         ["literature", "cin"]),
        ("ssc_models.md", "SSc细胞和组织模型", "皮肤/肺样本、成纤维细胞、单细胞、动物模型的选择与坑",
         ["clustering", "differential_expression"]),
        ("evidence_grading.md", "生物医学证据分级", "研究设计层级、样本量、过度解读的识别",
         ["evidence", "literature"]),
        ("omics_confounders.md", "组学常见混杂和统计规范", "批次效应、供体级重复、多重检验、pseudobulk",
         ["differential_expression", "clustering", "enrichment"]),
    ]:
        p = kh_dir / fname
        reg.register(ResourceSpec(name=name, kind="knowhow", domains=SSC,
                                  description=desc, callable_path=str(p),
                                  implemented=p.exists(), task_types=tasks,
                                  notes=("" if p.exists() else "文件缺失")))
    return reg


# ==========================================
# 基础工具实现
# ==========================================
_COMMON_ALIASES = {
    "CDC2": "CDK1", "PARP": "PARP1", "P53": "TP53", "HER2": "ERBB2",
    "PDL1": "CD274", "PDGFRA1": "PDGFRA", "ACTA": "ACTA2",
}


def deduplicate_articles(papers):
    """按 PMID/DOI/标题 去重文献列表。papers 是 dict 列表。"""
    seen, out = set(), []
    for p in papers:
        key = str(p.get("pmid") or p.get("doi") or p.get("title", "")).strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(p)
    return out


_HGNC_ALIAS = None


def _load_hgnc():
    global _HGNC_ALIAS
    if _HGNC_ALIAS is None:
        p = DATA_LAKE_DIR / "hgnc" / "hgnc_alias_map.json"
        if p.exists():
            _HGNC_ALIAS = json.loads(p.read_text(encoding="utf-8"))["data"]["alias_to_symbol"]
        else:
            _HGNC_ALIAS = {}
    return _HGNC_ALIAS


def standardize_genes(genes):
    """基因名标准化。优先用本地 HGNC 别名表（59k+ 别名映射），无则退回基础别名。"""
    if isinstance(genes, str):
        genes = re.split(r"[,\s]+", genes)
    hgnc = _load_hgnc()
    out = []
    for g in genes:
        g = (g or "").strip().upper()
        if not g:
            continue
        if g in hgnc:               # HGNC 别名 → 官方符号
            out.append(hgnc[g])
        else:
            out.append(_COMMON_ALIASES.get(g, g))
    return out


def _is_installed(mod):
    import importlib.util
    name = mod.replace("-", "_")
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


# 便捷单例
registry = build_registry()
retriever = ResourceRetriever(registry)
data_lake = DataLake()


if __name__ == "__main__":
    print(f"已注册 {len(registry.all())} 个资源：")
    for k in ["tool", "database", "software", "knowhow"]:
        items = registry.by_kind(k)
        impl = sum(1 for s in items if s.implemented)
        print(f"  {k}: {len(items)} 个（已实现 {impl}）")
    print("\n示例：问题='SSc 皮肤单细胞成纤维细胞亚群与纤维化' → 检索到的资源：\n")
    print(retriever.bundle_text("SSc 皮肤单细胞 scRNA fibroblast 成纤维细胞 纤维化 fibrosis"))
