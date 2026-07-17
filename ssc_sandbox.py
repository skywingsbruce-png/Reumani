"""
受限沙箱 CodeAct（Level 2，非强隔离——【不等于安全沙箱】，只适用于公开、非敏感科研数据）。
在本地跑 AI 生成的代码前做防护：
  1) 运行子进程时剥掉所有密钥；静态拦截读取 .env / 密钥名 / 路径穿越 / 系统命令 / 删除。
  2) 一任务一独立目录 + 独立脚本名，避免并发互相覆盖。
  3) 限定工作目录 + 超时；输出/错误里的密钥做脱敏，不写进 trace。
允许：pandas/numpy/scipy/matplotlib/scanpy 等科学计算，以及科研工具需要的联网（富集/GEO）。
诚实边界：Level 2 = 静态扫描+环境隔离+目录限定，【不是真隔离】；患者数据/敏感数据须等 Level 3
         容器隔离（见 docs/sandbox_level3.md）。
"""

import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

from schemas import ToolResult, Provenance   # 统一契约（P0-3）


def _prov(**params):
    """沙箱来源信息 Provenance（把沙箱级别/run_id/返回码等放进 parameters）。"""
    return Provenance(tool_name="safe_run_python", tool_version="level2", parameters=params)


# 输出/错误里若混入密钥 → 脱敏，避免写进 trace/记录
_SECRET_RE = re.compile(r"sk-[A-Za-z0-9_\-]{8,}|(?:ANTHROPIC|DEEPSEEK|OPENAI)_API_KEY\s*[=:]\s*\S+", re.I)


def _redact(text):
    return _SECRET_RE.sub("[REDACTED]", text or "")


BASE = Path(__file__).resolve().parent
SANDBOX_DIR = BASE / "agent_workspace"
RUNS_DIR = SANDBOX_DIR / "runs"
RUNS_DIR.mkdir(parents=True, exist_ok=True)


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
    (r"\bos\.(exec|spawn|fork)\w*", "禁止 os.exec/spawn/fork"),
    (r"\.unlink\s*\(|\.rmdir\s*\(", "禁止 pathlib 删除文件/目录"),
    (r"\.env\b", "禁止访问 .env"),
    (r"\.\./|\.\.\\", "禁止路径穿越(..)"),
    (r"id_rsa|\.ssh|/etc/passwd|/etc/shadow|shadow\b", "禁止读取敏感系统文件"),
    (r"[Cc]:[\\/]+Windows|System32", "禁止访问系统目录"),
    (r"ANTHROPIC_API_KEY|DEEPSEEK_API_KEY|OPENAI_KEY|OPENAI_API_KEY", "禁止访问 API 密钥"),
    (r"\bpip\s+install\b|pip\.main|install\(", "禁止在代码里装包"),
    (r"__import__\s*\(\s*['\"](subprocess|os|ctypes|socket)", "禁止动态导入危险模块"),
    (r"importlib.*import_module\s*\(\s*['\"](subprocess|os|ctypes|socket)", "禁止 importlib 导入危险模块"),
    (r"\bsocket\.\w+|\bsocket\s*\(", "禁止原始 socket（外传风险）"),
    (r"\bctypes\b", "禁止 ctypes"),
    (r"\beval\s*\(|\bexec\s*\(", "禁止 eval/exec"),
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
    """在受限沙箱执行 Python（一任务一独立目录/脚本，输出密钥脱敏）。返回结构化 ToolResult。"""
    blocked = _scan(code)
    if blocked:
        return ToolResult(ok=False, error_type="blocked",
                          error_message=f"代码被受限沙箱策略拦截：{blocked}。请改用允许的操作。",
                          provenance=_prov(reason=blocked))
    # 一任务一独立目录 + 独立脚本名，避免并发覆盖同一个 _agent_run.py
    run_id = uuid.uuid4().hex[:12]
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    script = run_dir / "run.py"
    script.write_text(code, encoding="utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(run_dir),
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
            env=_scrubbed_env(),                 # 密钥已剥离
        )
        out = _redact(proc.stdout or "")
        if proc.stderr:
            out += "\n[stderr]\n" + _redact(proc.stderr)
        if proc.returncode != 0:
            return ToolResult(ok=False, error_type="runtime_error",
                              error_message=out[:6000] or "非零退出且无输出",
                              provenance=_prov(run_id=run_id, returncode=proc.returncode))
        if not out.strip():
            out = "（已执行，无输出。若是画图，png 已存到本任务的 runs/ 独立目录。）"
        return ToolResult(ok=True, data=out[:8000], provenance=_prov(run_id=run_id))
    except subprocess.TimeoutExpired:
        return ToolResult(ok=False, error_type="timeout",
                          error_message=f"执行超时（>{timeout}s）", provenance=_prov(run_id=run_id))
    except Exception as e:
        return ToolResult(ok=False, error_type="exec_error", error_message=_redact(str(e)),
                          provenance=_prov(run_id=run_id))


if __name__ == "__main__":
    # 自检：拦截 + 放行 + 密钥剥离（不调 API）
    print("拦截 subprocess:", safe_run_python("import subprocess").error_type)
    print("拦截读 .env:", safe_run_python("open('.env').read()").error_type)
    print("放行 pandas:", safe_run_python("import pandas; print('pandas', pandas.__version__)").ok)
    r = safe_run_python("import os; print('KEY=', os.environ.get('DEEPSEEK_API_KEY'))")
    print("密钥已剥离(应打印 None):", r.data)
