"""
每日文献哨兵：每天自动扫描 SSc（系统性硬化症）+ SLE（系统性红斑狼疮）的新文章，
去重后存成一份带日期的中文报告；可选用 DeepSeek 写一段当日综述。

- 数据源：Europe PMC（免费，覆盖 PubMed 已发表 + bioRxiv/medRxiv 预印本）
- 去重：记住已见过的文献 ID，只报真正新增的
- 报告：写到 daily_reports/ 目录，网页「📰 每日文献」页面可查看
- 定时：由 Windows 任务计划每天自动调用本脚本（见 register_daily_task.ps1 / 我帮你注册的任务）

本脚本刻意做成「自包含」，不 import 交互式主程序，方便在无人值守的定时任务里稳定运行。
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

REPORT_DIR = BASE / "daily_reports"
REPORT_DIR.mkdir(exist_ok=True)
SEEN_FILE = REPORT_DIR / "_seen_ids.json"

# 每天扫的主题（可自行增删）
QUERIES = {
    "系统性硬化症 (SSc)": "(systemic sclerosis OR scleroderma)",
    "系统性红斑狼疮 (SLE)": '("systemic lupus erythematosus" OR "lupus nephritis" OR SLE)',
}
PER_TOPIC = 30                       # 每个主题每天最多看多少条最新文献
SUMMARIZE = os.environ.get("SSC_WATCH_SUMMARY", "1") != "0"   # 是否用 DeepSeek 写当日综述

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def fetch(query, n=PER_TOPIC):
    r = requests.get(
        EPMC,
        params={"query": query, "format": "json", "sort": "P_PDATE_D desc", "pageSize": n},
        timeout=25,
    )
    r.raise_for_status()
    return r.json().get("resultList", {}).get("result", [])


def paper_id(item):
    return str(item.get("pmid") or item.get("doi") or item.get("id") or item.get("title", ""))


def link_of(item):
    if item.get("pmid"):
        return f"https://pubmed.ncbi.nlm.nih.gov/{item['pmid']}/"
    if item.get("doi"):
        return f"https://doi.org/{item['doi']}"
    return ""


def fmt_item(item):
    title = item.get("title", "无标题")
    authors = item.get("authorString", "未知作者")
    journal = item.get("journalTitle") or item.get("source", "")
    date = item.get("firstPublicationDate", "")
    return f"- **{title}**\n  {authors} · {journal} · {date}\n  {link_of(item)}"


def load_seen():
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(sorted(seen), ensure_ascii=False), encoding="utf-8")


def summarize(new_by_topic):
    """用 DeepSeek 给当日新增文献写一段中文综述（可选，失败不影响报告）。"""
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        return ""
    try:
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(
            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            api_key=key,
            base_url="https://api.deepseek.com",
            temperature=0.3,
        )
        lit = ""
        for topic, items in new_by_topic.items():
            if items:
                lit += f"\n【{topic}】\n" + "\n".join(fmt_item(it) for it in items) + "\n"
        prompt = (
            "你是医学文献助理。下面是今天新检索到的 SSc / SLE 相关文献（真实检索结果）。"
            "请用中文写一段简短的「今日要点」（3-6 条），点出其中最值得关注的研究方向或发现，"
            "只依据下列文献，不要编造。\n" + lit
        )
        return llm.invoke(prompt).content
    except Exception as e:
        return f"（当日综述生成失败：{e}）"


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    seen = load_seen()
    first_run = len(seen) == 0

    new_by_topic = {}
    total_new = 0
    for topic, q in QUERIES.items():
        try:
            items = fetch(q)
        except Exception as e:
            new_by_topic[topic] = []
            print(f"[{topic}] 检索失败：{e}")
            continue
        fresh = [it for it in items if paper_id(it) not in seen]
        for it in fresh:
            seen.add(paper_id(it))
        new_by_topic[topic] = fresh
        total_new += len(fresh)
        print(f"[{topic}] 新增 {len(fresh)} 篇")

    # 首次运行：只建立基线，不刷屏
    if first_run:
        save_seen(seen)
        report = (
            f"# 每日文献哨兵 · {today}\n\n"
            f"✅ 首次运行，已建立文献基线（记录 {len(seen)} 篇已有文献）。\n"
            f"从下次运行起，只报告**新增**的 SSc / SLE 文章。\n"
        )
        out = REPORT_DIR / f"{today}.md"
        out.write_text(report, encoding="utf-8")
        print("首次运行完成，基线已建立。")
        return

    # 生成报告
    lines = [f"# 每日文献哨兵 · {today}\n"]
    if total_new == 0:
        lines.append("今日没有检索到符合条件的新文章。\n")
    else:
        if SUMMARIZE:
            digest = summarize(new_by_topic)
            if digest.strip():
                lines.append("## 🔖 今日要点\n")
                lines.append(digest + "\n")
        for topic, items in new_by_topic.items():
            lines.append(f"## {topic}（新增 {len(items)} 篇）\n")
            if items:
                lines.extend(fmt_item(it) for it in items)
            else:
                lines.append("（无新增）")
            lines.append("")

    save_seen(seen)
    out = REPORT_DIR / f"{today}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已生成：{out}（今日新增 {total_new} 篇）")


if __name__ == "__main__":
    # 让无控制台的定时任务也不会因编码报错
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
