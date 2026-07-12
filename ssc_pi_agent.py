import os
import sys
import PyPDF2
import requests
from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langchain.agents import create_agent as create_react_agent

# Windows 控制台默认用 GBK 编码，无法正确输入/输出中文和 emoji，这里切到 UTF-8
if os.name == "nt":
    os.system("chcp 65001 >nul")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stdin.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

# 默认文献库路径 = 本脚本所在目录的上一级 / Theo'S Article
# 这样换电脑、换盘符（比如从 F 盘换成 D 盘）时，只要保持
#   .../SSC/My_AGI_MrCat/ssc_pi_agent.py
#   .../SSC/Theo'S Article/
# 这个相对结构不变，路径就会自动算对，不用改代码。
DEFAULT_LIBRARY_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Theo'S Article")
)

# ==========================================
# ✋ 工具 1：阅读本地文献库（交给 Claude 主脑用）
# ==========================================

@tool
def list_directory_pdfs(directory_path: str = DEFAULT_LIBRARY_DIR) -> str:
    """列出指定文件夹下的所有 PDF 文件。如果不输入参数，默认浏览本地文献库目录。"""
    try:
        directory_path = directory_path.strip("\"'")
        if not os.path.exists(directory_path):
            return f"找不到文件夹: {directory_path}"

        pdf_files = [f for f in os.listdir(directory_path) if f.lower().endswith('.pdf')]
        if not pdf_files:
            return f"文件夹 {directory_path} 中没有找到 PDF 文件。"

        return f"文件夹 {directory_path} 下找到以下文献:\n" + "\n".join(pdf_files)
    except Exception as e:
        return f"读取文件夹失败: {e}"


@tool
def read_local_pdf(file_path: str) -> str:
    """读取指定的单个本地 PDF 文献。必须传入包含文件名的绝对路径。"""
    try:
        file_path = file_path.strip("\"'")
        text = ""
        with open(file_path, 'rb') as file:
            reader = PyPDF2.PdfReader(file)
            num_pages = min(len(reader.pages), 15)
            for page_num in range(num_pages):
                page = reader.pages[page_num]
                text += page.extract_text() + "\n"
        return text
    except Exception as e:
        return f"读取文件失败，请检查路径是否正确: {e}"


# ==========================================
# ✋ 工具 2：检索最新文献（交给 DeepSeek 辩手用，省 token）
# ==========================================

@tool
def search_literature(query: str, max_results: int = 10, preprints_only: bool = False) -> str:
    """检索系统性硬化症(SSc)相关的最新学术文献，覆盖 PubMed 已发表论文以及 bioRxiv/medRxiv 预印本，
    按最新发表时间排序返回标题、作者、期刊/来源、日期和链接。preprints_only=True 时只返回预印本。"""
    try:
        base = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        q = query if not preprints_only else f"{query} AND SRC:PPR"
        params = {
            "query": q,
            "format": "json",
            "sort": "P_PDATE_D desc",
            "pageSize": max_results,
        }
        r = requests.get(base, params=params, timeout=15)
        r.raise_for_status()
        results = r.json().get("resultList", {}).get("result", [])
        if not results:
            return "未检索到相关文献。"

        lines = []
        for item in results:
            title = item.get("title", "无标题")
            authors = item.get("authorString", "未知作者")
            journal = item.get("journalTitle") or item.get("source", "")
            date = item.get("firstPublicationDate", "")
            pmid = item.get("pmid")
            doi = item.get("doi")
            if pmid:
                link = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            elif doi:
                link = f"https://doi.org/{doi}"
            else:
                link = ""
            lines.append(f"- {title} | {authors} | {journal} | {date} | {link}")
        return "\n".join(lines)
    except Exception as e:
        return f"文献检索失败: {e}"


# ==========================================
# 🧠 大脑 1：DeepSeek 辩手（负责检索+论证，省 token）
# ==========================================

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")  # 如你的账号已开通 deepseek-v4，改成对应的模型名即可

debater_tools = [search_literature]

deepseek_llm_pro = ChatOpenAI(
    model=DEEPSEEK_MODEL,
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com",
    temperature=0.3,
)
deepseek_llm_con = ChatOpenAI(
    model=DEEPSEEK_MODEL,
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com",
    temperature=0.7,
)

