"""
安全 CodeAct（阶段5，Level 2：受限 Python）。
在本地跑 AI 生成的代码前，先做防护，重点保护 3 件事：
  1) API 密钥不被窃取——运行子进程时从环境变量里剥掉所有密钥；且静态拦截读取 .env / 密钥名。
  2) 不能删文件 / 执行系统命令——静态拦截 subprocess / os.system / 删除操作。
  3) 限定工作目录 + 超时。
允许：pandas/numpy/scipy/matplotlib/scanpy 等科学计算，以及科研工具需要的联网（富集/GEO）。
诚实边界：这是 Level 2（静态扫描+环境隔离+目录限定），不是真隔离；
         真正的强隔离(Level 3)需要 Docker/WSL 容器，后期再上。
"""

import os
import re
import subprocess
import sys
from pathlib import Path

from schemas import ToolResult, Provenance   # 统一契约（P0-3）


def _prov(**params):
    """沙箱来源信息 Provenance（把沙箱级别/返回码等放进 parameters）。"""
    return Provenance(tool_name="safe_run_python", tool_version="level2", parameters=params)


BASE = Path(__file__).resolve().parent
SANDBOX_DIR = BASE / "agent_workspace"
SANDBOX_DIR.mkdir(exist_ok=True)


# 静态拦截：这些出现即拒绝执行
_FORBIDDEN = [
    (r"\bimport\s+subprocess\b", "禁止 subprocess"),
    (r"\bsubprocess\.", "禁止 subprocess"),
    (r"\bos\.system\b", "禁止 os.system"),
    (r"\bos\.popen\b", "禁止 os.popen"),
    (r"\bos\.remove\b", "禁止删除文件"),
    (r"\bos\.unlink\b", "禁止删除文件"),
    (r"\bos\.rmdir\b", "禁止删除目录"),
    (r"\bshutil\.rmtree\b", "禁止递归删除"),
    (r"\.env\b", "禁止访问 .env"),
    (r"ANTHROPIC_API_KEY|DEEPSEEK_API_KEY|OPENAI_KEY|OPENAI_API_KEY", "禁止访问 API 密钥"),
    (r"\bpip\s+install\b|pip\.main|install\(", "禁止在代码里装包"),
    (r"__import__\s*\(\s*['\"]subprocess", "禁止动态导入 subprocess"),
]


def _scan(code: str):
    for pat, why in _FORBIDDEN:
        if re.search(pat, code):
            return why
    return None


def _scrubbed_env():
    """给子进程一份剥掉所有密钥的环境变量。"""
    env = {}
    for k, v in os.environ.items():
        ku = k.upper()
        if any(s in ku for s in ("API_KEY", "TOKEN", "SECRET", "ANTHROPIC", "DEEPSEEK", "OPENAI", "PASSWORD")):
            continue
        env[k] = v
    return env


def safe_run_python(code: str, timeout: int = 180) -> ToolResult:
    """安全执行 Python。返回结构化 ToolResult。"""
    blocked = _scan(code)
    if blocked:
        return ToolResult(ok=False, error_type="blocked",
                          error_message=f"代码被安全策略拦截：{blocked}。请改用允许的操作。",
                          provenance=_prov(reason=blocked))
    script = SANDBOX_DIR / "_agent_run.py"
    script.write_text(code, encoding="utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(SANDBOX_DIR),
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
            env=_scrubbed_env(),                 # 密钥已剥离
        )
        out = (proc.stdout or "")
        if proc.stderr:
            out += "\n[stderr]\n" + proc.stderr
        if proc.returncode != 0:
            return ToolResult(ok=False, error_type="runtime_error",
                              error_message=out[:6000] or "非零退出且无输出",
                              provenance=_prov(returncode=proc.returncode))
        if not out.strip():
            out = "（已执行，无输出。若是画图，检查 agent_workspace 是否生成 png。）"
        return ToolResult(ok=True, data=out[:8000],
                          provenance=_prov(cwd=str(SANDBOX_DIR)))
    except subprocess.TimeoutExpired:
        return ToolResult(ok=False, error_type="timeout",
                          error_message=f"执行超时（>{timeout}s）", provenance=_prov())
    except Exception as e:
        return ToolResult(ok=False, error_type="exec_error", error_message=str(e),
                          provenance=_prov())


if __name__ == "__main__":
    # 自检：拦截 + 放行 + 密钥剥离（不调 API）
    print("拦截 subprocess:", safe_run_python("import subprocess").error_type)
    print("拦截读 .env:", safe_run_python("open('.env').read()").error_type)
    print("放行 pandas:", safe_run_python("import pandas; print('pandas', pandas.__version__)").ok)
    r = safe_run_python("import os; print('KEY=', os.environ.get('DEEPSEEK_API_KEY'))")
    print("密钥已剥离(应打印 None):", r.data)
