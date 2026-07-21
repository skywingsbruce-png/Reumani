"""
工具权限层（把 Tool Retriever 从"推荐"升级为"权限控制"）。
职责：
  user_query → select_tool_names() 确定性选出允许的工具
             → resolve() 校验都是注册表里真实存在的工具（未知名报错，不许 LLM 猜替代）
             → 高风险工具未获批准 → 不进入本任务工具集（物理排除）
             → PermissionedToolset 对每次调用做：权限校验 + 参数 schema 校验 + trace 记录
铁律：未选择的工具不能被本任务调用；工具失败返回结构化 ToolResult，不伪装成正常结果。
"""

from dataclasses import dataclass, field
from typing import Optional

from schemas import ToolResult, Provenance


class UnknownToolError(Exception):
    """工具名不在注册表 → 硬报错，禁止用相近名替代。"""


@dataclass
class ToolPolicy:
    name: str
    risk_level: str                 # low / medium / high
    requires_approval: bool         # 高风险：须人工批准才可进入工具集
    keywords: list = field(default_factory=list)   # 确定性选择用（中/英）
    core: bool = False              # 安全核心：任何任务都可用
    description: str = ""


# ==== 14 个可执行 agent 工具的策略（name 必须与 @tool 函数名一致）====
TOOL_POLICIES = {
    "search_literature": ToolPolicy("search_literature", "low", False,
        ["文献", "literature", "paper", "综述", "search", "查文献", "最新"], core=True, description="Europe PMC 检索"),
    "query_data_lake": ToolPolicy("query_data_lake", "low", False,
        ["数据湖", "语料", "corpus", "趋势", "靶点", "gwas", "基因集", "ppi", "通路"], core=True, description="本地数据湖查询"),
    "retrieve_resources": ToolPolicy("retrieve_resources", "low", False,
        ["资源", "resource"], core=True, description="E1 资源检索"),
    "list_skills": ToolPolicy("list_skills", "low", False, ["技能", "skill"], core=True, description="列技能"),
    "read_skill": ToolPolicy("read_skill", "low", False, ["技能", "skill", "手册"], description="读技能手册"),
    "lab_lookup": ToolPolicy("lab_lookup", "low", False,
        ["抗体", "antibody", "自身抗体", "流式", "flow", "marker", "protocol", "sop", "cenp", "acpa", "样本"], description="湿实验知识库"),
    "experiment_next_step": ToolPolicy("experiment_next_step", "low", False,
        ["实验", "湿实验", "下一步", "副驾", "流式", "样本", "panel", "假说"], description="实验副驾"),
    "triage_hypothesis": ToolPolicy("triage_hypothesis", "medium", False,
        ["假说", "筛杀", "triage", "signature", "geo", "相关性"], description="假说筛杀器"),
    "search_evidence": ToolPolicy("search_evidence", "low", False,
        ["证据", "evidence", "样本量", "证据强度", "研究设计"], description="两阶段证据卡"),
    "verify_claim": ToolPolicy("verify_claim", "low", False,
        ["核对", "验证", "claim", "过度解读", "把关"], description="证据验证"),
    "list_directory_pdfs": ToolPolicy("list_directory_pdfs", "low", False,
        ["pdf", "本地文献", "目录"], description="列本地PDF"),
    "read_local_pdf": ToolPolicy("read_local_pdf", "medium", False,
        ["pdf", "读文献", "本地pdf"], description="读本地PDF（访问本地文件）"),
    "read_file": ToolPolicy("read_file", "medium", False,
        ["文件", "file", "csv", "列名", "读文件"], description="读本地文本/CSV（访问本地文件）"),
    # 执行任意代码 → 高风险，必须人工批准
    "run_python": ToolPolicy("run_python", "high", True,
        ["分析", "代码", "画图", "计算", "python", "统计", "作图", "火山图", "森林图"], description="沙箱执行代码（高风险）"),
}


def all_tool_names():
    """Planner 只能从这里取真实工具名（req1）。"""
    return list(TOOL_POLICIES)


def resolve(names):
    """校验所有名字都在注册表；未知名 → UnknownToolError（req6）。返回 policies。"""
    unknown = [n for n in names if n not in TOOL_POLICIES]
    if unknown:
        raise UnknownToolError(f"未知工具名 {unknown}，注册表里没有；不得用相近工具替代。")
    return [TOOL_POLICIES[n] for n in names]


# 精确 ID 路由：问题里出现可提取的 PMID / DOI 时必须进入的工具
EXACT_ID_TOOLS = ("search_evidence",)


