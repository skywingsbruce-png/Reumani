"""
SSc 评测集：客观比较不同系统在 SSc 问答上的表现，产出可写进论文的数字。
对比对象：
  - agent   : 你的技能 agent（读技能+查数据+证据卡）
  - deepseek: 直接问 DeepSeek（无检索）
  - claude  : 直接问 Claude（无检索）
打分：用 Claude 当裁判，核对答案是否覆盖关键事实(key_facts) + 是否有编造。
指标：准确率（覆盖关键事实的比例）+ 幻觉标记。
用法：python ssc_eval.py agent      /  deepseek  /  claude   [题数]
"""

import json
import re
from datetime import datetime
from pathlib import Path

# A.8.2b.2b.2：核心路径只接受**显式注入**，按科研操作取模型；缺依赖即 fail-closed，
# 绝不自动 import ssc_pi_agent。CLI 入口显式选用 pilot.legacy_compat_adapter（未受控）。
from pilot.legacy_model_bridge import ScientificOperation, require_injected_model

BASE = Path(__file__).resolve().parent
QUESTIONS_FILE = BASE / "ssc_eval_questions.json"
RESULT_DIR = BASE / "eval_results"

# A.8.2b.2b.2.1：import 期不做任何文件系统操作。题库读取与结果目录创建
# 都推迟到明确的业务调用边界（load_eval_questions / run）。
_QUESTIONS_CACHE: dict = {}


def load_eval_questions(path=None):
    """读取评测题库。**只在调用时**碰文件系统；同一路径只读一次。

    题目内容、标准答案与评分规则一概未改，仅改变读取时机。
    """
    p = Path(path) if path is not None else QUESTIONS_FILE
    key = str(p.resolve()) if p.exists() else str(p)
    if key not in _QUESTIONS_CACHE:
        _QUESTIONS_CACHE[key] = json.loads(p.read_text(encoding="utf-8"))
    return _QUESTIONS_CACHE[key]


def answer_with_agent(q):
    from ssc_skill_agent import build_skill_agent, SKILL_AGENT_SYSTEM
    agent = build_skill_agent("deepseek")
    r = agent.invoke({"messages": [("system", SKILL_AGENT_SYSTEM), ("user", q)]})
    m = r["messages"][-1]
    return m.content if hasattr(m, "content") else str(m)


def answer_plain(q, model, chat_model=None):
    """无检索基线回答（评测对照）。`chat_model` 必须显式注入。"""
    llm = require_injected_model(ScientificOperation.BASELINE_ANSWERING, chat_model)
    return llm.invoke(f"请简洁回答这个系统性硬化症(SSc)相关问题：{q}").content


def judge(q_item, answer, chat_model=None):
    """评测评分：是否覆盖关键事实 + 是否编造。返回 (0/1, 幻觉bool, 说明)。

    这是**评测评分**，不是科学论断核验 —— 见 ScientificOperation.EVALUATION_SCORING。
    """
    prompt = (
        "你是严格的评分裁判。判断下面的回答是否正确覆盖了该问题的关键事实。\n"
        f"问题：{q_item['q']}\n"
        f"关键事实(命中任一等价表述即算覆盖对应点)：{q_item['key_facts']}\n"
        f"回答：{answer}\n\n"
        "输出严格 JSON：{\"correct\": true/false（是否覆盖了核心关键事实）, "
        "\"hallucination\": true/false（是否有明显编造的事实/文献/数据）, \"note\":\"一句话\"}"
    )
    scorer = require_injected_model(ScientificOperation.EVALUATION_SCORING, chat_model)
    resp = scorer.invoke(prompt).content
    try:
        text = re.sub(r"^```(json)?|```$", "", resp.strip(), flags=re.MULTILINE).strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        d = json.loads(m.group(0) if m else text)
        return (1 if d.get("correct") else 0, bool(d.get("hallucination")), d.get("note", ""))
    except Exception:
        return (0, False, "裁判解析失败")


def run(target, limit=None, answer_model=None, scoring_model=None):
    """`answer_model` / `scoring_model` 由调用方显式提供；两者职责不同，不共用一个参数。"""
    all_qs = load_eval_questions()
    qs = all_qs[:limit] if limit else all_qs
    rows, correct, halluc = [], 0, 0
    for item in qs:
        print(f"  Q{item['id']}: {item['q'][:30]}...", flush=True)
        if target == "agent":
            ans = answer_with_agent(item["q"])
        else:
            ans = answer_plain(item["q"], target, chat_model=answer_model)
        c, h, note = judge(item, ans, chat_model=scoring_model)
        correct += c
        halluc += 1 if h else 0
        rows.append({"id": item["id"], "correct": c, "hallucination": h,
                     "note": note, "answer": ans[:400]})
    n = len(qs)
    summary = {"target": target, "n": n, "accuracy": round(correct / n, 3),
               "hallucination_rate": round(halluc / n, 3),
               "run_at": datetime.now().isoformat(timespec="seconds"), "rows": rows}
    RESULT_DIR.mkdir(exist_ok=True)          # 写之前才建目录，import 期不建
    out = RESULT_DIR / f"{target}_{n}q.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return f"[{target}] 准确率 {summary['accuracy']*100:.0f}%（{correct}/{n}），幻觉率 {summary['hallucination_rate']*100:.0f}% → {out}"


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    target = sys.argv[1] if len(sys.argv) > 1 else "deepseek"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
    # A.8.2b.2b.2：CLI 是应用入口，**显式**选用未受控的 legacy 兼容通道。
    # 回答用被测系统对应的 provider；评分固定用 Claude（与迁移前一致）。
    from pilot.legacy_compat_adapter import legacy_chat_model_for_preference
    print(run(target, limit,
              answer_model=legacy_chat_model_for_preference(target),
              scoring_model=legacy_chat_model_for_preference("claude")))