debater_pro = create_react_agent(deepseek_llm_pro, debater_tools)
debater_con = create_react_agent(deepseek_llm_con, debater_tools)

PRO_SYSTEM = (
    "你是研究方向辩手A。你的专长是系统性硬化症(SSc)领域。"
    "面对首席研究员提出的问题，你必须先调用 search_literature 工具检索最新文献（近1-2年为主），"
    "然后基于检索到的真实文献提出你认为当前最值得投入的研究方向，并给出证据支持。"
    "禁止编造文献，所有论据都要能追溯到检索结果中的具体文献。"
)

CON_SYSTEM = (
    "你是研究方向辩手B。你的专长同样是系统性硬化症(SSc)领域，但你的任务是挑战辩手A的观点。"
    "你必须先调用 search_literature 工具独立检索文献（可以用不同的关键词角度，例如更聚焦生物标志物或更聚焦机制），"
    "指出辩手A论证中的薄弱环节或被忽视的证据，并提出你认为更值得投入的替代方向或补充方向。"
    "禁止编造文献，所有论据都要能追溯到检索结果中的具体文献。"
)


def _invoke(agent, system_prompt: str, user_content: str) -> str:
    response = agent.invoke({
        "messages": [
            ("system", system_prompt),
            ("user", user_content),
        ]
    })
    return response["messages"][-1].content


def run_debate(topic: str, rounds: int = 2) -> str:
    """让两个 DeepSeek 辩手围绕 topic 检索文献并展开多轮辩论，返回完整辩论记录（字符串）。"""
    transcript = []

    print("\n🔎 辩手A 正在检索文献并陈述立场...")
    pro_arg = _invoke(debater_pro, PRO_SYSTEM, f"研究问题：{topic}\n请提出你认为最值得投入的方向。")
    transcript.append(f"【辩手A - 第1轮】\n{pro_arg}")
    print(pro_arg)

    print("\n🔎 辩手B 正在检索文献并反驳/提出替代方向...")
    con_arg = _invoke(
        debater_con, CON_SYSTEM,
        f"研究问题：{topic}\n\n辩手A的观点如下：\n{pro_arg}\n\n请挑战辩手A的观点，并提出你的替代或补充方向。"
    )
    transcript.append(f"【辩手B - 第1轮】\n{con_arg}")
    print(con_arg)

    for i in range(2, rounds + 1):
        print(f"\n🔎 辩手A 第{i}轮回应...")
        pro_arg = _invoke(
            debater_pro, PRO_SYSTEM,
            f"研究问题：{topic}\n\n辩手B刚刚提出以下反驳：\n{con_arg}\n\n请回应辩手B的质疑，必要时补充新的检索证据，坚持或修正你的立场。"
        )
        transcript.append(f"【辩手A - 第{i}轮】\n{pro_arg}")
        print(pro_arg)

        print(f"\n🔎 辩手B 第{i}轮回应...")
        con_arg = _invoke(
            debater_con, CON_SYSTEM,
            f"研究问题：{topic}\n\n辩手A刚刚回应如下：\n{pro_arg}\n\n请给出你的最终回应。"
        )
        transcript.append(f"【辩手B - 第{i}轮】\n{con_arg}")
        print(con_arg)

    return "\n\n".join(transcript)


# ==========================================
# 🧠 大脑 2：Claude 主脑（裁判 + 深度分析）
# ==========================================

judge_llm = ChatAnthropic(
    model="claude-opus-4-8",
    api_key=os.environ.get("ANTHROPIC_API_KEY"),
)

judge_tools = [list_directory_pdfs, read_local_pdf]
judge_agent = create_react_agent(judge_llm, judge_tools)

