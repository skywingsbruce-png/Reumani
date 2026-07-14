"""
跨会话记忆层 —— agent 的"笔记本"。这是它"慢慢学习"的正确方式：不改模型权重，
而是把【你验证过的事实 / 纠正过的错误 / 确认的结论 / 实验室习惯】存下来，下次自动注入提示词。
存成 JSONL（可读、可审计、可手改），放 data_lake/ 下（已 gitignore，不入公开库）。
kind: fact(事实) / feedback(你对它的纠正或偏好) / finding(验证过的发现) / labnote(实验室约定)
"""

import json
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
MEM_DIR = BASE / "data_lake" / "agent_memory"
MEM_DIR.mkdir(parents=True, exist_ok=True)
MEM_FILE = MEM_DIR / "memory.jsonl"


def add_memory(text, kind="fact", source="user"):
    """写入一条记忆。返回该条内容。"""
    text = (text or "").strip()
    if not text:
        return None
    rec = {"ts": datetime.now().isoformat(timespec="seconds"),
           "kind": kind, "source": source, "text": text}
    with MEM_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def load_memories(kind=None, limit=None):
    """读全部记忆（可按 kind 过滤，limit 取最近 N 条）。"""
    rows = []
    if MEM_FILE.exists():
        for line in MEM_FILE.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                if kind is None or r.get("kind") == kind:
                    rows.append(r)
            except Exception:
                pass
    return rows[-limit:] if limit else rows


def delete_memory(index):
    """按行号删除一条（人工纠错用）。"""
    rows = load_memories()
    if 0 <= index < len(rows):
        del rows[index]
        MEM_FILE.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        return True
    return False


def format_for_prompt(limit=30):
    """把记忆整理成可注入系统提示的一段文本；无记忆则返回空串。"""
    rows = load_memories(limit=limit)
    if not rows:
        return ""
    label = {"fact": "事实", "feedback": "用户反馈/纠正", "finding": "已验证发现", "labnote": "实验室约定"}
    lines = ["【长期记忆（历次积累，需遵守用户的纠正与偏好）】"]
    for r in rows:
        lines.append(f"- ({label.get(r['kind'], r['kind'])}) {r['text']}")
    return "\n".join(lines)


def memory_summary():
    rows = load_memories()
    by = {}
    for r in rows:
        by[r["kind"]] = by.get(r["kind"], 0) + 1
    return f"记忆 {len(rows)} 条：" + ("、".join(f"{k}{v}" for k, v in by.items()) if by else "空")


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(memory_summary())
    print(format_for_prompt() or "(暂无记忆)")
