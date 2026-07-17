"""reumani.core —— 核心数据契约（当前实现在顶层 schemas.py，此处 re-export 兼容）。"""
try:
    from schemas import *          # noqa: F401,F403
except Exception:                  # 兼容层：顶层未在 path 时不硬崩
    pass
