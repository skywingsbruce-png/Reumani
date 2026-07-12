"""
科研写作助手（后端逻辑）
—— 基于「真实检索到的文献」生成综述 / 研究空白 / SCI 段落草稿，
   通过强制「只准引用检索列表里的文献」来大幅降低 AI 编造引用的风险。

被 pages/1_科研写作助手.py（网页版）import 使用；也可单独调用测试。
"""

from ssc_pi_agent import (
    search_literature,
    judge_llm,          # Claude Opus，质量高、费用高
    deepseek_llm_pro,   # DeepSeek，省钱
)


def retrieve_literature(query: str, max_results: int = 15, preprints_only: bool = False) -> str:
    """调用 Europe PMC 真实检索，返回带链接的文献列表文本。"""
    return search_literature.invoke({
        "query": query,
        "max_results": max_results,
        "preprints_only": preprints_only,
    })


# ------------------------------------------------------------------
# 各写作场景的任务指令（对应解螺旋图里的八大场景，先做最核心的几个）
# ------------------------------------------------------------------
SCENARIOS = {
    "文献综述": (
        "请基于下方真实文献，撰写一篇结构化的中文文献综述初稿。要求：\n"
        "1. 先梳理该领域的整体逻辑框架（发病机制/诊断/治疗等主线）；\n"
        "2. 按主题分段归纳，每个论点后用 [作者, 年份] 的形式标注来源；\n"
        "3. 结尾单列一段【研究空白】，指出当前证据尚未解决、值得投入的问题；\n"
        "4. 语言符合医学综述规范，客观、有逻辑。"
    ),
    "研究空白提炼": (
        "请基于下方真实文献，专门提炼该领域当前的【研究空白 / 未解决问题】。要求：\n"
        "1. 列出 5-8 个具体、可切入的研究空白，每条说明为什么它还没被解决；\n"
        "2. 每个空白标注它是从哪几篇文献推断出来的 [作者, 年份]；\n"
        "3. 对每个空白给出一句「潜在课题方向」的建议。"
    ),
    "SCI引言草稿": (
        "请基于下方真实文献，为一篇 SCI 论文撰写规范的英文 Introduction 草稿（约 4 段）。要求：\n"
        "1. 从研究背景 → 已有进展 → 研究空白 → 本研究目的，层层递进；\n"
        "2. 需要引用文献的句子用 [作者, 年份] 占位标注，方便后续替换成期刊格式；\n"
        "3. 只使用下方列表中的文献，不得虚构。"
    ),
    "SCI讨论框架": (
        "请基于下方真实文献，为一篇 SCI 论文搭建 Discussion 部分的写作框架（中文提纲 + 关键论据）。要求：\n"
        "1. 给出讨论的段落结构（主要发现→与既往研究比较→机制解释→局限性→结论）；\n"
        "2. 在「与既往研究比较」中，指出下方哪些文献支持或矛盾，并标注 [作者, 年份]；\n"
        "3. 提示每段应补充哪些本研究自己的数据。"
    ),
}

# 强制溯源、防杜撰的通用「护栏」提示
GROUNDING_GUARD = (
    "【极其重要的规则】：\n"
    "- 下面提供的是从 PubMed / bioRxiv / medRxiv 真实检索到的文献列表（含标题、作者、期刊、年份、链接）。\n"
    "- 你【只能】引用这个列表中的文献。严禁编造列表之外的任何文献、作者、期刊或 DOI。\n"
    "- 如果某个论点在列表中找不到支持，请直接写「（此处需补充检索）」，不要凭空杜撰。\n"
    "- 每一处引用都要能对应到列表里的某一条，方便首席研究员核对。\n"
)


def generate_draft(
    scenario: str,
    topic: str,
    literature_text: str,
    model: str = "deepseek",
    extra_requirement: str = "",
) -> str:
    """根据场景 + 真实文献列表，生成写作草稿。model: 'deepseek'(省钱) 或 'claude'(质量高)。"""
    task = SCENARIOS.get(scenario, SCENARIOS["文献综述"])
    extra_block = f"\n\n【首席研究员的额外要求】：\n{extra_requirement}" if extra_requirement.strip() else ""

    prompt = (
        f"你是一位资深的医学科研写作导师，正在协助首席研究员完成「{scenario}」。\n\n"
        f"研究主题：{topic}\n\n"
        f"{GROUNDING_GUARD}\n"
        f"【本次写作任务】：\n{task}{extra_block}\n\n"
        f"【真实检索到的文献列表】：\n{literature_text}\n\n"
        f"现在请开始撰写。"
    )

    llm = judge_llm if model == "claude" else deepseek_llm_pro
    return llm.invoke(prompt).content


def refine_draft(history_prompt: str, model: str = "deepseek") -> str:
    """在已有草稿基础上，根据用户的修改要求继续润色/调整。history_prompt 已含上下文。"""
    llm = judge_llm if model == "claude" else deepseek_llm_pro
    return llm.invoke(history_prompt).content
