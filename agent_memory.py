"""
长期记忆（兼容层）——委托给分层记忆 memory_store.MemoryStore。
保留旧 API 给 skill_system / 数据对话页使用，同时获得分层 + 审核/撤销 + 注入防护。
关键：
- add_memory：用户【主动教】的 → 存为 project_memory 且 approved（用户是本 agent 的权威）。
- add_from_observed：网页/PDF/工具等【观察内容】→ 只能进 candidate 且过注入检测（防止变成全局规则）。
- format_for_prompt：默认按【高风险】口径，只注入已审核的可信层；candidate 永不注入。
"""

from memory_store import MemoryStore

_store = MemoryStore()


def store():
    return _store


def add_memory(text, kind="fact", source="user"):
    """用户主动教它记住 → project_memory / approved。"""
    return _store.add(text, kind="project_memory", source=source,
                      created_by="user", review_status="approved")


def add_from_observed(text, source, created_by="observed"):
    """观察内容(网页/PDF/工具) → candidate + 注入检测。命中注入即隔离，永不成为全局规则。"""
    return _store.add_from_observed(text, source, created_by=created_by)


def format_for_prompt(limit=30, high_risk=True):
    return _store.for_prompt(high_risk=high_risk)


def memory_summary():
    return _store.summary()


def load_memories(kind=None, limit=None):
    rows = [r.model_dump() for r in _store.active() if kind is None or r.kind == kind]
    return rows[-limit:] if limit else rows


def delete_memory(memory_id):
    """兼容旧接口：撤销一条记忆（保留版本历史，不物理删除）。"""
    return _store.revoke(memory_id, by="user")


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(memory_summary())
    print(format_for_prompt() or "(暂无可注入记忆)")
