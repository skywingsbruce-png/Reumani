"""
证据卡片 + 验证层（Biomni 式"证据接地"的核心）。
解决的问题：原来 search_literature 只返回标题/作者/期刊，裁判要评样本量、研究设计却只能靠标题猜。
本模块做两阶段：
  1) 检索时连摘要一起抓回（Europe PMC resultType=core）；
  2) 用 LLM 从摘要里提取结构化证据卡片（研究类型/样本量/主要发现/局限/证据强度）。
再提供 verify_claim：核对某个论断是否真被这些卡片支持、有没有过度解读。
"""

import json
import re

import requests

from ssc_pi_agent import deepseek_llm_pro, judge_llm

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def _clean_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").strip()


def search_with_abstracts(query: str, n: int = 8, preprints_only: bool = False):
    """检索并连摘要一起返回（结构化）。返回 list[dict]。"""
    q = f"{query} AND SRC:PPR" if preprints_only else query
    r = requests.get(EPMC, params={
        "query": q, "format": "json", "resultType": "core",
        "sort": "P_PDATE_D desc", "pageSize": n,
    }, timeout=30)
    r.raise_for_status()
    results = r.json().get("resultList", {}).get("result", [])
    papers = []
    for it in results:
        pmid = it.get("pmid")
        doi = it.get("doi")
        link = (f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid
                else (f"https://doi.org/{doi}" if doi else ""))
        papers.append({
            "title": it.get("title", "无标题"),
            "authors": it.get("authorString", "未知作者"),
            "journal": it.get("journalTitle") or it.get("source", ""),
            "year": (it.get("firstPublicationDate", "") or "")[:4],
            "pub_type": it.get("pubType", ""),
            "abstract": _clean_html(it.get("abstractText", "")),
            "pmid": pmid, "doi": doi, "link": link,
        })
    return papers


def _parse_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    m = re.search(r"[\[{].*[\]}]", text, re.DOTALL)
    if m:
        text = m.group(0)
    return json.loads(text)


def make_evidence_cards(papers, model: str = "deepseek"):
    """用 LLM 从摘要提取结构化证据卡片。返回 list[dict]（带 index 对应 papers）。"""
    have_abstract = [p for p in papers if p["abstract"]]
    if not have_abstract:
        return []
    llm = judge_llm if model == "claude" else deepseek_llm_pro

    listing = ""
    for i, p in enumerate(have_abstract):
        listing += (
            f"\n[{i}] 标题：{p['title']}\n类型：{p['pub_type']}\n"
            f"摘要：{p['abstract'][:1500]}\n"
        )
    prompt = (
        "你是循证医学助理。下面是若干篇文献的标题和摘要。请【只依据摘要】为每篇提取一张结构化证据卡片，"
        "严禁编造摘要里没有的信息，找不到就写\"未说明\"。\n"
        "输出严格的 JSON 数组，每个元素字段：\n"
        "{\"index\": 整数, \"study_type\": \"研究类型(RCT/队列/病例对照/横断面/综述/Meta/基础实验/单细胞等)\", "
        "\"sample_size\": \"样本量或N(未说明就写未说明)\", \"model_system\": \"研究对象/模型(人/小鼠/细胞系等)\", "
        "\"main_findings\": [\"主要发现1\",\"主要发现2\"], \"limitations\": [\"局限1\"], "
        "\"evidence_strength\": \"high/moderate/low\", \"strength_reason\": \"一句话理由\"}\n"
        "只输出 JSON 数组，不要额外文字。\n" + listing
    )
    resp = llm.invoke(prompt).content
    try:
        cards = _parse_json(resp)
    except Exception:
        return [{"index": -1, "error": "证据卡片解析失败", "raw": resp[:500]}]
    # 把原文献信息并回卡片
    for c in cards:
        idx = c.get("index")
        if isinstance(idx, int) and 0 <= idx < len(have_abstract):
            p = have_abstract[idx]
            c["title"] = p["title"]
            c["citation"] = f"{p['authors'].split(',')[0]} et al., {p['journal']}, {p['year']}"
            c["link"] = p["link"]
    return cards


def format_cards(cards) -> str:
    """把证据卡片格式化成可读文本。"""
    if not cards:
        return "（未获得可用摘要，无法生成证据卡片。）"
    lines = []
    for c in cards:
        if c.get("error"):
            lines.append(f"⚠️ {c['error']}")
            continue
        lines.append(
            f"### {c.get('title','?')}\n"
            f"- 来源：{c.get('citation','?')}  {c.get('link','')}\n"
            f"- 研究类型：{c.get('study_type','?')} ｜ 样本量：{c.get('sample_size','?')} ｜ 对象：{c.get('model_system','?')}\n"
            f"- 证据强度：**{c.get('evidence_strength','?')}**（{c.get('strength_reason','')}）\n"
            f"- 主要发现：{'；'.join(c.get('main_findings', []) or ['未说明'])}\n"
            f"- 局限：{'；'.join(c.get('limitations', []) or ['未说明'])}"
        )
    return "\n\n".join(lines)


def verify_claim(claim: str, cards, model: str = "claude") -> str:
    """核对一个论断是否真被证据卡片支持，有没有过度解读。默认用 Claude（判断更稳）。"""
    llm = judge_llm if model == "claude" else deepseek_llm_pro
    evidence = format_cards(cards)
    prompt = (
        "你是严格的证据验证员(Verifier)。下面是一个【论断】和一组【证据卡片】。"
        "请核对：\n1. 这个论断是否真的被这些证据支持？（支持/部分支持/不支持/证据不足）\n"
        "2. 支持它的是哪几篇？证据强度如何？\n3. 有没有过度解读（比如把小样本、动物实验、横断面研究当成强因果结论）？\n"
        "4. 给出一句最终裁定。\n只依据下方证据卡片，不要引入外部知识编造。\n\n"
        f"【论断】：{claim}\n\n【证据卡片】：\n{evidence}"
    )
    return llm.invoke(prompt).content