JUDGE_SYSTEM = (
    "你是一位顶尖的免疫学 Principal Investigator，专长是系统性硬化症 (SSc) 和染色体不稳定 (CIN)。"
    f"你的专属文献库默认位于 {DEFAULT_LIBRARY_DIR}，可用 list_directory_pdfs / read_local_pdf 工具查阅。"
    "现在两位 DeepSeek 辩手已经分别检索文献并就研究方向进行了多轮辩论，你的任务是作为裁判："
    "1. 客观评估双方论据的证据强度（是否有真实文献支持、文献新颖度、样本量/研究类型等）；"
    "2. 指出双方论证中的漏洞或过度解读之处；"
    "3. 如果首席研究员提供了补充意见（实验室实际条件、偏好方向、已有数据等），必须在裁决中明确纳入考虑，不能忽略；"
    "4. 给出你自己的最终判断——当前最值得课题组投入的研究方向排序，并说明理由；"
    "5. 如果本地文献库中有相关积累，可调用工具交叉印证。"
    "之后首席研究员可能会针对你的裁决继续提问、反驳或要求你设计具体的下一步实验方案，"
    "请像真正的合作导师一样持续对话、随时根据首席研究员的反馈修正或细化你的建议。"
    "请用中文回复。"
)


def judge_debate(topic: str, transcript: str, user_feedback: str = "") -> list:
    """让 Claude 主脑审阅辩论记录并给出裁决。返回完整消息历史（用于后续追问）。"""
    feedback_block = (
        f"\n\n【首席研究员的补充意见，请务必纳入裁决考虑】：\n{user_feedback}" if user_feedback else ""
    )
    user_msg = (
        f"研究问题：{topic}\n\n以下是两位 DeepSeek 辩手的完整辩论记录：\n\n{transcript}"
        f"{feedback_block}\n\n请给出你的裁决报告。"
    )
    response = judge_agent.invoke({
        "messages": [
            ("system", JUDGE_SYSTEM),
            ("user", user_msg),
        ]
    })
    return response["messages"]


def run_followup(history: list) -> str:
    """裁决之后，让首席研究员可以继续和 Claude 主脑对话、追问或提出自己的看法。
    返回 'next' 表示用户想开始新的研究问题，'quit' 表示用户想退出程序。"""
    while True:
        try:
            follow_up = input(
                "\n💬 你可以继续和主脑讨论你的看法/追问/让它设计下一步实验"
                "（输入 'n' 开始新的研究问题，'q' 退出）: "
            ).strip()
        except EOFError:
            print("\nPI下线，祝您实验顺利！")
            return "quit"

        if follow_up.lower() == 'q':
            print("PI下线，祝您实验顺利！")
            return "quit"
        if follow_up.lower() == 'n':
            return "next"
        if not follow_up:
            continue

        history.append(("user", follow_up))
        response = judge_agent.invoke({"messages": history})
        history[:] = response["messages"]

        print("\n📝 主脑回复：\n")
        print(history[-1].content)
        print("-" * 50)


# ==========================================
# 🎯 持续对话模式（仅在直接运行本文件时启动命令行界面；
#    被 ssc_pi_agent_web.py 等前端 import 时不会自动跑起来）
# ==========================================

def main():
    print("🧬 PI智能体（DeepSeek 辩论 + Claude 裁判版）已上线！(输入 'q' 退出)\n")

    while True:
        try:
            user_query = input("👉 首席研究员，请提出研究方向问题: ").strip()
        except EOFError:
            print("\nPI下线，祝您实验顺利！")
            break

        if user_query.lower() == 'q':
            print("PI下线，祝您实验顺利！")
            break
        if not user_query:
            continue

        if not DEEPSEEK_API_KEY:
            print("⚠️ 未检测到 DEEPSEEK_API_KEY，请先在 .env 中配置后重试。")
            continue

        debate_transcript = run_debate(user_query, rounds=2)

        try:
            feedback = input(
                "\n💭 在裁决前，你有什么想法、实验室实际情况或倾向想让裁判纳入考虑？"
                "（直接回车跳过）: "
            ).strip()
        except EOFError:
            feedback = ""

        print("\n⚖️ Claude 主脑正在审阅辩论记录并给出最终裁决...\n")
        judge_history = judge_debate(user_query, debate_transcript, feedback)

        print("\n📋 【裁决报告】\n")
        print(judge_history[-1].content)
        print("-" * 50)

        outcome = run_followup(judge_history)
        if outcome == "quit":
            break
        # outcome == "next" -> 回到循环顶部，开始新的研究问题


if __name__ == "__main__":
    main()