def routing_mode(query):
    """确定性路由判定：能提取到 PMID 或 DOI → exact_id，否则 semantic。
    只看能否**提取**，不看该标识符是否真实存在——不存在也要走精确查询并如实返回 zero_hits。"""
    from ids import extract_dois, extract_pmids
    q = query or ""
    pmids, dois = extract_pmids(q), extract_dois(q)
    return ("exact_id" if (pmids or dois) else "semantic"), pmids, dois


def select_tool_names(query):
    """确定性选出本任务相关工具：安全核心 + 关键词命中 + 精确 ID 路由。不交给 LLM 决定。"""
    q = (query or "").lower()
    sel = {n for n, p in TOOL_POLICIES.items() if p.core}
    for n, p in TOOL_POLICIES.items():
        if any(k.lower() in q for k in p.keywords):
            sel.add(n)
    # 问题含可提取的 PMID/DOI → 精确核验工具必须在集合内，
    # 且不因为标识符可能不存在就退回语义检索（search_literature 只做辅助）。
    mode, _pmids, _dois = routing_mode(query)
    if mode == "exact_id":
        sel.update(n for n in EXACT_ID_TOOLS if n in TOOL_POLICIES)
    return sorted(sel)


def apply_approvals(selected, approved=None, trace=None):
    """高风险且未批准的工具从工具集中【物理排除】，并记入 trace（req4/req5）。返回最终 allowed。"""
    approved = set(approved or [])
    allowed = []
    for n in selected:
        p = TOOL_POLICIES.get(n)
        if p and p.requires_approval and n not in approved:
            if trace is not None:
                trace.append({"event": "blocked_pending_approval", "tool": n,
                              "detail": f"risk={p.risk_level}，需人工批准"})
            continue
        allowed.append(n)
    return allowed


def _prov(name):
    return Provenance(tool_name=name, tool_version="permissioned")


class PermissionedToolset:
    """对一批已授权工具的受控执行门面：权限 + 参数校验 + 审批 + trace。
    registry: {name: {"func": callable(**params), "schema": Optional[pydantic 模型]}}。"""

    def __init__(self, allowed_names, registry, *, approved=None, trace=None):
        resolve(allowed_names)                       # 未知名 → 抛错（req6）
        self.allowed = list(dict.fromkeys(allowed_names))
        self.registry = registry
        self.approved = set(approved or [])
        self.trace = trace if trace is not None else []
        for n in self.allowed:
            self._log("selected", n)

    def _log(self, event, tool, detail=""):
        self.trace.append({"event": event, "tool": tool, "detail": detail})

    def call(self, name, **params) -> ToolResult:
        # req6：未知工具名 → 硬报错，不猜替代
        if name not in TOOL_POLICIES:
            self._log("rejected", name, "unknown_tool")
            raise UnknownToolError(f"未知工具名 {name!r}")
        # req2：未被本任务选择的工具 → 拒绝
        if name not in self.allowed:
            self._log("rejected", name, "not_selected")
            return ToolResult(ok=False, error_type="permission_denied",
                              error_message=f"工具 {name} 未被本任务授权，禁止调用", provenance=_prov(name))
        policy = TOOL_POLICIES[name]
        # req4：高风险未批准 → 拒绝
        if policy.requires_approval and name not in self.approved:
            self._log("blocked_pending_approval", name)
            return ToolResult(ok=False, error_type="approval_required",
                              error_message=f"高风险工具 {name} 需人工批准后才能执行", provenance=_prov(name))
        entry = self.registry.get(name)
        if not entry:
            self._log("rejected", name, "not_registered")
            return ToolResult(ok=False, error_type="not_registered",
                              error_message=f"工具 {name} 未提供可执行实现", provenance=_prov(name))
        # req3：参数 schema 校验
        schema = entry.get("schema")
        if schema is not None:
            try:
                schema(**params)
            except Exception as e:
                self._log("rejected", name, "invalid_params")
                return ToolResult(ok=False, error_type="invalid_params",
                                  error_message=str(e)[:300], provenance=_prov(name))
        # 执行
        self._log("call", name, str(params)[:200])
        try:
            out = entry["func"](**params)
        except Exception as e:
            self._log("error", name, str(e)[:200])
            return ToolResult(ok=False, error_type="tool_error",
                              error_message=str(e)[:300], provenance=_prov(name))
        self._log("result", name)
        return ToolResult(ok=True, data=out, provenance=_prov(name))
