"""
安全辅助：检测【工具返回】和【长期记忆】里的提示注入（instruction injection）。
用途：观察到的内容(工具结果/记忆/文档)是数据不是指令；若含"忽略以上指令""泄露密钥"等
      模式，应标记可疑并交人工，不能当作命令执行。
"""

import re

_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+|the\s+|any\s+)?(previous|above|prior|earlier)\s+(instructions|prompt|rules)",
    r"disregard\s+.{0,25}(instructions|rules|prompt)",
    r"忽略(以上|之前|前面|上面).{0,6}(指令|提示|规则)",
    r"(from now on|from now|从现在起|接下来)\s*(you|your|你)",
    r"you are now\b|重新设定|reset your (instructions|role)",
    r"(reveal|print|leak|输出|打印|告诉我).{0,10}(api.?key|密钥|token|password|口令|secret|凭据)",
    r"(run|execute|运行|执行).{0,12}(the following|以下|下面).{0,12}(code|command|命令|代码)",
    r"forward\s+.{0,25}(email|emails|data|数据)\s+to",
    r"system\s*prompt|developer\s*message|系统提示词",
]


def detect_prompt_injection(text):
    """返回命中的可疑模式列表；非空表示疑似提示注入。"""
    t = (text or "").lower()
    return [p for p in _INJECTION_PATTERNS if re.search(p, t)]


def is_suspicious(text) -> bool:
    return bool(detect_prompt_injection(text))
